#!/usr/bin/env python3
"""Connection-only OpenCode V2 probe. No model or repeated lifecycle test.

Writes only fresh temporary fixtures. Full configuration is mounted read-only
at the V2 managed location INSIDE Bubblewrap, never at the host's /etc.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

from synapsys_memory.opencode_runtime import (
    MANAGED_CONFIG, PILOT_BINARY, SERVER_NAME, configuration, native_binary, private_write,
    repository_runtime, runtime_arguments,
)

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def verdict(returncode: int, text: str, profiles: int) -> str:
    """Exit 0 or a label alone cannot prove a real MCP connection."""
    lines = ANSI.sub('', text).splitlines()
    connected = any(re.fullmatch(r'\s*(?:\u2713\s*)?synapsys-memory\s+connected\s*', line)
                    for line in lines)
    if returncode == 0 and connected and profiles > 0:
        return 'PASS'
    if any('No MCP servers configured' in line for line in lines):
        return 'CONFIG_NOT_LOADED'
    if returncode != 0:
        return 'CLIENT_EXIT_ERROR'
    if connected and profiles <= 0:
        return 'NO_BROKER_CONTACT'
    return 'CONNECTION_UNCONFIRMED'


def probe(binary: Path) -> int:
    evidence = Path(tempfile.mkdtemp(prefix='symem-opencode-only-'))
    summary = {
        'probe': 'opencode-v2-managed-config-v1', 'opencode_mcp': 'NOT_RUN',
        'lifecycle': 'NOT_RUN', 'model_inference': False,
        'real_projects_modified': False, 'host_configuration_modified': False,
        'network_enabled': False, 'stable_release_ready': False,
        'configuration_location_in_sandbox': MANAGED_CONFIG,
    }
    try:
        # Import production components only when actually running the probe.
        from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request
        from synapsys_memory.managed_session import ExclusiveSessionController, ManagedSandbox, SessionGrant
        from synapsys_memory.sandbox import load_policy, build_command, _check_memory_session, _trusted_bwrap

        bwrap = _trusted_bwrap()
        binary = native_binary(binary)
        venv, source = repository_runtime()

        class ProbeBroker(BrokerDaemon):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.profiles = 0
            def _client(self, message, *, peer_uid):
                result = super()._client(message, peer_uid=peer_uid)
                if message.get('action') == 'profile':
                    with self._lock:
                        self.profiles += 1
                return result

        with tempfile.TemporaryDirectory(prefix='symem-opencode-fixture-') as temporary:
            root = Path(temporary)
            workspace = root / 'project'; workspace.mkdir()
            data, admin, db = root / 'data.sock', root / 'admin.sock', root / 'memory.sqlite3'
            config, token_file, policy = root / 'opencode.json', root / 'session.token', root / 'policy.json'
            copied = root / 'opencode'
            shutil.copyfile(binary, copied); copied.chmod(0o500)
            with copied.open('rb') as stream:
                summary['opencode_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            private_write(config, json.dumps(configuration(str(venv / 'bin/python')), indent=2))
            key = secrets.token_urlsafe(48)
            project, space, session = 'connection-probe', 'temporary-probe', 'probe-' + secrets.token_hex(8)
            controller = ExclusiveSessionController()
            with ProbeBroker(db, data, admin, admin_key=key) as broker:
                issued = {}
                def issue(selected):
                    grant = admin_request(admin, key, 'grant', session=session,
                                          client='opencode-probe', space=space, project=selected, rights='read')
                    issued.update(grant)
                    try:
                        private_write(token_file, grant['token'])
                    except BaseException:
                        admin_request(admin, key, 'revoke', session=session)
                        raise
                    return SessionGrant(selected,
                        lambda: admin_request(admin, key, 'revoke', session=session))
                def launch(grant):
                    private_write(policy, json.dumps({'schema_version': 1, 'projects': {
                        project: {'workspace': str(workspace), 'read_only': False, 'memory': {
                            'data_socket': str(data), 'token_file': str(token_file), 'space': space,
                        }},
                    }}))
                    spec = load_policy(policy, grant.project)
                    _check_memory_session(spec)
                    argv = build_command(spec, [PILOT_BINARY, 'mcp', 'list'], bwrap=bwrap)
                    argv = runtime_arguments(argv, config=config, binary=copied, venv=venv, source=source)
                    # The launcher's own profile check is not evidence from OpenCode.
                    summary['broker_profiles_before_client'] = broker.profiles
                    return ManagedSandbox.start(argv, output=log)
                try:
                    with (evidence / 'opencode.log').open('w', encoding='utf-8') as log:
                        process = controller.activate(project, approve=lambda old, new: old is None and new == project,
                                                      issue=issue, launch=launch)
                        code = process.wait(timeout=45)
                        controller.close()
                    summary['opencode_exit_code'] = code
                    summary['sandbox_closed'] = process.stopped
                    profiles = broker.profiles - summary['broker_profiles_before_client']
                    summary['broker_profiles_from_client'] = profiles
                    try:
                        BrokerClient(data, issued['token']).profile()
                    except PermissionError:
                        summary['token_revoked'] = True
                    else:
                        raise RuntimeError('Session credential remained valid after closure')
                    text = (evidence / 'opencode.log').read_text(encoding='utf-8', errors='replace')
                    status = verdict(code, text, profiles)
                    summary['opencode_mcp'] = status
                    if status == 'PASS':
                        print('PASS: OpenCode real conectado a MemorY por MCP dentro de Bubblewrap')
                        return 0
                    print('NO CONFIRMADO: ' + status + '; revisar opencode.log')
                    return 2
                finally:
                    controller.close()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        summary['error_type'] = type(exc).__name__
        (evidence / 'failure.txt').write_text(str(exc) + '\n', encoding='utf-8')
        print('NO VALIDADO: ' + type(exc).__name__ + '; revisar failure.txt')
        return 2
    finally:
        (evidence / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        print('EVIDENCE: ' + str(evidence / 'summary.json'))
        print('Sin modelos ni inferencia; cambio A->B no repetido; version estable pendiente')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Solo conexion OpenCode V2 + MCP, sin modelos ni prueba A->B')
    parser.add_argument('--opencode', type=Path, default=Path.home() / '.opencode/bin/opencode')
    args = parser.parse_args(argv)
    return probe(args.opencode)


if __name__ == '__main__':
    raise SystemExit(main())
