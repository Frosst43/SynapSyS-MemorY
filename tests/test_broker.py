"""Pruebas de seguridad de broker por sockets Unix con archivos temporales."""

import os
from pathlib import Path
import secrets
import socket
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request, request
from synapsys_memory.store import MemoryConflictError


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Requiere sockets Unix")
class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="symem-b-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "memory.db"
        self.sock = self.base / "data.sock"
        self.admin_sock = self.base / "admin.sock"
        self.secret = secrets.token_urlsafe(48)
        self.broker = BrokerDaemon(self.db, self.sock, self.admin_sock,
                                   admin_key=self.secret)
        self.broker.start()
        self.addCleanup(self.broker.stop)

    def grant(self, session="qwen-terminal", client="qwen", project="terminal-pro",
              rights="read,write", space="private", ttl_seconds=1800):
        return admin_request(self.admin_sock, self.secret, "grant", session=session,
                             client=client, space=space, project=project, rights=rights,
                             ttl_seconds=ttl_seconds)

    def admin(self, action, **kwargs):
        return admin_request(self.admin_sock, self.secret, action, **kwargs)

    def test_mcp_bridge_uses_broker_policy_not_client_environment(self):
        from synapsys_memory import mcp_server
        from unittest.mock import MagicMock
        grant = self.grant(session="only-read", client="codex", project="synapsys", rights="read")
        fake_server = MagicMock()
        with patch.dict(os.environ, {
            "SYMEM_BROKER_SOCKET": str(self.sock),
            "SYMEM_SESSION_TOKEN": grant["token"],
            "SYMEM_RIGHTS": "read,write,delete",  # intento de elevacion
            "SYMEM_PROJECT": "sypcoin",  # intento de cambiar proyecto
        }), patch.object(mcp_server, "MemoryStore", side_effect=AssertionError("SQLite directo")), \
             patch.object(mcp_server, "make_server", return_value=fake_server) as make:
            mcp_server.main()
        self.assertEqual("read", ",".join(sorted(make.call_args.args[1].rights)))
        self.assertEqual("codex", make.call_args.args[1].client)
        fake_server.run.assert_called_once_with(transport="stdio")

    def test_mcp_default_rejects_direct_sqlite_without_broker(self):
        from synapsys_memory import mcp_server
        with patch.dict(os.environ, {"SYMEM_BROKER_SOCKET": "", "SYMEM_ALLOW_LEGACY_DIRECT": ""}):
            with self.assertRaises(RuntimeError):
                mcp_server.main()

    def test_owner_socket_permissions_and_no_tcp(self):
        self.assertTrue(self.sock.is_socket())
        self.assertEqual(0o600, stat.S_IMODE(self.sock.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(self.admin_sock.stat().st_mode))
        self.assertNotEqual(self.sock, self.admin_sock)

    def test_grant_memory_and_cross_project_denial(self):
        a = self.grant()
        old = BrokerClient(self.sock, a["token"])
        saved = old.remember("Terminal PRO es un subprograma para el minero", title="Terminal")
        self.assertTrue(saved["created"])
        self.assertEqual(1, len(old.recall("Terminal")))
        b = self.grant(session="codex-synapsys", client="codex", project="synapsys", rights="read")
        other = BrokerClient(self.sock, b["token"])
        self.assertEqual([], other.recall("Terminal"))
        self.assertEqual(0, other.stats()["count"])
        self.assertEqual(1, old.stats()["count"])
        with self.assertRaises(PermissionError):
            other.remember("Escritura sin permiso")
        self.assertIsNone(other.get(saved["memory"]["id"]))

    def test_switch_revokes_old_token_and_denies_prior_project(self):
        grant = self.grant()
        old = BrokerClient(self.sock, grant["token"])
        uid = old.remember("Dato solo de Terminal", title="Terminal")["memory"]["id"]
        changed = self.admin("switch", session="qwen-terminal", client="qwen",
                             space="private", project="synapsys", rights="read,write")
        new = BrokerClient(self.sock, changed["token"])
        with self.assertRaises(PermissionError):
            old.recall("Terminal")
        with self.assertRaises(PermissionError):
            old.remember("no puede escribir tras cambio")
        self.assertEqual([], new.recall("Terminal"))
        self.assertIsNone(new.get(uid))
        self.assertEqual("synapsys", new.profile()["project"])
        self.assertEqual(1, new.remember("Estado exclusivo SynapSyS")["memory"]["revision"])
        back = self.admin("switch", session="qwen-terminal", client="qwen",
                          space="private", project="terminal-pro", rights="read")
        with self.assertRaises(PermissionError):
            new.recall("SynapSyS")
        self.assertEqual(uid, BrokerClient(self.sock, back["token"]).recall("Terminal")[0]["id"])

    def test_no_credentials_admin_leak_or_admin_via_data_socket(self):
        with self.assertRaises(PermissionError):
            admin_request(self.admin_sock, "incorrect" * 6, "sessions")
        with self.assertRaises(PermissionError):
            request(self.sock, {"admin_key": self.secret, "action": "grant", "args": {}})
        self.assertEqual([], self.admin("sessions"))
        granted = self.grant()
        self.assertNotIn(granted["token"], str(self.admin("sessions")))
        self.assertNotIn(self.secret, str(self.admin("sessions")))

    def test_cannot_set_space_project_source_or_rights_from_client(self):
        token = self.grant()["token"]
        for field in ("project", "space", "source", "rights", "client", "actor"):
            with self.subTest(field=field), self.assertRaises(PermissionError):
                request(self.sock, {"token": token, "action": "remember",
                                    "args": {"body": "Intrusion", field: "synapsys"}})
        self.assertEqual(0, BrokerClient(self.sock, token).stats()["count"])

    def test_readonly_writeonly_and_delete_not_implicit(self):
        read = BrokerClient(self.sock, self.grant(session="r", client="codex", rights="read")["token"])
        with self.assertRaises(PermissionError):
            read.remember("no autorizado")
        write = BrokerClient(self.sock, self.grant(session="w", client="opencode", rights="write")["token"])
        rec = write.remember("Escribir sin leer")
        self.assertEqual({"created", "id", "revision"}, set(rec))
        with self.assertRaises(PermissionError):
            write.recall("Escribir")
        with self.assertRaises(PermissionError):
            write.forget(rec["id"], expected_revision=1)
        with self.assertRaises(ValueError):
            self.grant(session="bad", rights="delete")

    def test_edit_conflict_and_forget_revision_check(self):
        token = self.grant(rights="read,write,delete")["token"]
        c = BrokerClient(self.sock, token)
        uid = c.remember("Inicial")["memory"]["id"]
        self.assertEqual(2, c.update(uid, body="Actualizado", expected_revision=1)["revision"])
        with self.assertRaises(MemoryConflictError):
            c.forget(uid, expected_revision=1)
        self.assertTrue(c.forget(uid, expected_revision=2))
        self.assertIsNone(c.get(uid))
        self.assertEqual(["delete", "update", "create"],
                         [row["action"] for row in c.audit(uid)])

    def test_admin_revocation_stops_old_session(self):
        old = BrokerClient(self.sock, self.grant()["token"])
        self.assertTrue(self.admin("revoke", session="qwen-terminal")["revoked"])
        with self.assertRaises(PermissionError):
            old.profile()
        self.assertFalse(self.admin("revoke", session="qwen-terminal")["revoked"])

    def test_single_project_per_session_and_invalid_switch_keeps_old(self):
        token = self.grant()["token"]
        with self.assertRaises(ValueError):
            self.grant(session="qwen-terminal", project="synapsys")
        with self.assertRaises(ValueError):
            self.admin("switch", session="qwen-terminal", client="qwen",
                       space="private", project="synapsys", rights="admin")
        self.assertEqual("terminal-pro", BrokerClient(self.sock, token).profile()["project"])

    def test_two_clients_same_scope_share_only_that_project(self):
        writer = BrokerClient(self.sock, self.grant(session="open", client="opencode",
                                                    project="sypcoin")["token"])
        reader = BrokerClient(self.sock, self.grant(session="codex", client="codex",
                                                    project="sypcoin", rights="read")["token"])
        foreign = BrokerClient(self.sock, self.grant(session="lab", client="qwen",
                                                     project="synapsys-lab", rights="read")["token"])
        writer.remember("SYPcoin usa identificador SYPC", title="Ticker")
        self.assertEqual(1, len(reader.recall("SYPC")))
        self.assertEqual([], foreign.recall("SYPC"))

    def test_sessions_expire_fail_closed(self):
        granted = self.grant(ttl_seconds=30)
        client = BrokerClient(self.sock, granted["token"])
        expires = granted["expires_at"]
        with patch("synapsys_memory.broker.time.time", return_value=expires + 10):
            with self.assertRaises(PermissionError):
                client.profile()

    def test_revocation_linearizes_against_concurrent_operations(self):
        token = self.grant()["token"]
        cli = BrokerClient(self.sock, token)
        errors = []
        start = threading.Event()
        def writer():
            start.wait()
            try:
                for n in range(10):
                    cli.remember(f"Trabajo {n}")
            except PermissionError:
                return
            except Exception as exc:
                errors.append(exc)
        t = threading.Thread(target=writer)
        t.start(); start.set()
        self.admin("switch", session="qwen-terminal", client="qwen", space="private",
                   project="synapsys", rights="read,write")
        t.join(timeout=3)
        self.assertFalse(t.is_alive())
        self.assertFalse(errors)
        with self.assertRaises(PermissionError):
            cli.remember("Tardio")

    def test_restart_invalidates_old_token(self):
        token = self.grant()["token"]
        self.broker.stop()
        self.broker.start()
        with self.assertRaises(PermissionError):
            BrokerClient(self.sock, token).profile()


if __name__ == "__main__":
    unittest.main()
