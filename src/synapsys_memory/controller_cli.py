"""Aprobación de proyectos desde la terminal de confianza del propietario."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import select
import signal
import stat
import subprocess
import sys
import tempfile
from typing import TextIO

from .broker import admin_request
from .broker_cli import read_admin_key_file
from .client_terminal import OPENCODE_COMMAND, client_pty
from .managed_session import ExclusiveSessionController, ManagedSandbox, SessionGrant
from .opencode_runtime import (
    PILOT_BINARY, configuration, native_binary, private_write, repository_runtime, runtime_arguments,
)
from .policy import ConnectorPolicy
from .runtime_bridge import RUNTIME_PORT, SANDBOX_SOCKET, RuntimeBridge, validate_model, validate_port
from .sandbox import (
    MemoryBinding, ProjectSandbox, SandboxError, _check_memory_session, _trusted_bwrap,
    _trusted_memory_path, build_command, load_policy,
)
from .store import _scope

CLIENT = "owner-controller"


def _issue(
    spec: ProjectSandbox,
    token_dir: Path,
    *,
    data: Path,
    admin: Path,
    key: str,
    space: str,
    rights: str,
) -> SessionGrant:
    session = "owner-" + secrets.token_hex(12)
    token_file = token_dir / (session + ".token")

    def revoke() -> None:
        try:
            # A grant that already expired or vanished after a restart is revoked as well.
            admin_request(admin, key, "revoke", session=session)
        finally:
            token_file.unlink(missing_ok=True)

    grant = None
    try:
        grant = admin_request(
            admin, key, "grant", session=session, client=CLIENT,
            space=space, project=spec.project_id, rights=rights, peer_uid=os.geteuid(),
        )
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(grant["token"])
        binding = MemoryBinding(data, token_file, space)
        return SessionGrant(spec.project_id, revoke, replace(spec, memory=binding))
    except BaseException as exc:
        # An explicit refusal installs no grant. An interrupted response may have done so.
        if grant is not None or not isinstance(exc, (PermissionError, ValueError)):
            revoke()
        raise


def _prompt(reader: TextIO, writer: TextIO, text: str, active: ManagedSandbox | None) -> str | None:
    print(text, end="", file=writer, flush=True)
    if active is not None:
        while active.process.poll() is None:
            if select.select([reader], [], [], 0.2)[0]:
                break
        else:
            return None
    return reader.readline()


def run_terminal(
    policy: Path,
    program: list[str],
    *,
    space: str,
    rights: str,
    reader: TextIO,
    writer: TextIO,
    opencode: Path | None = None,
    interactive: Path | None = None,
    runtime_model: str | None = None,
    runtime_port: int = RUNTIME_PORT,
) -> None:
    space = _scope(space, "space")
    if runtime_model is not None:
        if opencode is None:
            raise ValueError("El runtime local requiere --opencode")
        validate_model(runtime_model)
        validate_port(runtime_port)
    rights = ",".join(sorted(ConnectorPolicy.from_values(CLIENT, rights).rights))
    bwrap = _trusted_bwrap()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.geteuid()}")
    info = runtime.lstat()
    if (runtime.resolve(strict=True) != runtime or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise SandboxError("El directorio de ejecución del usuario debe ser privado y canónico")
    sockets = runtime / "synapsys-memory-broker"
    data = _trusted_memory_path(str(sockets / "data.sock"), socket_file=True)
    admin = _trusted_memory_path(str(sockets / "admin.sock"), socket_file=True)
    if data.samefile(admin):
        raise SandboxError("Los sockets de datos y administración deben ser distintos")

    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "synapsys-memory"
    key = read_admin_key_file(config / "broker-admin.key")
    admin_request(admin, key, "sessions")
    protected = (
        runtime, config.resolve(), (home / ".local/state/synapsys-memory").resolve(),
        (home / ".local/share/synapsys-memory").resolve(),
    )
    if interactive is not None:
        if opencode is None or program:
            raise ValueError("Use --interactive con --opencode y sin un comando adicional")
        program = [*OPENCODE_COMMAND, PILOT_BINARY]

    if opencode is not None:
        binary = native_binary(opencode)
        venv, source = repository_runtime()
        if any(path.is_relative_to(mount) or mount.is_relative_to(path)
               for path in protected for mount in (venv, source)):
            raise SandboxError("El runtime de OpenCode se solapa con una ruta privada del controlador")
        protected += (binary, venv, source)

    controller = ExclusiveSessionController()
    active = None
    with tempfile.TemporaryDirectory(prefix="symem-controller-", dir=runtime) as temporary:
        token_dir = Path(temporary)
        managed_config = token_dir / "opencode.json"
        if opencode is not None:
            private_write(managed_config, json.dumps(configuration(
                str(venv / "bin/python"), runtime_model=runtime_model,
            )))

        def launch(grant: SessionGrant) -> ManagedSandbox:
            spec = grant.private
            _check_memory_session(spec)
            argv = build_command(spec, program, bwrap=bwrap)
            if opencode is not None:
                argv = runtime_arguments(argv, config=managed_config, binary=binary, venv=venv, source=source)
                split = argv.index("--")
                argv[split:split] = ["--setenv", "OPENCODE_DISABLE_MODELS_FETCH", "1"]
            if runtime_model is not None:
                bridge = RuntimeBridge(spec.memory.token_file.with_suffix(".sock"), runtime_port, runtime_model)
                revoke_memory = grant.revoke

                def revoke():
                    try:
                        bridge.close()
                    finally:
                        revoke_memory()

                grant.revoke = revoke
                bridge.start()
                split = argv.index("--")
                argv[split:split + 1] = [
                    "--ro-bind", str(bridge.path), SANDBOX_SOCKET, "--",
                    str(venv / "bin/python"), "-IB", "-m", "synapsys_memory.runtime_bridge", "client", "--",
                ]
            if interactive is not None:
                split = argv.index("--")
                argv[split:split] = ["--setenv", "TERM", "xterm-256color", "--unsetenv", "NO_COLOR"]
                with client_pty(interactive, reader.fileno()) as terminal:
                    return ManagedSandbox.start(argv, stdin=terminal, output=terminal)
            return ManagedSandbox.start(argv, output=subprocess.DEVNULL)

        try:
            while True:
                if active is not None and active.process.poll() is not None:
                    project = controller.project
                    controller.close()
                    active = None
                    print(f"\nEl sandbox de {project} terminó.", file=writer, flush=True)
                current = controller.project
                choice = _prompt(reader, writer, "Proyecto de destino (:salir para cerrar) ", active)
                if choice is None:
                    continue
                if not choice or choice.strip() == ":salir":
                    return
                try:
                    target = _scope(choice.strip(), "project")
                except ValueError as exc:
                    print(str(exc), file=writer, flush=True)
                    continue
                if target == current:
                    print(f"Proyecto activo {current}.", file=writer, flush=True)
                    continue
                question = f"¿Cambiar de {current} a {target}?" if current else f"¿Activar {target}?"
                reply = _prompt(reader, writer, question + " [s/N] ", active)
                if reply is None:
                    continue
                if not reply:
                    return
                if reply.strip().casefold() not in {"s", "si", "sí"}:
                    status = f"Proyecto activo {current}." if current else "La sesión está vacía."
                    print(f"Cambio denegado. {status}", file=writer, flush=True)
                    continue
                try:
                    spec = load_policy(policy, target, protected_paths=protected, workspace_only=True)
                except (OSError, ValueError, SandboxError) as exc:
                    print(f"Proyecto no disponible. {exc}", file=writer, flush=True)
                    continue
                active = controller.activate(
                    target,
                    approve=lambda previous, new: (previous, new) == (current, target),
                    issue=lambda _: _issue(
                        spec, token_dir, data=data, admin=admin, key=key, space=space, rights=rights,
                    ),
                    launch=launch,
                )
                print(f"Proyecto activo {target}.", file=writer, flush=True)
        finally:
            controller.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True, help="Política privada de workspaces")
    parser.add_argument("--space", required=True, help="Espacio de memoria autorizado")
    parser.add_argument("--rights", default="read", help="Permisos del broker separados por coma (por defecto read)")
    parser.add_argument("--opencode", type=Path, help=f"Monta OpenCode V2 en {PILOT_BINARY}")
    parser.add_argument("--interactive", type=Path, metavar="SOCKET",
                        help="Usa la terminal separada de synapsys_memory.client_terminal")
    parser.add_argument("--runtime-model", metavar="MODELO", help="Autoriza un único modelo del runtime local")
    parser.add_argument("--runtime-port", type=int, metavar="PUERTO",
                        help=f"Puerto del runtime en 127.0.0.1 (por defecto {RUNTIME_PORT})")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- programa [argumentos]")
    args = parser.parse_args(argv)
    program = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.interactive is not None and (args.opencode is None or program):
        parser.error("Use --interactive con --opencode y sin un comando adicional")
    if not program and args.interactive is None:
        parser.error("Indique el programa para ejecutar dentro de Bubblewrap")
    if args.runtime_model is not None and args.opencode is None:
        parser.error("--runtime-model requiere --opencode")
    if args.runtime_port is not None and args.runtime_model is None:
        parser.error("--runtime-port requiere --runtime-model")

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        with open("/dev/tty", encoding="utf-8") as reader, open("/dev/tty", "w", encoding="utf-8") as writer:
            run_terminal(
                args.policy, program, space=args.space, rights=args.rights,
                reader=reader, writer=writer, opencode=args.opencode,
                interactive=args.interactive,
                runtime_model=args.runtime_model,
                runtime_port=RUNTIME_PORT if args.runtime_port is None else args.runtime_port,
            )
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Controlador detenido. {exc}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
