"""Prueba opcional de aislamiento OS real, SOLO en entorno temporal Unix/root.

No crea usuarios, no usa IA, no modifica proyectos ni privilegios del host.
El proceso que ejecuta el script debe disponer de setpriv y UID root.
No es un sustituto de un sandbox real de archivos.
"""

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
import sqlite3
import sys
from synapsys_memory.broker import BrokerClient
sock, token, db, admin, foreign = sys.argv[1:]
client = BrokerClient(sock, token)
assert client.profile()["project"] == "app-arbitraria"
result = client.remember("Dato compartido solo con UID autorizado")
assert result["memory"]["project"] == "app-arbitraria"
assert len(client.recall("compartido")) == 1
for path in (db, admin, foreign):
    try:
        if path == db:
            sqlite3.connect(f"file:{path}?mode=ro", uri=True).execute("SELECT name FROM sqlite_master").fetchall()
        else:
            open(path, "rb").read(1)
    except (PermissionError, sqlite3.OperationalError):
        pass
    else:
        raise AssertionError("Acceso OS no esperado: " + path)
print("CHILD PASS: memoria autorizada; SQLite/admin/proyecto ajeno denegados")
'''


def main():
    if sys.platform != "linux" or os.geteuid() != 0 or not shutil.which("setpriv"):
        print("SKIP: requiere Linux, root aislado y setpriv; no se ejecuta con sudo automaticamente")
        return 0
    nobody = pwd.getpwnam("nobody")
    uid, gid = nobody.pw_uid, nobody.pw_gid
    if uid == 0:
        raise RuntimeError("El usuario de prueba no puede ser root")
    project_src = str(Path(__file__).resolve().parents[1] / "src")
    with tempfile.TemporaryDirectory(prefix="symem-crossuid-") as temporary:
        root = Path(temporary)
        root.chmod(0o710)
        os.chown(root, -1, gid)
        db, socket_data, socket_admin = [root / n for n in ("memory.sqlite3", "data.sock", "admin.sock")]
        foreign = root / "otro-proyecto.txt"
        foreign.write_text("archivo de otro proyecto")
        foreign.chmod(0o600)
        key = secrets.token_urlsafe(48)
        with BrokerDaemon(db, socket_data, socket_admin, admin_key=key, client_gid=gid):
            auth = admin_request(socket_admin, key, "grant", session="test-real-linux",
                                 client="cliente-sandbox", space="privado",
                                 project="app-arbitraria", rights="read,write", peer_uid=uid)
            try:
                BrokerClient(socket_data, auth["token"]).profile()
            except PermissionError:
                pass
            else:
                raise AssertionError("El UID del broker no debe utilizar token del UID cliente")
            cmd = [shutil.which("setpriv"), "--reuid", str(uid), "--regid", str(gid),
                   "--clear-groups", sys.executable, "-c", CHILD_SCRIPT,
                   str(socket_data), auth["token"], str(db), str(socket_admin), str(foreign)]
            environment = {"PATH": "/usr/bin:/bin", "PYTHONPATH": project_src,
                           "PYTHONDONTWRITEBYTECODE": "1"}
            run = subprocess.run(cmd, env=environment, capture_output=True, text=True,
                                 timeout=15, check=False)
            if run.returncode != 0:
                raise AssertionError("Comprobacion UID real fallo: " + run.stderr)
            print(run.stdout.strip())
            self_client = BrokerClient(socket_data, auth["token"])
            try:
                self_client.recall("compartido")
            except PermissionError:
                pass
            else:
                raise AssertionError("UID del broker accedio con token ajeno")
            admin_request(socket_admin, key, "revoke", session="test-real-linux")
    print("PASS: dos UIDs Linux reales, token vinculado y recursos privados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
