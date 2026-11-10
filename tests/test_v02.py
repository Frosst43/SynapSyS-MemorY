from contextlib import closing
"""Validaciones v0.2 sin dependencias externas ni sesiones reales de agentes."""

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from synapsys_memory.mcp_server import make_server
from synapsys_memory.policy import ConnectorPolicy
from synapsys_memory.store import MemoryConflictError, MemoryStore, SCHEMA_VERSION


class FakeFastMCP:
    """Solo comprueba el registro y llamadas de herramientas, NO protocolo MCP."""

    def __init__(self, name, instructions):
        self.name = name
        self.instructions = instructions
        self.tools = {}

    def tool(self):
        def register(func):
            self.tools[func.__name__] = func
            return func
        return register


class V02Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path, space="shared", project="syptest", actor="opencode")
        self.addCleanup(self.store.close)

    def fake_server(self, policy):
        package = types.ModuleType("mcp")
        server = types.ModuleType("mcp.server")
        fastmcp = types.ModuleType("mcp.server.fastmcp")
        fastmcp.FastMCP = FakeFastMCP
        with patch.dict(sys.modules, {"mcp": package, "mcp.server": server,
                                       "mcp.server.fastmcp": fastmcp}):
            return make_server(self.store, policy)

    def test_schema_version_and_integrity(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(SCHEMA_VERSION, version)
        self.assertTrue(self.store.check_integrity())

    def test_migration_from_v01_keeps_memories(self):
        original = self.store.remember("Decision inicial persistida")["memory"]["id"]
        self.store.close()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DROP TABLE memory_events")
            conn.execute("ALTER TABLE memories DROP COLUMN revision")
            conn.execute("PRAGMA user_version=0")
        with MemoryStore(self.path, space="shared", project="syptest") as upgraded:
            self.assertEqual(original, upgraded.recall("persistida")[0]["id"])
            self.assertEqual(1, upgraded.get(original)["revision"])
            self.assertTrue(upgraded.check_integrity())


    def test_audit_does_not_resurrect_deleted_content(self):
        saved = self.store.remember("Contenido reservado de la prueba")
        uid = saved["memory"]["id"]
        self.store.update(uid, body="Contenido nuevo", expected_revision=1)
        self.store.forget(uid, expected_revision=2)
        self.assertIsNone(self.store.get(uid))
        audit = self.store.audit(uid)
        self.assertEqual(["delete", "update", "create"], [e["action"] for e in audit])
        self.assertEqual([2, 2, 1], [e["revision"] for e in audit])
        self.assertNotIn("Contenido", repr(audit))
        with MemoryStore(self.path, space="other", project="syptest") as other:
            self.assertEqual([], other.audit(uid))

    def test_optimistic_revision_prevents_lost_update(self):
        uid = self.store.remember("Contenido original")["memory"]["id"]
        with MemoryStore(self.path, space="shared", project="syptest", actor="codex") as codex:
            self.assertEqual(1, codex.get(uid)["revision"])
            self.store.update(uid, body="Cambio de OpenCode", expected_revision=1)
            with self.assertRaises(MemoryConflictError):
                codex.update(uid, body="Cambio obsoleto", expected_revision=1)
            with self.assertRaises(MemoryConflictError):
                codex.forget(uid, expected_revision=1)
            updated = codex.update(uid, body="Cambio consensuado", expected_revision=2)
            self.assertEqual(3, updated["revision"])
            self.assertEqual("Cambio consensuado", self.store.get(uid)["body"])

    def test_bad_revision_rejected(self):
        uid = self.store.remember("dato")["memory"]["id"]
        for rev in (-1, 0, True, "1"):
            with self.assertRaises(ValueError):
                self.store.update(uid, body="dato diferente", expected_revision=rev)
            with self.assertRaises(ValueError):
                self.store.forget(uid, expected_revision=rev)

    def test_readonly_client_only_sees_read_tools(self):
        server = self.fake_server(ConnectorPolicy.from_values("codex", "read"))
        self.assertEqual({"memory_recall", "memory_recent", "memory_get", "memory_stats", "memory_audit"},
                         set(server.tools))
        saved = self.store.remember("Dato de OpenCode", title="Prueba")
        self.assertEqual(saved["memory"]["id"], server.tools["memory_recall"]("OpenCode")[0]["id"])
        self.assertNotIn("database", server.tools["memory_stats"]())

    def test_readwrite_client_cannot_delete(self):
        server = self.fake_server(ConnectorPolicy.from_values("opencode", "read,write"))
        self.assertIn("memory_update", server.tools)
        self.assertIn("memory_remember", server.tools)
        self.assertNotIn("memory_forget", server.tools)
        uid = server.tools["memory_remember"]("Dato compartido")["memory"]["id"]
        self.assertEqual("mcp:opencode", self.store.get(uid)["source"])
        self.assertEqual("Dato corregido", server.tools["memory_update"](
            uid, "Dato corregido", expected_revision=1)["body"])

    def test_admin_can_delete_only_with_revision(self):
        server = self.fake_server(ConnectorPolicy.from_values("admin", "read,write,delete"))
        uid = self.store.remember("Dato a borrar")["memory"]["id"]
        with self.assertRaises(TypeError):
            server.tools["memory_forget"](uid)
        self.assertTrue(server.tools["memory_forget"](uid, 1)["deleted"])

    def test_write_only_does_not_expose_full_record(self):
        server = self.fake_server(ConnectorPolicy.from_values("writer", "write"))
        self.assertEqual({"memory_remember", "memory_update"}, set(server.tools))
        result = server.tools["memory_remember"]("Contenido original")
        self.assertNotIn("memory", result)
        self.assertEqual({"created", "id", "revision"}, set(result))
        updated = server.tools["memory_update"](result["id"], "Contenido nuevo", result["revision"])
        self.assertEqual({"id", "revision"}, set(updated))

    def test_future_schema_rejected_before_any_migration(self):
        future = Path(self.tmp.name) / "future.sqlite3"
        with closing(sqlite3.connect(future)) as conn, conn:
            conn.execute("PRAGMA user_version=900")
        future.chmod(0o600)  # A valid private fixture reaches the version check.
        with self.assertRaises(RuntimeError):
            MemoryStore(future, space="shared", project="syptest")
        with closing(sqlite3.connect(future)) as conn, conn:
            self.assertEqual(900, conn.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual([], conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())

    def test_policy_rejects_invalid_rights(self):
        for rights in ("", "all", "delete", "read,admin", "read,,write"):
            with self.assertRaises(ValueError):
                ConnectorPolicy.from_values("agent", rights)
        for client in ("", "../other", "client name", "a" * 65):
            with self.assertRaises(ValueError):
                ConnectorPolicy.from_values(client, "read")

    def test_per_client_environment_is_explicit(self):
        with patch.dict(os.environ, {"SYMEM_CLIENT": "opencode", "SYMEM_RIGHTS": "read"}):
            self.assertEqual(frozenset({"read"}), ConnectorPolicy.from_environment().rights)

    def test_multiple_concurrent_clients_same_project(self):
        self.store.remember("Acuerdo del equipo 4 minutos", title="Tiempo por bloque")
        with MemoryStore(self.path, space="shared", project="syptest", actor="openclaw") as client2:
            self.assertEqual(1, len(client2.recall("bloque")))
            client2.remember("Nueva nota de OpenClaw", title="Otra nota")
        self.assertEqual(2, self.store.stats()["count"])
        with MemoryStore(self.path, space="shared", project="separado", actor="codex") as denied:
            self.assertEqual([], denied.recall("bloque"))
            self.assertEqual(0, denied.stats()["count"])


if __name__ == "__main__":
    unittest.main()
