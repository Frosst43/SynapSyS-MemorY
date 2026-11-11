"""Experimental trusted controller, not an MCP tool or an authorization UI.

Linux pidfds refer to the PID-namespace init, not only to bwrap's monitor.
Only trusted host code may provide launch arguments and approval callbacks.
No subprocess is launched outside Bubblewrap when enforcement is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import threading
import time
from typing import Any, Callable

from .sandbox import SandboxError, _trusted_bwrap


class LifecycleError(RuntimeError):
    """Closure is not confirmed; do not grant another project."""


def _wait_readable(fd: int, timeout: float) -> bool:
    return bool(select.select([fd], [], [], max(0.0, timeout))[0])


def _read_info(fd: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        left = deadline - time.monotonic()
        if left <= 0 or not _wait_readable(fd, left):
            raise LifecycleError('Timed out waiting for sandbox identity')
        part = os.read(fd, 4096)
        if not part:
            break
        data.extend(part)
        if len(data) > 16384:
            raise LifecycleError('Sandbox identity response exceeded limit')
    try:
        info = json.loads(data)
        if not isinstance(info, dict) or type(info.get('child-pid')) is not int:
            raise ValueError('invalid PID field')
        return info
    except (ValueError, UnicodeError) as exc:
        raise LifecycleError('Invalid sandbox identity') from exc


def _validate_child(child: int, monitor: int) -> None:
    if child <= 1 or child in (os.getpid(), os.getppid(), monitor):
        raise LifecycleError('Invalid sandbox init PID')
    fields = {}
    for line in Path(f'/proc/{child}/status').read_text().splitlines():
        key, _, value = line.partition(':')
        fields[key] = value.strip()
    if int(fields.get('PPid', '0')) != monitor:
        raise LifecycleError('Sandbox init is not a child of our monitor')
    # The init of a fresh PID namespace is PID 1 within that namespace.
    if fields.get('NSpid', '').split()[-1:] != ['1']:
        raise LifecycleError('Reported process is not PID-namespace init')
    if os.readlink(f'/proc/{child}/ns/pid') == os.readlink('/proc/self/ns/pid'):
        raise LifecycleError('Sandbox did not get a separate PID namespace')


class ManagedSandbox:
    """Own a bwrap monitor plus a stable kernel handle to its namespace init.

    stop() force-stops the namespace; it does not save unsaved application
    state. Approval must happen in a trusted host UI before a project switch.
    """
    def __init__(self, process: subprocess.Popen, init_pidfd: int):
        self.process = process
        self._init_pidfd = init_pidfd
        self.stopped = False

    @classmethod
    def start(cls, argv: list[str], *, output, stdin=subprocess.DEVNULL, timeout: float = 10.0) -> 'ManagedSandbox':
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise SandboxError('Linux pidfds are required; no insecure fallback')
        binary = _trusted_bwrap()
        if not argv or argv[0] != binary or '--' not in argv:
            raise SandboxError('Managed launches require the trusted Bubblewrap binary')
        opts = argv[1:argv.index('--')]
        for flag in ('--unshare-pid', '--unshare-user', '--disable-userns', '--die-with-parent'):
            if flag not in opts:
                raise SandboxError('Missing required namespace/lifecycle flag')
        for flag in ('--as-pid-1', '--pidns', '--info-fd', '--block-fd', '--json-status-fd'):
            if flag in opts:
                raise SandboxError('Caller may not override lifecycle monitoring')
        info_r, info_w = os.pipe()
        gate_r, gate_w = os.pipe()
        proc = None
        handle = None
        try:
            command = [binary, '--info-fd', str(info_w), '--block-fd', str(gate_r), *argv[1:]]
            proc = subprocess.Popen(command, stdin=stdin, stdout=output,
                                    stderr=subprocess.STDOUT, close_fds=True,
                                    pass_fds=(info_w, gate_r), start_new_session=True,
                                    env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
            os.close(info_w); info_w = -1
            os.close(gate_r); gate_r = -1
            info = _read_info(info_r, timeout)
            child = info['child-pid']
            _validate_child(child, proc.pid)
            handle = os.pidfd_open(child, 0)
            # While blocked, revalidate parentage after opening the stable handle.
            _validate_child(child, proc.pid)
            if _wait_readable(handle, 0):
                raise LifecycleError('Sandbox died before release')
            os.write(gate_w, b'1')
            result = cls(proc, handle)
            handle = None
            return result
        except BaseException:
            # A closed block-fd means EOF, not denial! Kill first and only then
            # close the gate. Never release unverified application code.
            if handle is not None:
                try:
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                    _wait_readable(handle, timeout)
                except ProcessLookupError:
                    pass
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise LifecycleError('Failed launch could not be reaped') from exc
            raise
        finally:
            for fd in (info_r, info_w, gate_r, gate_w, handle):
                if fd is not None and fd >= 0:
                    os.close(fd)

    def wait(self, timeout: float = 30.0) -> int:
        """Wait for the main command only; close() must still stop descendants."""
        return self.process.wait(timeout=timeout)

    def stop(self, timeout: float = 5.0) -> None:
        if self.stopped:
            return
        if self._init_pidfd is None:
            raise LifecycleError('Lost namespace handle; closure not confirmed')
        try:
            if not _wait_readable(self._init_pidfd, 0):
                try:
                    signal.pidfd_send_signal(self._init_pidfd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not _wait_readable(self._init_pidfd, timeout):
                raise LifecycleError('Namespace init is still alive; switch denied')
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LifecycleError('Sandbox closure could not be confirmed') from exc
        os.close(self._init_pidfd)
        self._init_pidfd = None
        self.stopped = True


@dataclass
class SessionGrant:
    project: str
    revoke: Callable[[], None] = field(repr=False)
    private: Any = field(default=None, repr=False)


class ExclusiveSessionController:
    """One project; trusted approval -> revoke -> stop/reap -> grant -> launch.

    Callbacks belong to the trusted host controller, NEVER the model or MCP.
    A failed revocation/closure permanently blocks this controller instance.
    """
    def __init__(self):
        self._active: tuple[SessionGrant, ManagedSandbox] | None = None
        self._blocked = False
        self._lock = threading.RLock()

    @property
    def project(self) -> str | None:
        return self._active[0].project if self._active else None

    @property
    def blocked(self) -> bool:
        return self._blocked

    def _close(self) -> None:
        if self._active is None:
            return
        grant, process = self._active
        errors = []
        for operation in (grant.revoke, process.stop):
            try:
                operation()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            self._blocked = True
            raise LifecycleError('Revocation or closure failed; new project denied') from errors[0]
        self._active = None

    def activate(self, project: str, *, approve: Callable[[str | None, str], bool],
                 issue: Callable[[str], SessionGrant],
                 launch: Callable[[SessionGrant], ManagedSandbox]) -> ManagedSandbox:
        from .store import _scope
        project = _scope(project, 'project')
        with self._lock:
            if self._blocked:
                raise LifecycleError('Controller blocked; administrative recovery required')
            if approve(self.project, project) is not True:
                raise PermissionError('Project change not authorized by trusted controller')
            self._close()
            grant = issue(project)
            try:
                if not isinstance(grant, SessionGrant) or grant.project != project:
                    raise LifecycleError('Granted project differs from approved project')
                process = launch(grant)
            except BaseException:
                # A launch factory must clean up its own partially started child.
                # ManagedSandbox.start does this before it raises.
                self._blocked = True
                if isinstance(grant, SessionGrant):
                    grant.revoke()
                raise
            self._active = (grant, process)
            return process

    def close(self) -> None:
        with self._lock:
            self._close()
