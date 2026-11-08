"""Broker local de memoria. Python stdlib, sin IA.

El broker es el unico proceso que debe poder abrir SQLite en un despliegue
seguro. ACL por token de sesion + administracion por socket separado.
Advertencia: misma UID sin sandbox NO es frontera de seguridad fuerte.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import threading
import time
from typing import Any

from .policy import ConnectorPolicy
from .store import MemoryStore, MemoryConflictError, _scope

MAX_MESSAGE = 128 * 1024
MAX_RESPONSE = 1024 * 1024
RIGHTS_BY_ACTION = {
    "remember": "write", "update": "write", "forget": "delete",
    "recall": "read", "recent": "read", "get": "read",
    "stats": "read", "audit": "read",
}


class BrokerError(RuntimeError):
    """Solicitud rechazada por el broker."""


def request(socket_path: str | Path, data: dict[str, Any], *, timeout: float = 3.0) -> dict:
    """Una peticion JSON por conexion; sin TCP ni servicios de terceros."""
    encoded = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_MESSAGE:
        raise ValueError("Solicitud demasiado grande")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(encoded)
        chunks = bytearray()
        while not chunks.endswith(b"\n"):
            part = sock.recv(65536)
            if not part:
                raise BrokerError("Respuesta incompleta")
            chunks.extend(part)
            if len(chunks) > MAX_RESPONSE:
                raise BrokerError("Respuesta demasiado grande")
    try:
        response = json.loads(chunks)
    except (ValueError, UnicodeError) as exc:
        raise BrokerError("Respuesta no valida") from exc
    if not isinstance(response, dict) or "ok" not in response:
        raise BrokerError("Respuesta de protocolo desconocida")
    if not response["ok"]:
        kind = response.get("error", "error")
        message = response.get("message", "Solicitud denegada")
        if kind == "permission":
            raise PermissionError(message)
        if kind == "conflict":
            raise MemoryConflictError(message)
        if kind == "validation":
            raise ValueError(message)
        raise BrokerError(message)
    return response["result"]


class BrokerClient:
    """Cliente de datos sin acceso SQLite. Credencial enlazada a UNA sesion."""

    def __init__(self, socket_path: str | Path, token: str):
        if not socket_path or not isinstance(token, str) or len(token) < 32:
            raise ValueError("Se requiere socket y token de sesion")
        self.socket_path = str(socket_path)
        self._token = token

    def _op(self, action: str, **kwargs: Any) -> Any:
        return request(self.socket_path, {"token": self._token, "action": action, "args": kwargs})

    def profile(self) -> dict:
        return self._op("profile")

    def remember(self, body: str, *, title: str = "", kind: str = "fact",
                 importance: int = 3, tags: list[str] | None = None,
                 ttl_days: int | None = None, source: str | None = None) -> dict:
        # El source recibido no se reenvia: solo el broker lo decide.
        return self._op("remember", body=body, title=title, kind=kind,
                        importance=importance, tags=tags, ttl_days=ttl_days)

    def recall(self, query: str, *, limit: int = 5, max_chars: int = 4000) -> list[dict]:
        return self._op("recall", query=query, limit=limit, max_chars=max_chars)

    def list_recent(self, *, limit: int = 20, kind: str | None = None) -> list[dict]:
        return self._op("recent", limit=limit, kind=kind)

    def get(self, uid: str) -> dict | None:
        return self._op("get", id=uid)

    def update(self, uid: str, *, body: str, title: str = "",
               expected_revision: int, importance: int | None = None,
               tags: list[str] | None = None) -> dict | None:
        return self._op("update", id=uid, body=body, title=title,
                        expected_revision=expected_revision, importance=importance, tags=tags)

    def forget(self, uid: str, *, expected_revision: int) -> bool:
        return self._op("forget", id=uid, expected_revision=expected_revision)["deleted"]

    def audit(self, uid: str, *, limit: int = 50) -> list[dict]:
        return self._op("audit", id=uid, limit=limit)

    def stats(self) -> dict:
        return self._op("stats")

    def close(self) -> None:
        pass

    def __enter__(self) -> "BrokerClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    block_on_close = True

    def __init__(self, path: str, handler: type, broker: "BrokerDaemon", admin: bool):
        self.broker = broker
        self.admin = admin
        super().__init__(path, handler)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        # One request per connection, and always respond with a bounded JSON line.
        self.connection.settimeout(3.0)
        try:
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE:
                raise ValueError("Solicitud incompleta o demasiado grande")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("La solicitud debe ser un objeto")
            if self.server.admin:
                value = self.server.broker._admin(message)
            else:
                value = self.server.broker._client(message)
            response = {"ok": True, "result": value}
        except PermissionError:
            response = {"ok": False, "error": "permission", "message": "Acceso denegado"}
        except MemoryConflictError:
            response = {"ok": False, "error": "conflict", "message": "Revision obsoleta"}
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            response = {"ok": False, "error": "validation", "message": "Solicitud no valida"}
        except (OSError, RuntimeError):
            response = {"ok": False, "error": "server", "message": "Error interno del servidor"}
        result = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(result) > MAX_RESPONSE:
            result = b'{"ok":false,"error":"validation","message":"Respuesta demasiado grande"}\n'
        try:
            self.wfile.write(result)
        except (BrokenPipeError, OSError):
            pass


class BrokerDaemon:
    """Sesiones de proyecto exclusivo y operaciones serializadas con revocacion.

    El servidor administrador requiere una clave separada; su socket no
    se publica en el MCP. El aislamiento fuerte requiere permisos OS y
    sandboxes: el proceso agente NO debe poder abrir SQLite ni admin.sock.
    """

    def __init__(self, db: str | Path, client_socket: str | Path, admin_socket: str | Path,
                 *, admin_key: str):
        if not isinstance(admin_key, str) or len(admin_key) < 48:
            raise ValueError("Clave admin de 48 caracteres minimo obligatoria")
        self.db = Path(db).expanduser()
        self.client_socket = Path(client_socket)
        self.admin_socket = Path(admin_socket)
        if self.client_socket == self.admin_socket:
            raise ValueError("Sockets de datos y administracion distintos")
        if self.db.resolve() in (self.client_socket.resolve(), self.admin_socket.resolve()):
            raise ValueError("Base de datos y sockets deben ser diferentes")
        self._admin_hash = hashlib.sha256(admin_key.encode()).digest()
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._stores: dict[tuple[str, str, str], MemoryStore] = {}
        self._servers: list[_Server] = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._servers:
            raise RuntimeError("Broker ya iniciado")
        paths = [self.client_socket, self.admin_socket]
        for p in paths:
            if p.exists() or p.is_symlink():
                raise FileExistsError(f"El socket ya existe: {p}")
            p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            for path, is_admin in ((self.client_socket, False), (self.admin_socket, True)):
                server = _Server(str(path), _Handler, self, is_admin)
                os.chmod(path, 0o600)
                self._servers.append(server)
            for server in self._servers:
                th = threading.Thread(target=server.serve_forever, name="symem-broker", daemon=True)
                th.start()
                self._threads.append(th)
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        for server in self._servers:
            if self._threads:
                server.shutdown()
            server.server_close()
        self._servers.clear()
        for th in self._threads:
            th.join(timeout=3)
        self._threads.clear()
        for path in (self.client_socket, self.admin_socket):
            if path.is_socket():
                path.unlink()
        with self._lock:
            for store in self._stores.values():
                store.close()
            self._stores.clear()
            self._sessions.clear()

    def __enter__(self) -> "BrokerDaemon":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()

    def _issue(self, session: str, client: str, space: str, project: str,
               rights: str, ttl_seconds: int) -> dict:
        policy = ConnectorPolicy.from_values(client, rights)
        session = _scope(session, "session")
        space = _scope(space, "space")
        project = _scope(project, "project")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 86400:
            raise ValueError("ttl_seconds debe estar entre 30 y 86400")
        token = secrets.token_urlsafe(40)
        expires_at = time.time() + ttl_seconds
        self._sessions[session] = {
            "token_hash": hashlib.sha256(token.encode()).digest(),
            "client": policy.client, "rights": policy.rights, "space": space,
            "project": project, "expires_at": expires_at,
        }
        return {"session": session, "token": token, "client": client,
                "space": space, "project": project, "rights": sorted(policy.rights),
                "expires_at": int(expires_at)}

    def _admin(self, msg: dict) -> dict:
        auth = msg.get("admin_key")
        if not isinstance(auth, str) or not hmac.compare_digest(
                hashlib.sha256(auth.encode()).digest(), self._admin_hash):
            raise PermissionError("No autorizado")
        action = msg.get("action")
        args = msg.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("Args invalidos")
        with self._lock:
            if action == "grant":
                session = _scope(args["session"], "session")
                if session in self._sessions:
                    raise ValueError("La sesion ya tiene proyecto; use switch")
                return self._issue(session, args["client"], args["space"], args["project"],
                                   args["rights"], args.get("ttl_seconds", 1800))
            if action == "switch":
                session = _scope(args["session"], "session")
                if session not in self._sessions:
                    raise ValueError("Sesion no registrada")
                # Validate all new inputs BEFORE revoking the old session.
                client, space, project = args["client"], args["space"], args["project"]
                rights, ttl = args["rights"], args.get("ttl_seconds", 1800)
                ConnectorPolicy.from_values(client, rights)
                _scope(space, "space"); _scope(project, "project")
                if type(ttl) is not int or not 30 <= ttl <= 86400:
                    raise ValueError("ttl_seconds invalido")
                # All data operations use this same lock. No old-token operation
                # can begin after this switch returns.
                return self._issue(session, client, space, project, rights, ttl)
            if action == "revoke":
                session = _scope(args["session"], "session")
                return {"revoked": self._sessions.pop(session, None) is not None}
            if action == "sessions":
                return [{"session": s, "client": g["client"], "space": g["space"],
                         "project": g["project"], "rights": sorted(g["rights"]),
                         "expires_at": int(g["expires_at"])}
                        for s, g in self._sessions.items() if time.time() < g["expires_at"]]
            raise ValueError("Accion admin desconocida")

    def _client(self, msg: dict) -> Any:
        token = msg.get("token")
        action = msg.get("action")
        args = msg.get("args", {})
        if not isinstance(token, str) or len(token) < 32 or not isinstance(args, dict):
            raise PermissionError("Credencial invalida")
        with self._lock:
            token_hash = hashlib.sha256(token.encode()).digest()
            grant = next((g for g in self._sessions.values()
                          if hmac.compare_digest(token_hash, g["token_hash"])
                          and time.time() < g["expires_at"]), None)
            if grant is None:
                raise PermissionError("Sesion revocada, desconocida o caducada")
            if action == "profile":
                return {k: (sorted(v) if isinstance(v, frozenset) else v)
                        for k, v in grant.items() if k in ("client", "space", "project", "rights", "expires_at")}
            if action not in RIGHTS_BY_ACTION:
                raise PermissionError("Accion no permitida")
            if RIGHTS_BY_ACTION[action] not in grant["rights"]:
                raise PermissionError("Permiso no concedido")
            # No project/space/client/rights can be supplied by the agent.
            if any(k in args for k in ("project", "space", "client", "rights", "source", "actor")):
                raise PermissionError("No se permite elegir contexto")
            key = (grant["space"], grant["project"], grant["client"])
            store = self._stores.get(key)
            if store is None:
                store = MemoryStore(self.db, space=grant["space"],
                                    project=grant["project"], actor=grant["client"])
                self._stores[key] = store
            if action == "remember":
                saved = store.remember(**args, source="mcp:" + grant["client"])
                if "read" not in grant["rights"]:
                    return {"id": saved["memory"]["id"],
                            "revision": saved["memory"]["revision"],
                            "created": saved["created"]}
                return saved
            if action == "recall":
                return store.recall(**args)
            if action == "recent":
                return store.list_recent(**args)
            if action == "get":
                return store.get(args["id"])
            if action == "update":
                if "expected_revision" not in args:
                    raise ValueError("Revision esperada obligatoria")
                # Normalize naming for MemoryStore.update.
                options = dict(args)
                uid = options.pop("id")
                updated = store.update(uid, **options)
                if updated and "read" not in grant["rights"]:
                    return {"id": updated["id"], "revision": updated["revision"]}
                return updated
            if action == "forget":
                if "expected_revision" not in args:
                    raise ValueError("Revision esperada obligatoria")
                return {"deleted": store.forget(args["id"],
                                                 expected_revision=args["expected_revision"])}
            if action == "audit":
                return store.audit(args["id"], limit=args.get("limit", 50))
            if action == "stats":
                return {k: v for k, v in store.stats().items() if k != "database"}
            raise ValueError("Operacion desconocida")


def admin_request(socket_path: str | Path, admin_key: str, action: str, **kwargs: Any) -> dict:
    return request(socket_path, {"admin_key": admin_key, "action": action, "args": kwargs})
