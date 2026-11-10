#!/usr/bin/env python3
"""Connection-only OpenCode V2 probe. No model or repeated lifecycle test.

Writes only fresh temporary fixtures. Full configuration is mounted read-only
at the V2 managed location INSIDE Bubblewrap, never at the host's /etc.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

SERVER_NAME = 'synapsys-memory'
MANAGED_CONFIG = '/etc/opencode/opencode.json'
PILOT_BINARY = '/opt/symem-pilot/opencode'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def configuration(python: str) -> dict:
    """V2 schema, local stdio transport, no provider or model settings."""
    if not Path(python).is_absolute():
        raise ValueError('Python runtime must be an absolute path')
    return {
        '$schema': 'https://opencode.ai/config.json',
        'update': 'disable', 'share': 'disabled',
        'mcp': {'servers': {SERVER_NAME: {
            'type': 'local', 'protocol': 'legacy',
            'command': [python, '-I', '-m', 'synapsys_memory.mcp_server'],
            'environment': {
                'SYMEM_BROKER_SOCKET': '/run/synapsys-memory/data.sock',
                'SYMEM_SESSION_TOKEN_FILE': '/run/synapsys-memory/session.token',
            },
        }}},
    }


def private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)


def runtime_arguments(base: list[str], *, config: Path, binary: Path,
                      venv: Path, source: Path) -> list[str]:
    """Mount only explicit runtime paths; do not expose the host HOME or /etc."""
    if '--' not in base or '--unshare-net' not in base[:base.index('--')]:
        raise ValueError('An isolated Bubblewrap command is required')
    for path in (config, binary, venv, source):
        if not path.is_absolute():
            raise ValueError('Runtime paths must be absolute')
    split = base.index('--')
    extra = [
        '--dir', '/opt/symem-pilot', '--dir', '/etc/opencode',
        '--ro-bind', str(config), MANAGED_CONFIG,
        '--ro-bind', str(binary), PILOT_BINARY,
        '--ro-bind', str(venv), str(venv),
        '--ro-bind', str(source), str(source),
        '--setenv', 'XDG_DATA_HOME', '/tmp/data',
        '--setenv', 'XDG_STATE_HOME', '/tmp/state',
        '--setenv', 'NO_COLOR', '1',
    ]
    # No OPENCODE_CONFIG: OpenCode V2 ignores this legacy environment setting.
    return [*base[:split], *extra, *base[split:]]


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


def native_binary(value: Path) -> Path:
    path = value.expanduser().resolve(strict=True)
    st = path.stat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid not in (0, os.geteuid())
            or st.st_mode & 0o022 or not os.access(path, os.X_OK)):
        raise ValueError('OpenCode executable is not trusted')
    with path.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            raise ValueError('A native Linux ELF OpenCode binary is required')
    return path


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
        root_src = Path(__file__).resolve().parents[1]
        venv, source = root_src / '.venv', root_src / 'src'
        if Path(sys.prefix).resolve() != venv.resolve() or venv.is_symlink() or source.is_symlink():
            raise ValueError('Run with the repository .venv/bin/python and non-symlinked runtime')
        if not Path(getattr(sys, '_base_executable', sys.executable)).resolve().is_relative_to('/usr'):
            raise ValueError('Base Python outside /usr needs a reviewed runtime mount')
        if importlib.util.find_spec('mcp') is None:
            raise ValueError('MCP extra is missing; this probe does not install anything')

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
