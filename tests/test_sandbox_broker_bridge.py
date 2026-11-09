"""Aislamiento universal del puente al broker; todo con sockets temporales."""
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import sys
from synapsys_memory.sandbox import SandboxError, load_policy, build_command, run, _check_memory_session


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="symem-bridge-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a = self.root / "arbitrary-app-a"
        self.b = self.root / "arbitrary-app-b"
        self.a.mkdir(); self.b.mkdir()
        self.sock = self.root / "data.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.sock)); self.listener.listen()
        self.addCleanup(self.listener.close)
        self.sock.chmod(0o600)
        self.token = self.root / "session.token"
        self.token.write_text("A"*48)
        self.token.chmod(0o600)
        self.cfg = self.root / "policy.json"
        self.write_policy()

    def memory(self, **changes):
        v = {"data_socket": str(self.sock), "token_file": str(self.token), "space": "pilot"}
        v.update(changes)
        return v

    def write_policy(self, memory="default"):
        project = {"workspace": str(self.a), "read_only": False}
        if memory == "default":
            project["memory"] = self.memory()
        elif memory is not None:
            project["memory"] = memory
        self.cfg.write_text(json.dumps({"schema_version": 1, "projects": {
            "arbitrary-app-a": project,
            "arbitrary-app-b": {"workspace": str(self.b), "read_only": True}
        }}))
        self.cfg.chmod(0o600)

    def test_mounts_only_data_socket_and_private_token(self):
        spec = load_policy(self.cfg, "arbitrary-app-a")
        argv = build_command(spec, ["python3", "-V"], bwrap="/usr/bin/bwrap")
        self.assertIn(str(self.sock), argv)
        self.assertIn(str(self.token), argv)
        self.assertIn("SYMEM_SESSION_TOKEN_FILE", argv)
        self.assertNotIn(self.token.read_text(), str(argv))
        self.assertNotIn(str(self.b), argv)
        self.assertNotIn("admin.sock", argv)
        self.assertIn("--unshare-net", argv)

    def test_old_policy_has_no_memory_exposure(self):
        self.write_policy(None)
        argv = run(self.cfg, "arbitrary-app-a", ["python3", "-V"], dry_run=True)
        self.assertNotIn("SYMEM_SESSION_TOKEN_FILE", argv)
        self.assertNotIn(str(self.sock), argv)

    def test_missing_project_denied(self):
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "unknown-project-2090")

    def test_credential_cannot_be_readable_by_others(self):
        self.token.chmod(0o644)
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_socket_not_accessible_to_others(self):
        self.sock.chmod(0o666)
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_socket_must_exist(self):
        self.listener.close(); self.sock.unlink()
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_socket_path_cannot_be_symlink(self):
        alias = self.root / "link.sock"
        alias.symlink_to(self.sock)
        self.write_policy(self.memory(data_socket=str(alias)))
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_token_cannot_be_inside_another_project(self):
        other_token = self.b / "token"
        other_token.write_text("A"*48)
        other_token.chmod(0o600)
        self.write_policy(self.memory(token_file=str(other_token)))
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_token_cannot_be_inside_current_project(self):
        other_token = self.a / "token"
        other_token.write_text("A"*48)
        other_token.chmod(0o600)
        self.write_policy(self.memory(token_file=str(other_token)))
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_memory_policy_cannot_include_admin_socket(self):
        self.write_policy(self.memory(admin_socket=str(self.sock)))
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "arbitrary-app-a")

    def test_wrong_broker_project_refused_before_launch(self):
        spec = load_policy(self.cfg, "arbitrary-app-a")
        fake = SimpleNamespace(BrokerClient=lambda *_: SimpleNamespace(profile=lambda: {
            "project": "arbitrary-app-b", "space": "pilot"}))
        with patch.dict(sys.modules, {"synapsys_memory.broker": fake}):
            with self.assertRaises(SandboxError):
                _check_memory_session(spec)

    def test_wrong_broker_space_refused_before_launch(self):
        spec = load_policy(self.cfg, "arbitrary-app-a")
        fake = SimpleNamespace(BrokerClient=lambda *_: SimpleNamespace(profile=lambda: {
            "project": "arbitrary-app-a", "space": "other"}))
        with patch.dict(sys.modules, {"synapsys_memory.broker": fake}):
            with self.assertRaises(SandboxError):
                _check_memory_session(spec)

    def test_matching_broker_profile_allows_launch(self):
        spec = load_policy(self.cfg, "arbitrary-app-a")
        fake = SimpleNamespace(BrokerClient=lambda *_: SimpleNamespace(profile=lambda: {
            "project": "arbitrary-app-a", "space": "pilot"}))
        with patch.dict(sys.modules, {"synapsys_memory.broker": fake}):
            _check_memory_session(spec)


if __name__ == "__main__":
    unittest.main()
