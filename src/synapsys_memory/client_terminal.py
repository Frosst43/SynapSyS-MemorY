"""Dedicated host terminal for an approved sandbox's private pseudoterminal."""

from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import socket
import struct
import tempfile
import termios
import tty


# Linux TIOCGDEV also resolves /dev/tty to its actual terminal device.
_TIOCGDEV = 0x80045432
_DEVICE = struct.Struct("=I")
_PEER = struct.Struct("=iii")

OPENCODE_COMMAND = [
    "/usr/bin/python3", "-IB", "-c",
    "import fcntl, os, sys, termios\n"
    "if os.getsid(0) != os.getpid(): os.setsid()\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "os.execv(sys.argv[1], sys.argv[1:])",
]


def _device(fd: int) -> bytes:
    return fcntl.ioctl(fd, _TIOCGDEV, bytes(_DEVICE.size))


def _check_peer(channel: socket.socket) -> None:
    _, uid, _ = _PEER.unpack(channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER.size))
    if uid != os.geteuid():
        raise PermissionError("La terminal debe pertenecer al mismo usuario")


@contextmanager
def client_pty(path: Path, approval_fd: int):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as channel:
        channel.settimeout(5)
        channel.connect(str(path))
        _check_peer(channel)
        device = channel.recv(_DEVICE.size + 1)
        if len(device) != _DEVICE.size or device == _device(approval_fd):
            raise ValueError("OpenCode requiere una terminal distinta de la de aprobación")
        master, slave = pty.openpty()
        try:
            termios.tcsetwinsize(slave, (24, 80))
            socket.send_fds(channel, [b"PTY"], [master])
            os.close(master)
            master = None
            yield slave
        finally:
            if master is not None:
                os.close(master)
            os.close(slave)


def _relay(terminal: int, master: int) -> None:
    saved = termios.tcgetattr(terminal)

    def resize(_signum=None, _frame=None):
        termios.tcsetwinsize(master, termios.tcgetwinsize(terminal))

    previous = signal.signal(signal.SIGWINCH, resize)
    try:
        resize()
        tty.setraw(terminal)
        while True:
            ready, _, _ = select.select([master, terminal], [], [])
            for source in ready:
                try:
                    data = os.read(source, 16384)
                except OSError as exc:
                    if source == master and exc.errno == errno.EIO:
                        return
                    raise
                if not data:
                    return
                destination = terminal if source == master else master
                pending = memoryview(data)
                while pending:
                    try:
                        pending = pending[os.write(destination, pending):]
                    except OSError as exc:
                        if destination == master and exc.errno == errno.EIO:
                            return
                        raise
    finally:
        signal.signal(signal.SIGWINCH, previous)
        termios.tcsetattr(terminal, termios.TCSANOW, saved)
        os.write(terminal, b"\x1b[?1049l\x1b[?25h\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[0m")


def serve_terminal(terminal: int, runtime: Path) -> None:
    device = _device(terminal)
    with tempfile.TemporaryDirectory(prefix="symem-tui-", dir=runtime) as directory:
        path = Path(directory) / "client.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen(1)
            print(f"Terminal de OpenCode preparada. Use --interactive {path}", flush=True)
            while True:
                channel, _ = listener.accept()
                with channel:
                    channel.settimeout(5)
                    fds = []
                    try:
                        _check_peer(channel)
                        channel.sendall(device)
                        message, fds, flags, _ = socket.recv_fds(channel, 8, 1)
                        if message != b"PTY" or len(fds) != 1 or flags & socket.MSG_CTRUNC:
                            continue
                        _relay(terminal, fds[0])
                    except (ConnectionError, TimeoutError):
                        continue
                    finally:
                        for fd in fds:
                            os.close(fd)
                print("\nEsperando al siguiente proyecto aprobado.", flush=True)


def main() -> None:
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGHUP, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.geteuid()}")
    terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    try:
        serve_terminal(terminal, runtime)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(terminal)


if __name__ == "__main__":
    main()
