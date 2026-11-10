"""Private file and protocol validation; temporary files only."""
from pathlib import Path
import json
import os
import secrets
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch

from synapsys_memory.secureio import strict_json_loads, read_private, reserve_private
from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request


class SecureIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='symem-io-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_duplicate_keys_rejected_at_every_depth(self):
        for text in ('{"a":1,"a":2}', '{"outer":{"a":1,"a":2}}'):
            with self.subTest(text=text), self.assertRaises(ValueError): strict_json_loads(text)

    def test_nonfinite_values_are_not_json(self):
        for text in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError): strict_json_loads(text)

    def test_private_reservation_does_not_change_other_file_modes(self):
        path = self.root/'new'
        previous = os.umask(0)
        try: reserve_private(path)
        finally: os.umask(previous)
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        with self.assertRaises(ValueError): reserve_private(path, exclusive=True)

    def test_private_file_read_is_bounded(self):
        path = self.root/'token'; path.write_bytes(b'x'*64); path.chmod(0o600)
        self.assertEqual(b'x'*64, read_private(path, limit=64))
        with self.assertRaises(ValueError): read_private(path, limit=63)

    def test_private_reader_does_not_open_fifo(self):
        path = self.root/'fifo'; os.mkfifo(path, 0o600)
        with self.assertRaises(ValueError): read_private(path, limit=64)

    def test_sqlite_sidecar_symlink_is_rejected(self):
        db = self.root/'db'; reserve_private(db)
        (self.root/'db-wal').symlink_to(self.root/'elsewhere')
        with self.assertRaises(ValueError): reserve_private(db)

    def test_symlinked_parent_is_not_private_path(self):
        actual = self.root/'actual'; actual.mkdir()
        alias = self.root/'alias'; alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError): reserve_private(alias/'db')


class BrokerLimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='symem-limits-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.key = secrets.token_urlsafe(48)
        self.data, self.admin = root/'data.sock', root/'admin.sock'
        self.broker = BrokerDaemon(root/'db', self.data, self.admin, admin_key=self.key)
        self.broker.start(); self.addCleanup(self.broker.stop)

    def grant(self, name, project='one'):
        return admin_request(self.admin, self.key, 'grant', session=name, client='sdk',
                             space='test', project=project, rights='read,write')

    def test_session_limit_and_revocation_reclaim_capacity(self):
        with patch('synapsys_memory.broker.MAX_SESSIONS', 2):
            a, b = self.grant('a'), self.grant('b')
            with self.assertRaises(ValueError): self.grant('c')
            admin_request(self.admin, self.key, 'revoke', session='a')
            c = self.grant('c')
            with self.assertRaises(PermissionError): BrokerClient(self.data, a['token']).profile()
            self.assertEqual('one', BrokerClient(self.data, c['token']).profile()['project'])
            self.assertEqual(2, len(self.broker._tokens))

    def test_connection_cache_is_bounded_and_memories_persist(self):
        with patch('synapsys_memory.broker.MAX_STORES', 2):
            grants = [self.grant(f's-{i}', project=f'p-{i}') for i in range(4)]
            for i, grant in enumerate(grants):
                BrokerClient(self.data, grant['token']).remember(f'memory {i}')
            self.assertLessEqual(len(self.broker._stores), 2)
            for grant in grants:
                self.assertEqual(1, BrokerClient(self.data, grant['token']).stats()['count'])
            self.assertLessEqual(len(self.broker._stores), 2)

    def test_malformed_json_gets_no_traceback_or_secret(self):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3); client.connect(str(self.data))
            client.sendall(b'{"action":"profile","action":"grant"}\n')
            result = json.loads(client.recv(8192))
        self.assertFalse(result['ok']); self.assertEqual('validation', result['error'])
        self.assertNotIn(self.key, str(result))
