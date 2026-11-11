from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import pty
import select
import socket
import tempfile
import unittest

from synapsys_memory.client_terminal import _device, client_pty


class ClientTerminalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "client.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(self.listener.close)
        self.listener.bind(str(self.path))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.approval = self.open_terminal()

    def open_terminal(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        return slave

    def receive_master(self, terminal):
        connection, _ = self.listener.accept()
        with connection:
            connection.settimeout(2)
            connection.sendall(_device(terminal))
            message, fds, _, _ = socket.recv_fds(connection, 8, 1)
        for fd in fds:
            self.addCleanup(os.close, fd)
        return message, fds

    def test_approval_terminal_is_rejected_before_passing_a_pty(self):
        with ThreadPoolExecutor(1) as executor:
            received = executor.submit(self.receive_master, self.approval)
            with self.assertRaisesRegex(ValueError, "terminal distinta"):
                with client_pty(self.path, self.approval):
                    self.fail("The approval terminal was accepted")
            self.assertEqual((b"", []), received.result(timeout=2))

    def test_second_terminal_receives_a_fresh_pty(self):
        frontend = self.open_terminal()
        with ThreadPoolExecutor(1) as executor:
            received = executor.submit(self.receive_master, frontend)
            with client_pty(self.path, self.approval) as slave:
                message, fds = received.result(timeout=2)
                self.assertEqual(b"PTY", message)
                master, = fds
                self.assertNotIn(_device(slave), (_device(frontend), _device(self.approval)))
                os.write(slave, b"OpenCode")
                self.assertEqual([master], select.select([master], [], [], 2)[0])
                self.assertEqual(b"OpenCode", os.read(master, 8))
