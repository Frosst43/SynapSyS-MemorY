#!/usr/bin/env python3
"""One private OpenCode service, bounded readiness polling, no inference.

The host reuses the established Bubblewrap mounts and read-only broker grant.
The child keeps one server alive while inspecting its configuration and MCP.
No changes to OpenCode installation, user settings, memory engine or A->B test.
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
import time
from urllib.parse import urlsplit

BINARY = '/opt/symem-pilot/opencode'
CONFIG = '/etc/opencode/opencode.json'
SERVER = 'synapsys-memory'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
READINESS_SECONDS = 15.0
MAX_SAMPLES = 16


def local_endpoint(value: str) -> str:
    """Accept only the private service's loopback HTTP URL without credentials."""
    if not isinstance(value, str):
        raise ValueError('Invalid private service URL')
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1')
            or url.username is not None or url.password is not None
            or url.path not in ('', '/') or url.query or url.fragment
            or url.port is None or not 1 <= url.port <= 65535):
        raise ValueError('Private service must listen only on isolated loopback')
    return value


def read_command(endpoint: str, kind: str) -> list[str]:
    operations = {'config': ['debug', 'config'], 'mcp': ['mcp', 'list']}
    if kind not in operations:
        raise ValueError('Only configuration and MCP listing are allowed')
    return [BINARY, '--server', local_endpoint(endpoint), *operations[kind]]


def mcp_state(code: int, text: str) -> str:
    if code != 0:
        return 'COMMAND_ERROR'
    text = ANSI.sub('', text)
    for line in text.splitlines():
        if re.fullmatch(r'\s*(?:\u2713\s*)?synapsys-memory\s+connected\s*', line):
            return 'CONNECTED'
        if re.match(r'^\s*(?:[^\w\s]\s*)?synapsys-memory\s+failed\b', line):
            return 'FAILED'
        if re.fullmatch(r'\s*(?:[^\w\s]\s*)?synapsys-memory\s+disabled\s*', line):
            return 'DISABLED'
    if 'No MCP servers configured' in text:
        return 'EMPTY'
    return 'PENDING_OR_UNRECOGNIZED'


def poll_mcp(endpoint, execute, alive, *, clock=time.monotonic, pause=time.sleep):
    """Poll read-only status in ONE service; do not restart or repeat writes."""
    start = clock()
    samples, last = [], {}
    for _ in range(MAX_SAMPLES):
        left = READINESS_SECONDS - (clock() - start)
        if left <= 0 or not alive():
            break
        last = execute(read_command(endpoint, 'mcp'), 'mcp', min(4.0, left))
        state = mcp_state(last['returncode'], last['stdout'])
        samples.append({'elapsed_s': round(clock() - start, 3), 'state': state,
                        'exit_code': last['returncode']})
        if state in ('CONNECTED', 'FAILED', 'DISABLED', 'COMMAND_ERROR'):
            break
        left = READINESS_SECONDS - (clock() - start)
        if left > 0:
            pause(min(0.5, left))
    return last, samples


def connected_result(report: dict, profile_count: int, closed: bool, revoked: bool) -> bool:
    return bool(report.get('config_exact') and report.get('runtime', {}).get('returncode') == 0
                and report.get('config_recognized') and report.get('service_starts') == 1
                and mcp_state(report.get('mcp', {}).get('returncode', -1),
                              report.get('mcp', {}).get('stdout', '')) == 'CONNECTED'
                and profile_count > 0 and closed and revoked)


def config_names(value):
    if isinstance(value, dict):
        value = value.get('data', value.get('entries', []))
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if not isinstance(item, dict):
            continue
        info = item.get('info', {})
        mcp = info.get('mcp', {}) if isinstance(info, dict) else {}
        servers = mcp.get('servers', {}) if isinstance(mcp, dict) else {}
        if isinstance(servers, dict):
            found.extend(servers.keys())
    return sorted(set(found))


def child_main(expected_hash: str) -> int:
    """Runs only inside the existing supervised network/PID namespace."""
    report = {'service_starts': 0, 'samples': [], 'config_recognized': False}
    target = Path('/workspace/readiness.json')
    service = None
    password = secrets.token_urlsafe(36)
    environment = dict(os.environ, OPENCODE_PASSWORD=password,
                       OPENCODE_DISABLE_MODELS_FETCH='1', OPENCODE_PRINT_LOGS='1')

    def execute(argv, name, timeout):
        out, err = Path('/workspace/'+name+'.out'), Path('/workspace/'+name+'.err')
        with out.open('w') as stdout, err.open('w') as stderr:
            try:
                code = subprocess.run(argv, env=environment, stdin=subprocess.DEVNULL,
                                      stdout=stdout, stderr=stderr, timeout=timeout,
                                      close_fds=True, check=False).returncode
            except subprocess.TimeoutExpired:
                code = 124
        return {'returncode': code, 'stdout': out.read_text(errors='replace')[:65536],
                'stderr': err.read_text(errors='replace')[:65536]}

    try:
        report['config_exact'] = hashlib.sha256(Path(CONFIG).read_bytes()).hexdigest() == expected_hash
        report['runtime'] = execute([sys.executable, '-I', '-c',
            'import mcp, synapsys_memory; print("MCP_RUNTIME_IMPORT_OK")'], 'runtime', 5)
        if not report['config_exact'] or report['runtime']['returncode']:
            raise RuntimeError('Configuration mount or Python runtime check failed')
        out, err = Path('/workspace/server.out'), Path('/workspace/server.err')
        with out.open('w') as stdout, err.open('w') as stderr:
            service = subprocess.Popen([BINARY, '--print-logs', 'serve', '--stdio', '--port', '0'],
                                       env=environment, stdin=subprocess.PIPE, stdout=stdout,
                                       stderr=stderr, close_fds=True)
        report['service_starts'] = 1
        endpoint = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and service.poll() is None:
            for line in out.read_text(errors='replace')[:65536].splitlines():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and 'url' in message:
                    endpoint = local_endpoint(message['url'])
                    break
            if endpoint:
                break
            time.sleep(0.05)
        if endpoint is None:
            raise RuntimeError('Private service did not report readiness')
        report['config'] = execute(read_command(endpoint, 'config'), 'config', 8)
        if report['config']['returncode']:
            raise RuntimeError('Could not inspect private service configuration')
        report['config_servers'] = config_names(json.loads(report['config']['stdout']))
        report['config_recognized'] = SERVER in report['config_servers']
        if not report['config_recognized']:
            raise RuntimeError('Memory server is absent from parsed configuration')
        report['mcp'], report['samples'] = poll_mcp(endpoint, execute, lambda: service.poll() is None)
    except (OSError, ValueError, RuntimeError) as exc:
        report['error'] = str(exc)
    finally:
        if service is not None:
            # EOF ends the service lease. The outer controller independently kills
            # and confirms closure of the entire namespace, including descendants.
            try:
                service.stdin.close()
                try:
                    service.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    service.kill()
                    service.wait(timeout=3)
                report['service_reaped'] = True
            except (OSError, subprocess.TimeoutExpired):
                report['service_reaped'] = False
            report['server_stderr'] = Path('/workspace/server.err').read_text(errors='replace')[-32768:]
        # Never retain the ephemeral service password in diagnostics.
        safe = json.dumps(report).replace(password, '[REDACTED]')
        target.write_text(safe, encoding='utf-8')
    return 0


def run(binary: Path) -> int:
    from probe_opencode_mcp import configuration, native_binary, private_write, runtime_arguments
    from diagnose_opencode_config import redact
    from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request
    from synapsys_memory.managed_session import ExclusiveSessionController, ManagedSandbox, SessionGrant
    from synapsys_memory.sandbox import load_policy, build_command, _check_memory_session, _trusted_bwrap

    evidence = Path(tempfile.mkdtemp(prefix='symem-opencode-ready-'))
    summary = {'probe': 'same-service-readiness-1', 'result': 'NOT_RUN', 'lifecycle': 'NOT_RUN',
               'model_inference': False, 'host_network_enabled': False,
               'real_projects_modified': False, 'host_configuration_modified': False,
               'stable_release_ready': False}
    key = token = ''
    try:
        repo = Path(__file__).resolve().parents[1]
        venv, source = repo/'.venv', repo/'src'
        if Path(sys.prefix).resolve() != venv.resolve() or venv.is_symlink() or source.is_symlink():
            raise ValueError('Run with the existing repository .venv/bin/python')
        if not Path(getattr(sys, '_base_executable', sys.executable)).resolve().is_relative_to('/usr'):
            raise ValueError('Base Python needs reviewed runtime mounts')
        bwrap, binary = _trusted_bwrap(), native_binary(binary)
        python = str(venv/'bin/python')
        class ObservedBroker(BrokerDaemon):
            profiles = 0
            def _client(self, message, *, peer_uid):
                value = super()._client(message, peer_uid=peer_uid)
                if message.get('action') == 'profile':
                    with self._lock:
                        self.profiles += 1
                return value
        with tempfile.TemporaryDirectory(prefix='symem-ready-fixture-') as temporary:
            root = Path(temporary)
            workspace = root/'project'; workspace.mkdir()
            copied, config, token_file, policy = (root/n for n in ('opencode','opencode.json','session.token','policy.json'))
            shutil.copyfile(binary, copied); copied.chmod(0o500)
            with copied.open('rb') as stream:
                summary['opencode_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            config_text = json.dumps(configuration(python), indent=2)
            private_write(config, config_text)
            expected = hashlib.sha256(config_text.encode()).hexdigest()
            data, admin, db = root/'data.sock', root/'admin.sock', root/'memory.sqlite3'
            key, session = secrets.token_urlsafe(48), 'ready-'+secrets.token_hex(8)
            with ObservedBroker(db, data, admin, admin_key=key) as broker:
                controller = ExclusiveSessionController()
                def issue(project):
                    nonlocal token
                    grant = admin_request(admin,key,'grant',session=session,client='readiness-probe',
                                          space='temporary',project=project,rights='read')
                    token = grant['token']
                    try:
                        private_write(token_file, token)
                    except BaseException:
                        admin_request(admin,key,'revoke',session=session)
                        raise
                    return SessionGrant(project, lambda: admin_request(admin,key,'revoke',session=session))
                def launch(grant):
                    private_write(policy,json.dumps({'schema_version':1,'projects':{grant.project:{
                        'workspace':str(workspace),'read_only':False,'memory':{
                            'data_socket':str(data),'token_file':str(token_file),'space':'temporary'}}}}))
                    spec = load_policy(policy,grant.project)
                    _check_memory_session(spec)
                    argv = build_command(spec,[python,'-I','-c',Path(__file__).read_text(),'--child',expected],bwrap=bwrap)
                    argv = runtime_arguments(argv,config=config,binary=copied,venv=venv,source=source)
                    summary['profiles_before'] = broker.profiles
                    return ManagedSandbox.start(argv,output=log)
                try:
                    with (evidence/'sandbox.log').open('w') as log:
                        proc = controller.activate('readiness-project',approve=lambda old,new: old is None and new=='readiness-project',
                                                   issue=issue,launch=launch)
                        summary['sandbox_exit_code'] = proc.wait(timeout=60)
                        controller.close()
                    summary['sandbox_closed'] = proc.stopped
                    summary['client_profile_requests'] = broker.profiles - summary['profiles_before']
                    try:
                        BrokerClient(data,token).profile()
                    except PermissionError:
                        summary['token_revoked'] = True
                    else:
                        raise RuntimeError('Fixture credential was not revoked')
                    report = json.loads((workspace/'readiness.json').read_text())
                    for field in ('config_exact','config_servers','config_recognized','service_starts','service_reaped','samples'):
                        summary[field] = report.get(field)
                    passed = (summary['sandbox_exit_code'] == 0 and
                              connected_result(report,summary['client_profile_requests'],proc.stopped,True))
                    summary['result'] = 'CONNECTED_IN_SAME_SERVICE' if passed else 'NOT_CONFIRMED'
                    private_write(evidence/'report.json',redact(json.dumps(report,indent=2),(key,token))+'\n')
                    for stage in ('runtime','mcp'):
                        result = report.get(stage,{})
                        print('\n=== '+stage.upper()+' ===')
                        print(redact(result.get('stdout',''),(key,token))[:4000])
                        print(redact(result.get('stderr',''),(key,token))[:4000])
                    if not passed:
                        print('\n=== SERVER DIAGNOSTICS ===')
                        print(redact(report.get('error',''),(key,token)))
                        print(redact(report.get('server_stderr',''),(key,token))[-10000:])
                finally:
                    controller.close()
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        summary['result'] = 'PROBE_INCOMPLETE'
        private_write(evidence/'failure.txt',redact(str(exc),(key,token))+'\n')
        print('NO VALIDADO: '+redact(str(exc),(key,token)))
    finally:
        private_write(evidence/'summary.json',json.dumps(summary,indent=2)+'\n')
        print('\n=== RESUMEN ===\n'+json.dumps(summary,indent=2))
        print('EVIDENCE: '+str(evidence/'summary.json'))
        print('Un servicio temporal; sin modelos, sin red del host y sin repetir A->B.')
    return 0 if summary['result']=='CONNECTED_IN_SAME_SERVICE' else 2


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        raise SystemExit(child_main(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opencode',type=Path,default=Path.home()/'.opencode/bin/opencode')
    raise SystemExit(run(parser.parse_args().opencode))
