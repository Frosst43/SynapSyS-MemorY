#!/usr/bin/env python3
"""Exercise unmodified OpenCode with a deterministic provider and disposable memory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from synapsys_memory.opencode_runtime import native_binary, private_write, repository_runtime
from synapsys_memory.sandbox import _trusted_bwrap

from verify_opencode_interactive_memory_get import (
    READ_TOOLS, Terminal, disposable_broker, require, verify_report, verify_switch, wait_for,
)
from verify_opencode_memory_get import OPENCODE_VERSION

HERE = Path(__file__).resolve().parent


def prepare_project(workspace: Path) -> None:
    plugin = workspace / "provider"
    plugin.mkdir(mode=0o700)
    private_write(plugin / "package.json", json.dumps({
        "name": "symem-fixture", "type": "module", "exports": {"./server": "./server.js"},
    }))
    private_write(plugin / "server.js", (HERE / "fixtures/simulated-provider.js").read_text())
    config = {
        "model": "symem-fixture/scripted",
        "snapshots": False,
        "plugins": ["./provider"],
        "providers": {"symem-fixture": {
            "name": "Deterministic local fixture", "package": "aisdk:symem-fixture-sdk",
            "settings": {"apiKey": "synthetic-fixture"},
            "models": {"scripted": {
                "name": "Scripted fixture", "capabilities": {"tools": True},
                "limit": {"context": 64000, "output": 4096},
            }},
        }},
        "permissions": [{"action": "*", "resource": "*", "effect": "deny"}] + [
            {"action": f"synapsys-memory_{tool}", "resource": "*", "effect": "allow"}
            for tool in sorted(READ_TOOLS)
        ],
    }
    private_write(workspace / "opencode.json", json.dumps(config))


def verify_client(terminal: Terminal, workspace: Path, init: int) -> None:
    wait_for(lambda: (workspace / "provider-ready.json").is_file(), "The simulated provider did not load")
    terminal.expect(rb"1 MCP", "OpenCode did not display an MCP server")
    terminal.send(b"\x18")
    time.sleep(0.08)
    terminal.send(b"s")
    terminal.expect(rb"synapsys-memory[ \t]*Connected", "The MCP did not connect")
    terminal.output.clear()
    terminal.send(b"\x1b")
    terminal.expect(rb"Ask anything", "The OpenCode composer did not regain focus")
    terminal.send(b"\x1b[200~Run the deterministic memory fixture.\x1b[201~")
    terminal.expect(rb"Run the deterministic memory fixture", "The synthetic prompt did not reach the composer")
    time.sleep(0.2)
    terminal.send(b"\r")
    terminal.expect(rb"SYMEM_PROVIDER_PASS", "The normal OpenCode tool workflow did not complete")
    verify_report(workspace, init)


def run(opencode: Path) -> dict:
    venv, source = repository_runtime()
    _trusted_bwrap()
    opencode = native_binary(opencode)
    with opencode.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").digest()
    with tempfile.TemporaryDirectory(prefix="symem-provider-", dir="/tmp") as temporary:
        root = Path(temporary)
        env = {
            "HOME": str(root), "PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONPATH": str(source),
            "PYTHONDONTWRITEBYTECODE": "1", "XDG_RUNTIME_DIR": str(root / "run"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "OPENCODE_DISABLE_MODELS_FETCH": "1", "BUN_TELEMETRY_DISABLED": "1",
        }
        version = subprocess.check_output([opencode, "--version"], cwd=root, env=env, text=True, timeout=6)
        require(version.strip() == f"opencode v{OPENCODE_VERSION}", "Unexpected native OpenCode version")
        with disposable_broker(root, venv / "bin/synapsys-memory-broker", env) as (admin, data, key):
            verify_switch(root, opencode, admin, data, key, env,
                          prepare_project=prepare_project, verify_client=verify_client)
    require(not root.exists(), "Disposable provider test directory remained")
    with opencode.open("rb") as stream:
        require(hashlib.file_digest(stream, "sha256").digest() == digest, "The OpenCode binary changed")
    return {
        "result": "PASS", "opencode_version": OPENCODE_VERSION, "opencode_binary_unchanged": True,
        "provider": "deterministic local SDK fixture", "tool": "memory_get", "mcp_calls": 4,
        "a_own_memory_matches": True, "a_foreign_memory_null": True,
        "b_own_memory_matches": True, "b_foreign_memory_null": True, "read_only_grants": True,
        "a_revoked_and_closed_before_b": True, "b_revoked_and_closed_at_exit": True,
        "disposable_database_removed": True, "synthetic_prompts_submitted": 2, "model_inference": False,
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
