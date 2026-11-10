"""New controller tests. Kernel sandbox behavior is a separate physical gate."""
from pathlib import Path
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from synapsys_memory.managed_session import (
    ExclusiveSessionController, LifecycleError, ManagedSandbox, SessionGrant, _read_info, _validate_child,
)
from synapsys_memory.sandbox import SandboxError


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.controller=ExclusiveSessionController()
        self.processes={}
        self.grants={}
    def issue(self, project):
        self.events.append(('grant',project))
        grant=SessionGrant(project,lambda:self.events.append(('revoke',project)), 'secret-not-for-logs')
        self.grants[project]=grant
        return grant
    def launch(self, grant):
        self.events.append(('launch',grant.project))
        process=Mock()
        process.stop.side_effect=lambda:self.events.append(('stop',grant.project))
        self.processes[grant.project]=process
        return process
    def active(self, project='arbitrary-a', approve=lambda a,b:True):
        return self.controller.activate(project,approve=approve,issue=self.issue,launch=self.launch)
    def test_exact_revoke_stop_grant_order(self):
        self.active();self.active('other-project')
        self.assertEqual([('grant','arbitrary-a'),('launch','arbitrary-a'),('revoke','arbitrary-a'),
                          ('stop','arbitrary-a'),('grant','other-project'),('launch','other-project')],self.events)
        self.assertEqual('other-project',self.controller.project)
    def test_denied_switch_keeps_old_session_unchanged(self):
        self.active();before=list(self.events)
        with self.assertRaises(PermissionError):self.active('other',approve=lambda a,b:False)
        self.assertEqual(before,self.events);self.assertEqual('arbitrary-a',self.controller.project)
    def test_truthy_model_like_string_is_not_approval(self):
        with self.assertRaises(PermissionError):self.active(approve=lambda a,b:'yes')
        self.assertEqual([],self.events)
    def test_failed_close_never_grants_next_project(self):
        proc=self.active();proc.stop.side_effect=LifecycleError('unconfirmed')
        with self.assertRaises(LifecycleError):self.active('b')
        self.assertTrue(self.controller.blocked)
        self.assertNotIn(('grant','b'),self.events)
    def test_failed_revocation_still_attempts_to_kill_old_process(self):
        proc=self.active();self.grants['arbitrary-a'].revoke=Mock(side_effect=OSError('broker unreachable'))
        with self.assertRaises(LifecycleError):self.active('b')
        proc.stop.assert_called_once();self.assertTrue(self.controller.blocked)
        self.assertNotIn(('grant','b'),self.events)
    def test_blocked_controller_cannot_be_overridden_by_new_approval(self):
        proc=self.active();proc.stop.side_effect=LifecycleError('unconfirmed')
        with self.assertRaises(LifecycleError):self.active('b')
        approval=Mock(return_value=True)
        with self.assertRaises(LifecycleError):self.active('c',approve=approval)
        approval.assert_not_called()
    def test_launch_failure_revokes_new_grant_and_blocks_controller(self):
        with self.assertRaises(OSError):
            self.controller.activate('a',approve=lambda a,b:True,issue=self.issue,launch=Mock(side_effect=OSError('launch')))
        self.assertEqual([('grant','a'),('revoke','a')],self.events)
        self.assertIsNone(self.controller.project);self.assertTrue(self.controller.blocked)
    def test_wrong_project_returned_by_issuer_not_launched(self):
        launch=Mock();grant=SessionGrant('unapproved',Mock())
        with self.assertRaises(LifecycleError):
            self.controller.activate('a',approve=lambda a,b:True,issue=lambda _:grant,launch=launch)
        launch.assert_not_called();grant.revoke.assert_called_once()
    def test_close_is_idempotent_after_success(self):
        self.active();self.controller.close();before=list(self.events);self.controller.close()
        self.assertEqual(before,self.events);self.assertIsNone(self.controller.project)
    def test_secret_not_in_repr(self):
        self.assertNotIn('secret-not-for-logs',repr(self.issue('a')))
    def test_unknown_project_name_has_no_special_privileges(self):
        for name in ('new-app-2060','another-tool','parent/child'):
            self.active(name)
        self.controller.close()
        self.assertEqual(3,len([x for x in self.events if x[0]=='revoke']))


class MonitorTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Linux pidfds')
    def test_real_pidfd_for_own_temporary_child(self):
        proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        fd=None
        try:
            fd=os.pidfd_open(proc.pid)
            managed=ManagedSandbox(proc,fd)
            managed.stop(timeout=2)
            fd=None
            self.assertTrue(managed.stopped)
            self.assertEqual(-signal.SIGKILL,proc.returncode)
        finally:
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=2)
            if fd is not None:os.close(fd)
    def test_stop_waits_for_pid_namespace_not_main_process(self):
        proc=Mock();proc.poll.return_value=0
        managed=ManagedSandbox(proc,90)
        with patch('synapsys_memory.managed_session._wait_readable',side_effect=[False,True]), \
             patch('synapsys_memory.managed_session.signal.pidfd_send_signal') as kill, \
             patch('synapsys_memory.managed_session.os.close') as close:
            managed.stop()
        kill.assert_called_once_with(90,signal.SIGKILL);close.assert_called_once_with(90)
        proc.wait.assert_called_once();self.assertTrue(managed.stopped)
    def test_namespace_closure_timeout_keeps_handle_and_blocks(self):
        managed=ManagedSandbox(Mock(),90)
        with patch('synapsys_memory.managed_session._wait_readable',return_value=False), \
             patch('synapsys_memory.managed_session.signal.pidfd_send_signal'), \
             patch('synapsys_memory.managed_session.os.close') as close:
            with self.assertRaises(LifecycleError):managed.stop(timeout=0.01)
        self.assertFalse(managed.stopped);close.assert_not_called()
    def test_missing_bwrap_never_runs_program(self):
        with patch('synapsys_memory.managed_session._trusted_bwrap',side_effect=SandboxError('missing')), \
             patch('synapsys_memory.managed_session.subprocess.Popen') as popen:
            with self.assertRaises(SandboxError):ManagedSandbox.start(['/usr/bin/bwrap','--','true'],output=None)
        popen.assert_not_called()
    def test_missing_required_namespace_refused(self):
        with patch('synapsys_memory.managed_session._trusted_bwrap',return_value='/usr/bin/bwrap'):
            with self.assertRaises(SandboxError):ManagedSandbox.start(['/usr/bin/bwrap','--','true'],output=None)
    def test_own_pid_cannot_be_used_as_sandbox_init(self):
        with self.assertRaises(LifecycleError):_validate_child(os.getpid(),12345)
    def test_monitor_protocol_bounded_and_validated(self):
        for payload in (b'bad',b'{"child-pid":true}',b'{"child-pid":"2"}'):
            r,w=os.pipe()
            try:
                os.write(w,payload);os.close(w);w=-1
                with self.assertRaises(LifecycleError):_read_info(r,1)
            finally:
                os.close(r)
                if w>=0:os.close(w)
    def test_monitor_protocol_real_pipe(self):
        r,w=os.pipe()
        try:
            os.write(w,b'{"child-pid":2345}');os.close(w);w=-1
            self.assertEqual(2345,_read_info(r,1)['child-pid'])
        finally:
            os.close(r)
            if w>=0:os.close(w)


class OpenCodeProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parents[1]/'scripts/pilot_opencode_managed.py'
        spec=importlib.util.spec_from_file_location('symem_pilot_managed',path)
        cls.pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.pilot)
    def test_v2_config_uses_servers_not_v1_direct_entries(self):
        config=self.pilot.opencode_config('/private/venv/bin/python')
        self.assertNotIn('autoupdate',config)
        self.assertEqual({'servers'},set(config['mcp']))
        server=config['mcp']['servers']['synapsys-memory']
        self.assertEqual('local',server['type']);self.assertNotIn('enabled',server)
        self.assertIn('SYMEM_SESSION_TOKEN_FILE',server['environment'])
        self.assertNotIn('SYMEM_SESSION_TOKEN',server['environment'])
    def test_connection_parser_rejects_failure_and_disconnected(self):
        for text in ('synapsys-memory disconnected','synapsys-memory not connected','synapsys-memory failed',
                     'other-server connected','synapsys-memory connected error'):
            self.assertFalse(self.pilot.connected(text))
    def test_connection_parser_accepts_real_status_shape(self):
        self.assertTrue(self.pilot.connected('\x1b[32m✓ synapsys-memory  connected\x1b[0m'))
    def test_scripts_are_not_accepted_in_place_of_native_opencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'opencode';path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o700)
            with self.assertRaises(ValueError):self.pilot.trusted_binary(path)


if __name__=='__main__':unittest.main()
