#!/usr/bin/env python3
"""Two temporary project lifecycles, then real OpenCode V2 MCP connection.

No inference, Ollama calls, network, sudo, installs or real repository writes.
The owner invokes this trusted test explicitly; it is not an agent-side tool.
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
import time

from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request
from synapsys_memory.managed_session import (
    ExclusiveSessionController, LifecycleError, ManagedSandbox, SessionGrant,
)
from synapsys_memory.sandbox import (
    SandboxError, load_policy, build_command, _check_memory_session, _trusted_bwrap,
)

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def connected(text: str) -> bool:
    for line in ANSI.sub('', text).splitlines():
        if 'synapsys-memory' in line and re.search(r'\bconnected\b', line, re.I):
            if not re.search(r'\b(disconnected|failed|error|not connected)\b', line, re.I):
                return True
    return False


def trusted_binary(value: Path) -> Path:
    path = value.expanduser().resolve(strict=True)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022 or not os.access(path, os.X_OK)):
        raise ValueError('OpenCode must be an owner/root executable, not group/world writable')
    with path.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            raise ValueError('This pilot requires the native Linux OpenCode ELF binary')
    return path


def opencode_config(python: str) -> dict:
    return {'$schema': 'https://opencode.ai/config.json', 'share': 'disabled',
            'mcp': {'servers': {'synapsys-memory': {
                'type': 'local', 'command': [python, '-I', '-m', 'synapsys_memory.mcp_server'],
                'environment': {
                    'SYMEM_BROKER_SOCKET': '/run/synapsys-memory/data.sock',
                    'SYMEM_SESSION_TOKEN_FILE': '/run/synapsys-memory/session.token',
                },
            }}}}


# A detached, SIGTERM-ignoring child must not survive a switch.
WORKER = r'''
import os, signal, time
from pathlib import Path
if os.fork() == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        with Path('/workspace/heartbeat').open('ab', buffering=0) as stream:
            stream.write(b'x')
        time.sleep(0.02)
else:
    Path('/workspace/ready').write_text('ready')
    while True:
        time.sleep(1)
'''


class ProbeBroker(BrokerDaemon):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.profiles = 0
    def _client(self, message, *, peer_uid):
        result = super()._client(message, peer_uid=peer_uid)
        if message.get('action') == 'profile':
            with self._lock:
                self.profiles += 1
        return result


def pilot(binary: Path, *, lifecycle_only: bool = False) -> int:
    root_src = Path(__file__).resolve().parents[1]
    evidence = Path(tempfile.mkdtemp(prefix='symem-managed-evidence-'))
    summary = {'base_commit': '033a0414ae97e60fdab61a0f99405a284d3737a5',
               'lifecycle': 'NOT_RUN', 'opencode_mcp': 'NOT_RUN',
               'model_inference': False, 'real_projects_modified': False,
               'stable_release_ready': False}
    try:
        _trusted_bwrap()
        if not lifecycle_only:
            binary = trusted_binary(binary)
            if importlib.util.find_spec('mcp') is None:
                raise ValueError('MCP is missing in the current virtual environment')
            # We expose this specific, trusted runtime in read-only mode, not HOME.
            venv = root_src/'.venv'
            if Path(sys.prefix).resolve() != venv.resolve():
                raise ValueError('Run with the repository .venv/bin/python')
            python = str(venv/'bin/python')
            base_python = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
            if not base_python.is_relative_to('/usr'):
                raise ValueError('Python base outside /usr needs a separately reviewed runtime mount')
            source = root_src/'src'
            if source.is_symlink() or venv.is_symlink():
                raise ValueError('Symlinked source/runtime paths are not accepted in this pilot')
        with tempfile.TemporaryDirectory(prefix='symem-managed-projects-') as temporary:
            root = Path(temporary)
            a, b = root/'project-a', root/'project-b'
            a.mkdir(); b.mkdir()
            sentinel = b/'foreign.txt'; sentinel.write_text('UNCHANGED')
            data, admin, db = root/'data.sock', root/'admin.sock', root/'memory.sqlite3'
            key = secrets.token_urlsafe(48)
            controller = ExclusiveSessionController()
            with ProbeBroker(db, data, admin, admin_key=key) as broker:
                issued = []
                def issue(project):
                    session = 'pilot-'+secrets.token_hex(8)
                    grant = admin_request(admin, key, 'grant', session=session, client='opencode-pilot',
                                          space='temporary-pilot', project=project, rights='read,write')
                    token_file = root/(session+'.token')
                    fd = os.open(token_file, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'w', encoding='ascii') as f: f.write(grant['token'])
                    def revoke():
                        # Success includes an already-revoked session, but not a communication error.
                        admin_request(admin, key, 'revoke', session=session)
                    g = SessionGrant(project, revoke, (grant['token'], token_file))
                    issued.append(g)
                    return g
                def arguments(grant, program):
                    token, token_file = grant.private
                    policy = root/('policy-'+secrets.token_hex(4)+'.json')
                    fd = os.open(policy, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                    config = {'schema_version':1, 'projects':{
                        'project-a': {'workspace':str(a),'read_only':False},
                        'project-b': {'workspace':str(b),'read_only':False},
                    }}
                    config['projects'][grant.project]['memory'] = {
                        'data_socket':str(data), 'token_file':str(token_file), 'space':'temporary-pilot'}
                    with os.fdopen(fd, 'w') as f: json.dump(config, f)
                    spec = load_policy(policy, grant.project)
                    _check_memory_session(spec)
                    return build_command(spec, program, bwrap=_trusted_bwrap())
                # All approvals below concern freshly created temporary fixtures only.
                # There is no grant or policy for the owner's real projects.
                approve = lambda old, new: True
                try:
                    with (evidence/'lifecycle.log').open('w') as log:
                        old = controller.activate('project-a', approve=approve, issue=issue,
                            launch=lambda grant: ManagedSandbox.start(arguments(grant, ['python3','-c',WORKER]), output=log))
                        deadline = time.monotonic()+10
                        while not (a/'heartbeat').exists():
                            if time.monotonic() >= deadline or old.process.poll() is not None:
                                raise LifecycleError('First sandbox did not start its detached worker')
                            time.sleep(0.02)
                        old_client = BrokerClient(data, issued[-1].private[0])
                        old_client.remember('Only project A can retrieve this marker')
                        child = 'from pathlib import Path; Path("/workspace/ok").write_text("B")'
                        new = controller.activate('project-b', approve=approve, issue=issue,
                            launch=lambda grant: ManagedSandbox.start(arguments(grant, ['python3','-c',child]), output=log))
                        if new.wait() != 0 or (b/'ok').read_text() != 'B':
                            raise LifecycleError('Second sandbox did not finish normally')
                        size = (a/'heartbeat').stat().st_size
                        time.sleep(0.15)
                        if (a/'heartbeat').stat().st_size != size or not old.stopped:
                            raise LifecycleError('Old detached worker survived project switch')
                        try: old_client.profile()
                        except PermissionError: pass
                        else: raise LifecycleError('Old token remains authorized')
                        if BrokerClient(data, issued[-1].private[0]).recall('marker'):
                            raise LifecycleError('Project memory leaked during switch')
                        controller.close()
                        if sentinel.read_text() != 'UNCHANGED':
                            raise LifecycleError('Foreign fixture changed')
                    summary['lifecycle'] = 'PASS'
                    print('PASS: cambio A->B; permiso anterior revocado y descendiente detenido')
                    if not lifecycle_only:
                        # Copy only the native binary. Never expose its containing HOME directory.
                        copied = root/'opencode'; shutil.copyfile(binary, copied); copied.chmod(0o500)
                        summary['opencode_sha256'] = hashlib.sha256(copied.read_bytes()).hexdigest()
                        (a/'opencode.json').write_text(json.dumps(opencode_config(python)))
                        managed_config = root/'opencode-managed.json'
                        managed_config.write_text(json.dumps({'update':'disable', 'share':'disabled'}))
                        managed_config.chmod(0o600)
                        before = {'profiles': None}
                        def launch_opencode(grant):
                            cmd = arguments(grant, ['/opt/symem-pilot/opencode','mcp','list'])
                            split = cmd.index('--')
                            extra = ['--dir','/opt/symem-pilot',
                                     '--dir','/etc/opencode',
                                     '--ro-bind',str(managed_config),'/etc/opencode/opencode.json',
                                     '--ro-bind',str(copied),'/opt/symem-pilot/opencode',
                                     '--ro-bind',str(venv),str(venv),
                                     '--ro-bind',str(source),str(source),
                                     '--setenv','XDG_DATA_HOME','/tmp/data',
                                     '--setenv','XDG_STATE_HOME','/tmp/state',
                                     '--setenv','OPENCODE_CONFIG','/workspace/opencode.json',
                                     '--setenv','NO_COLOR','1']
                            cmd[split:split] = extra
                            before['profiles'] = broker.profiles
                            return ManagedSandbox.start(cmd, output=log)
                        with (evidence/'opencode.log').open('w') as log:
                            process = controller.activate('project-a', approve=approve, issue=issue, launch=launch_opencode)
                            code = process.wait(timeout=45)
                            controller.close()
                        text = (evidence/'opencode.log').read_text(errors='replace')
                        # Avoid false PASS from exit status alone or from a disconnected label.
                        if code != 0 or not connected(text) or broker.profiles <= before['profiles']:
                            summary['opencode_mcp'] = 'UNCONFIRMED'
                            print('NO CONFIRMADO: revisar opencode.log; no se ha abierto la red ni relajado el sandbox')
                            return 2
                        summary['opencode_mcp'] = 'PASS'
                        print('PASS: OpenCode real conectado a MemorY por MCP dentro de Bubblewrap')
                finally:
                    controller.close()
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        summary['error_type'] = type(exc).__name__
        # Exception text can contain arbitrary child input; only controlled diagnostics here.
        print('NO VALIDADO: '+type(exc).__name__+'; revisar evidencias privadas')
        (evidence/'failure.txt').write_text(str(exc)+'\n')
        return 2
    finally:
        (evidence/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print('EVIDENCE: '+str(evidence/'summary.json'))
        print('Sin inferencia, sin proyectos reales; version estable aun no declarada')


def main():
    parser = argparse.ArgumentParser(description='Piloto temporal OpenCode V2 + cierre coordinado')
    parser.add_argument('--opencode', type=Path, default=Path.home()/'.opencode/bin/opencode')
    parser.add_argument('--lifecycle-only', action='store_true', help='Solo ensayo de dos proyectos sin cliente de IA')
    args = parser.parse_args()
    return pilot(args.opencode, lifecycle_only=args.lifecycle_only)


if __name__ == '__main__':
    raise SystemExit(main())
