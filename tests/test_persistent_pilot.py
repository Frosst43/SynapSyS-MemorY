"""New persistence pilot tests. All DB writes are synthetic and temporary."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_persistent_memory.py'
SPEC = importlib.util.spec_from_file_location('verify_persistent_memory', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PersistentPilotTests(unittest.TestCase):
    def test_new_database_persists_across_broker_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / 'memory.sqlite3'
            info = MODULE.run(db, evidence=False)
            self.assertEqual(info['database'], str(db))
            for field in ('broker_restarted', 'saved', 'new_authorized_client_read',
                          'fts_recall', 'revoked_writer_denied', 'foreign_project_denied',
                          'read_only_write_denied'):
                self.assertTrue(info[field], field)
            self.assertFalse(info['model_inference'])
            self.assertTrue(db.is_file())
            self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                found = connection.execute('SELECT space,project FROM memories WHERE uid=?',
                                           (info['memory_id'],)).fetchone()
                self.assertEqual(found, (MODULE.SPACE, MODULE.PROJECT))

    def test_existing_database_not_replaced_on_second_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / 'memory.sqlite3'
            first = MODULE.run(db, evidence=False)
            second = MODULE.run(db, evidence=False)
            self.assertNotEqual(first['memory_id'], second['memory_id'])
            with sqlite3.connect(db) as conn:
                ids = set(row[0] for row in conn.execute('SELECT uid FROM memories'))
            self.assertIn(first['memory_id'], ids)
            self.assertIn(second['memory_id'], ids)

    def test_evidence_is_private_and_contains_no_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / 'memory.sqlite3'
            result = MODULE.run(db, evidence=True)
            evidence = Path(result['evidence'])
            self.assertTrue(evidence.is_file())
            self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)
            self.assertEqual(json.loads(evidence.read_text())['memory_id'], result['memory_id'])
            self.assertNotIn('token', evidence.read_text().lower())

    def test_rejects_world_readable_database_without_changing_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / 'memory.sqlite3'
            db.write_bytes(b'not sqlite')
            db.chmod(0o644)
            with self.assertRaises(PermissionError):
                MODULE.run(db, evidence=False)
            self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o644)
            self.assertEqual(db.read_bytes(), b'not sqlite')

    def test_rejects_symlink_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'other.sqlite3'
            target.write_text('unchanged')
            link = root / 'memory.sqlite3'
            link.symlink_to(target)
            with self.assertRaises(PermissionError):
                MODULE.run(link, evidence=False)
            self.assertEqual(target.read_text(), 'unchanged')

    def test_rejects_world_executable_parent_without_chmod(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'open'
            directory.mkdir(mode=0o755)
            with self.assertRaises(PermissionError):
                MODULE.run(directory / 'memory.sqlite3', evidence=False)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)

    def test_rejects_existing_broker_socket_and_keeps_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / 'admin.sock'
            sentinel.write_text('untouched')
            with self.assertRaises(RuntimeError):
                MODULE.run(root / 'memory.sqlite3', evidence=False)
            self.assertEqual(sentinel.read_text(), 'untouched')
            self.assertFalse((root / 'memory.sqlite3').exists())

    def test_denial_helper_rejects_false_positive(self):
        with self.assertRaises(AssertionError):
            MODULE.assert_forbidden(lambda: None)
        MODULE.assert_forbidden(lambda: (_ for _ in ()).throw(PermissionError()))


if __name__ == '__main__':
    unittest.main()
