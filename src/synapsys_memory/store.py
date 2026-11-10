"""Motor local, persistente y aislado de SynapSyS-MemorY.

Sin proveedores de IA, servicios de red ni dependencias de terceros.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4
from contextlib import contextmanager, closing

from .secureio import reserve_private

SCHEMA_VERSION = 2


class MemoryConflictError(RuntimeError):
    """Version del recuerdo cambiada desde la ultima lectura del cliente."""


KINDS = frozenset({"fact", "decision", "preference", "episode", "summary", "task"})
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,95}$")
_WORD = re.compile(r"\w+", flags=re.UNICODE)
_DEFAULT_DB = Path.home() / ".local" / "share" / "synapsys-memory" / "memory.sqlite3"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scope(value: str, name: str, *, optional: bool = False) -> str:
    if optional and value == "":
        return ""
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"{name} debe contener 1-96 caracteres alfanumericos, ., _, / o -")
    return value


def _text(value: str, name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} debe ser texto")
    result = value.strip()
    if not result or len(result) > max_length:
        raise ValueError(f"{name} debe tener 1-{max_length} caracteres")
    return result


def _tags(tags: list[str] | None) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list) or len(tags) > 12:
        raise ValueError("tags debe ser una lista de hasta 12 etiquetas")
    result: list[str] = []
    for tag in tags:
        cleaned = _text(tag, "tag", 40).lower()
        if cleaned not in result:
            result.append(cleaned)
    return result


def _validate_limit(limit: int, max_value: int = 100) -> int:
    if type(limit) is not int or not 1 <= limit <= max_value:
        raise ValueError(f"limit debe estar entre 1 y {max_value}")
    return limit


def _expiry(ttl_days: int | None, now: int) -> int | None:
    if ttl_days is None:
        return None
    if type(ttl_days) is not int or not 1 <= ttl_days <= 3650:
        raise ValueError("ttl_days debe estar entre 1 y 3650")
    return now + ttl_days * 86_400_000


def _public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["uid"],
        "space": row["space"],
        "project": row["project"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "tags": json.loads(row["tags_json"]),
        "source": row["source"],
        "importance": row["importance"],
        "revision": row["revision"],
        "created_ms": row["created_ms"],
        "updated_ms": row["updated_ms"],
        "expires_ms": row["expires_ms"],
    }


class MemoryStore:
    """Almacen persistente. Cada instancia tiene un espacio y proyecto fijos.

    Para compartir recuerdos, varios conectores deben usar deliberadamente los
    mismos valores de space, project y database. No hay lecturas entre espacios.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        space: str = "personal",
        project: str = "",
        actor: str = "local",
    ) -> None:
        self.actor = _scope(actor, "actor")
        self.space = _scope(space, "space")
        self.project = _scope(project, "project", optional=True)
        self.path = Path(path or os.environ.get("SYMEM_DB", _DEFAULT_DB)).expanduser()
        if str(self.path) == ":memory:":
            raise ValueError("Se requiere una base persistente; use un archivo SQLite")
        self.path = reserve_private(self.path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            with self._lock:
                version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise RuntimeError(f"Base version {version} mas nueva que el codigo {SCHEMA_VERSION}")
                self._db.execute("PRAGMA busy_timeout=5000")
                self._configure_wal()
                self._db.execute("PRAGMA synchronous=FULL")
                self._db.execute("PRAGMA secure_delete=ON")
                self._db.execute("PRAGMA foreign_keys=ON")
                # Schema DDL and revision marker commit atomically. Serialize
                # first-open migrations across processes before checking version.
                with self._transaction():
                    self._create_schema()
                    self._migrate()
        except BaseException:
            self._db.close()  # No filtrar descriptores si falla el inicio o migracion.
            raise

    def _configure_wal(self) -> None:
        # The first journal-mode switch may report SQLITE_BUSY even when the
        # connection busy timeout is set. Retry only this idempotent PRAGMA,
        # never replay application writes or failed migrations.
        deadline = time.monotonic() + 5.0
        while True:
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xff
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def _create_schema(self) -> None:
        # executescript() would commit an outer transaction; execute complete
        # statements individually, including complete CREATE TRIGGER bodies.
        new_fts = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_fts'"
        ).fetchone() is None
        schema = """
                CREATE TABLE IF NOT EXISTS memories (
                    row_id INTEGER PRIMARY KEY,
                    uid TEXT NOT NULL UNIQUE,
                    space TEXT NOT NULL,
                    project TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL,
                    expires_ms INTEGER,
                    fingerprint TEXT NOT NULL,
                    UNIQUE(space, project, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_scope_time
                    ON memories(space, project, updated_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_scope_kind
                    ON memories(space, project, kind);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    title, body, tags_json,
                    content='memories', content_rowid='row_id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memory_fts(rowid, title, body, tags_json)
                    VALUES(new.row_id, new.title, new.body, new.tags_json);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, title, body, tags_json)
                    VALUES('delete', old.row_id, old.title, old.body, old.tags_json);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, title, body, tags_json)
                    VALUES('delete', old.row_id, old.title, old.body, old.tags_json);
                    INSERT INTO memory_fts(rowid, title, body, tags_json)
                    VALUES(new.row_id, new.title, new.body, new.tags_json);
                END;
            """
        statement = ""
        for line in schema.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                self._db.execute(statement)
                statement = ""
        if new_fts:
            self._db.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")


    def _migrate(self) -> None:
        """Actualizacion no destructiva v0.1 (user_version=0) -> v0.2.

        Mantiene las tablas y registros anteriores. Nuevos eventos auditables
        no incluyen el cuerpo, para no resucitar contenido tras un borrado.
        """
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Base version {version} mas nueva que el codigo {SCHEMA_VERSION}")
        if version < 2:
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(memories)")}
            if "revision" not in columns:
                self._db.execute("ALTER TABLE memories ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id INTEGER PRIMARY KEY,
                    uid TEXT NOT NULL,
                    space TEXT NOT NULL,
                    project TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('create','update','delete')),
                    revision INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    occurred_ms INTEGER NOT NULL
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_events_scope
                ON memory_events(space, project, uid, event_id)
            """)
            self._db.execute("PRAGMA user_version=2")

    def _audit(self, uid: str, action: str, revision: int) -> None:
        self._db.execute("""
            INSERT INTO memory_events(uid, space, project, action, revision, actor, occurred_ms)
            VALUES(?,?,?,?,?,?,?)
        """, (uid, self.space, self.project, action, revision, self.actor, _now_ms()))

    def audit(self, uid: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Registro de cambios de una memoria; deliberadamente sin contenido."""
        uid = _text(uid, "id", 100)
        _validate_limit(limit)
        with self._lock:
            rows = self._db.execute("""
                SELECT uid, action, revision, actor, occurred_ms FROM memory_events
                WHERE uid=? AND space=? AND project=?
                ORDER BY event_id DESC LIMIT ?
            """, (uid, self.space, self.project, limit)).fetchall()
        return [dict(row) for row in rows]

    def check_integrity(self) -> bool:
        """Comprobacion basica de SQLite; no comprueba el indice FTS externo."""
        with self._lock:
            return self._db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _fingerprint(self, kind: str, title: str, body: str) -> str:
        payload = json.dumps([kind, title, body], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def remember(
        self,
        body: str,
        *,
        title: str = "",
        kind: str = "fact",
        tags: list[str] | None = None,
        source: str = "manual",
        importance: int = 3,
        ttl_days: int | None = None,
    ) -> dict[str, Any]:
        body = _text(body, "body", 20_000)
        if not isinstance(title, str):
            raise ValueError("title debe ser texto")
        title = _text(title, "title", 160) if title.strip() else body[:100].splitlines()[0]
        source = _text(source, "source", 160)
        if kind not in KINDS:
            raise ValueError(f"kind debe ser uno de {sorted(KINDS)}")
        if type(importance) is not int or not 1 <= importance <= 5:
            raise ValueError("importance debe estar entre 1 y 5")
        normalized_tags = _tags(tags)
        now = _now_ms()
        fingerprint = self._fingerprint(kind, title, body)
        uid = str(uuid4())
        with self._transaction():
            # Una expiracion no impide volver a guardar explicitamente el mismo dato.
            self._db.execute("""
                DELETE FROM memories WHERE space=? AND project=? AND fingerprint=?
                    AND expires_ms IS NOT NULL AND expires_ms <= ?
            """, (self.space, self.project, fingerprint, now))
            # INSERT OR IGNORE hace la deduplicacion atomica incluso entre procesos.
            cursor = self._db.execute("""
                INSERT OR IGNORE INTO memories(
                    uid, space, project, kind, title, body, tags_json, source,
                    importance, created_ms, updated_ms, expires_ms, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uid, self.space, self.project, kind, title, body,
                json.dumps(normalized_tags, ensure_ascii=False), source,
                importance, now, now, _expiry(ttl_days, now), fingerprint,
            ))
            created = cursor.rowcount == 1
            if created:
                self._audit(uid, "create", 1)
            actual = self.get(uid) if created else _public(self._db.execute(
                "SELECT * FROM memories WHERE space=? AND project=? AND fingerprint=?",
                (self.space, self.project, fingerprint),
            ).fetchone())
            return {"created": created, "memory": actual}

    def get(self, uid: str) -> dict[str, Any] | None:
        uid = _text(uid, "id", 100)
        with self._lock:
            row = self._db.execute("""
                SELECT * FROM memories
                WHERE uid=? AND space=? AND project=?
                  AND (expires_ms IS NULL OR expires_ms > ?)
            """, (uid, self.space, self.project, _now_ms())).fetchone()
            return _public(row) if row is not None else None

    def forget(self, uid: str, *, expected_revision: int | None = None) -> bool:
        uid = _text(uid, "id", 100)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("expected_revision debe ser un entero positivo")
        with self._transaction():
            # Expiry hides a record from retrieval, not from explicit erasure.
            row = self._db.execute(
                "SELECT * FROM memories WHERE uid=? AND space=? AND project=?",
                (uid, self.space, self.project),
            ).fetchone()
            current = _public(row) if row is not None else None
            if current is None:
                return False
            if expected_revision is not None and current["revision"] != expected_revision:
                raise MemoryConflictError("Revision distinta; recupera de nuevo el recuerdo")
            cursor = self._db.execute(
                "DELETE FROM memories WHERE uid=? AND space=? AND project=? AND revision=?",
                (uid, self.space, self.project, current["revision"]),
            )
            if cursor.rowcount == 1:
                self._audit(uid, "delete", current["revision"])
                return True
            raise MemoryConflictError("Cambio concurrente; reintenta tras recuperar")

    def update(
        self,
        uid: str,
        *,
        body: str,
        title: str = "",
        importance: int | None = None,
        tags: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        uid = _text(uid, "id", 100)
        body = _text(body, "body", 20_000)
        if not isinstance(title, str):
            raise ValueError("title debe ser texto")
        title = _text(title, "title", 160) if title.strip() else body[:100].splitlines()[0]
        if importance is not None and (type(importance) is not int or not 1 <= importance <= 5):
            raise ValueError("importance debe estar entre 1 y 5")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("expected_revision debe ser un entero positivo")
        with self._transaction():
            current = self.get(uid)
            if current is None:
                return None
            if expected_revision is not None and current["revision"] != expected_revision:
                raise MemoryConflictError("Revision distinta; recupera de nuevo el recuerdo")
            tags_json = json.dumps(_tags(tags) if tags is not None else current["tags"], ensure_ascii=False)
            fingerprint = self._fingerprint(current["kind"], title, body)
            try:
                self._db.execute("""
                    UPDATE memories SET body=?, title=?, tags_json=?, importance=?,
                        updated_ms=?, fingerprint=?, revision=revision+1
                    WHERE uid=? AND space=? AND project=? AND revision=?
                """, (
                    body, title, tags_json,
                    current["importance"] if importance is None else importance,
                    max(_now_ms(), current["updated_ms"] + 1), fingerprint, uid, self.space, self.project,
                    current["revision"],
                ))
                if self._db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise MemoryConflictError("Cambio concurrente; reintenta tras recuperar")
            except sqlite3.IntegrityError as error:
                raise ValueError("Ya existe una memoria identica en este espacio") from error
            self._audit(uid, "update", current["revision"] + 1)
            return self.get(uid)

    def list_recent(self, *, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        _validate_limit(limit)
        if kind is not None and kind not in KINDS:
            raise ValueError("kind invalido")
        sql = """SELECT * FROM memories WHERE space=? AND project=?
                 AND (expires_ms IS NULL OR expires_ms > ?)"""
        params: list[Any] = [self.space, self.project, _now_ms()]
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY updated_ms DESC, row_id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [_public(row) for row in rows]

    def recall(self, query: str, *, limit: int = 5, max_chars: int = 4000) -> list[dict[str, Any]]:
        query = _text(query, "query", 500)
        _validate_limit(limit, 20)
        if type(max_chars) is not int or not 100 <= max_chars <= 20_000:
            raise ValueError("max_chars debe estar entre 100 y 20000")
        terms = [word for word in _WORD.findall(query) if len(word) >= 2][:12]
        if not terms:
            return []
        match = " OR ".join('"' + word.replace('"', '""') + '"' for word in dict.fromkeys(terms))
        now = _now_ms()
        with self._lock:
            rows = self._db.execute("""
                SELECT m.*, bm25(memory_fts, 4.0, 1.0, 1.5) AS lexical_rank
                FROM memory_fts JOIN memories AS m ON m.row_id=memory_fts.rowid
                WHERE memory_fts MATCH ?
                  AND m.space=? AND m.project=?
                  AND (m.expires_ms IS NULL OR m.expires_ms > ?)
                ORDER BY lexical_rank ASC, m.row_id ASC
                LIMIT ?
            """, (match, self.space, self.project, now, max(40, limit * 5))).fetchall()
        candidates = []
        for rank, row in enumerate(rows):
            age_days = max(0.0, (now - row["updated_ms"]) / 86_400_000)
            # Relevancia lexica domina; actualidad e importancia resuelven cercanias.
            score = 2.0 / (1.0 + rank) + row["importance"] * 0.02 + 0.10 * math.exp(-age_days / 45)
            candidates.append((score, row))
        candidates.sort(key=lambda item: item[0], reverse=True)
        result: list[dict[str, Any]] = []
        remaining = max_chars
        for score, row in candidates[:limit]:
            if remaining <= 0:
                break
            entry = _public(row)
            original = entry["body"]
            allowed = min(2000, remaining)
            entry["body"] = original[:allowed]
            entry["truncated"] = len(original) > allowed
            entry["score"] = round(score, 4)
            result.append(entry)
            remaining -= len(entry["body"])
        return result

    def stats(self) -> dict[str, Any]:
        now = _now_ms()
        with self._lock:
            rows = self._db.execute("""
                SELECT kind, COUNT(*) as n FROM memories
                WHERE space=? AND project=? AND (expires_ms IS NULL OR expires_ms>?)
                GROUP BY kind
            """, (self.space, self.project, now)).fetchall()
        per_kind = {row["kind"]: row["n"] for row in rows}
        return {
            "space": self.space, "project": self.project,
            "count": sum(per_kind.values()), "by_kind": per_kind,
            "database": str(self.path),
        }

    def backup(self, destination: str | Path) -> str:
        """Create a consistent, private, non-overwriting backup of the whole DB.

        This is a trusted local administrator operation, NOT a per-project
        export. It is deliberately never exposed by the client broker or MCP.
        """
        dest = Path(destination).expanduser()
        if dest.resolve() == self.path.resolve():
            raise ValueError("El destino del respaldo debe ser diferente de la base de datos")
        dest = reserve_private(dest, exclusive=True)
        identity = dest.stat()
        try:
            with self._lock, closing(sqlite3.connect(dest)) as target:
                target.execute("PRAGMA synchronous=FULL")
                self._db.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
        except BaseException:
            # Do not remove a destination replaced by another process.
            try:
                now = dest.lstat()
                if (now.st_dev, now.st_ino) == (identity.st_dev, identity.st_ino):
                    dest.unlink()
            except FileNotFoundError:
                pass
            raise
        return str(dest)
