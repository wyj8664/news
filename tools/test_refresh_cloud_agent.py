import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from refresh_cloud_agent import Relay


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        directory=self.root/'.local-refresh/online';directory.mkdir(parents=True)
        path=directory/'agent.json';path.write_text(json.dumps({'agent_token':'test'}));path.chmod(0o600)
        self.relay=Relay(self.root)

    def tearDown(self):
        self.relay.db.close();self.temp.cleanup()

    def test_existing_job_is_reused_without_starting_another(self):
        self.relay.local=Mock(return_value={'job':{'id':'existing','terminal':False},'nonce':'nonce'})
        command={'id':'1234567890'}
        self.assertEqual(self.relay.accept(command),'existing')
        self.assertEqual(self.relay.accept(command),'existing')
        self.relay.local.assert_called_once_with('/status')

    def test_ack_loss_recovers_local_job_even_after_cooldown(self):
        self.relay.put('command:1234567890',{'attempted_at':time.time()-1200})
        from datetime import datetime,timezone
        self.relay.local=Mock(return_value={'job':{'id':'recovered','terminal':True,
            'created_at':datetime.fromtimestamp(time.time()-1100,timezone.utc).isoformat()}})
        self.assertEqual(self.relay.accept({'id':'1234567890'}),'recovered')
        self.relay.local.assert_called_once_with('/status')

    def test_cooldown_result_is_bound_without_submitting_a_request(self):
        from datetime import datetime,timezone
        self.relay.local=Mock(return_value={'cooldown_seconds':600,'job':{'id':'cooldown-job','terminal':True,
            'finished_at':datetime.now(timezone.utc).isoformat()}})
        self.assertEqual(self.relay.accept({'id':'1234567890'}),'cooldown-job')
        self.relay.local.assert_called_once_with('/status')

    def test_daily_budget_fails_closed_and_survives_restart(self):
        self.relay.spend(750)
        with self.assertRaises(RuntimeError):self.relay.spend()
        another=Relay(self.root)
        try:
            with self.assertRaises(RuntimeError):another.spend()
        finally:another.db.close()

    def test_initial_connect_does_not_start_collection_or_publish(self):
        self.relay.request=Mock(return_value={'commands':[],'ok':True})
        self.relay.local=Mock(return_value={'job':{'id':'old','terminal':True}})
        self.relay.publish=Mock()
        self.relay.tick()
        self.relay.publish.assert_not_called()
        self.relay.local.assert_called_once_with('/status')
        calls=self.relay.request.call_args_list
        self.assertEqual(calls[1].args[0],'status');self.assertIsNone(calls[1].args[1]['job'])


if __name__=='__main__':unittest.main()
