#!/usr/bin/env python3
"""Prueba REAL de tres clientes MCP stdio sin OpenCode/Codex/OpenClaw instalados.

Requiere pip install -e '.[mcp]'. Solo escribe en tempfile.
No modifica configuraciones ni repos externos.
"""

import asyncio
import os
from pathlib import Path
import sys
import tempfile


def _joined(result):
    return str(getattr(result, "structuredContent", None)) + " " + " ".join(
        str(getattr(item, "text", "")) for item in result.content
    )


async def _session(db, *, client, project, rights, callback):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({
        "SYMEM_DB": str(db),
        "SYMEM_SPACE": "shared",
        "SYMEM_PROJECT": project,
        "SYMEM_CLIENT": client,
        "SYMEM_RIGHTS": rights,
        "PYTHONPATH": str(root / "src") + os.pathsep + env.get("PYTHONPATH", ""),
    })
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "synapsys_memory.mcp_server"],
        env=env,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            return await callback(session, tools)


async def main():
    with tempfile.TemporaryDirectory(prefix="symem-mcp-test-") as temp:
        db = Path(temp) / "memory.sqlite3"

        async def writer(session, tools):
            assert {"memory_remember", "memory_recall"} <= tools, tools
            assert "memory_forget" not in tools, tools
            result = await session.call_tool("memory_remember", {
                "body": "La memoria compartida mantiene la persistencia entre clientes.",
                "title": "Continuidad MCP",
            })
            assert not result.isError, _joined(result)
            assert "created" in _joined(result), _joined(result)

        await _session(db, client="opencode", project="demo", rights="read,write", callback=writer)

        async def reader(session, tools):
            assert "memory_recall" in tools and "memory_remember" not in tools, tools
            result = await session.call_tool("memory_recall", {"query": "persistencia", "limit": 5})
            assert not result.isError, _joined(result)
            assert "memoria compartida" in _joined(result), _joined(result)

        await _session(db, client="codex", project="demo", rights="read", callback=reader)

        async def isolated(session, tools):
            assert "memory_remember" not in tools, tools
            result = await session.call_tool("memory_recall", {"query": "persistencia"})
            assert not result.isError, _joined(result)
            assert "memoria compartida" not in _joined(result), _joined(result)

        await _session(db, client="openclaw", project="aislado", rights="read", callback=isolated)

    print("PASS: 3 procesos MCP stdio, lectura compartida, permiso de lectura y aislamiento")


if __name__ == "__main__":
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise SystemExit("NO EJECUTADO: instala MCP con python -m pip install -e '.[mcp]'")
    asyncio.run(asyncio.wait_for(main(), timeout=45))
