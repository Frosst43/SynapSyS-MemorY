"""Fail-closed Linux filesystem sandbox launcher; no model, network or GPU.

This is an *outer* trusted launcher. Never execute it with policies selected
by an untrusted agent. The target program and all descendants remain inside
Bubblewrap's namespaces; memory grants are handled independently by broker.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,95}$")
_SYSTEM_RUNTIME = ("/usr", "/bin", "/lib", "/lib64", "/sbin")
_UNSAFE_ROOTS = frozenset({
    "/", "/home", "/root", "/usr", "/bin", "/lib", "/lib64", "/sbin",
    "/etc", "/var", "/opt", "/run", "/tmp", "/mnt", "/media", "/proc",
    "/sys", "/dev", "/srv", "/boot",
})


class SandboxError(RuntimeError):
    """Invalid policy, unavailable enforcement or unsafe filesystem mapping."""


@dataclass(frozen=True)
class ProjectSandbox:
    project_id: str
    workspace: Path
    read_only: bool


def _config_file(path: Path) -> None:
    """Owner-only policy outside project; do not accept symlinked policies."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise SandboxError("La politica debe ser un archivo regular absoluto sin symlinks")
    if path.resolve(strict=True) != path:
        raise SandboxError("Ruta de politica no canonica")
    st = path.stat()
    if st.st_uid not in (os.geteuid(), 0) or stat.S_IMODE(st.st_mode) & 0o077:
        raise SandboxError("La politica debe pertenecer al administrador y tener modo 0600")
    parent = path.parent
    if parent.is_symlink():
        raise SandboxError("Directorio de politica con symlink")
    pst = parent.stat()
    if pst.st_uid not in (os.geteuid(), 0) or (pst.st_mode & 0o022):
        raise SandboxError("Directorio de politica no confiable")


def _mountinfo_lines() -> list[str]:
    return Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()


def _reject_nested_mounts(workspace: Path) -> None:
    """Avoid importing arbitrary mounted filesystems recursively into a workspace."""
    try:
        lines = _mountinfo_lines()
    except OSError as exc:
        raise SandboxError("No puedo revisar montajes del sistema") from exc
    prefix = workspace.as_posix().rstrip("/") + "/"
    for line in lines:
        fields = line.split(" ", 5)
        if len(fields) < 5:
            raise SandboxError("Formato invalido de mountinfo")
        raw = fields[4]
        mountpoint = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), raw)
        if mountpoint.startswith(prefix):
            raise SandboxError("El workspace contiene un montaje anidado: " + mountpoint)


def _workspace(value: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise SandboxError("El workspace debe tener ruta absoluta")
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise SandboxError("El workspace debe ser directorio real, sin symlink en la raiz")
    real = path.resolve(strict=True)
    if real.as_posix() in _UNSAFE_ROOTS or real == Path.home().resolve():
        raise SandboxError("Workspace demasiado amplio o reservado para el sistema")
    if real != path:
        # Rutas con .. o componentes intermedios redirigidos; exigir forma canonica.
        raise SandboxError("Workspace no canonico: evita symlinks y componentes ..")
    _reject_nested_mounts(real)
    return real


def load_policy(policy_path: str | Path, project_id: str) -> ProjectSandbox:
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id) or project_id in (".", ".."):
        raise SandboxError("Identificador de proyecto no valido")
    path = Path(policy_path)
    _config_file(path)
    try:
        config: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise SandboxError("No se puede leer la politica de proyectos") from exc
    if not isinstance(config, dict) or set(config) != {"schema_version", "projects"} or type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise SandboxError("Version o campos de politica no admitidos")
    projects = config["projects"]
    if not isinstance(projects, dict) or project_id not in projects:
        raise SandboxError("El proyecto no esta autorizado en esta politica")
    item = projects[project_id]
    if not isinstance(item, dict) or set(item) != {"workspace", "read_only"}:
        raise SandboxError("Politica del proyecto incompleta o ambigua")
    if type(item["read_only"]) is not bool:
        raise SandboxError("read_only debe ser booleano")
    ws = _workspace(item["workspace"])
    if path.is_relative_to(ws):
        raise SandboxError("Politica ubicada dentro del workspace escribible")
    result = ProjectSandbox(project_id, ws, item["read_only"])
    # Una base de SQLite del broker no debe quedar dentro del workspace de agente.
    # Esa separacion se asegura al desplegar el broker bajo otro UID.
    return result


def build_command(spec: ProjectSandbox, program: list[str], *, bwrap: str) -> list[str]:
    """Assemble bwrap without shell, host home, privileged paths or network."""
    if not program or any(not isinstance(a, str) or "\x00" in a for a in program):
        raise SandboxError("Es necesario un comando valido")
    if not bwrap or not Path(bwrap).is_absolute():
        raise SandboxError("Bubblewrap debe indicarse con ruta absoluta")
    # Use the same trusted project binding for all programs, with no exceptions.
    args = [
        bwrap,
        "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-uts",
        "--unshare-net", "--disable-userns", "--new-session", "--die-with-parent",
        "--clearenv",
    ]
    for path in _SYSTEM_RUNTIME:
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--tmpfs", "/run", "--dir", "/workspace",
        "--ro-bind" if spec.read_only else "--bind", str(spec.workspace), "/workspace",
        "--setenv", "HOME", "/tmp", "--setenv", "TMPDIR", "/tmp",
        "--setenv", "XDG_CACHE_HOME", "/tmp/.cache",
        "--setenv", "XDG_CONFIG_HOME", "/tmp/.config",
        "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "LANG", "C.UTF-8", "--chdir", "/workspace",
        "--", *program,
    ]
    return args


def _trusted_bwrap() -> str:
    """Never use PATH: a user-controlled binary must not impersonate bwrap."""
    binary = Path("/usr/bin/bwrap")
    try:
        info = binary.stat()
    except OSError as exc:
        raise SandboxError("Falta /usr/bin/bwrap: instala bubblewrap") from exc
    if (not binary.is_file() or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022 or not os.access(binary, os.X_OK)):
        raise SandboxError("Binary bwrap no es confiable: debe pertenecer a root")
    return str(binary)


def run(policy: str | Path, project_id: str, program: list[str], *, dry_run: bool = False) -> int | list[str]:
    # Policy verification takes place before even looking for bwrap.
    spec = load_policy(policy, project_id)
    bwrap = "/usr/bin/bwrap" if dry_run else _trusted_bwrap()
    cmd = build_command(spec, program, bwrap=bwrap)
    if dry_run:
        return cmd
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:
        raise SandboxError("No se ha podido iniciar el aislamiento; no se ejecuta sin sandbox") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sandbox de archivos por proyecto (Linux, experimental)")
    parser.add_argument("--policy", required=True, help="JSON de politica de confianza, modo 0600")
    parser.add_argument("--project", required=True, help="Proyecto autorizado por la politica")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra argv; NO valida aislamiento real")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- comando [argumentos]")
    args = parser.parse_args(argv)
    program = args.command
    if program and program[0] == "--":
        program = program[1:]
    try:
        result = run(args.policy, args.project, program, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        return int(result)
    except (SandboxError, ValueError) as exc:
        print(f"Sandbox denegado: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
