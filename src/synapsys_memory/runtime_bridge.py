"""Session-scoped access to one loopback chat endpoint from Bubblewrap."""
from __future__ import annotations

import argparse
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import select
import socket
from socketserver import BaseRequestHandler, TCPServer, ThreadingMixIn, UnixStreamServer
import struct
import subprocess
import sys
from threading import BoundedSemaphore, Thread

from .secureio import strict_json_loads

LOOPBACK = "127.0.0.1"
RUNTIME_PORT = 11434
SANDBOX_SOCKET = "/run/synapsys-runtime.sock"
CHAT_PATH = "/v1/chat/completions"
MAX_REQUEST = 1024 * 1024
IO_TIMEOUT = 120
_MODEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._:/-]{0,127}")
_CHAT_FIELDS = frozenset({
    "model", "messages", "stream", "stream_options", "tools", "tool_choice", "temperature",
    "top_p", "frequency_penalty", "presence_penalty", "max_tokens", "max_completion_tokens",
    "seed", "stop", "response_format", "reasoning_effort", "parallel_tool_calls", "logprobs",
    "top_logprobs", "logit_bias", "user", "n",
})
_MESSAGE_FIELDS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id", "reasoning_content"})


def validate_model(model: str) -> str:
    if not _MODEL.fullmatch(model):
        raise ValueError("Identificador de modelo local no válido")
    return model


def validate_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ValueError("El puerto del runtime local debe estar entre 1 y 65535")
    return port


def _text_content(value: object) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, list) and all(
            isinstance(part, dict) and set(part) == {"type", "text"}
            and part["type"] == "text" and isinstance(part["text"], str)
            for part in value
        )
    )


def _function_tool(value: object) -> bool:
    return (
        isinstance(value, dict) and set(value) == {"type", "function"}
        and value["type"] == "function" and isinstance(value["function"], dict)
    )


def _chat_body(raw: bytes, model: str) -> bytes:
    value = strict_json_loads(raw)
    if not isinstance(value, dict) or value.get("model") != model or set(value) - _CHAT_FIELDS:
        raise ValueError("Chat request outside runtime policy")
    messages = value.get("messages")
    if not isinstance(messages, list) or not messages or any(
        not isinstance(item, dict) or set(item) - _MESSAGE_FIELDS
        or item.get("role") not in ("system", "developer", "user", "assistant", "tool")
        or not _text_content(item.get("content"))
        for item in messages
    ):
        raise ValueError("Only text chat messages are allowed")
    tools = value.get("tools", [])
    choice = value.get("tool_choice")
    if not isinstance(tools, list) or not all(_function_tool(tool) for tool in tools):
        raise ValueError("Only function tools are allowed")
    if choice not in (None, "auto", "none", "required") and not _function_tool(choice):
        raise ValueError("Only function tool selection is allowed")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class _LimitedThreads(ThreadingMixIn):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _HostServer(_LimitedThreads, UnixStreamServer):
    def __init__(self, path: Path, port: int, model: str) -> None:
        self.port = validate_port(port)
        self.model = validate_model(model)
        super().__init__(str(path), _ChatHandler)

    def verify_request(self, request, _address):
        credentials = request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("=iii"))
        _, uid, _ = struct.unpack("=iii", credentials)
        return uid == os.geteuid()


class _ChatHandler(BaseHTTPRequestHandler):
    # One request per connection. The upstream request has fresh framing and headers.
    protocol_version = "HTTP/1.0"
    timeout = 10

    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        if self.path != CHAT_PATH:
            self.send_error(403, "Only chat completions are allowed")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (
            len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,8}", lengths[0])
            or self.headers.get_all("Transfer-Encoding")
            or self.headers.get_content_type() != "application/json"
        ):
            self.send_error(400, "A fixed-length JSON body is required")
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_REQUEST:
            self.send_error(413, "Invalid request size")
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request")
            body = _chat_body(raw, self.server.model)
        except (ValueError, RecursionError):
            self.send_error(403, "Request outside runtime policy")
            return

        upstream = HTTPConnection(LOOPBACK, self.server.port, timeout=IO_TIMEOUT)
        started = False
        try:
            upstream.request("POST", CHAT_PATH, body, headers={"Content-Type": "application/json"})
            with upstream.getresponse() as response:
                if 300 <= response.status < 400:
                    self.send_error(502, "Runtime redirects are disabled")
                    return
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get_content_type())
                self.send_header("Connection", "close")
                self.end_headers()
                started = True
                while chunk := response.read1(16384):
                    self.wfile.write(chunk)
        except (OSError, HTTPException):
            if not started:
                self.send_error(502, "Local runtime unavailable")
        finally:
            upstream.close()


class _RelayHandler(BaseRequestHandler):
    def handle(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
                upstream.settimeout(IO_TIMEOUT)
                self.request.settimeout(IO_TIMEOUT)
                upstream.connect(SANDBOX_SOCKET)
                destinations = {self.request: upstream, upstream: self.request}
                while destinations:
                    ready, _, _ = select.select(tuple(destinations), [], [], IO_TIMEOUT)
                    if not ready:
                        return
                    for source in ready:
                        target = destinations[source]
                        chunk = source.recv(16384)
                        if chunk:
                            target.sendall(chunk)
                        else:
                            target.shutdown(socket.SHUT_WR)
                            del destinations[source]
        except OSError:
            pass


class _LoopbackServer(_LimitedThreads, TCPServer):
    pass


class RuntimeBridge:
    """Own one Unix listener and its in-flight connections for a session."""

    def __init__(self, path: Path, port: int, model: str):
        self.path = path
        self.port = port
        self.model = model
        self.process = None

    def start(self) -> None:
        if os.path.lexists(self.path):
            raise FileExistsError("El socket de runtime de esta sesión ya existe")
        self.process = subprocess.Popen(
            [sys.executable, "-IB", "-m", __name__, "host", str(self.path), str(self.port), self.model],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, umask=0o077, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        output = self.process.stdout
        if not select.select([output], [], [], 5)[0] or output.readline() != b"READY\n":
            raise RuntimeError("El puente al runtime local no pudo arrancar")

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        self.path.unlink(missing_ok=True)
        self.process = None


def _serve_host(path: Path, port: int, model: str) -> None:
    # The controller's pipe is a lease. EOF tears down even in-flight requests.
    with _HostServer(path, port, model) as server:
        try:
            path.chmod(0o600)
            server.timeout = 0
            print("READY", flush=True)
            while True:
                readable, _, _ = select.select([sys.stdin, server], [], [])
                if sys.stdin in readable:
                    return
                server.handle_request()
        finally:
            path.unlink(missing_ok=True)


def _run_client(command: list[str]) -> int:
    with _LoopbackServer((LOOPBACK, RUNTIME_PORT), _RelayHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.run(command, check=False).returncode
        finally:
            server.shutdown()
            thread.join()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    host = commands.add_parser("host")
    host.add_argument("path", type=Path)
    host.add_argument("port", type=int)
    host.add_argument("model")
    client = commands.add_parser("client")
    client.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == "host":
        _serve_host(args.path, args.port, args.model)
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return _run_client(command)


if __name__ == "__main__":
    raise SystemExit(main())
