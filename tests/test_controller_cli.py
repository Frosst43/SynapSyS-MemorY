"""Tests for the terminal controller's credential lifecycle."""

from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from synapsys_memory import controller_cli
from synapsys_memory.sandbox import ProjectSandbox


class ControllerCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="symem-controller-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = ProjectSandbox("project-a", self.root / "workspace", True)
        self.data = self.root / "data.sock"
        self.admin = self.root / "admin.sock"
        self.token = "synthetic_token_" * 3

    def issue(self):
        return controller_cli._issue(
            self.spec, self.root, data=self.data, admin=self.admin,
            key="synthetic-admin-key", space="test-space", rights="read",
        )

    def test_issued_token_is_private_and_removed_on_revoke(self):
        with patch.object(controller_cli, "admin_request", side_effect=[{"token": self.token}, {"revoked": True}]) as request:
            grant = self.issue()
            binding = grant.private.memory
            self.assertEqual(binding.data_socket, self.data)
            self.assertEqual(binding.space, "test-space")
            self.assertEqual(binding.token_file.read_text(), self.token)
            self.assertEqual(stat.S_IMODE(binding.token_file.stat().st_mode), 0o600)
            issued = request.call_args.kwargs
            self.assertEqual((issued["project"], issued["space"], issued["rights"]), ("project-a", "test-space", "read"))
            self.assertEqual(issued["peer_uid"], os.geteuid())
            grant.revoke()
            request.assert_called_with(self.admin, "synthetic-admin-key", "revoke", session=issued["session"])
            self.assertFalse(binding.token_file.exists())

    def test_token_creation_failure_revokes_the_grant(self):
        with (
            patch.object(controller_cli, "admin_request", side_effect=[{"token": self.token}, {"revoked": True}]) as request,
            patch.object(controller_cli.os, "open", side_effect=OSError("token file unavailable")),
            self.assertRaisesRegex(OSError, "token file unavailable"),
        ):
            self.issue()
        grant_call, revoke_call = request.call_args_list
        self.assertEqual((grant_call.args[2], revoke_call.args[2]), ("grant", "revoke"))
        self.assertEqual(grant_call.kwargs["session"], revoke_call.kwargs["session"])

    def test_explicit_grant_refusal_does_not_revoke_an_existing_session(self):
        with (
            patch.object(controller_cli, "admin_request", side_effect=ValueError("session exists")) as request,
            self.assertRaisesRegex(ValueError, "session exists"),
        ):
            self.issue()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[2], "grant")

    def test_missing_terminal_never_starts_the_controller(self):
        with (
            patch.object(controller_cli, "open", side_effect=OSError("no controlling terminal"), create=True),
            patch.object(controller_cli, "run_terminal") as run,
            redirect_stderr(io.StringIO()),
        ):
            code = controller_cli.main(["--policy", str(self.root / "policy.json"), "--space", "test", "--", "true"])
        self.assertEqual(code, 2)
        run.assert_not_called()
