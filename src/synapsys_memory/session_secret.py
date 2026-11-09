"""Local session credential loader. Never writes tokens to logs or arguments."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,512}")


def read_token_file(file: str | Path) -> str:
    path = Path(file)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Ruta de token invalida")
    try:
        real = path.resolve(strict=True)
        st = path.stat()
    except OSError as exc:
        raise ValueError("Token inaccesible") from exc
    if real != path or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) & 0o077:
        raise ValueError("Token no protegido por el sistema de archivos")
    if st.st_size > 1024:
        raise ValueError("Tamano de token incorrecto")
    value = path.read_text(encoding="ascii").strip()
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
