#!/usr/bin/env python3
"""Safe universal project-memory pilot: temporary DB, no real repo writes.

This proves broker cross-client retrieval + project isolation + revocation.
It does NOT launch OpenCode/Qwen/Codex, use MCP, or enforce file sandboxes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import socket
import tempfile

from synapsys_memory.broker import BrokerClient, BrokerDaemon, admin_request, request


def run_pilot(project: str = "synapsys-os-mobile", foreign_project: str = "unrelated-project") -> dict:
    if not isinstance(project, str) or not isinstance(foreign_project, str):
        raise ValueError("Los proyectos deben ser texto")
    if project == foreign_project:
        raise ValueError("Los proyectos de prueba deben ser distintos")
    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("El piloto necesita sockets Unix")

    # No tocar bases ni repositorios reales: todo vive y desaparece en tempfile.
    with tempfile.TemporaryDirectory(prefix="synapsys-memory-pilot-") as folder:
        root = Path(folder)
        socket_data = root / "data.sock"
        socket_admin = root / "admin.sock"
        secret = secrets.token_urlsafe(48)
        with BrokerDaemon(root / "pilot.sqlite3", socket_data, socket_admin, admin_key=secret):
            def grant(session: str, client: str, target: str, rights: str) -> dict:
                return admin_request(
                    socket_admin, secret, "grant", session=session, client=client,
                    space="pilot", project=target, rights=rights,
                )

            # OpenCode and Qwen are LABELS for two simulated client processes.
            # Real OpenCode/Qwen must still be tested separately via MCP.
            writer = grant("mobile-open", "opencode", project, "read,write")
            reader = grant("mobile-qwen", "qwen", project, "read")
            foreign = grant("outside", "codex", foreign_project, "read")
            open_client = BrokerClient(socket_data, writer["token"])
            qwen_client = BrokerClient(socket_data, reader["token"])
            other_client = BrokerClient(socket_data, foreign["token"])

            seeded = open_client.remember(
                "El piloto de memoria de este proyecto debe ser independiente de otros proyectos.",
                title="Prueba de continuidad", kind="decision",
            )
            record_id = seeded["memory"]["id"]
            hits = qwen_client.recall("continuidad proyectos memoria")
            if not any(item["id"] == record_id for item in hits):
                raise AssertionError("La segunda IA no recupera el recuerdo compartido")
            if other_client.get(record_id) is not None or other_client.recall("continuidad"):
                raise AssertionError("Una memoria privada se ha filtrado a otro proyecto")
            try:
                other_client.remember("escritura sin derecho")
            except PermissionError:
                pass
            else:
                raise AssertionError("Un cliente solo lectura ha podido escribir")

            # Requests cannot select arbitrary project/space even with a valid token.
            try:
                request(socket_data, {
                    "token": writer["token"], "action": "recall",
                    "args": {"query": "continuidad", "project": foreign_project},
                })
            except PermissionError:
                pass
            else:
                raise AssertionError("El cliente ha podido cambiar de proyecto")

            # Switching revokes the writer's old token, without deleting the
            # project's persisted memory. The reader retains its legitimate grant.
            switched = admin_request(
                socket_admin, secret, "switch", session="mobile-open",
                client="opencode", space="pilot", project=foreign_project,
                rights="read",
            )
            try:
                open_client.profile()
            except PermissionError:
                pass
            else:
                raise AssertionError("Credencial antigua sigue activa tras cambio")
            new_client = BrokerClient(socket_data, switched["token"])
            if new_client.recall("continuidad") or new_client.get(record_id):
                raise AssertionError("El nuevo proyecto puede consultar el anterior")
            if qwen_client.get(record_id) is None:
                raise AssertionError("Cambio de cliente elimino la memoria del proyecto")

            return {
                "result": "PASS", "scope": "broker-local-temporal",
                "project": project, "foreign_project": foreign_project,
                "checks": [
                    "shared_recall", "foreign_project_denied", "readonly_write_denied",
                    "project_override_denied", "old_token_revoked", "project_memory_preserved",
                ],
                "real_clients_tested": False, "mcp_tested": False,
                "filesystem_sandbox_tested": False, "writes_real_projects": False,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Piloto de memoria universal sin tocar proyectos")
    parser.add_argument("--project", default="synapsys-os-mobile")
    parser.add_argument("--foreign-project", default="otro-proyecto")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_pilot(args.project, args.foreign_project),
                         indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, AssertionError) as exc:
        parser.exit(2, f"FAIL: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
