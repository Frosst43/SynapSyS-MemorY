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

from .secureio import strict_json_loads, read_private

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
class MemoryBinding:
    # Trusted host paths. Never accepted from the child process or its environment.
    data_socket: Path
    token_file: Path
    space: str


@dataclass(frozen=True)
class ProjectSandbox:
    project_id: str
    workspace: Path
    read_only: bool
    memory: MemoryBinding | None = None


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
    system_trees = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/proc", "/sys", "/dev", "/boot", "/run")
    if (real.as_posix() in _UNSAFE_ROOTS or real == Path.home().resolve()
            or any(real.is_relative_to(Path(base).resolve()) for base in system_trees)):
        raise SandboxError("Workspace demasiado amplio o reservado para el sistema")
    if real != path:
        # Rutas con .. o componentes intermedios redirigidos; exigir forma canonica.
        raise SandboxError("Workspace no canonico: evita symlinks y componentes ..")
    _reject_nested_mounts(real)
    return real


def _audit_workspace(workspace: Path) -> None:
    """Preflight only: host processes must not mutate this tree during launch.

    A hardlink to a foreign file remains the same inode through a bind mount.
    Special files could expose host sockets/devices despite network isolation.
    """
    count = 0
    def fail(exc):
        raise SandboxError("Workspace tree cannot be inspected") from exc
    for folder, dirs, files in os.walk(workspace, followlinks=False, onerror=fail):
        for name in dirs + files:
            count += 1
            if count > 200_000:
                raise SandboxError("Workspace audit limit exceeded")
            item = Path(folder) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not item.resolve(strict=False).is_relative_to(workspace):
                    raise SandboxError("Workspace symlink escapes its project")
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise SandboxError("Workspace contains hardlinked files")
            elif not stat.S_ISDIR(info.st_mode):
                raise SandboxError("Workspace contains socket, FIFO or device")


def _trusted_memory_path(value: str, *, socket_file: bool) -> Path:
    """Check trusted host-side broker socket or admin-provisioned token file."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise SandboxError("La ruta de memoria debe ser absoluta")
    path = Path(value)
    try:
        real = path.resolve(strict=True)
        st = path.lstat()
        parent = path.parent.stat()
    except OSError as exc:
        raise SandboxError("Ruta de memoria inexistente o inaccesible") from exc
    if real != path or path.is_symlink():
        raise SandboxError("Ruta de memoria no canonica o con symlink")
    if stat.S_IMODE(parent.st_mode) & 0o022:
        raise SandboxError("Directorio de credenciales modificable por terceros")
    if socket_file:
        if not stat.S_ISSOCK(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o007:
            raise SandboxError("El socket de datos debe ser Unix y no accesible a otros")
    else:
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise SandboxError("La credencial debe ser un archivo regular sin hardlinks")
        if st.st_uid not in (os.geteuid(), 0) or stat.S_IMODE(st.st_mode) & 0o077:
            raise SandboxError("Credencial no protegida (requiere propietario y 0600)")
    return real


def load_policy(policy_path: str | Path, project_id: str) -> ProjectSandbox:
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id) or project_id in (".", ".."):
        raise SandboxError("Identificador de proyecto no valido")
    path = Path(policy_path)
    _config_file(path)
    try:
        config: Any = strict_json_loads(read_private(path, limit=1024 * 1024))
    except (OSError, ValueError, UnicodeError) as exc:
        raise SandboxError("No se puede leer la politica de proyectos") from exc
    if not isinstance(config, dict) or set(config) != {"schema_version", "projects"} or type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise SandboxError("Version o campos de politica no admitidos")
    projects = config["projects"]
    if not isinstance(projects, dict) or project_id not in projects:
        raise SandboxError("El proyecto no esta autorizado en esta politica")
    required = {"workspace", "read_only"}
    workspaces: dict[str, Path] = {}
    for name, definition in projects.items():
        if (not isinstance(name, str) or not _PROJECT_ID.fullmatch(name)
                or not isinstance(definition, dict)
                or not required <= set(definition)
                or not set(definition) <= required | {"memory"}
                or type(definition["read_only"]) is not bool):
            raise SandboxError("Politica del proyecto incompleta o ambigua")
        candidate = _workspace(definition["workspace"])
        if path.is_relative_to(candidate):
            raise SandboxError("Policy is inside a registered project")
        for previous in workspaces.values():
            if candidate.is_relative_to(previous) or previous.is_relative_to(candidate):
                raise SandboxError("Project workspaces overlap; combined projects are not enabled")
        workspaces[name] = candidate
    item = projects[project_id]
    ws = workspaces[project_id]
    _audit_workspace(ws)
    binding = None
    if "memory" in item:
        memory = item["memory"]
        if not isinstance(memory, dict) or set(memory) != {"data_socket", "token_file", "space"}:
            raise SandboxError("Memoria: se exigen exactamente data_socket, token_file y space")
        if not isinstance(memory["space"], str) or not _PROJECT_ID.fullmatch(memory["space"]) or memory["space"] in (".", ".."):
            raise SandboxError("Espacio de memoria no valido")
        sock = _trusted_memory_path(memory["data_socket"], socket_file=True)
        token = _trusted_memory_path(memory["token_file"], socket_file=False)
        if sock == token:
            raise SandboxError("Socket y credencial deben estar separados")
        for other in projects.values():
            if not isinstance(other, dict) or not isinstance(other.get("workspace"), str):
                raise SandboxError("Definicion de proyecto invalida")
            other_ws = _workspace(other["workspace"])
            if sock.is_relative_to(other_ws) or token.is_relative_to(other_ws):
                raise SandboxError("Credenciales de memoria dentro de un workspace")
        binding = MemoryBinding(sock, token, memory["space"])
    result = ProjectSandbox(project_id, ws, item["read_only"], binding)
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
    ]
    if spec.memory is not None:
        # Bind precisely the data socket and single-project token, never
        # the parent directory (which may contain admin.sock / SQLite).
        args += [
            "--dir", "/run/synapsys-memory",
            "--ro-bind", str(spec.memory.data_socket), "/run/synapsys-memory/data.sock",
            "--ro-bind", str(spec.memory.token_file), "/run/synapsys-memory/session.token",
            "--setenv", "SYMEM_BROKER_SOCKET", "/run/synapsys-memory/data.sock",
            "--setenv", "SYMEM_SESSION_TOKEN_FILE", "/run/synapsys-memory/session.token",
        ]
    args += [
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


def _check_memory_session(spec: ProjectSandbox) -> None:
    """Fail closed on wrong project/space grants; broker supplies identity."""
    if spec.memory is None:
        return
    from .broker import BrokerClient
    from .session_secret import read_token_file
    try:
        token = read_token_file(spec.memory.token_file)
        actual = BrokerClient(spec.memory.data_socket, token).profile()
    except (OSError, ValueError, PermissionError, RuntimeError) as exc:
        raise SandboxError("No se puede verificar el permiso de memoria del proyecto") from exc
    if actual.get("project") != spec.project_id or actual.get("space") != spec.memory.space:
        raise SandboxError("Permiso de memoria asignado a un proyecto/espacio distinto")


def run(policy: str | Path, project_id: str, program: list[str], *, dry_run: bool = False) -> int | list[str]:
    # Policy verification takes place before even looking for bwrap.
    spec = load_policy(policy, project_id)
    bwrap = "/usr/bin/bwrap" if dry_run else _trusted_bwrap()
    cmd = build_command(spec, program, bwrap=bwrap)
    if dry_run:
        return cmd
    _check_memory_session(spec)
    try:
        return subprocess.run(cmd, check=False, close_fds=True).returncode
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
