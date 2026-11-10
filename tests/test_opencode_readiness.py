"""Readiness regressions. No OpenCode, models or network are executed here."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ready_probe', Path(__file__).resolve().parents[1]/'scripts/probe_opencode_readiness.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
URL = 'http://127.0.0.1:43210'


class EndpointTests(unittest.TestCase):
    def test_only_loopback_url(self):
        self.assertEqual(URL, probe.local_endpoint(URL))
        self.assertEqual('http://[::1]:1234', probe.local_endpoint('http://[::1]:1234'))

    def test_reject_external_hosts(self):
        for host in ('example.com','127.0.0.1.example.com','192.168.1.79','0.0.0.0','localhost'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                probe.local_endpoint('http://'+host+':1234')

    def test_reject_credentials_paths_and_bad_ports(self):
        for value in ('https://127.0.0.1:12','http://u:p@127.0.0.1:12','http://127.0.0.1:12/mcp',
                      'http://127.0.0.1:12?x=y','http://127.0.0.1:12#foo','http://127.0.0.1:0',
                      'http://127.0.0.1:65536','http://127.0.0.1','file:///etc/passwd'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.local_endpoint(value)

    def test_allowlist_has_no_inference_commands(self):
        for kind in ('run','chat','serve','models','ollama','mcp add'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                probe.read_command(URL,kind)

    def test_reads_reuse_url_never_standalone(self):
        for kind, suffix in (('config',['debug','config']),('mcp',['mcp','list'])):
            argv = probe.read_command(URL,kind)
            self.assertEqual([probe.BINARY,'--server',URL,*suffix],argv)
            self.assertNotIn('--standalone',argv)


class ReadinessTests(unittest.TestCase):
    def run_poll(self, replies, alive=lambda: True):
        calls=[]; now=[0.0]
        def execute(argv,name,timeout):
            calls.append((argv,name,timeout))
            code,text = replies[min(len(calls)-1,len(replies)-1)]
            return {'returncode':code,'stdout':text,'stderr':''}
        def pause(seconds): now[0]+=seconds
        last,samples = probe.poll_mcp(URL,execute,alive,clock=lambda:now[0],pause=pause)
        return calls,last,samples

    def test_initial_empty_is_not_final_failure(self):
        calls,last,samples = self.run_poll([(0,'No MCP servers configured'),(0,'○ synapsys-memory pending'),
                                          (0,'✓ synapsys-memory connected')])
        self.assertEqual(['EMPTY','PENDING_OR_UNRECOGNIZED','CONNECTED'],[x['state'] for x in samples])
        self.assertEqual(3,len(calls))
        self.assertEqual(1,len({tuple(call[0]) for call in calls}))

    def test_permanent_empty_is_bounded(self):
        calls,last,samples = self.run_poll([(0,'No MCP servers configured')])
        self.assertEqual(probe.MAX_SAMPLES,len(calls))
        self.assertEqual('EMPTY',samples[-1]['state'])

    def test_failed_connection_is_not_restarted(self):
        calls,last,samples = self.run_poll([(0,'✗ synapsys-memory failed: rejected')])
        self.assertEqual(1,len(calls)); self.assertEqual('FAILED',samples[-1]['state'])

    def test_disabled_connection_not_reenabled(self):
        calls,last,samples = self.run_poll([(0,'○ synapsys-memory disabled')])
        self.assertEqual(1,len(calls)); self.assertEqual('DISABLED',samples[-1]['state'])

    def test_nonzero_exit_is_not_retried(self):
        calls,last,samples = self.run_poll([(2,'problem')])
        self.assertEqual(1,len(calls)); self.assertEqual('COMMAND_ERROR',samples[-1]['state'])

    def test_dead_service_no_read(self):
        calls,last,samples = self.run_poll([(0,'')],alive=lambda:False)
        self.assertEqual([],calls); self.assertEqual([],samples)

    def test_deadline_bounds_remaining_command_timeout(self):
        now=[0.0]; calls=[]
        def execute(argv,name,timeout):
            calls.append(timeout); now[0]+=timeout
            return {'returncode':0,'stdout':'No MCP servers configured'}
        probe.poll_mcp(URL,execute,lambda:True,clock=lambda:now[0],pause=lambda n:now.__setitem__(0,now[0]+n))
        self.assertLessEqual(now[0],probe.READINESS_SECONDS)
        self.assertTrue(all(0<t<=4 for t in calls))


class VerdictTests(unittest.TestCase):
    def report(self):
        return {'config_exact':True,'runtime':{'returncode':0},'config_recognized':True,
                'service_starts':1,'mcp':{'returncode':0,'stdout':'✓ synapsys-memory connected'}}

    def test_success_requires_all_evidence(self):
        self.assertTrue(probe.connected_result(self.report(),1,True,True))

    def test_label_without_broker_not_pass(self):
        self.assertFalse(probe.connected_result(self.report(),0,True,True))

    def test_close_and_revoke_required(self):
        self.assertFalse(probe.connected_result(self.report(),1,False,True))
        self.assertFalse(probe.connected_result(self.report(),1,True,False))

    def test_wrong_config_runtime_or_service_count_not_pass(self):
        for key,value in (('config_exact',False),('runtime',{'returncode':1}),
                          ('config_recognized',False),('service_starts',0),('service_starts',2)):
            r=self.report(); r[key]=value
            with self.subTest(key=key,value=value):
                self.assertFalse(probe.connected_result(r,1,True,True))

    def test_false_positive_connected_text_rejected(self):
        for text in ('not connected','synapsys-memory disconnected','synapsys-memory not connected',
                     'other-server connected','synapsys-memory connected yesterday',''):
            with self.subTest(text=text):
                self.assertNotEqual('CONNECTED',probe.mcp_state(0,text))

    def test_ansi_connected_recognized(self):
        self.assertEqual('CONNECTED',probe.mcp_state(0,'\x1b[32m✓ synapsys-memory connected\x1b[0m'))

    def test_names_only_no_env_or_secrets(self):
        value=[{'type':'document','info':{'mcp':{'servers':{'synapsys-memory':{'environment':{'TOKEN':'secret'}}}}}}]
        self.assertEqual(['synapsys-memory'],probe.config_names(value))
        self.assertEqual(['synapsys-memory'],probe.config_names({'data':value}))
        self.assertEqual([],probe.config_names({'info':{'mcp':{}}}))

    def test_malformed_config_shapes(self):
        for value in (None,4,'text',{'data':4},[None,{'info':4},{'info':{'mcp':4}}]):
            with self.subTest(value=value): self.assertEqual([],probe.config_names(value))

if __name__=='__main__': unittest.main()
