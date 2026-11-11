"""Credential source regressions for the broker's normal CLI."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from synapsys_memory import broker_cli


class BrokerCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="symem-broker-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key_file = self.root / "admin.key"
        self.file_key = "file-key-" * 8
        self.env_key = "legacy-key-" * 8
        self.key_file.write_text(self.file_key + "\n", encoding="utf-8")
        self.key_file.chmod(0o600)
        self.socket = self.root / "admin.sock"

    def test_explicit_private_file_overrides_environment(self):
        args = ["--admin-key-file", str(self.key_file), "--admin-socket", str(self.socket), "sessions"]
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"SYMEM_ADMIN_KEY": self.env_key}),
            redirect_stdout(output),
            patch.object(broker_cli, "admin_request", return_value=[]) as request,
        ):
            self.assertEqual(broker_cli.main(args), 0)
        request.assert_called_once_with(self.socket, self.file_key, "sessions")
        self.assertEqual(output.getvalue(), "[]\n")

    def test_environment_key_remains_supported(self):
        args = ["--admin-socket", str(self.socket), "sessions"]
        with (
            patch.dict(os.environ, {"SYMEM_ADMIN_KEY": self.env_key}),
            redirect_stdout(io.StringIO()),
            patch.object(broker_cli, "admin_request", return_value=[]) as request,
        ):
            self.assertEqual(broker_cli.main(args), 0)
        request.assert_called_once_with(self.socket, self.env_key, "sessions")

    def test_unsafe_or_invalid_explicit_file_never_falls_back_to_environment(self):
        public_file = self.root / "public.key"
        public_file.write_text(self.file_key, encoding="utf-8")
        public_file.chmod(0o644)
        symlink = self.root / "link.key"
        symlink.symlink_to(self.key_file)
        malformed = self.root / "invalid.key"
        malformed.write_bytes(b"\xff" + self.file_key.encode())
        malformed.chmod(0o600)
        short = self.root / "short.key"
        short.write_text("short", encoding="utf-8")
        short.chmod(0o600)
        for path in (public_file, symlink, malformed, short, self.root / "missing.key"):
            with self.subTest(path=path.name):
                error = io.StringIO()
                with (
                    patch.dict(os.environ, {"SYMEM_ADMIN_KEY": self.env_key}),
                    redirect_stderr(error),
                    patch.object(broker_cli, "admin_request") as request,
                ):
                    with self.assertRaises(SystemExit) as failure:
                        broker_cli.main(["--admin-key-file", str(path), "sessions"])
                self.assertEqual(failure.exception.code, 2)
                request.assert_not_called()
                self.assertNotIn(self.file_key, error.getvalue())
                self.assertNotIn(self.env_key, error.getvalue())
