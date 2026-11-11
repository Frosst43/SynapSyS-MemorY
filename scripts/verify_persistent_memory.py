#!/usr/bin/env python3
"""Persistent-memory broker pilot; no model, MCP CLI, agent, network or real projects.

Uses a private, durable SQLite DB outside Git and two sequential brokers.
Only the synthetic (space, project) pair below is written. Existing data in
other scopes is neither queried nor changed. Every grant is ephemeral.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from datetime import datetime, timezone

from synapsys_memory.broker import BrokerClient, BrokerDaemon, admin_request

SPACE = 'symem-validation'
PROJECT = 'persistence-pilot'
FOREIGN_PROJECT = 'persistence-foreign'
DEFAULT_DB = Path.home() / '.local' / 'state' / 'synapsys-memory' / 'memory.sqlite3'


def protected_directory(directory: Path) -> None:
    """Create private directory or reject paths that we cannot trust.

    Never chmod or replace an existing directory: permissions are an admin task.
    """
    if not directory.is_absolute() or directory.is_symlink():
        raise PermissionError('Directory must be absolute and not a symlink')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = directory.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) & 0o077):
        raise PermissionError('Directory must be owned by the current UID and private (0700)')
    if directory.resolve(strict=True) != directory:
        raise PermissionError('Directory must have a canonical non-symlink path')


def persistent_path(path: Path) -> Path:
    """Use only a private, regular file; leave any existing DB untouched."""
    path = path.expanduser().absolute()
    protected_directory(path.parent)
    if path.is_symlink():
        raise PermissionError('SQLite symlink is forbidden')
    if path.exists():
        st = path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) & 0o077):
            raise PermissionError('Existing SQLite must be private regular 0600, single link')
    return path


def assert_forbidden(call) -> None:
    try:
        call()
    except PermissionError:
        return
    raise AssertionError('A revoked or unauthorized client was accepted')


def run(db: Path, *, evidence: bool = True) -> dict:
    db = persistent_path(db)
    checkout = Path(__file__).resolve().parents[1]
    if db == checkout or db.is_relative_to(checkout):
        raise ValueError('SQLite must stay outside the source repository')
    base = db.parent
    # Do not compete with the normal broker at its default socket locations.
    for name in ('data.sock', 'admin.sock'):
        candidate = base / name
        if candidate.exists() or candidate.is_symlink():
            raise RuntimeError('A broker socket exists; refusing to start a second broker')

    key = secrets.token_urlsafe(48)
    unique = secrets.token_hex(12)
    marker = 'symempersist' + unique
    body = 'Recuerdo sintetico del piloto persistente: ' + marker
    session1 = 'persist-writer-' + secrets.token_hex(6)
    session2 = 'persist-reader-' + secrets.token_hex(6)
    session3 = 'persist-foreign-' + secrets.token_hex(6)
    result = {'database': str(db), 'scope': {'space': SPACE, 'project': PROJECT},
              'broker_restarted': False, 'saved': False,
              'new_authorized_client_read': False, 'fts_recall': False,
              'revoked_writer_denied': False, 'foreign_project_denied': False,
              'read_only_write_denied': False, 'no_real_project_modified': True,
              'model_inference': False, 'stable_release_ready': False}

    # Same socket pathname but different broker process lifecycle. No live sessions survive.
    with tempfile.TemporaryDirectory(prefix='symem-persistence-sockets-', dir=base) as temporary:
        root = Path(temporary)
        data, admin = root / 'data.sock', root / 'admin.sock'
        with BrokerDaemon(db, data, admin, admin_key=key):
            first = admin_request(admin, key, 'grant', session=session1,
                                  client='pilot-writer', space=SPACE, project=PROJECT,
                                  rights='read,write', ttl_seconds=120)
            try:
                writer = BrokerClient(data, first['token'])
                saved = writer.remember(body, title='SynapSyS-MemorY persistence pilot',
                                        kind='fact', tags=['synthetic', 'pilot'])
                record = saved['memory']
                memory_id = record['id']
                if record['body'] != body or writer.get(memory_id)['id'] != memory_id:
                    raise AssertionError('First session could not confirm saved record')
                result['saved'] = True
                result['memory_id'] = memory_id
            finally:
                if not admin_request(admin, key, 'revoke', session=session1)['revoked']:
                    raise AssertionError('Writer session was not revoked')
            assert_forbidden(lambda: writer.profile())
            result['revoked_writer_denied'] = True
        # BrokerDaemon.stop has now closed all SQLite connections and sockets.
        if not db.is_file() or db.stat().st_size == 0:
            raise AssertionError('Persistent SQLite file was not created')

        # A *new* broker instance and *different* authorized client use the same DB.
        key2 = secrets.token_urlsafe(48)
        with BrokerDaemon(db, data, admin, admin_key=key2):
            result['broker_restarted'] = True
            assert_forbidden(lambda: BrokerClient(data, first['token']).profile())
            reader_grant = admin_request(admin, key2, 'grant', session=session2,
                                         client='pilot-independent-reader',
                                         space=SPACE, project=PROJECT, rights='read', ttl_seconds=120)
            foreign_grant = admin_request(admin, key2, 'grant', session=session3,
                                          client='pilot-other-project',
                                          space=SPACE, project=FOREIGN_PROJECT,
                                          rights='read', ttl_seconds=120)
            reader = BrokerClient(data, reader_grant['token'])
            foreign = BrokerClient(data, foreign_grant['token'])
            try:
                recovered = reader.get(memory_id)
                if recovered is None or recovered['id'] != memory_id or recovered['body'] != body:
                    raise AssertionError('A new authorized client could not read the persisted record')
                result['new_authorized_client_read'] = True
                recalled = reader.recall(marker, limit=10)
                if not any(item['id'] == memory_id for item in recalled):
                    raise AssertionError('FTS5 could not retrieve the record after restart')
                result['fts_recall'] = True
                if foreign.get(memory_id) is not None or foreign.recall(marker):
                    raise AssertionError('The unrelated project saw the record')
                result['foreign_project_denied'] = True
                assert_forbidden(lambda: reader.remember('Unauthorized write from a read-only client'))
                result['read_only_write_denied'] = True
            finally:
                for session in (session2, session3):
                    if not admin_request(admin, key2, 'revoke', session=session)['revoked']:
                        raise AssertionError('A reader session could not be revoked')
            assert_forbidden(lambda: reader.profile())
            assert_forbidden(lambda: foreign.profile())
    # Preserve the synthetic marker for future independently authorized clients.
    # Do not store admin credentials or session tokens in the evidence file.
    if evidence:
        directory = base / 'validaciones'
        protected_directory(directory)
        when = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        evidence_path = directory / ('persistencia-' + when + '-' + unique + '.json')
        fd = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        result['evidence'] = str(evidence_path)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Prueba real de persistencia local sin IA ni proyectos reales')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB,
                        help='SQLite persistente (por defecto, ~/.local/state/synapsys-memory/memory.sqlite3)')
    parser.add_argument('--no-evidence', action='store_true', help='No guardar resumen de evidencia')
    args = parser.parse_args(argv)
    try:
        summary = run(args.db, evidence=not args.no_evidence)
    except (OSError, ValueError, RuntimeError, PermissionError, AssertionError) as exc:
        print('NO VALIDADO: ' + type(exc).__name__ + ': ' + str(exc))
        return 2
    print('PASS: SQLite persistente tras reiniciar broker; otro cliente lee; otro proyecto y escritura no autorizada bloqueados')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
