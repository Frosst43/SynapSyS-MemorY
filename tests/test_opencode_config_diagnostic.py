"""Pure diagnostic classification/command tests. No OpenCode or model execution."""
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1]/'scripts/diagnose_opencode_config.py'
spec = importlib.util.spec_from_file_location('config_diagnostic', path)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.report = {'config_sha256':'abc','expected_config_sha256':'abc',
                       'runtime':{'returncode':0},'stages':{
            'config':{'returncode':0,'json_valid':True,'json':[
                {'type':'document','path':diag.CONFIG,
                 'info':{'mcp':{'servers':{diag.SERVER:{'type':'local'}}}}}]},
            'mcp':{'returncode':0,'stdout':'\u2713 synapsys-memory  connected\n'}}}

    def test_commands_only_version_config_and_list(self):
        self.assertEqual([x[0] for x in diag.commands()],['version','config','mcp'])
        self.assertEqual(diag.commands()[1][1], [diag.BINARY,'--standalone','--print-logs','debug','config'])
        self.assertEqual(diag.commands()[2][1], [diag.BINARY,'--standalone','--print-logs','mcp','list'])
        for _, argv in diag.commands():
            self.assertFalse(set(argv)&{'run','chat','serve','models','pull','ollama','--yolo'})

    def test_child_is_valid_python(self):
        compile(diag.CHILD,'<diagnostic-child>','exec')

    def test_confirmed_connection(self):
        self.assertEqual('CONNECTED_IN_STANDALONE_DIAGNOSTIC', diag.classify(self.report,1))

    def test_exit_zero_and_empty_list_not_pass(self):
        self.report['stages']['mcp']['stdout']='No MCP servers configured\n'
        self.assertEqual('CONFIG_PRESENT_BUT_MCP_LIST_EMPTY',diag.classify(self.report,0))

    def test_config_empty_distinct_from_connection_error(self):
        self.report['stages']['config']['json']=[]
        self.assertEqual('SERVER_ABSENT_FROM_LOADED_CONFIG',diag.classify(self.report,0))

    def test_file_wrong_hash(self):
        self.report['config_sha256']='wrong'
        self.assertEqual('CONFIG_MOUNT_MISMATCH',diag.classify(self.report,0))

    def test_python_import_fail(self):
        self.report['runtime']['returncode']=1
        self.assertEqual('MCP_RUNTIME_IMPORT_FAILED',diag.classify(self.report,0))

    def test_config_exit_error(self):
        self.report['stages']['config']['returncode']=1
        self.assertEqual('CONFIG_COMMAND_FAILED',diag.classify(self.report,0))

    def test_config_not_json(self):
        self.report['stages']['config']['json_valid']=False
        self.assertEqual('CONFIG_OUTPUT_NOT_JSON',diag.classify(self.report,0))

    def test_connected_label_requires_broker_contact(self):
        self.assertEqual('CONNECTED_LABEL_WITHOUT_BROKER_CONTACT',diag.classify(self.report,0))

    def test_disconnected_not_pass(self):
        self.report['stages']['mcp']['stdout']='synapsys-memory disconnected\n'
        self.assertEqual('MCP_STARTUP_UNCONFIRMED',diag.classify(self.report,1))

    def test_client_failure_even_if_text_connected(self):
        self.report['stages']['mcp']['returncode']=1
        self.assertEqual('MCP_STARTUP_UNCONFIRMED',diag.classify(self.report,1))

    def test_ansi_connected(self):
        self.report['stages']['mcp']['stdout']='\x1b[32m\u2713 synapsys-memory connected\x1b[0m\n'
        self.assertEqual('CONNECTED_IN_STANDALONE_DIAGNOSTIC',diag.classify(self.report,1))

    def test_config_report_drops_sensitive_fields(self):
        raw=copy.deepcopy(self.report['stages']['config']['json'])
        raw[0]['info']['password']='DO_NOT_PRINT'
        raw[0]['info']['mcp']['servers'][diag.SERVER]['environment']={'secret':'DO_NOT_PRINT'}
        report=diag.config_entries(raw)
        self.assertNotIn('DO_NOT_PRINT',repr(report))
        self.assertEqual(set(report[0]),{'type','path','mcp_servers'})
        self.assertEqual(report,diag.config_entries({'data':raw}))

    def test_unknown_config_shapes(self):
        for value in (None,False,123,'bad',{},[None,23,False]):
            self.assertEqual([],diag.config_entries(value))

    def test_redaction_known_and_keyed_secrets(self):
        text='password=abc token: def Authorization: Bearer xyz\nknown=secret_value'
        safe=diag.redact(text,('secret_value',))
        for secret in ('abc','def','xyz','secret_value'):
            self.assertNotIn(secret,safe)

    def test_redaction_long_opaque_values(self):
        self.assertNotIn('A'*48,diag.redact('opaque='+'A'*48))


if __name__=='__main__': unittest.main()
