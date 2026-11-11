"""Administracion local del broker, sin modelos ni acceso a Internet."""

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

from .broker import BrokerDaemon, admin_request
from .secureio import read_private

_BASE = Path.home() / ".local" / "state" / "synapsys-memory"


def read_admin_key_file(path: Path) -> str:
    try:
        key = read_private(path, limit=4096).decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise ValueError("El archivo de clave administrativa debe ser texto UTF-8") from exc
    if len(key) < 48:
        raise ValueError("--admin-key-file debe contener al menos 48 caracteres")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synapsys-memory-broker")
    parser.add_argument("--socket", type=Path, default=_BASE / "data.sock")
    parser.add_argument("--admin-socket", type=Path, default=_BASE / "admin.sock")
    parser.add_argument("--admin-key-file", type=Path, help="Archivo privado con la clave administrativa")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Servidor local propietario de SQLite")
    serve.add_argument("--db", type=Path, default=_BASE / "memory.sqlite3")
    serve.add_argument("--client-gid", type=int, default=None, help="GID autorizado para socket de datos")
    for cmd in ("grant", "switch", "revoke", "sessions"):
        subp = sub.add_parser(cmd)
        if cmd in ("grant", "switch", "revoke"):
            subp.add_argument("--session", required=True)
        if cmd in ("grant", "switch"):
            subp.add_argument("--client", required=True)
            subp.add_argument("--space", required=True)
            subp.add_argument("--project", required=True)
            subp.add_argument("--rights", default="read")
            subp.add_argument("--ttl-seconds", type=int, default=1800)
            subp.add_argument("--peer-uid", type=int, default=None, help="UID Linux del ejecutor autorizado")
    args = parser.parse_args(argv)
    try:
        if args.admin_key_file is None:
            key = os.environ.get("SYMEM_ADMIN_KEY", "")
            if len(key) < 48:
                parser.error("SYMEM_ADMIN_KEY debe contener al menos 48 caracteres")
        else:
            key = read_admin_key_file(args.admin_key_file)

        if args.command == "serve":
            stopping = threading.Event()
            def end(_sig, _frame):
                stopping.set()
            signal.signal(signal.SIGINT, end)
            signal.signal(signal.SIGTERM, end)
            with BrokerDaemon(args.db, args.socket, args.admin_socket,
                              admin_key=key, client_gid=args.client_gid):
                print("Broker local activo (no exponer sockets ni clave a agentes no confiables)", file=sys.stderr)
                stopping.wait()
            return 0
        opts = {}
        for key_name in ("session", "client", "space", "project", "rights", "ttl_seconds", "peer_uid"):
            if hasattr(args, key_name):
                opts[key_name] = getattr(args, key_name)
        result = admin_request(args.admin_socket, key, args.command, **opts)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, PermissionError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
