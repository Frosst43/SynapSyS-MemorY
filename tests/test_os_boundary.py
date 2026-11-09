"""Pruebas de identidad Unix, revocacion, rutas privadas y sockets, sin sudo."""

import os
from pathlib import Path
import secrets
import socket
import stat
import tempfile
import unittest

from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and
                     (hasattr(socket, "SO_PEERCRED") or hasattr(socket.socket, "getpeereid")),
                     "Requiere identificacion Unix de procesos")
class OSBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="symem-os-boundary-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.secret = secrets.token_urlsafe(48)
        self.data = self.root / "data.sock"
        self.admin = self.root / "admin.sock"
        self.db = self.root / "memory.sqlite3"
        self.broker = BrokerDaemon(self.db, self.data, self.admin, admin_key=self.secret)
        self.broker.start()
        self.addCleanup(self.broker.stop)

    def grant(self, uid=None):
        return admin_request(self.admin, self.secret, "grant",
                             session="qwen-test", client="qwen",
                             space="privado", project="cualquier-proyecto",
                             rights="read,write", peer_uid=uid)

    def test_real_kernel_peer_uid_match(self):
        grant = self.grant(os.geteuid())
        client = BrokerClient(self.data, grant["token"])
        self.assertEqual(os.geteuid(), grant["peer_uid"])
        self.assertEqual("cualquier-proyecto", client.profile()["project"])
        self.assertEqual(os.geteuid(), admin_request(self.admin, self.secret, "sessions")[0]["peer_uid"])

    def test_valid_token_rejected_when_kernel_uid_differs(self):
        grant = self.grant(os.geteuid() + 1)
        client = BrokerClient(self.data, grant["token"])
        with self.assertRaises(PermissionError):
            client.profile()
        with self.assertRaises(PermissionError):
            client.remember("No escribir con UID diferente")

    def test_switch_invalidates_previous_token_and_changes_uid(self):
        first = self.grant()
        old = BrokerClient(self.data, first["token"])
        old.remember("Antes de cambio")
        second = admin_request(self.admin, self.secret, "switch",
                               session="qwen-test", client="opencode",
                               space="privado", project="proyecto-otro",
                               rights="read", peer_uid=os.geteuid() + 1)
        with self.assertRaises(PermissionError):
            old.profile()
        with self.assertRaises(PermissionError):
            BrokerClient(self.data, second["token"]).profile()
        self.assertEqual(os.geteuid() + 1, second["peer_uid"])

    def test_peer_uid_cannot_be_forged_in_request(self):
        from synapsys_memory.broker import request
        grant = self.grant()
        with self.assertRaises(PermissionError):
            request(self.data, {"token": grant["token"], "action": "remember",
                                "args": {"body": "Intrusion", "peer_uid": 0}})

    def test_symlink_sqlite_is_rejected_before_start(self):
        second = self.root / "second"
        second.mkdir()
        (second / "db").symlink_to(self.db)
        b = BrokerDaemon(second / "db", second / "data.sock",
                         second / "admin.sock", admin_key=self.secret)
        with self.assertRaises(PermissionError):
            b.start()
        self.assertFalse((second / "data.sock").exists())

    def test_excessive_sqlite_permissions_refuse_start(self):
        db = self.root / "insecure.sqlite3"
        db.write_text("No deberia abrirse")
        db.chmod(0o666)
        b = BrokerDaemon(db, self.root / "s2.sock", self.root / "a2.sock",
                         admin_key=self.secret)
        with self.assertRaises(PermissionError):
            b.start()
        self.assertEqual(0o666, stat.S_IMODE(db.stat().st_mode))

    def test_group_socket_explicit_and_admin_private(self):
        base = self.root / "separate"
        base.mkdir(mode=0o710)
        data = base / "data.sock"
        admin = base / "admin.sock"
        b = BrokerDaemon(base / "db.sqlite3", data, admin, admin_key=self.secret,
                         client_gid=os.getegid())
        try:
            b.start()
            self.assertEqual(0o660, stat.S_IMODE(data.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(admin.stat().st_mode))
            self.assertEqual(os.getegid(), data.stat().st_gid)
        finally:
            b.stop()

    def test_group_mode_no_group_traversal_fails_closed(self):
        base = self.root / "private"
        base.mkdir(mode=0o700)
        b = BrokerDaemon(base / "db.sqlite3", base / "data.sock",
                         base / "admin.sock", admin_key=self.secret,
                         client_gid=os.getegid())
        with self.assertRaises(PermissionError):
            b.start()

    def test_unsafe_directory_rejected_before_sockets(self):
        base = self.root / "group-write"
        base.mkdir(mode=0o770)
        base.chmod(0o770)  # La umask no debe volver seguro el fixture
        b = BrokerDaemon(base / "db.sqlite3", base / "data.sock",
                         base / "admin.sock", admin_key=self.secret)
        with self.assertRaises(PermissionError):
            b.start()
        self.assertFalse((base / "data.sock").exists())

    def test_bad_uid_input_cannot_replace_existing_session(self):
        first = self.grant()
        for uid in (True, -1, 2**32, "1000"):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                admin_request(self.admin, self.secret, "switch",
                              session="qwen-test", client="qwen",
                              space="privado", project="otro-proyecto",
                              rights="read", peer_uid=uid)
            self.assertEqual("cualquier-proyecto",
                             BrokerClient(self.data, first["token"]).profile()["project"])


class PrivateCreationTests(unittest.TestCase):
    def test_sqlite_created_private_even_with_umask_zero(self):
        from synapsys_memory.store import MemoryStore
        with tempfile.TemporaryDirectory(prefix="symem-priv-") as temp:
            db = Path(temp) / "private.sqlite3"
            former = os.umask(0)
            try:
                with MemoryStore(db, space="test", project="foo") as store:
                    store.remember("Contenido privado desde el principio")
            finally:
                os.umask(former)
            self.assertEqual(0o600, stat.S_IMODE(db.stat().st_mode))

    def test_direct_store_rejects_symlink(self):
        from synapsys_memory.store import MemoryStore
        with tempfile.TemporaryDirectory(prefix="symem-slink-") as temp:
            base = Path(temp)
            target = base / "existing.sqlite3"
            target.write_text("No tocar")
            link = base / "link.sqlite3"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                MemoryStore(link, space="test", project="foo")
            self.assertEqual("No tocar", target.read_text())
