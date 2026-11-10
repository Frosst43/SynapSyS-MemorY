"""Local session credential loader. Never writes tokens to logs or arguments."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,512}")


def read_token_file(file: str | Path) -> str:
    from .secureio import read_private
    if not Path(file).is_absolute():
        raise ValueError("Ruta de token invalida")
    try:
        value = read_private(file, limit=1024).decode("ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Token inaccesible o invalido") from exc
    if not _TOKEN.fullmatch(value):
        raise ValueError("Formato de token invalido")
    return value


def token_from_environment() -> str:
    file = os.environ.get("SYMEM_SESSION_TOKEN_FILE", "")
    inline = os.environ.get("SYMEM_SESSION_TOKEN", "")
    if file and inline:
        raise ValueError("No combine token de entorno y archivo")
    if file:
        return read_token_file(file)
    if not _TOKEN.fullmatch(inline):
        raise ValueError("SYMEM_SESSION_TOKEN ausente o invalido")
    return inline
