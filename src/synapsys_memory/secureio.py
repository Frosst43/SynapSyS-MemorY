"""Bounded local IO helpers. No network, inference or credential logging.

These checks complement OS isolation; they do not defend against a process
with the same UID racing every pathname or a privileged host administrator.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def strict_json_loads(data: str | bytes) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("Non-finite JSON value")
    return json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=reject_constant)


def canonical_path(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    if path.resolve(strict=False) != path or path.is_symlink():
        raise ValueError("Non-canonical or symlinked private path")
    return path


def _private_stat(info: os.stat_result, *, root_allowed: bool = False) -> None:
    owners = {os.geteuid(), 0} if root_allowed else {os.geteuid()}
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid not in owners or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("Private file requires one regular inode, correct owner and private mode")


def reserve_private(value: str | Path, *, exclusive: bool = False) -> Path:
    """Create with 0600 from the first instant, or verify an existing private DB."""
    path = canonical_path(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        if exclusive:
            raise ValueError("Destination already exists") from None
        flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
    try:
        _private_stat(os.fstat(fd))
    finally:
        os.close(fd)
    # Never let SQLite follow hostile WAL/SHM/journal files either.
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            if sidecar.is_symlink():
                raise ValueError("Symlinked SQLite sidecar")
            _private_stat(sidecar.lstat())
    return path


def read_private(value: str | Path, *, limit: int, root_allowed: bool = True) -> bytes:
    path = canonical_path(value)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(fd)
        _private_stat(info, root_allowed=root_allowed)
        if info.st_size > limit:
            raise ValueError("Private file exceeds size limit")
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Private file exceeds size limit")
        return data
    finally:
        os.close(fd)
