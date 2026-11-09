"""Permisos locales deterministas por conector MCP.

No son autenticacion fuerte frente a procesos capaces de leer SQLite.
"""

from dataclasses import dataclass
import os
import re

VALID_RIGHTS = frozenset({"read", "write", "delete"})
_CLIENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ConnectorPolicy:
    client: str
    rights: frozenset[str]

    @classmethod
    def from_values(cls, client: str = "local-mcp", rights: str = "read,write") -> "ConnectorPolicy":
        if not isinstance(client, str) or not _CLIENT.fullmatch(client):
            raise ValueError("client debe ser un identificador de 1 a 64 caracteres")
        if not isinstance(rights, str):
            raise ValueError("rights debe ser texto")
        parsed = [part.strip().lower() for part in rights.split(",")]
        if not parsed or any(part not in VALID_RIGHTS for part in parsed):
            raise ValueError("rights admite read,write,delete separados por coma")
        parsed_set = frozenset(parsed)
        if "delete" in parsed_set and "write" not in parsed_set:
            raise ValueError("delete exige write")
        return cls(client=client, rights=parsed_set)

    @classmethod
    def from_environment(cls) -> "ConnectorPolicy":
        return cls.from_values(
            os.environ.get("SYMEM_CLIENT", "local-mcp"),
            os.environ.get("SYMEM_RIGHTS", "read,write"),
        )

    def permits(self, right: str) -> bool:
        return right in self.rights

    def require(self, right: str) -> None:
        if not self.permits(right):
            raise PermissionError(f"Permiso {right} denegado para conector {self.client}")
