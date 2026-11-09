"""Deterministic tests of the generic Linux sandbox launcher (no bwrap required)."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from synapsys_memory.sandbox import SandboxError, build_command, load_policy, main, run


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="symem-sandbox-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root_a = self.base / "programa-a"
        self.root_b = self.base / "programa-b"
        self.root_a.mkdir(); self.root_b.mkdir()
        self.cfg = self.base / "policy.json"
        self.write_config({"a": {"workspace": str(self.root_a), "read_only": False},
                           "b": {"workspace": str(self.root_b), "read_only": True}})

    def write_config(self, projects):
        self.cfg.write_text(json.dumps({"schema_version": 1, "projects": projects}), encoding="utf-8")
        self.cfg.chmod(0o600)

    def test_isolation_only_current_project_is_mounted(self):
        for name, target, foreign in (("a", self.root_a, self.root_b), ("b", self.root_b, self.root_a)):
            spec = load_policy(self.cfg, name)
            argv = build_command(spec, ["python3", "app.py"], bwrap="/usr/bin/bwrap")
            self.assertIn(str(target), argv)
            self.assertNotIn(str(foreign), argv)
            self.assertEqual("/workspace", argv[argv.index(str(target)) + 1])
            self.assertIn("--unshare-net", argv)
            self.assertIn("--clearenv", argv)
            self.assertIn("--disable-userns", argv)
            self.assertIn("--die-with-parent", argv)
            self.assertEqual(["python3", "app.py"], argv[-2:])
            flag = argv[argv.index(str(target)) - 1]
            self.assertEqual("--ro-bind" if spec.read_only else "--bind", flag)
            self.assertNotIn("--share-net", argv)

    def test_five_arbitrary_programs_same_policy(self):
        projects = {f"new-app-{n}": {"workspace": str(self.base / f"app-{n}"), "read_only": False}
                    for n in range(5)}
        for data in projects.values():
            Path(data["workspace"]).mkdir()
        self.write_config(projects)
        for name in projects:
            cmd = run(self.cfg, name, ["python3", "-V"], dry_run=True)
            self.assertEqual(1, sum(1 for value in projects.values() if value["workspace"] in cmd))

    def test_unknown_project_fails_closed(self):
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "proyecto-no-autorizado")

    def test_no_bwrap_denies_execution(self):
        with patch("synapsys_memory.sandbox._trusted_bwrap", side_effect=SandboxError("not installed")), \
             patch("synapsys_memory.sandbox.subprocess.run") as execute:
            with self.assertRaises(SandboxError):
                run(self.cfg, "a", ["python3", "-V"])
            execute.assert_not_called()

    def test_policy_cannot_be_inside_workspace(self):
        self.cfg = self.root_a / "sandbox-policy.json"
        self.write_config({"a": {"workspace": str(self.root_a), "read_only": False}})
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_bwrap_launch_failure_does_not_fallback(self):
        with patch("synapsys_memory.sandbox._trusted_bwrap", return_value="/usr/bin/bwrap"), \
             patch("synapsys_memory.sandbox.subprocess.run", side_effect=OSError("namespace denied")):
            with self.assertRaises(SandboxError):
                run(self.cfg, "a", ["python3", "-V"])

    def test_subprocess_only_runs_bwrap(self):
        class Result:
            returncode = 17
        with patch("synapsys_memory.sandbox._trusted_bwrap", return_value="/usr/bin/bwrap"), \
             patch("synapsys_memory.sandbox.subprocess.run", return_value=Result()) as execute:
            self.assertEqual(17, run(self.cfg, "a", ["sh", "-c", "printf test"]))
            command = execute.call_args.args[0]
            self.assertEqual("/usr/bin/bwrap", command[0])
            self.assertNotIn("shell", execute.call_args.kwargs)

    def test_policy_mode_strict(self):
        self.cfg.chmod(0o644)
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_policy_symlink_refused(self):
        link = self.base / "fake.json"
        link.symlink_to(self.cfg)
        with self.assertRaises(SandboxError):
            load_policy(link, "a")

    def test_workspace_symlink_refused(self):
        link = self.base / "alias-a"
        link.symlink_to(self.root_a)
        self.write_config({"a": {"workspace": str(link), "read_only": False}})
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_nested_mounts_are_rejected(self):
        # Simulate a mountpoint inside a project; never bind such workspace.
        fake = f"41 30 0:31 / {self.root_a}/foreign \n".replace("\\n", "\n")
        with patch("synapsys_memory.sandbox._mountinfo_lines", return_value=fake.splitlines()):
            with self.assertRaises(SandboxError):
                load_policy(self.cfg, "a")

    def test_workspace_root_denied(self):
        self.write_config({"a": {"workspace": "/", "read_only": False}})
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_workspace_home_denied(self):
        self.write_config({"a": {"workspace": str(Path.home()), "read_only": False}})
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_noncanonical_workspace_refused(self):
        path = str(self.root_a / ".." / "programa-b")
        self.write_config({"a": {"workspace": path, "read_only": False}})
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_extra_fields_rejected(self):
        self.write_config({"a": {"workspace": str(self.root_a), "read_only": False, "network": True}})
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "a")

    def test_duplicate_clarification_no_inheritance(self):
        self.write_config({"parent/child": {"workspace": str(self.root_a), "read_only": False}})
        self.assertEqual("parent/child", load_policy(self.cfg, "parent/child").project_id)
        with self.assertRaises(SandboxError):
            load_policy(self.cfg, "parent")

    def test_empty_command_refused(self):
        spec = load_policy(self.cfg, "a")
        with self.assertRaises(SandboxError):
            build_command(spec, [], bwrap="/usr/bin/bwrap")

    def test_bwrap_requires_absolute_path(self):
        spec = load_policy(self.cfg, "a")
        with self.assertRaises(SandboxError):
            build_command(spec, ["python3"], bwrap="bwrap")

    def test_host_variables_not_copied(self):
        with patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "do-not-inherit"}):
            cmd = run(self.cfg, "a", ["python3", "-V"], dry_run=True)
            self.assertNotIn("do-not-inherit", str(cmd))
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", str(cmd))

    def test_cli_dry_run_has_no_side_effect(self):
        from contextlib import redirect_stdout
        from io import StringIO
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, main(["--policy", str(self.cfg), "--project", "a", "--dry-run", "--", "python3", "-V"]))
        self.assertIn("--unshare-net", out.getvalue())


if __name__ == "__main__":
    unittest.main()
