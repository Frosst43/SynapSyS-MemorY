#!/usr/bin/env python3
"""Drive stock OpenCode across a project switch against a simulated chat server."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time

from synapsys_memory.opencode_runtime import native_binary, private_write, repository_runtime
from synapsys_memory.runtime_bridge import CHAT_PATH, LOOPBACK, MAX_REQUEST
from synapsys_memory.sandbox import _trusted_bwrap

from verify_opencode_interactive_memory_get import (
    PROJECT_A, READ_TOOLS, Terminal, disposable_broker, require, verify_switch,
)

MODEL = "fixture-local:chat"
TOOL = "synapsys-memory_memory_get"
PROMPT = "Check the runtime bridge for "


def _text(content):
    if isinstance(content, list):
        return "".join(part["text"] for part in content if part["type"] == "text")
    return content or ""


def _result(message):
    value = json.loads(_text(message["content"]))
    return value["result"] if isinstance(value, dict) and "result" in value else value


class SimulatedRuntime(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__((LOOPBACK, 0), ChatFixture)
        self.projects = {}
        self.completed = set()
        self.unexpected = []
        self.hold_closed = Event()


class ChatFixture(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        self.server.unexpected.append("GET reached the runtime")
        self.send_error(405)

    def do_POST(self):
        try:
            self._chat()
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            self.server.unexpected.append(str(exc))
            self.send_error(400, "Unexpected simulated runtime request")

    def _chat(self):
        require(self.path == CHAT_PATH, "Unapproved route reached the runtime")
        require("Authorization" not in self.headers and "Cookie" not in self.headers,
                "Client headers reached the runtime")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        require(body["model"] == MODEL, "Unapproved model reached the runtime")
        messages = body["messages"]
        user_text = [_text(item.get("content")) for item in messages if item["role"] == "user"]
        if user_text == ["hold fixture"]:
            self._headers("text/event-stream")
            self.wfile.write(b"data: holding\n\n")
            self.connection.settimeout(15)
            try:
                if not self.connection.recv(1):
                    self.server.hold_closed.set()
            except ConnectionResetError:
                self.server.hold_closed.set()
            except TimeoutError:
                pass
            return
        if user_text == ["redirect fixture"]:
            self.send_response(307)
            self.send_header("Location", "/api/pull")
            self.end_headers()
            return
        require("denied fixture" not in user_text, "Rejected request reached the runtime")

        tools = [item["function"]["name"] for item in body.get("tools", [])]
        if TOOL not in tools:
            self._reply(body, {"content": "Runtime fixture"}, "stop")
            return
        project, = (name for name in self.server.projects if PROMPT + name in user_text)
        fixture = self.server.projects[project]
        read_tools = sorted(name.removeprefix("synapsys-memory_") for name in tools
                            if name.startswith("synapsys-memory_"))
        require(set(read_tools) == READ_TOOLS, "Unexpected tools exposed to the runtime")
        results = {item["tool_call_id"]: item for item in messages if item["role"] == "tool"}
        if "fixture-own" not in results:
            self._call(body, "fixture-own", fixture["own"]["id"])
            return
        own = _result(results["fixture-own"])
        require(
            isinstance(own, dict) and own["id"] == fixture["own"]["id"]
            and own["space"] == fixture["space"] and own["project"] == fixture["project"]
            and own["revision"] == fixture["own"]["revision"]
            and hashlib.sha256(own["body"].encode()).hexdigest() == fixture["own"]["sha256"],
            "OpenCode did not return the project's synthetic record",
        )
        if "fixture-foreign" not in results:
            self._call(body, "fixture-foreign", fixture["foreign"])
            return
        require(_result(results["fixture-foreign"]) is None, "OpenCode exposed a different project's memory")
        require(project not in self.server.completed, "Project completed twice")
        self.server.completed.add(project)
        self._reply(body, {"content": "SYMEM_RUNTIME_PASS"}, "stop")

    def _call(self, body, call_id, record_id):
        self._reply(body, {"tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": TOOL, "arguments": json.dumps({"id": record_id})},
        }]}, "tool_calls")

    def _headers(self, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()

    def _reply(self, request, delta, reason):
        base = {"id": "chatcmpl-fixture", "created": 1, "model": MODEL}
        if request.get("stream"):
            self._headers("text/event-stream")
            for part, finish in (({"role": "assistant", **delta}, None), ({}, reason)):
                chunk = {**base, "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": part, "finish_reason": finish}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self._headers("application/json")
            self.wfile.write(json.dumps({
                **base, "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", **delta}, "finish_reason": reason}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }).encode())


class UnixClient(HTTPConnection):
    def __init__(self, path: Path):
        super().__init__("localhost", timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def chat_body(text: str, *, model: str = MODEL) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": text}], "stream": True}


def assert_refused(path: Path) -> None:
    image = chat_body("denied fixture")
    image["messages"][0]["content"] = [{"type": "image_url", "image_url": {"url": "http://127.0.0.1/private"}}]
    external_tool = {**chat_body("denied fixture"), "tools": [{"type": "web_search"}]}
    for method, route, body, status in (
        ("GET", "/v1/models", None, 501),
        ("POST", "/api/pull", chat_body("denied fixture"), 403),
        ("POST", CHAT_PATH + "?proxy=1", chat_body("denied fixture"), 403),
        ("POST", CHAT_PATH, chat_body("denied fixture", model="other-model"), 403),
        ("POST", CHAT_PATH, image, 403),
        ("POST", CHAT_PATH, external_tool, 403),
        ("POST", CHAT_PATH, chat_body("redirect fixture"), 502),
    ):
        with closing(UnixClient(path)) as connection:
            connection.request(method, route, None if body is None else json.dumps(body),
                               {"Content-Type": "application/json", "Cookie": "test-only"})
            with connection.getresponse() as response:
                require(response.status == status, "Runtime bridge accepted a forbidden request")

    with closing(UnixClient(path)) as connection:
        connection.request("POST", CHAT_PATH, iter([json.dumps(chat_body("denied fixture")).encode()]),
                           {"Content-Type": "application/json"}, encode_chunked=True)
        with connection.getresponse() as response:
            require(response.status == 400, "Runtime bridge accepted chunked request framing")
    with closing(UnixClient(path)) as connection:
        connection.request("POST", CHAT_PATH, headers={
            "Content-Type": "application/json", "Content-Length": str(MAX_REQUEST + 1),
        })
        with connection.getresponse() as response:
            require(response.status == 413, "Runtime bridge accepted an oversized request")
    with closing(UnixClient(path)) as connection:
        duplicate_model = json.dumps(chat_body("denied fixture"))[:-1] + ',"model":"other-model"}'
        connection.request("POST", CHAT_PATH, duplicate_model, {"Content-Type": "application/json"})
        with connection.getresponse() as response:
            require(response.status == 403, "Runtime bridge accepted duplicate model fields")


def run(opencode: Path) -> dict:
    venv, source = repository_runtime()
    _trusted_bwrap()
    opencode = native_binary(opencode)
    with tempfile.TemporaryDirectory(prefix="symem-runtime-", dir="/tmp") as temporary, SimulatedRuntime() as upstream:
        root = Path(temporary)
        runtime = root / "run"
        env = {
            "HOME": str(root), "PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONPATH": str(source),
            "PYTHONDONTWRITEBYTECODE": "1", "XDG_RUNTIME_DIR": str(runtime),
            "XDG_CONFIG_HOME": str(root / "config"),
        }
        thread = Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        sockets = {}
        holder = None
        held_response = None

        def prepare(workspace: Path) -> None:
            upstream.projects[workspace.name] = json.loads((workspace / "symem-probe.json").read_text())
            permissions = [{"action": "*", "resource": "*", "effect": "deny"}] + [
                {"action": f"synapsys-memory_{tool}", "resource": "*", "effect": "allow"}
                for tool in sorted(READ_TOOLS)
            ]
            private_write(workspace / "opencode.json", json.dumps({"permissions": permissions}))

        def verify(terminal: Terminal, workspace: Path, _init: int) -> None:
            nonlocal holder, held_response
            path, = runtime.glob("symem-controller-*/owner-*.sock")
            sockets[workspace.name] = path
            require(path.stat().st_mode & 0o777 == 0o600, "Runtime socket is not private")
            if workspace.name != PROJECT_A:
                require(path != sockets[PROJECT_A] and not sockets[PROJECT_A].exists(),
                        "The old runtime socket survived the switch")
                require(upstream.hold_closed.wait(5), "The old upstream connection survived the switch")
                require(held_response.read() == b"", "The old client connection survived the switch")
            assert_refused(path)
            terminal.expect(rb"1 MCP", "OpenCode did not connect to MCP")
            prompt = (PROMPT + workspace.name).encode()
            terminal.send(b"\x1b[200~" + prompt + b"\x1b[201~")
            terminal.expect(prompt, "Synthetic prompt did not reach OpenCode")
            time.sleep(0.2)
            terminal.send(b"\r")
            terminal.expect(rb"SYMEM_RUNTIME_PASS", "OpenCode did not complete the simulated HTTP tool workflow")
            require(workspace.name in upstream.completed, "The simulated server did not observe both MCP results")
            if workspace.name == PROJECT_A:
                holder = UnixClient(path)
                holder.request("POST", CHAT_PATH, json.dumps(chat_body("hold fixture")),
                               {"Content-Type": "application/json"})
                held_response = holder.getresponse()
                require(held_response.readline() == b"data: holding\n", "The bridge buffered streaming output")
                require(held_response.readline() == b"\n", "Invalid streaming fixture")

        try:
            with disposable_broker(root, venv / "bin/synapsys-memory-broker", env) as (admin, data, key):
                verify_switch(
                    root, opencode, admin, data, key, env, prepare_project=prepare, verify_client=verify,
                    controller_options=("--runtime-model", MODEL, "--runtime-port", str(upstream.server_port)),
                )
            require(len(upstream.completed) == 2 and not upstream.unexpected,
                    "Unexpected traffic reached the simulated runtime")
            require(all(not path.exists() for path in sockets.values()), "Runtime socket remained after exit")
        finally:
            if held_response is not None:
                held_response.close()
            if holder is not None:
                holder.close()
            upstream.shutdown()
            thread.join(timeout=5)
    require(not root.exists(), "Disposable test files remained")
    return {
        "result": "PASS", "installed_opencode": True, "runtime": "simulated loopback HTTP",
        "approved_chat_model_only": True, "forbidden_requests_refused": True,
        "client_credentials_stripped": True, "network_namespace_separate": True,
        "streaming": True, "mcp_calls": 4, "foreign_memory_null_in_both_projects": True,
        "old_runtime_socket_removed": True, "old_in_flight_request_closed": True,
        "disposable_database_removed": True, "model_inference": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", type=Path, default=Path.home() / ".opencode/bin/opencode")
    args = parser.parse_args()

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        result = run(args.opencode)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
