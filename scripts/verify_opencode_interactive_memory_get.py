#!/usr/bin/env python3
"""Verify memory_get from OpenCode's interactive TUI across an approved A-to-B switch."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import secrets
import select
import signal
import subprocess
import sys
import tarfile
import tempfile
import termios
import time
from typing import Callable

from synapsys_memory.broker import BrokerClient, admin_request
from synapsys_memory.opencode_runtime import PILOT_BINARY, native_binary, private_write, repository_runtime
from synapsys_memory.sandbox import _trusted_bwrap
from synapsys_memory.secureio import read_private
from synapsys_memory.session_secret import read_token_file

from diagnose_opencode_config import redact
from verify_opencode_memory_get import BUN_VERSION, OPENCODE_COMMIT, OPENCODE_VERSION, bun_environment, prepare_checkout
from verify_persistent_memory import assert_forbidden

HERE = Path(__file__).resolve().parent
PROJECT_A, PROJECT_B = "project-a", "project-b"
READ_TOOLS = {"memory_get", "memory_recall", "memory_audit", "memory_recent", "memory_stats"}
ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def wait_for(check, message: str, *, timeout: float = 25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.025)
    raise TimeoutError(message)


def build_probe(root: Path, opencode: Path) -> Path:
    checkout = prepare_checkout(root, opencode, "@opencode/cli")
    patch = HERE / "fixtures/opencode-2.2.5-interactive-memory-get.patch"
    env = bun_environment(root)
    subprocess.run(
        ["/usr/bin/git", "apply", str(patch)], cwd=checkout, env=env, check=True, timeout=10,
    )
    env.update(OPENCODE_CHANNEL="local", OPENCODE_VERSION=OPENCODE_VERSION)
    print("Building the temporary OpenCode TUI probe", file=sys.stderr, flush=True)
    build = subprocess.run(
        [opencode, "run", "packages/cli/script/build.ts", "--single", "--skip-install", "--skip-web-ui"],
        cwd=checkout, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=180,
    )
    if build.returncode:
        raise RuntimeError("Could not build the temporary OpenCode probe\n" + redact(build.stdout)[-3000:])
    binaries = list((checkout / "packages/cli/dist").glob("cli-linux-*/bin/opencode"))
    require(len(binaries) == 1, "Expected one native Linux OpenCode build")
    return native_binary(binaries[0])


class Terminal:
    """Drive one real controlling terminal and reap its process on exit."""

    def __init__(self, stack: ExitStack, module: str, args: list[str], env: dict[str, str], *, tui: bool = False):
        self.output = bytearray()
        self.status = None
        self.tui = tui
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                termios.tcsetwinsize(0, (38, 120))
                os.execve(sys.executable, [sys.executable, "-B", "-m", module, *args], env)
            finally:
                os._exit(127)
        stack.callback(self.close)

    def send(self, data: bytes) -> None:
        os.write(self.fd, data)

    def expect(self, pattern: bytes, message: str):
        def check():
            visible = ANSI.sub(b"", self.output) if self.tui else bytes(self.output)
            if match := re.search(pattern, visible):
                return match
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    raise RuntimeError(message + " (terminal closed)")
                if self.tui and b"\x1b[6n" in chunk:
                    self.send(b"\x1b[1;1R")
                self.output.extend(chunk)
                if len(self.output) > 1_000_000:
                    del self.output[:-500_000]
            return None

        return wait_for(check, message)

    def expect_line(self, text: str) -> None:
        marker = text.encode()
        self.expect(re.escape(marker), f"Terminal did not show {text!r}")
        del self.output[:self.output.index(marker) + len(marker)]

    def reaped(self) -> bool:
        if self.status is None:
            child, status = os.waitpid(self.pid, os.WNOHANG)
            if child:
                self.status = os.waitstatus_to_exitcode(status)
        return self.status is not None

    def finish(self) -> None:
        wait_for(self.reaped, "Terminal process did not exit", timeout=8)
        require(self.status == 0, f"Terminal process exited with code {self.status}")

    def close(self) -> None:
        try:
            if not self.reaped():
                # The PID belongs to our own unreaped child.
                os.kill(self.pid, signal.SIGTERM)
                try:
                    wait_for(self.reaped, "Terminal did not stop", timeout=6)
                except TimeoutError:
                    os.kill(self.pid, signal.SIGKILL)
                    os.waitpid(self.pid, 0)
        finally:
            os.close(self.fd)


@contextmanager
def disposable_broker(root: Path, executable: Path, env: dict[str, str]):
    key_dir = Path(env["XDG_CONFIG_HOME"]) / "synapsys-memory"
    key_dir.mkdir(mode=0o700, parents=True)
    key_path = key_dir / "broker-admin.key"
    key = secrets.token_urlsafe(48)
    private_write(key_path, key)
    runtime = Path(env["XDG_RUNTIME_DIR"])
    runtime.mkdir(mode=0o700)
    socket_dir = runtime / "synapsys-memory-broker"
    socket_dir.mkdir(mode=0o700)
    admin, data = socket_dir / "admin.sock", socket_dir / "data.sock"
    process = subprocess.Popen(
        [executable, "--admin-key-file", key_path, "--socket", data, "--admin-socket", admin,
         "serve", "--db", root / "memory.sqlite3"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        def ready():
            require(process.poll() is None, "Disposable broker exited during startup")
            return admin.is_socket() and data.is_socket()

        wait_for(ready, "Disposable broker did not bind its sockets", timeout=8)
        yield admin, data, key
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    require(process.returncode == 0 and not admin.exists() and not data.exists(),
            "Disposable broker did not stop cleanly")


def seed_projects(root: Path, admin: Path, data: Path, key: str, space: str) -> Path:
    fixtures = {}
    projects = {}
    for project in (PROJECT_A, PROJECT_B):
        workspace = root / project
        workspace.mkdir(mode=0o700)
        # The workspace holds the private probe report. The broker grant is read-only.
        projects[project] = {"workspace": str(workspace), "read_only": False}
        session = "seed-" + project
        grant = admin_request(admin, key, "grant", session=session, client="synthetic-fixture",
                              space=space, project=project, rights="read,write", ttl_seconds=120)
        try:
            body = "Synthetic interactive memory_get " + secrets.token_hex(16)
            record = BrokerClient(data, grant["token"]).remember(body)["memory"]
            fixtures[project] = {"id": record["id"], "revision": record["revision"],
                                 "sha256": hashlib.sha256(body.encode()).hexdigest()}
        finally:
            admin_request(admin, key, "revoke", session=session)
    for project, other in ((PROJECT_A, PROJECT_B), (PROJECT_B, PROJECT_A)):
        private_write(root / project / "symem-probe.json", json.dumps({
            "space": space, "project": project, "own": fixtures[project], "foreign": fixtures[other]["id"],
        }))
    policy = root / "policy.json"
    private_write(policy, json.dumps({"schema_version": 1, "projects": projects}))
    return policy


def sandbox_handles(stack: ExitStack, controller: Terminal) -> tuple[int, list[int]]:
    def children(pid: int) -> list[int]:
        return [int(child) for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]

    monitor, = (child for child in children(controller.pid)
                if Path(f"/proc/{child}/comm").read_text().strip() == "bwrap")
    init, = children(monitor)
    require(os.readlink(f"/proc/{init}/ns/net") != os.readlink("/proc/self/ns/net"),
            "Sandbox network namespace matches the host")
    handles = []
    for pid in (monitor, init):
        handle = os.pidfd_open(pid)
        stack.callback(os.close, handle)
        handles.append(handle)
    return init, handles


def closed(handles: list[int]) -> bool:
    return len(select.select(handles, [], [], 0)[0]) == len(handles)


def verify_tui(terminal: Terminal, workspace: Path, init: int) -> None:
    terminal.expect(rb"1 MCP", "OpenCode did not display an MCP server")
    # Ctrl+X then s opens Status. No text or Enter is sent to the composer.
    terminal.send(b"\x18")
    time.sleep(0.08)
    terminal.send(b"s")
    terminal.expect(rb"synapsys-memory[ \t]*Connected", "OpenCode did not display synapsys-memory Connected")
    outcome = terminal.expect(rb"Synapsys MCP check (passed|failed)", "The TUI did not report the MCP call")
    require(outcome.group(1) == b"passed", "The interactive OpenCode MCP check failed")
    verify_report(workspace, init)


def verify_report(workspace: Path, init: int) -> None:
    report = json.loads(read_private(workspace / "symem-result.json", limit=4096))
    require(report["own_memory_matches"] is True and report["foreign_memory_null"] is True,
            "The MCP result did not prove project isolation")
    require(set(report["read_only_tools"]) == READ_TOOLS, "Unexpected tools in the read-only MCP catalog")
    service_pid = report["service_pid"]
    require(type(service_pid) is int and service_pid > 1, "Invalid OpenCode service PID")
    argv = Path(f"/proc/{init}/root/proc/{service_pid}/cmdline").read_bytes().split(b"\0")
    require(PILOT_BINARY.encode() in argv and b"serve" in argv,
            "The MCP result did not come from the OpenCode server in this sandbox")


def verify_switch(
    root: Path, binary: Path, admin: Path, data: Path, key: str, env: dict[str, str],
    *,
    prepare_project: Callable[[Path], None] | None = None,
    verify_client: Callable[[Terminal, Path, int], None] = verify_tui,
    controller_options: tuple[str, ...] = (),
) -> None:
    space = "interactive-memory-get"
    policy = seed_projects(root, admin, data, key, space)
    if prepare_project is not None:
        for project in (PROJECT_A, PROJECT_B):
            prepare_project(root / project)
    runtime = Path(env["XDG_RUNTIME_DIR"])

    def sessions():
        return admin_request(admin, key, "sessions")

    def credentials(project: str, grant: dict):
        token, = runtime.glob(f"symem-controller-*/{grant['session']}.token")
        client = BrokerClient(data, read_token_file(token))
        profile = client.profile()
        require((profile["space"], profile["project"], profile["rights"]) == (space, project, ["read"]),
                "The broker grant is not read-only for the approved project")
        return token, client

    require(not sessions(), "A fixture writer grant is still active")
    with ExitStack() as stack:
        ui = Terminal(stack, "synapsys_memory.client_terminal", [], env, tui=True)
        socket_path = ui.expect(rb"--interactive ([^\r\n]+\.sock)", "OpenCode terminal did not announce its socket")
        cli = Terminal(stack, "synapsys_memory.controller_cli", [
            "--policy", str(policy), "--space", space, "--rights", "read", "--opencode", str(binary),
            "--interactive", os.fsdecode(socket_path.group(1)),
            *controller_options,
        ], env)
        cli.expect_line("Proyecto de destino")
        cli.send(b"project-a\n")
        cli.expect_line("¿Activar project-a?")
        cli.send(b"s\n")
        cli.expect_line("Proyecto activo project-a.")
        first, = sessions()
        token_a, client_a = credentials(PROJECT_A, first)
        init_a, handles_a = sandbox_handles(stack, cli)
        verify_client(ui, root / PROJECT_A, init_a)
        print("A recovered its synthetic memory and received null for B", file=sys.stderr, flush=True)

        ui.output.clear()
        cli.expect_line("Proyecto de destino")
        cli.send(b"project-b\n")
        cli.expect_line("¿Cambiar de project-a a project-b?")
        require(sessions() == [first] and not closed(handles_a), "A did not stay active until approval")
        cli.send(b"s\n")

        def b_grant():
            return next((grant for grant in sessions() if grant["project"] == PROJECT_B), None)

        second = wait_for(b_grant, "B did not receive a grant after approval")
        require(closed(handles_a) and not token_a.exists() and sessions() == [second],
                "B received a grant before A was fully closed")
        assert_forbidden(client_a.profile)
        cli.expect_line("Proyecto activo project-b.")
        token_b, client_b = credentials(PROJECT_B, second)
        init_b, handles_b = sandbox_handles(stack, cli)
        ui.expect_line("Esperando al siguiente proyecto aprobado.")
        verify_client(ui, root / PROJECT_B, init_b)
        print("B recovered its synthetic memory and received null for A", file=sys.stderr, flush=True)

        ui.output.clear()
        cli.expect_line("Proyecto de destino")
        cli.send(b":salir\n")
        cli.finish()
        require(closed(handles_b) and not token_b.exists() and not sessions(),
                "B's sandbox or grant remained active at exit")
        assert_forbidden(client_b.profile)
        ui.expect_line("Esperando al siguiente proyecto aprobado.")
        ui.send(b"\x03")
        ui.finish()
        require(not Path(os.fsdecode(socket_path.group(1))).exists(), "The terminal listener socket remained")


def run(opencode: Path) -> dict:
    venv, source = repository_runtime()
    _trusted_bwrap()
    opencode = native_binary(opencode)
    with tempfile.TemporaryDirectory(prefix="symem-tui-get-", dir="/tmp") as temporary:
        root = Path(temporary)
        build_root = root / "build"
        build_root.mkdir(mode=0o700)
        binary = build_probe(build_root, opencode)
        env = {
            "HOME": str(root), "PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONPATH": str(source),
            "PYTHONDONTWRITEBYTECODE": "1", "XDG_RUNTIME_DIR": str(root / "run"),
            "XDG_CONFIG_HOME": str(root / "config"),
        }
        with disposable_broker(root, venv / "bin/synapsys-memory-broker", env) as (admin, data, key):
            verify_switch(root, binary, admin, data, key, env)
    require(not root.exists(), "The disposable SQLite directory remained")
    return {
        "result": "PASS", "opencode_version": OPENCODE_VERSION, "opencode_source_commit": OPENCODE_COMMIT,
        "bun_version": BUN_VERSION, "transport": "MCP stdio", "tool": "memory_get",
        "interactive_mcp_calls": 4,
        "a_own_memory_matches": True, "a_foreign_memory_null": True,
        "b_own_memory_matches": True, "b_foreign_memory_null": True, "read_only_project_grants": True,
        "a_revoked_and_closed_before_b": True, "b_revoked_and_closed_at_exit": True,
        "disposable_database_removed": True, "prompts_submitted": 0, "agents_run": 0, "model_inference": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", type=Path, default=Path.home() / ".opencode/bin/opencode",
                        help=f"Native OpenCode {OPENCODE_VERSION} executable")
    args = parser.parse_args()

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        result = run(args.opencode)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
