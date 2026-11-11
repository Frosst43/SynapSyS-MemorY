#!/usr/bin/env python3
"""Call memory_get through OpenCode's MCP service inside Bubblewrap."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

from synapsys_memory.broker import BrokerClient, BrokerDaemon, admin_request
from synapsys_memory.managed_session import ManagedSandbox
from synapsys_memory.opencode_runtime import (
    PILOT_BINARY, SERVER_NAME, configuration, native_binary, private_write, repository_runtime, runtime_arguments,
)
from synapsys_memory.sandbox import _check_memory_session, _trusted_bwrap, build_command, load_policy
from synapsys_memory.store import MemoryStore

from diagnose_opencode_config import redact
from verify_persistent_memory import SPACE, assert_forbidden

OPENCODE_VERSION = "2.2.5"
OPENCODE_COMMIT = "9bbb8ce18c80f099e0e448775f59d965ead40a21"
BUN_VERSION = "1.4.4"
PROJECT = "opencode-memory-get"
ENTRYPOINT = "packages/core/test/synapsys-memory-get.ts"
RESULT_PREFIX = "SYMEM_OPENCODE_RESULT "

PROBE = """
import { createHash } from "node:crypto"
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from "node:fs"
import { Effect, Layer, Schema, Stream } from "effect"
import { ID } from "@opencode/schema/event"
import { ConfigMCP } from "@opencode/schema/config/mcp"
import { Bus } from "@opencode/core/bus"
import { Credential } from "@opencode/core/credential"
import { Form } from "@opencode/core/form"
import { Integration } from "@opencode/core/integration"
import { Location } from "@opencode/core/location"
import { Mcp } from "@opencode/core/mcp/index"
import { AbsolutePath } from "@opencode/core/schema"
import { hostEnvironmentLayer } from "./fixture/environment"
import { location } from "./fixture/location"

const input = JSON.parse(readFileSync("/workspace/case.json", "utf8"))
const managed = JSON.parse(readFileSync("/etc/opencode/opencode.json", "utf8"))
const config = Schema.decodeUnknownSync(ConfigMCP.Local)(managed.mcp.servers[input.server])
const directory = AbsolutePath.make("/workspace")
const networkIsolated = readlinkSync("/proc/self/ns/net") !== input.hostNet
const mounts = readdirSync("/run/synapsys-memory").sort().join(",")
const privatePathsHidden = input.hostPrivatePaths.every((path: string) => !existsSync(path))
if (!networkIsolated || !privatePathsHidden || mounts !== "data.sock,session.token" || process.env.SYMEM_ADMIN_KEY)
  throw new Error("Unexpected Bubblewrap isolation or broker mounts")

// Use OpenCode's real process spawner. OAuth and credential services are unused for local stdio.
const environment = Layer.mergeAll(
  Layer.succeed(Location.Service, Location.Service.of(location({ directory }))),
  Layer.mock(Bus.Service, {
    publish: (definition, data) => Effect.succeed({ id: ID.create(), type: definition.type, data }),
    subscribe: () => Stream.never,
  }),
  Layer.mock(Integration.Service, {}),
  Layer.mock(Credential.Service, {}),
  hostEnvironmentLayer,
)
const mcpLayer = Mcp.layer({ clientInfo: { name: "opencode", version: input.version } }).pipe(
  Layer.provide(Form.layer),
  Layer.provide(environment),
)

const probe = Effect.gen(function* () {
  const service = yield* Mcp.Service
  yield* service.add(input.server, config)
  for (let attempt = 0; attempt < 180; attempt++) {
    const status = (yield* service.servers()).find((entry) => entry.name === input.server)?.status
    if (status?.status === "connected") break
    if (status?.status === "failed") throw new Error("MCP connection failed: " + status.error)
    if (status?.status === "disabled" || status?.status === "needs_auth")
      throw new Error("Unexpected local MCP status: " + status.status)
    if (attempt === 179) throw new Error("OpenCode MCP readiness timed out")
    yield* Effect.sleep("50 millis")
  }

  const names = (yield* service.tools()).filter((tool) => tool.server === input.server).map((tool) => tool.name)
  const writeTools = ["memory_remember", "memory_update", "memory_forget"]
  if (!names.includes("memory_get") || writeTools.some((name) => names.includes(name)))
    throw new Error("Unexpected tools for read-only token")

  const reply = yield* service.callTool({ server: input.server, name: "memory_get", args: { id: input.id } })
  if (reply.isError || reply.server !== input.server || reply.tool !== "memory_get")
    throw new Error("OpenCode received an unsuccessful memory_get result")
  const memory = (reply.structured as { result?: {
    id: string; body: string; space: string; project: string; revision: number
  } })?.result
  const absent = input.expectAbsent === true
  if (absent && memory !== null)
    throw new Error("memory_get exposed a record from another project")
  if (!absent && (!memory || memory.id !== input.id || memory.space !== input.space ||
      memory.project !== input.project || memory.revision !== input.revision || typeof memory.body !== "string" ||
      createHash("sha256").update(memory.body).digest("hex") !== input.bodySha256))
    throw new Error("memory_get did not return the expected synthetic record")

  return {
    tool: reply.tool, record_matches: !absent, read_only_tools: names.sort(),
    ...(absent ? { record_absent: true } : {}),
    network_namespace_isolated: networkIsolated, host_private_paths_hidden: privatePathsHidden,
  }
}).pipe(Effect.provide(mcpLayer), Effect.timeout("15 seconds"))

const report = JSON.stringify(await Effect.runPromise(probe))
if (input.holdForSwitch === true) {
  // Keep this probe process alive so the controller must close its namespace.
  writeFileSync("/workspace/memory-get.json", report, { flag: "wx", mode: 0o600 })
  await Bun.sleep(120_000)
} else {
  console.log("SYMEM_OPENCODE_RESULT " + report)
}
"""


class ObservedBroker(BrokerDaemon):
    def __init__(self, db: Path, data: Path, admin: Path, *, admin_key: str):
        super().__init__(db, data, admin, admin_key=admin_key)
        self.reads = []

    def _client(self, message, *, peer_uid):
        value = super()._client(message, peer_uid=peer_uid)
        if message.get("action") == "get":
            with self._lock:
                self.reads.append(value)
        return value


def bun_environment(root: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "BUN_BE_BUN": "1",
        "NO_COLOR": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "BUN_TELEMETRY_DISABLED": "1",
        "BUN_INSTALL_CACHE_DIR": str(root / "bun-cache"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_STATE_HOME": str(root / "state"),
    }


def prepare_checkout(root: Path, binary: Path, package: str) -> Path:
    env = bun_environment(root)
    env.pop("BUN_BE_BUN")
    opencode = subprocess.check_output([binary, "--version"], cwd=root, env=env, text=True, timeout=6)
    if opencode.strip() != f"opencode v{OPENCODE_VERSION}":
        raise ValueError(f"This probe requires OpenCode {OPENCODE_VERSION}")
    env["BUN_BE_BUN"] = "1"
    bun = subprocess.check_output([binary, "--version"], cwd=root, env=env, text=True, timeout=6)
    if bun.strip() != BUN_VERSION:
        raise ValueError(f"This probe requires bundled Bun {BUN_VERSION}")
    if not hasattr(tarfile, "data_filter"):
        raise ValueError("This Python needs tarfile's data extraction filter")

    print("Preparing pinned OpenCode source and dependencies", file=sys.stderr, flush=True)
    archive = root / "source.tar.gz"
    url = f"https://codeload.github.com/anomalyco/opencode/tar.gz/{OPENCODE_COMMIT}"
    with urlopen(url, timeout=30) as download, archive.open("xb") as target:
        shutil.copyfileobj(download, target)
    with tarfile.open(archive, "r:gz") as source:
        source.extractall(root, filter="data")
    archive.unlink()
    checkout = root / f"opencode-{OPENCODE_COMMIT}"
    install = subprocess.run(
        [binary, "install", "--filter", package, "--frozen-lockfile", "--ignore-scripts", "--no-progress"],
        cwd=checkout, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=120,
    )
    if install.returncode:
        raise RuntimeError("Could not prepare OpenCode dependencies\n" + redact(install.stdout)[-3000:])
    return checkout


def prepare_source(root: Path, binary: Path) -> Path:
    checkout = prepare_checkout(root, binary, "@opencode/core")
    private_write(checkout / ENTRYPOINT, PROBE)
    return checkout


def run(binary: Path) -> dict:
    venv, source = repository_runtime()
    bwrap, binary = _trusted_bwrap(), native_binary(binary)

    with tempfile.TemporaryDirectory(prefix="symem-opencode-get-", dir="/tmp") as temporary:
        root = Path(temporary)
        copied = root / "opencode"
        shutil.copyfile(binary, copied)
        copied.chmod(0o500)
        checkout = prepare_source(root, copied)
        workspace = root / "project"
        workspace.mkdir(mode=0o700)
        db, data, admin = (root / name for name in ("memory.sqlite3", "data.sock", "admin.sock"))
        token_file, policy, config = (root / name for name in ("session.token", "policy.json", "opencode.json"))
        body = "Synthetic memory_get fixture " + secrets.token_hex(12)
        with MemoryStore(db, space=SPACE, project=PROJECT, actor="opencode-fixture") as store:
            record = store.remember(body, title="OpenCode memory_get pilot")["memory"]
        sqlite_before = db.read_bytes()

        private_write(workspace / "case.json", json.dumps({
            "version": OPENCODE_VERSION, "server": SERVER_NAME,
            "id": record["id"], "revision": record["revision"], "space": SPACE, "project": PROJECT,
            "bodySha256": hashlib.sha256(body.encode()).hexdigest(),
            "hostNet": os.readlink("/proc/self/ns/net"),
            "hostPrivatePaths": [str(db), str(admin), "/run/synapsys-memory/admin.sock"],
        }))
        settings = configuration(str(venv / "bin/python"))
        server = settings["mcp"]["servers"][SERVER_NAME]
        server["command"].insert(2, "-B")
        server["timeout"] = {"startup": 7500, "catalog": 5000, "execution": 5000}
        private_write(config, json.dumps(settings))
        private_write(policy, json.dumps({"schema_version": 1, "projects": {PROJECT: {
            "workspace": str(workspace), "read_only": True,
            "memory": {"data_socket": str(data), "token_file": str(token_file), "space": SPACE},
        }}}))

        key, session = secrets.token_urlsafe(48), "opencode-" + secrets.token_hex(6)
        with ObservedBroker(db, data, admin, admin_key=key) as broker, tempfile.TemporaryFile(dir=root) as log:
            token = admin_request(
                admin, key, "grant", session=session, client="opencode-mcp-probe",
                space=SPACE, project=PROJECT, rights="read", ttl_seconds=120, peer_uid=os.geteuid(),
            )["token"]
            client = BrokerClient(data, token)
            process = None
            try:
                private_write(token_file, token)
                spec = load_policy(policy, PROJECT)
                _check_memory_session(spec)
                if client.profile()["rights"] != ["read"]:
                    raise PermissionError("The broker grant must be read-only")
                command = [PILOT_BINARY, "--no-install", "/opt/opencode-source/" + ENTRYPOINT]
                argv = build_command(spec, command, bwrap=bwrap)
                argv = runtime_arguments(argv, config=config, binary=copied, venv=venv, source=source)
                split = argv.index("--")
                argv[split:split] = [
                    "--ro-bind", str(checkout), "/opt/opencode-source",
                    "--setenv", "BUN_BE_BUN", "1",
                    "--setenv", "BUN_TELEMETRY_DISABLED", "1",
                    "--setenv", "OPENCODE_DISABLE_MODELS_FETCH", "1",
                ]
                process = ManagedSandbox.start(argv, output=log)
                code = process.wait(timeout=25)
            finally:
                try:
                    if not admin_request(admin, key, "revoke", session=session)["revoked"]:
                        raise RuntimeError("The broker did not revoke the test grant")
                finally:
                    if process is not None:
                        process.stop()

            assert_forbidden(client.profile)
            log.seek(0)
            output = log.read(64_000).decode(errors="replace")
            reports = [line.removeprefix(RESULT_PREFIX) for line in output.splitlines()
                       if line.startswith(RESULT_PREFIX)]
            if code or len(reports) != 1:
                detail = redact(output, (token, key, body, record["id"]))[-4000:]
                raise RuntimeError(f"OpenCode MCP probe exited with code {code}\n{detail}")
            report = json.loads(reports[0])
            if (report.get("tool") != "memory_get" or report.get("record_matches") is not True
                    or report.get("network_namespace_isolated") is not True
                    or report.get("host_private_paths_hidden") is not True):
                raise RuntimeError("Incomplete OpenCode MCP result")
            if broker.reads != [record] or not process.stopped:
                raise RuntimeError("Expected one matching broker read and confirmed sandbox closure")
        if db.read_bytes() != sqlite_before or data.exists() or admin.exists():
            raise RuntimeError("The read changed SQLite or left broker sockets behind")

    return {
        "result": "PASS", "opencode_version": OPENCODE_VERSION, "opencode_source_commit": OPENCODE_COMMIT,
        "bun_version": BUN_VERSION, "transport": "MCP stdio", **report,
        "broker_get_requests": 1, "read_only_grant": True, "token_revoked": True,
        "sandbox_closed": True, "disposable_database_removed": True,
        "sqlite_unchanged_after_read": True, "agent_sessions": False, "model_inference": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", type=Path, default=Path.home() / ".opencode/bin/opencode",
                        help="Native OpenCode 2.2.5 executable")
    args = parser.parse_args()
    try:
        summary = run(args.opencode)
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
