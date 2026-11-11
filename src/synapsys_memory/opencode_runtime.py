"""Managed OpenCode V2 configuration and mounts for Bubblewrap."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys

from .runtime_bridge import LOOPBACK, RUNTIME_PORT

SERVER_NAME = "synapsys-memory"
MANAGED_CONFIG = "/etc/opencode/opencode.json"
PILOT_BINARY = "/opt/symem-pilot/opencode"


def configuration(python: str, *, runtime_model: str | None = None) -> dict:
    if not Path(python).is_absolute():
        raise ValueError("Python runtime must be an absolute path")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "update": "disable",
        "share": "disabled",
        "mcp": {"servers": {SERVER_NAME: {
            "type": "local",
            "protocol": "legacy",
            "codemode": False,
            "command": [python, "-I", "-m", "synapsys_memory.mcp_server"],
            "environment": {
                "SYMEM_BROKER_SOCKET": "/run/synapsys-memory/data.sock",
                "SYMEM_SESSION_TOKEN_FILE": "/run/synapsys-memory/session.token",
            },
        }}},
    }
    if runtime_model is not None:
        config["model"] = "local-runtime/chat"
        config["providers"] = {"local-runtime": {
            "name": "Local runtime",
            "package": "@opencode/ai/providers/openai-compatible",
            "settings": {"baseURL": f"http://{LOOPBACK}:{RUNTIME_PORT}/v1", "apiKey": "local"},
            "models": {"chat": {
                "modelID": runtime_model, "name": runtime_model, "capabilities": {"tools": True},
            }},
        }}
    return config


def private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def repository_runtime() -> tuple[Path, Path]:
    repo = Path(__file__).resolve().parents[2]
    venv, source = repo / ".venv", repo / "src"
    if Path(sys.prefix).resolve() != venv.resolve() or venv.is_symlink() or source.is_symlink():
        raise ValueError("Run with the repository .venv/bin/python and non-symlinked runtime")
    if not Path(getattr(sys, "_base_executable", sys.executable)).resolve().is_relative_to("/usr"):
        raise ValueError("Base Python outside /usr needs a reviewed runtime mount")
    if importlib.util.find_spec("mcp") is None:
        raise ValueError("The repository virtual environment needs the MCP extra")
    return venv, source


def native_binary(value: Path) -> Path:
    path = value.expanduser().resolve(strict=True)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022 or not os.access(path, os.X_OK)):
        raise ValueError("OpenCode executable is not trusted")
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError("A native Linux ELF OpenCode binary is required")
    return path


def runtime_arguments(
    base: list[str], *, config: Path, binary: Path, venv: Path, source: Path,
) -> list[str]:
    """Mount the specified runtime and managed document read-only."""
    if "--" not in base or "--unshare-net" not in base[:base.index("--")]:
        raise ValueError("An isolated Bubblewrap command is required")
    if not all(path.is_absolute() for path in (config, binary, venv, source)):
        raise ValueError("Runtime paths must be absolute")
    split = base.index("--")
    extra = [
        "--dir", "/opt/symem-pilot", "--dir", "/etc/opencode",
        "--ro-bind", str(config), MANAGED_CONFIG,
        "--ro-bind", str(binary), PILOT_BINARY,
        "--ro-bind", str(venv), str(venv),
        "--ro-bind", str(source), str(source),
        "--setenv", "XDG_DATA_HOME", "/tmp/data",
        "--setenv", "XDG_STATE_HOME", "/tmp/state",
        "--setenv", "NO_COLOR", "1",
    ]
    return [*base[:split], *extra, *base[split:]]
