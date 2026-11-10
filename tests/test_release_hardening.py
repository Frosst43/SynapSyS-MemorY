"""Regression tests for the v1 audit. No models, real projects or network."""
from contextlib import redirect_stderr, closing
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from synapsys_memory.broker import BrokerClient, BrokerDaemon, BrokerError, admin_request, request
from synapsys_memory.mcp_server import make_server
from synapsys_memory.policy import ConnectorPolicy
from synapsys_memory.sandbox import SandboxError, load_policy
from synapsys_memory.session_secret import read_token_file
from synapsys_memory.store import MemoryStore


class StoreReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='symem-release-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_schema_failure_rolls_back_alter(self):
        db = self.root / 'legacy.sqlite3'
        with MemoryStore(db) as store:
            store.remember('preserve this record')
        with closing(sqlite3.connect(db)) as con, con:
            con.execute('DROP TABLE memory_events')
            con.execute('ALTER TABLE memories DROP COLUMN revision')
            con.execute('PRAGMA user_version=0')
        real_connect = sqlite3.connect
        class FaultConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kw):
                if 'CREATE TABLE IF NOT EXISTS memory_events' in sql:
                    raise sqlite3.OperationalError('injected migration error')
                return super().execute(sql, *args, **kw)
        def failing(*args, **kw):
            return real_connect(*args, **kw, factory=FaultConnection)
        with patch('synapsys_memory.store.sqlite3.connect', side_effect=failing):
            with self.assertRaises(sqlite3.OperationalError):
                MemoryStore(db)
        with closing(real_connect(db)) as con, con:
            columns = [r[1] for r in con.execute('PRAGMA table_info(memories)')]
            self.assertNotIn('revision', columns)
            self.assertEqual(0, con.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(1, con.execute('SELECT COUNT(*) FROM memories').fetchone()[0])

    def test_parallel_first_open_migration(self):
        db = self.root / 'parallel.sqlite3'
        gate = threading.Barrier(6)
        errors = []
        def worker(i):
            try:
                gate.wait(timeout=5)
                with MemoryStore(db) as store:
                    store.remember(f'record {i}')
            except Exception as exc:
                errors.append(repr(exc))
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads: t.start()
        for t in threads: t.join(15)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual([], errors)
        with MemoryStore(db) as store:
            self.assertEqual(6, store.stats()['count'])

    def test_backup_private_before_sqlite_opens_it(self):
        dest = self.root / 'backup.sqlite3'
        observed = []
        connect = sqlite3.connect
        def inspect(path, *args, **kw):
            result = connect(path, *args, **kw)
            if Path(path) == dest:
                observed.append(stat.S_IMODE(dest.stat().st_mode))
            return result
        with MemoryStore(self.root / 'db.sqlite3') as store:
            store.remember('backup payload')
            with patch('synapsys_memory.store.sqlite3.connect', side_effect=inspect):
                store.backup(dest)
        self.assertEqual([0o600], observed)
        with MemoryStore(dest) as restored:
            self.assertEqual(1, restored.stats()['count'])

    def test_backup_cannot_follow_broken_symlink(self):
        link = self.root / 'backup.sqlite3'
        target = self.root / 'must-not-create.sqlite3'
        link.symlink_to(target)
        with MemoryStore(self.root / 'db.sqlite3') as store:
            with self.assertRaises((ValueError, OSError)):
                store.backup(link)
        self.assertFalse(target.exists())

    def test_direct_store_cannot_open_hardlink(self):
        db = self.root / 'db.sqlite3'
        with MemoryStore(db): pass
        alias = self.root / 'alias.sqlite3'
        os.link(db, alias)
        with self.assertRaises((ValueError, PermissionError)):
            with MemoryStore(alias): pass

    def test_direct_store_rejects_unsafe_existing_mode_without_chmod(self):
        db = self.root / 'db.sqlite3'
        with MemoryStore(db): pass
        db.chmod(0o644)
        with self.assertRaises((ValueError, PermissionError)):
            with MemoryStore(db): pass
        self.assertEqual(0o644, stat.S_IMODE(db.stat().st_mode))

    def test_expired_record_can_be_explicitly_deleted(self):
        with MemoryStore(self.root / 'db.sqlite3') as store:
            with patch('synapsys_memory.store._now_ms', return_value=1000):
                uid = store.remember('expired secret', ttl_days=1)['memory']['id']
            with patch('synapsys_memory.store._now_ms', return_value=1000 + 2*86400000):
                self.assertIsNone(store.get(uid))
                self.assertTrue(store.forget(uid, expected_revision=1))
            self.assertEqual(['delete', 'create'], [x['action'] for x in store.audit(uid)])

    def test_wal_durability_full(self):
        with MemoryStore(self.root / 'db.sqlite3') as store:
            self.assertEqual(2, store._db.execute('PRAGMA synchronous').fetchone()[0])

    def test_metadata_version_matches_package(self):
        import synapsys_memory
        import tomllib
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root/'pyproject.toml').read_text())
        self.assertEqual(metadata['project']['version'], synapsys_memory.__version__)


class BrokerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='symem-protocol-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data, self.admin = self.root/'data.sock', self.root/'admin.sock'
        self.key = secrets.token_urlsafe(48)
        self.broker = BrokerDaemon(self.root/'db.sqlite3', self.data, self.admin, admin_key=self.key)
        self.broker.start()
        self.addCleanup(self.broker.stop)

    def grant(self, session='one', rights='read,write,delete'):
        return admin_request(self.admin, self.key, 'grant', session=session, client='client',
                             space='test', project='arbitrary-project', rights=rights)

    def test_null_revision_rejected_at_broker_update(self):
        g = self.grant()
        client = BrokerClient(self.data, g['token'])
        uid = client.remember('original')['memory']['id']
        with self.assertRaises(ValueError):
            client.update(uid, body='should fail', expected_revision=None)
        self.assertEqual('original', client.get(uid)['body'])

    def test_null_revision_rejected_at_broker_delete(self):
        g = self.grant()
        client = BrokerClient(self.data, g['token'])
        uid = client.remember('original')['memory']['id']
        with self.assertRaises(ValueError):
            client.forget(uid, expected_revision=None)
        self.assertIsNotNone(client.get(uid))

    def test_write_only_mcp_can_use_real_broker(self):
        g = self.grant(rights='write')
        class FakeMCP:
            def __init__(self, *args, **kw): self.tools = {}
            def tool(self):
                def register(f): self.tools[f.__name__] = f; return f
                return register
        package = types.ModuleType('mcp')
        server = types.ModuleType('mcp.server')
        fast = types.ModuleType('mcp.server.fastmcp'); fast.FastMCP = FakeMCP
        with patch.dict(sys.modules, {'mcp': package, 'mcp.server': server, 'mcp.server.fastmcp': fast}):
            api = make_server(BrokerClient(self.data, g['token']), ConnectorPolicy.from_values('client', 'write'))
            result = api.tools['memory_remember']('private payload')
        self.assertEqual({'created', 'id', 'revision'}, set(result))
        self.assertNotIn('private payload', repr(result))

    def test_sqlite_errors_have_bounded_protocol_response(self):
        g = self.grant()
        err = StringIO()
        with patch.object(MemoryStore, 'remember', side_effect=sqlite3.OperationalError('SENSITIVE_FILE_PATH')):
            with redirect_stderr(err), self.assertRaisesRegex(BrokerError, 'Error interno del servidor'):
                BrokerClient(self.data, g['token']).remember('anything')
        self.assertNotIn('SENSITIVE_FILE_PATH', err.getvalue())

    def test_unknown_arguments_rejected_instead_of_silently_ignored(self):
        g = self.grant()
        with self.assertRaises(ValueError):
            request(self.data, {'token': g['token'], 'action': 'stats', 'args': {'not_a_field': True}})

    def test_expiry_not_extended_by_clock_rollback(self):
        g = self.grant()
        client = BrokerClient(self.data, g['token'])
        import time
        before = time.monotonic()
        with patch('synapsys_memory.broker.time.monotonic', return_value=before+3600), \
             patch('synapsys_memory.broker.time.time', return_value=g['expires_at']-3600):
            with self.assertRaises(PermissionError): client.profile()

    def test_stopping_unstarted_broker_preserves_someone_elses_socket(self):
        import socket
        other = self.root/'foreign.sock'
        with socket.socket(socket.AF_UNIX) as owner:
            owner.bind(str(other))
            unused = BrokerDaemon(self.root/'other.db', other, self.root/'other-admin.sock', admin_key=self.key)
            unused.stop()
            self.assertTrue(other.is_socket())


class SandboxReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='symem-policy-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a, self.b = self.root/'a', self.root/'b'
        self.a.mkdir(); self.b.mkdir()
        self.policy = self.root/'policy.json'

    def config(self, projects, path=None):
        p = path or self.policy
        p.write_text(json.dumps({'schema_version':1,'projects':projects}))
        p.chmod(0o600)
        return p

    def test_overlapping_project_directories_rejected(self):
        child = self.a/'child'; child.mkdir()
        self.config({'a':{'workspace':str(self.a),'read_only':False},
                     'b':{'workspace':str(child),'read_only':False}})
        with self.assertRaises(SandboxError): load_policy(self.policy, 'a')

    def test_same_directory_two_projects_rejected(self):
        self.config({'a':{'workspace':str(self.a),'read_only':False},
                     'b':{'workspace':str(self.a),'read_only':True}})
        with self.assertRaises(SandboxError): load_policy(self.policy, 'a')

    def test_policy_inside_another_project_rejected(self):
        policy = self.b/'policy.json'
        self.config({'a':{'workspace':str(self.a),'read_only':False},
                     'b':{'workspace':str(self.b),'read_only':False}}, policy)
        with self.assertRaises(SandboxError): load_policy(policy, 'a')

    def test_foreign_hardlink_inside_workspace_rejected(self):
        secret = self.b/'foreign'; secret.write_text('unchanged')
        os.link(secret, self.a/'same-inode')
        self.config({'a':{'workspace':str(self.a),'read_only':False}})
        with self.assertRaises(SandboxError): load_policy(self.policy, 'a')
        self.assertEqual('unchanged', secret.read_text())

    def test_fifo_inside_workspace_rejected_without_opening(self):
        os.mkfifo(self.a/'fifo')
        self.config({'a':{'workspace':str(self.a),'read_only':False}})
        with self.assertRaises(SandboxError): load_policy(self.policy, 'a')

    def test_system_subdirectory_rejected(self):
        self.config({'a':{'workspace':'/usr/local','read_only':False}})
        with self.assertRaises(SandboxError): load_policy(self.policy, 'a')

    def test_duplicate_json_policy_key_rejected(self):
        path = json.dumps(str(self.a))
        self.policy.write_text('{"schema_version":1,"projects":{"a":{"workspace":'+path+',"read_only":true,"read_only":false}}}')
        self.policy.chmod(0o600)
        with self.assertRaises(SandboxError): load_policy(self.policy, 'a')


if __name__ == '__main__': unittest.main()
