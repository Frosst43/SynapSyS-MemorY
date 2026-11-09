#!/usr/bin/env python3
"""Prueba fisica del sandbox+broker en proyectos temporales, sin IAs ni repos reales."""
from __future__ import annotations
import json
from pathlib import Path
import secrets
import tempfile
from synapsys_memory.broker import BrokerDaemon, BrokerClient, admin_request
from synapsys_memory.sandbox import SandboxError, run

CHILD = r'''
import json, os, socket
from pathlib import Path

sockpath=os.environ['SYMEM_BROKER_SOCKET']
token=Path(os.environ['SYMEM_SESSION_TOKEN_FILE']).read_text(encoding='ascii').strip()
assert len(token) > 32
assert not Path('FORBIDDEN_HOST_PATH').exists(), 'Foreign project became visible'
assert not Path('PRIVATE_BROKER_DB').exists(), 'SQLite became visible'
assert not Path('PRIVATE_ADMIN_SOCK').exists(), 'Admin socket became visible'
try:
    Path(os.environ['SYMEM_SESSION_TOKEN_FILE']).write_text('CORRUPTED')
    raise AssertionError('Writable token file')
except OSError:
    pass

def query(action,**args):
    message={'token':token,'action':action,'args':args}
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(4)
        client.connect(sockpath)
        client.sendall((json.dumps(message)+'\n').encode('utf-8'))
        buf=bytearray()
        while not buf.endswith(b'\n'):
            block=client.recv(16384)
            if not block: raise AssertionError('Broken broker response')
            buf.extend(block)
        return json.loads(buf.decode('utf-8'))

profile=query('profile')
assert profile.get('ok') is True, profile
assert profile['result']['project']=='synapsys-os-mobile'
assert profile['result']['space']=='pilot'
record=query('remember',body='Decision aislada para SynapSyS Mobile',title='Prueba con sandbox')
assert record['ok'] is True, record
Path('/workspace/written-from-sandbox.txt').write_text('WORKSPACE_OK',encoding='utf-8')
print('SANDBOX_BROKER_CHILD_PASS')
'''

def main() -> int:
    with tempfile.TemporaryDirectory(prefix='symem-full-pilot-') as temporary:
        root=Path(temporary)
        own=root/'demo-mobile'; own.mkdir()
        other=root/'other-app'; other.mkdir()
        foreign_file=other/'protected.txt'; foreign_file.write_text('UNMODIFIED')
        data=root/'data.sock'; admin=root/'admin.sock'; db=root/'memory.sqlite3'
        token_file=root/'session.token'; policy=root/'policy.json'
        key=secrets.token_urlsafe(48)
        with BrokerDaemon(db,data,admin,admin_key=key):
            grant=admin_request(admin,key,'grant',session='mobile',client='opencode',
                                space='pilot',project='synapsys-os-mobile',rights='read,write')
            foreign=admin_request(admin,key,'grant',session='foreign',client='opencode',
                                  space='pilot',project='another-unrelated-project',rights='read')
            token_file.write_text(grant['token'],encoding='ascii');token_file.chmod(0o600)
            policy.write_text(json.dumps({'schema_version':1,'projects':{
                'synapsys-os-mobile':{'workspace':str(own),'read_only':False,'memory':{
                    'data_socket':str(data),'token_file':str(token_file),'space':'pilot'}}}}),encoding='utf-8')
            policy.chmod(0o600)
            child=(CHILD.replace('FORBIDDEN_HOST_PATH',str(foreign_file))
                        .replace('PRIVATE_BROKER_DB',str(db))
                        .replace('PRIVATE_ADMIN_SOCK',str(admin)))
            try:
                rc=run(policy,'synapsys-os-mobile',['python3','-c',child])
            except SandboxError as exc:
                print(f'NO VALIDADO: no se pudo establecer el sandbox+broker: {exc}')
                return 2
            if rc != 0:
                print(f'FAIL: proceso dentro del sandbox devolvio {rc}')
                return 1
            if (own/'written-from-sandbox.txt').read_text() != 'WORKSPACE_OK':
                print('FAIL: no se confirmo escritura autorizada');return 1
            if foreign_file.read_text() != 'UNMODIFIED':
                print('FAIL: archivo externo modificado');return 1
            records=BrokerClient(data,grant['token']).recall('Decision aislada')
            if not records:
                print('FAIL: memoria escrita en sandbox no fue persistida');return 1
            if BrokerClient(data,foreign['token']).recall('Decision aislada'):
                print('FAIL: memoria filtrada a proyecto externo');return 1
            admin_request(admin,key,'revoke',session='mobile')
            try:
                BrokerClient(data,grant['token']).profile()
            except PermissionError:
                pass
            else:
                print('FAIL: token no revocado');return 1
            print('PASS: Bubblewrap+broker; memoria autorizada, proyecto externo bloqueado y token revocado')
            return 0

if __name__=='__main__':
    raise SystemExit(main())
