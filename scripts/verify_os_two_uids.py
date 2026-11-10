"""Optional cross-UID Linux check in temporary directories; never invokes sudo.

Requires an already isolated root environment and setpriv. Ephemeral tokens
are sent through stdin, not exposed in the child process command line.
"""
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import sys
import tempfile

from synapsys_memory.broker import BrokerClient, BrokerDaemon, admin_request

CHILD_SCRIPT = r'''
from contextlib import closing
import json, sqlite3, sys
from synapsys_memory.broker import BrokerClient
info = json.load(sys.stdin)
client = BrokerClient(info['socket'], info['token'])
assert client.profile()['project'] == 'arbitrary-app'
assert client.remember('UID scoped memory')['memory']['project'] == 'arbitrary-app'
assert len(client.recall('scoped')) == 1
for key in ('db', 'admin', 'foreign'):
    try:
        if key == 'db':
            with closing(sqlite3.connect('file:'+info[key]+'?mode=ro', uri=True)) as db:
                db.execute('SELECT name FROM sqlite_master').fetchall()
        else:
            with open(info[key], 'rb') as stream: stream.read(1)
    except (PermissionError, sqlite3.OperationalError):
        pass
    else:
        raise AssertionError('Unexpected OS access: '+key)
print('CHILD PASS: authorized memory; SQLite/admin/foreign file denied')
'''


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or not shutil.which('setpriv'):
        print('NOT RUN: isolated Linux root and setpriv required; no automatic sudo')
        return 77
    nobody = pwd.getpwnam('nobody')
    uid, gid = nobody.pw_uid, nobody.pw_gid
    if uid == 0: raise RuntimeError('Test client must not be root')
    with tempfile.TemporaryDirectory(prefix='symem-crossuid-') as temp:
        root = Path(temp); root.chmod(0o710); os.chown(root, -1, gid)
        db, data, admin = [root/name for name in ('memory.sqlite3','data.sock','admin.sock')]
        foreign = root/'other-project.txt'; foreign.write_text('unchanged'); foreign.chmod(0o600)
        key = secrets.token_urlsafe(48)
        with BrokerDaemon(db, data, admin, admin_key=key, client_gid=gid):
            grant = admin_request(admin, key, 'grant', session='cross-uid', client='test-client',
                                  space='private', project='arbitrary-app', rights='read,write', peer_uid=uid)
            client = BrokerClient(data, grant['token'])
            try: client.profile()
            except PermissionError: pass
            else: raise AssertionError('Broker UID accepted a client UID token')
            cmd = [shutil.which('setpriv'), '--reuid', str(uid), '--regid', str(gid),
                   '--clear-groups', sys.executable, '-c', CHILD_SCRIPT]
            payload = dict(socket=str(data), token=grant['token'], db=str(db), admin=str(admin), foreign=str(foreign))
            env = {'PATH':'/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE':'1',
                   'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
            child = subprocess.run(cmd, input=json.dumps(payload), env=env, capture_output=True,
                                   text=True, timeout=15, check=False)
            if child.returncode: raise RuntimeError('Cross-UID check failed: '+child.stderr)
            print(child.stdout.strip())
            admin_request(admin, key, 'revoke', session='cross-uid')
    print('PASS: two real Linux UIDs, token binding and private resources')
    return 0


if __name__ == '__main__': raise SystemExit(main())
