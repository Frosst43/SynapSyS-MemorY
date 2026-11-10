"""Universal sandbox/broker bridge tests; no model, network or user projects."""
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest

from synapsys_memory.sandbox import SandboxError, build_command, load_policy, run


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symem-bridge-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "mobile-app"
        self.foreign = self.root / "foreign-app"
        self.workspace.mkdir()
        self.foreign.mkdir()
        self.socket = self.root / "data.sock"
        self.socket_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket_srv.bind(str(self.socket))
        self.socket_srv.listen()
        self.addCleanup(self.socket_srv.close)
        self.socket.chmod(0o600)
        self.token = self.root / "session.token"
        self.token.write_text("demo-token-example-private-123456789012345678901234")
        self.token.chmod(0o600)
        self.policy = self.root / "policy.json"
        self.persist_policy()

    def persist_policy(self, memory=None):
        if memory is None:
            memory={"data_socket":str(self.socket),"token_file":str(self.token),"space":"pilot"}
        value={"schema_version":1,"projects":{
            "mobile-app":{"workspace":str(self.workspace),"read_only":False,"memory":memory},
            "foreign-app":{"workspace":str(self.foreign),"read_only":True}
        }}
        self.policy.write_text(json.dumps(value))
        self.policy.chmod(0o600)

    def test_binding_only_mounts_data_socket_and_token(self):
        spec=load_policy(self.policy,"mobile-app")
        cmd=build_command(spec,["python3","-V"],bwrap="/usr/bin/bwrap")
        s=" ".join(cmd)
        self.assertIn("/run/synapsys-memory/data.sock",cmd)
        self.assertIn("/run/synapsys-memory/session.token",cmd)
        self.assertIn("SYMEM_SESSION_TOKEN_FILE",cmd)
        self.assertNotIn("admin.sock",s)
        self.assertNotIn("sqlite3",s)
        self.assertNotIn(self.token.read_text(),s)
        self.assertIn("--unshare-net",cmd)
        self.assertNotIn(str(self.foreign),cmd)

    def test_old_policy_without_memory_works_and_sees_no_broker(self):
        policy={"schema_version":1,"projects":{"mobile-app":{
            "workspace":str(self.workspace),"read_only":False}}}
        self.policy.write_text(json.dumps(policy))
        self.policy.chmod(0o600)
        cmd=run(self.policy,"mobile-app",["python3","-V"],dry_run=True)
        self.assertNotIn("SYMEM_SESSION_TOKEN_FILE",cmd)
        self.assertNotIn(str(self.socket),cmd)

    def test_missing_data_socket_rejected(self):
        self.socket_srv.close()
        self.socket.unlink()
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_other_readable_socket_rejected(self):
        self.socket.chmod(0o666)
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_token_world_readable_rejected(self):
        self.token.chmod(0o644)
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_token_inside_workspace_rejected(self):
        token=self.workspace/"secret.token"
        token.write_text("irrelevant");token.chmod(0o600)
        self.persist_policy({"data_socket":str(self.socket),"token_file":str(token),"space":"pilot"})
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_token_inside_foreign_workspace_rejected(self):
        token=self.foreign/"secret.token"
        token.write_text("irrelevant");token.chmod(0o600)
        self.persist_policy({"data_socket":str(self.socket),"token_file":str(token),"space":"pilot"})
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_socket_in_project_workspace_rejected(self):
        self.socket_srv.close();self.socket.unlink()
        new=self.workspace/"data.sock"
        srv=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        srv.bind(str(new));srv.listen();self.addCleanup(srv.close)
        new.chmod(0o600)
        self.persist_policy({"data_socket":str(new),"token_file":str(self.token),"space":"pilot"})
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_socket_symlink_rejected(self):
        link=self.root/"linked.sock"
        link.symlink_to(self.socket)
        self.persist_policy({"data_socket":str(link),"token_file":str(self.token),"space":"pilot"})
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_extra_memory_settings_rejected(self):
        self.persist_policy({"data_socket":str(self.socket),"token_file":str(self.token),"space":"pilot","admin_socket":str(self.socket)})
        with self.assertRaises(SandboxError):load_policy(self.policy,"mobile-app")

    def test_denied_unknown_project(self):
        with self.assertRaises(SandboxError):load_policy(self.policy,"unregistered-program")

    def test_no_secret_leak_in_command(self):
        self.assertNotIn(self.token.read_text(),str(run(self.policy,"mobile-app",["python3","-V"],dry_run=True)))


# These preflight tests use a fake broker client, not a live broker.
class BrokerGrantPreflightTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    persist_policy = BridgeTests.persist_policy
    def test_fail_closed_on_mismatched_project(self):
        from synapsys_memory.sandbox import _check_memory_session
        from unittest.mock import patch
        import sys
        from types import SimpleNamespace
        spec=load_policy(self.policy,'mobile-app')
        fake=SimpleNamespace(BrokerClient=lambda *a: SimpleNamespace(profile=lambda: {'project':'synapsys','space':'pilot'}))
        with patch.dict(sys.modules,{'synapsys_memory.broker':fake}):
            with self.assertRaises(SandboxError):_check_memory_session(spec)

    def test_fail_closed_on_mismatched_space(self):
        from synapsys_memory.sandbox import _check_memory_session
        from unittest.mock import patch
        import sys
        from types import SimpleNamespace
        spec=load_policy(self.policy,'mobile-app')
        fake=SimpleNamespace(BrokerClient=lambda *a: SimpleNamespace(profile=lambda: {'project':'mobile-app','space':'other'}))
        with patch.dict(sys.modules,{'synapsys_memory.broker':fake}):
            with self.assertRaises(SandboxError):_check_memory_session(spec)

    def test_matching_project_and_space_pass(self):
        from synapsys_memory.sandbox import _check_memory_session
        from unittest.mock import patch
        import sys
        from types import SimpleNamespace
        spec=load_policy(self.policy,'mobile-app')
        fake=SimpleNamespace(BrokerClient=lambda *a: SimpleNamespace(profile=lambda: {'project':'mobile-app','space':'pilot'}))
        with patch.dict(sys.modules,{'synapsys_memory.broker':fake}):
            _check_memory_session(spec)


if __name__=="__main__":unittest.main()
