#!/usr/bin/env python3
"""Inspect OpenCode config inside the existing sandbox; never load a model.

No retries or alternate config guesses. Keeps the previous read-only managed
config, broker grant and network namespace. Uses a fresh private OpenCode
server (--standalone) so --print-logs includes server-side diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SERVER = 'synapsys-memory'
CONFIG = '/etc/opencode/opencode.json'
BINARY = '/opt/symem-pilot/opencode'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def commands() -> list[tuple[str, list[str]]]:
    return [
        ('version', [BINARY, '--version']),
        ('config', [BINARY, '--standalone', '--print-logs', 'debug', 'config']),
        ('mcp', [BINARY, '--standalone', '--print-logs', 'mcp', 'list']),
    ]


def config_entries(value) -> list[dict]:
    """Report only paths and MCP names, never env, provider config or secrets."""
    if isinstance(value, dict):
        value = value.get('data', value.get('entries', []))
    if not isinstance(value, list):
        return []
    result = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        info = entry.get('info', {})
        mcp = info.get('mcp', {}) if isinstance(info, dict) else {}
        servers = mcp.get('servers', {}) if isinstance(mcp, dict) else {}
        result.append({'type': entry.get('type'), 'path': entry.get('path'),
                       'mcp_servers': sorted(servers) if isinstance(servers, dict) else []})
    return result


def classify(report: dict, profiles: int) -> str:
    if report.get('config_sha256') != report.get('expected_config_sha256'):
        return 'CONFIG_MOUNT_MISMATCH'
    if report.get('runtime', {}).get('returncode') != 0:
        return 'MCP_RUNTIME_IMPORT_FAILED'
    stages = report.get('stages', {})
    config = stages.get('config', {})
    if config.get('returncode') != 0:
        return 'CONFIG_COMMAND_FAILED'
    if not config.get('json_valid'):
        return 'CONFIG_OUTPUT_NOT_JSON'
    if not any(SERVER in e['mcp_servers'] for e in config_entries(config.get('json'))):
        return 'SERVER_ABSENT_FROM_LOADED_CONFIG'
    mcp = stages.get('mcp', {})
    text = ANSI.sub('', mcp.get('stdout', ''))
    connected = any(re.fullmatch(r'\s*(?:\u2713\s*)?synapsys-memory\s+connected\s*', line)
                    for line in text.splitlines())
    if mcp.get('returncode') == 0 and connected and profiles > 0:
        return 'CONNECTED_IN_STANDALONE_DIAGNOSTIC'
    if 'No MCP servers configured' in text:
        return 'CONFIG_PRESENT_BUT_MCP_LIST_EMPTY'
    if connected and profiles <= 0:
        return 'CONNECTED_LABEL_WITHOUT_BROKER_CONTACT'
    return 'MCP_STARTUP_UNCONFIRMED'


def redact(text: str, secrets_to_hide=()) -> str:
    text = ANSI.sub('', text)
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)([^\r\n]+)', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:password|client_secret|api_key|token)\s*[\"\']?\s*[:=]\s*[\"\']?)([^\s\"\',}]+)',
                  r'\1[REDACTED]', text)
    return re.sub(r'(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{40,}', '[LONG_VALUE_REDACTED]', text)


# Executed with the mounted venv Python inside Bubblewrap, not on the host.
CHILD = r'''
import hashlib, json, os, subprocess, sys
from pathlib import Path
config = Path('/etc/opencode/opencode.json')
report = {'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
          'cwd': os.getcwd(), 'stages': {}}
Path('/workspace/diagnostic.json').write_text(json.dumps(report))

def execute(argv, name, timeout):
    out = Path('/workspace/' + name + '.stdout')
    err = Path('/workspace/' + name + '.stderr')
    with out.open('w') as stdout, err.open('w') as stderr:
        try:
            code = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stdout,
                                  stderr=stderr, timeout=timeout, check=False,
                                  close_fds=True).returncode
        except subprocess.TimeoutExpired:
            code = 124
    # Bound what we put into the report. Original logs remain temporary/private.
    return {'returncode': code, 'stdout': out.read_text(errors='replace')[:65536],
            'stderr': err.read_text(errors='replace')[:65536]}

report['runtime'] = execute([sys.executable, '-I', '-c',
    'import mcp; import synapsys_memory; print("MCP_RUNTIME_IMPORT_OK")'], 'runtime', 5)
for name, argv in json.loads(sys.argv[1]):
    result = execute(argv, name, 12)
    if name == 'config':
        try:
            result['json'] = json.loads(result['stdout'])
            result['json_valid'] = True
        except ValueError:
            result['json_valid'] = False
    report['stages'][name] = result
    Path('/workspace/diagnostic.json').write_text(json.dumps(report))
    if result['returncode'] == 124:
        break
print('CONFIG_DIAGNOSTIC_CAPTURED')
'''


def run(binary: Path) -> int:
    # Reuse the unchanged fixture configuration and runtime mount allowlist.
    from probe_opencode_mcp import configuration, native_binary, private_write, runtime_arguments
    from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request
    from synapsys_memory.managed_session import ExclusiveSessionController, ManagedSandbox, SessionGrant
    from synapsys_memory.sandbox import load_policy, build_command, _check_memory_session, _trusted_bwrap
    import secrets

    evidence = Path(tempfile.mkdtemp(prefix='symem-config-diagnostic-'))
    summary = {'probe': 'config-diagnostic-1', 'lifecycle': 'NOT_RUN',
               'model_inference': False, 'network_enabled': False,
               'real_projects_modified': False, 'host_configuration_modified': False,
               'stable_release_ready': False}
    key = token = ''
    try:
        root_src = Path(__file__).resolve().parents[1]
        venv, source = root_src/'.venv', root_src/'src'
        if Path(sys.prefix).resolve() != venv.resolve() or venv.is_symlink() or source.is_symlink():
            raise ValueError('Use repository .venv/bin/python with non-symlinked runtime')
        if not Path(getattr(sys, '_base_executable', sys.executable)).resolve().is_relative_to('/usr'):
            raise ValueError('Base Python outside /usr needs reviewed mounts')
        bwrap, binary = _trusted_bwrap(), native_binary(binary)
        python = str(venv/'bin/python')
        class ObservedBroker(BrokerDaemon):
            profiles = 0
            def _client(self, message, *, peer_uid):
                result = super()._client(message, peer_uid=peer_uid)
                if message.get('action') == 'profile':
                    with self._lock: self.profiles += 1
                return result
        with tempfile.TemporaryDirectory(prefix='symem-diag-fixture-') as temporary:
            root = Path(temporary)
            workspace = root/'project'; workspace.mkdir()
            copied, config, token_file, policy = (root/n for n in ('opencode','opencode.json','session.token','policy.json'))
            shutil.copyfile(binary, copied); copied.chmod(0o500)
            with copied.open('rb') as stream:
                summary['opencode_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            config_text = json.dumps(configuration(python), indent=2)
            private_write(config, config_text)
            expected_hash = hashlib.sha256(config_text.encode()).hexdigest()
            data, admin, db = root/'data.sock', root/'admin.sock', root/'memory.sqlite3'
            key = secrets.token_urlsafe(48)
            session = 'diagnostic-'+secrets.token_hex(8)
            with ObservedBroker(db, data, admin, admin_key=key) as broker:
                controller = ExclusiveSessionController()
                def issue(project):
                    nonlocal token
                    grant = admin_request(admin, key, 'grant', session=session, client='opencode-diagnostic',
                                          space='temporary', project=project, rights='read')
                    token = grant['token']
                    try: private_write(token_file, token)
                    except BaseException:
                        admin_request(admin, key, 'revoke', session=session)
                        raise
                    return SessionGrant(project, lambda: admin_request(admin,key,'revoke',session=session))
                def launch(grant):
                    private_write(policy, json.dumps({'schema_version':1,'projects':{grant.project:{
                        'workspace':str(workspace),'read_only':False,'memory':{
                            'data_socket':str(data),'token_file':str(token_file),'space':'temporary'}}}}))
                    spec = load_policy(policy, grant.project)
                    _check_memory_session(spec)
                    argv = build_command(spec,[python,'-I','-c',CHILD,json.dumps(commands())],bwrap=bwrap)
                    argv = runtime_arguments(argv,config=config,binary=copied,venv=venv,source=source)
                    split = argv.index('--')
                    argv[split:split] = ['--setenv','OPENCODE_DISABLE_MODELS_FETCH','1']
                    summary['profiles_before'] = broker.profiles
                    return ManagedSandbox.start(argv,output=log)
                try:
                    with (evidence/'sandbox.log').open('w') as log:
                        process = controller.activate('diagnostic-project',approve=lambda old,new: old is None and new=='diagnostic-project',
                                                      issue=issue,launch=launch)
                        summary['sandbox_exit_code'] = process.wait(timeout=45)
                        controller.close()
                    summary['sandbox_closed'] = process.stopped
                    summary['client_profile_requests'] = broker.profiles - summary['profiles_before']
                    try: BrokerClient(data,token).profile()
                    except PermissionError: summary['token_revoked'] = True
                    else: raise RuntimeError('Credential not revoked')
                    report = json.loads((workspace/'diagnostic.json').read_text())
                    report['expected_config_sha256'] = expected_hash
                    summary['config_readable_and_exact'] = report.get('config_sha256') == expected_hash
                    summary['runtime'] = report.get('runtime',{}).get('returncode')
                    summary['config_sources'] = config_entries(report.get('stages',{}).get('config',{}).get('json'))
                    summary['result'] = (classify(report,summary['client_profile_requests'])
                                         if summary['sandbox_exit_code'] == 0 else 'DIAGNOSTIC_CHILD_FAILED')
                    # All persistent diagnostics are sanitized before writing or printing.
                    for name, result in [('runtime',report.get('runtime',{})),*report.get('stages',{}).items()]:
                        stdout = redact(result.get('stdout',''),(key,token))
                        stderr = redact(result.get('stderr',''),(key,token))
                        private_write(evidence/(name+'.log'),stdout+'\n=== STDERR ===\n'+stderr)
                        print('\n=== '+name.upper()+' (exit '+str(result.get('returncode'))+') ===')
                        if name == 'config':
                            print(json.dumps(summary['config_sources'],ensure_ascii=True))
                        else: print(stdout[:4000].strip())
                        lines = [line for line in stderr.splitlines()
                                 if re.search('config|mcp|error|warn|fail|invalid|denied',line,re.I)]
                        print('\n'.join(lines[:30])[:8000])
                finally:
                    controller.close()
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        summary['result'] = 'DIAGNOSTIC_INCOMPLETE'
        summary['error_type'] = type(exc).__name__
        private_write(evidence/'failure.txt',redact(str(exc),(key,token))+'\n')
        print('Diagnostico incompleto: '+redact(str(exc),(key,token)))
    finally:
        summary['result'] = summary.get('result','DIAGNOSTIC_INCOMPLETE')
        private_write(evidence/'summary.json',json.dumps(summary,indent=2)+'\n')
        print('\n=== RESUMEN ===\n'+json.dumps(summary,indent=2))
        print('EVIDENCE: '+str(evidence/'summary.json'))
        print('Diagnostico, no certificacion de v1. Sin modelos ni prueba A->B.')
    return 0 if summary['result']=='CONNECTED_IN_STANDALONE_DIAGNOSTIC' else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opencode',type=Path,default=Path.home()/'.opencode/bin/opencode')
    return run(parser.parse_args().opencode)


if __name__ == '__main__':
    raise SystemExit(main())
