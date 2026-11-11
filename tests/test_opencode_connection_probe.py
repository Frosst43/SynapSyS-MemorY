"""Configuration/probe regressions. These tests do not run OpenCode or models."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from synapsys_memory.sandbox import MemoryBinding, ProjectSandbox, build_command

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('opencode_connection_probe', ROOT / 'scripts/probe_opencode_mcp.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ConfigTests(unittest.TestCase):
    def test_server_registered_with_v2_schema(self):
        doc = probe.configuration('/runtime/bin/python')
        self.assertEqual({'synapsys-memory'}, set(doc['mcp']['servers']))
        server = doc['mcp']['servers']['synapsys-memory']
        self.assertEqual('local', server['type'])
        self.assertEqual('legacy', server['protocol'])
        self.assertEqual(['/runtime/bin/python', '-I', '-m', 'synapsys_memory.mcp_server'], server['command'])

    def test_no_provider_or_model_or_admin_credential(self):
        doc = probe.configuration('/runtime/bin/python')
        self.assertEqual('disable', doc['update'])
        self.assertEqual('disabled', doc['share'])
        self.assertNotIn('model', doc)
        self.assertNotIn('provider', doc)
        environment = doc['mcp']['servers']['synapsys-memory']['environment']
        self.assertEqual({'SYMEM_BROKER_SOCKET','SYMEM_SESSION_TOKEN_FILE'}, set(environment))
        self.assertNotIn('SYMEM_ADMIN_KEY', json.dumps(doc))

    def test_relative_python_denied(self):
        with self.assertRaises(ValueError): probe.configuration('python3')

    def args(self, net=True):
        base = ['/usr/bin/bwrap', '--clearenv', '--unshare-pid']
        if net: base.append('--unshare-net')
        base += ['--chdir', '/workspace', '--', probe.PILOT_BINARY, 'mcp', 'list']
        args = probe.runtime_arguments(base, config=Path('/fixture/config.json'), binary=Path('/fixture/opencode'),
                                       venv=Path('/runtime/.venv'), source=Path('/runtime/src'))
        return base, args

    def test_full_managed_config_readonly_and_before_separator(self):
        base, args = self.args()
        i = args.index(probe.MANAGED_CONFIG)
        self.assertEqual(['--ro-bind', '/fixture/config.json', probe.MANAGED_CONFIG], args[i-2:i+1])
        self.assertLess(i, args.index('--'))
        self.assertEqual(base[base.index('--'):], args[args.index('--'):])
        self.assertNotIn('/fixture/config.json', base)  # pure helper

    def test_ignored_legacy_env_removed(self):
        _, args = self.args()
        self.assertNotIn('OPENCODE_CONFIG', args)
        self.assertNotIn('OPENCODE_CONFIG_CONTENT', args)

    def test_network_isolation_required_and_preserved(self):
        with self.assertRaises(ValueError): self.args(net=False)
        _, args = self.args()
        self.assertIn('--unshare-net', args)
        self.assertNotIn('--share-net', args)

    def test_no_broad_host_mounts(self):
        _, args = self.args()
        for i, value in enumerate(args):
            if value in ('--bind', '--ro-bind'):
                self.assertNotIn(args[i+1], ('/', '/home', '/etc', str(Path.home())))
        self.assertEqual([probe.PILOT_BINARY, 'mcp', 'list'], args[args.index('--')+1:])
        self.assertNotIn('ollama', args)

    def test_runtime_paths_must_be_absolute(self):
        with self.assertRaises(ValueError):
            probe.runtime_arguments(['/usr/bin/bwrap','--unshare-net','--','program'],
                config=Path('relative'), binary=Path('/binary'), venv=Path('/venv'), source=Path('/src'))

    def test_runtime_keeps_only_the_active_broker_binding(self):
        data = Path('/broker/data.sock')
        token = Path('/controller/project-b.token')
        spec = ProjectSandbox('project-b', Path('/project-b'), True, MemoryBinding(data, token, 'space'))
        base = build_command(spec, [probe.PILOT_BINARY, 'mcp', 'list'], bwrap='/usr/bin/bwrap')
        args = probe.runtime_arguments(
            base, config=Path('/fixture/config.json'), binary=Path('/fixture/opencode'),
            venv=Path('/runtime/.venv'), source=Path('/runtime/src'),
        )
        readonly = {args[i + 2]: args[i + 1] for i, arg in enumerate(args) if arg == '--ro-bind'}
        self.assertEqual(str(data), readonly['/run/synapsys-memory/data.sock'])
        self.assertEqual(str(token), readonly['/run/synapsys-memory/session.token'])
        self.assertEqual('/runtime/.venv', readonly['/runtime/.venv'])
        self.assertEqual('/runtime/src', readonly['/runtime/src'])
        self.assertNotIn(str(data.parent), readonly.values())
        self.assertNotIn(str(token.parent), readonly.values())


class EvidenceTests(unittest.TestCase):
    def test_success_requires_all_three_signals(self):
        text = '\u2713 synapsys-memory  connected\n'
        self.assertEqual('PASS', probe.verdict(0, text, 1))
        self.assertEqual('CLIENT_EXIT_ERROR', probe.verdict(1, text, 1))
        self.assertEqual('NO_BROKER_CONTACT', probe.verdict(0, text, 0))
        self.assertNotEqual('PASS', probe.verdict(0, 'different-server connected', 1))

    def test_no_servers_is_a_diagnostic_not_pass(self):
        self.assertEqual('CONFIG_NOT_LOADED', probe.verdict(0, 'No MCP servers configured\n', 0))

    def test_negative_statuses_never_pass(self):
        for status in ('disconnected','not connected','failed: connected was expected','pending','disabled'):
            with self.subTest(status=status):
                self.assertNotEqual('PASS', probe.verdict(0, 'synapsys-memory  '+status, 5))

    def test_ansi_connected_is_recognized(self):
        self.assertEqual('PASS', probe.verdict(0, '\x1b[32m\u2713 synapsys-memory  connected\x1b[0m\n', 1))

    def test_private_file_created_without_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'config'
            probe.private_write(path, 'first')
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            with self.assertRaises(FileExistsError): probe.private_write(path,'second')
            self.assertEqual('first',path.read_text())

    def test_main_calls_only_connection_probe(self):
        with patch.object(probe, 'probe', return_value=2) as run:
            self.assertEqual(2, probe.main(['--opencode','/approved/opencode']))
            run.assert_called_once_with(Path('/approved/opencode'))

    def test_probe_does_not_claim_historical_lifecycle_pass(self):
        source = (ROOT/'scripts/probe_opencode_mcp.py').read_text()
        self.assertIn("'lifecycle': 'NOT_RUN'", source)
        self.assertNotIn('WORKER', source)
        self.assertNotIn('heartbeat', source)


if __name__ == '__main__': unittest.main()
