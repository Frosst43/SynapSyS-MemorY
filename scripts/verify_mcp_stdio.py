#!/usr/bin/env python3
"""Prueba REAL MCP stdio en cuatro procesos con broker local y SQLite temporal.

Requiere el paquete mcp. No lee memorias ni configuraciones del usuario.
"""

import asyncio
import os
from pathlib import Path
import secrets
import sys
import tempfile

from synapsys_memory.broker import BrokerDaemon, admin_request


def _joined(result):
    return str(getattr(result, "structuredContent", None)) + " " + " ".join(
        str(getattr(item, "text", "")) for item in result.content
    )


async def _session(socket_path, *, token, callback):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(__file__).resolve().parents[1]
    # Do not forward real admin keys, provider secrets or token files to tests.
    env = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env.update({
        "SYMEM_BROKER_SOCKET": str(socket_path),
        "SYMEM_SESSION_TOKEN": token,
        "SYMEM_ALLOW_LEGACY_DIRECT": "0",
        "PYTHONPATH": str(root / "src"),
    })
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "synapsys_memory.mcp_server"], env=env,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            return await callback(session, tools)


async def main():
    with tempfile.TemporaryDirectory(prefix="symem-mcp-") as temp:
        root = Path(temp)
        client_sock = root / "client.sock"
        admin_sock = root / "admin.sock"
        key = secrets.token_urlsafe(48)
        with BrokerDaemon(root / "memory.sqlite3", client_sock, admin_sock, admin_key=key):
            def grant(session, client, project, rights):
                result = admin_request(admin_sock, key, "grant", session=session,
                                       client=client, project=project,
                                       space="shared", rights=rights)
                return result["token"]

            opencode = grant("opencode", "opencode", "demo", "read,write")
            codex = grant("codex", "codex", "demo", "read")
            openclaw = grant("openclaw", "openclaw", "aislado", "read")

            async def writer(session, tools):
                assert {"memory_remember", "memory_recall"} <= tools, tools
                assert "memory_forget" not in tools, tools
                response = await session.call_tool("memory_remember", {
                    "body": "La memoria compartida mantiene la persistencia entre clientes.",
                    "title": "Continuidad MCP",
                })
                assert not response.isError, _joined(response)
                assert "created" in _joined(response), _joined(response)
            await _session(client_sock, token=opencode, callback=writer)

            async def reader(session, tools):
                assert "memory_recall" in tools and "memory_remember" not in tools, tools
                result = await session.call_tool("memory_recall", {"query": "persistencia"})
                assert not result.isError, _joined(result)
                assert "memoria compartida" in _joined(result), _joined(result)
            await _session(client_sock, token=codex, callback=reader)

            async def isolated(session, tools):
                assert "memory_remember" not in tools, tools
                result = await session.call_tool("memory_recall", {"query": "persistencia"})
                assert not result.isError, _joined(result)
                assert "memoria compartida" not in _joined(result), _joined(result)
            await _session(client_sock, token=openclaw, callback=isolated)

            write_only = grant("write-only", "sdk-writer", "demo", "write")
            async def writer_only(session, tools):
                assert tools == {"memory_remember", "memory_update"}, tools
                result = await session.call_tool("memory_remember", {
                    "body": "PRIVATE_WRITE_ONLY_PAYLOAD", "title": "Private writer test",
                })
                assert not result.isError, _joined(result)
                assert "PRIVATE_WRITE_ONLY_PAYLOAD" not in _joined(result), "Content leaked in acknowledgement"
                import json
                ack = getattr(result, "structuredContent", None)
                if ack is None:
                    ack = json.loads(next(item.text for item in result.content if hasattr(item, "text")))
                assert set(ack) == {"created", "id", "revision"}, ack
                updated = await session.call_tool("memory_update", {
                    "id": ack["id"], "body": "PRIVATE_WRITE_ONLY_UPDATE", "expected_revision": ack["revision"],
                })
                assert not updated.isError, _joined(updated)
                assert "PRIVATE_WRITE_ONLY_UPDATE" not in _joined(updated)
                conflict = await session.call_tool("memory_update", {
                    "id": ack["id"], "body": "STALE", "expected_revision": ack["revision"],
                })
                assert conflict.isError, "Stale update unexpectedly accepted"
            await _session(client_sock, token=write_only, callback=writer_only)

    print("PASS: MCP stdio + broker; shared/read-only/write-only, isolation and conflict rejection")


if __name__ == "__main__":
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise SystemExit("NO EJECUTADO: instala el extra MCP con pip install -e '.[mcp]'")
    asyncio.run(asyncio.wait_for(main(), timeout=45))
