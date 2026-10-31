"""Offline broker wire and real durable journal integration (no compositor)."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from tools.native_spike.ownership import OwnershipBrokerClient
from tools.native_spike.hyprland import create_outputs, plan_outputs, OwnershipBlocked
from tools.native_spike.journal import Journal, recover

class Transport:
    def __init__(self, path):
        self.path = path
        self.epoch = 'epoch-one'
        self.registry = {}
        self.events = []
        self.fail_index = None
        self.bad_readback = False
        self.lost_reply = False
    def verified_session(self): return {'id': 'session'}
    def _request(self, wire):
        assert wire.startswith('j/xrspike ')
        j = json.loads(wire[10:]); p = j['payload']; op = j['op']
        self.events.append(op)
        if op == 'hello': result = {'qualified': True, 'fallback': 'physical-handle'}
        else:
            if j['epoch'] != self.epoch: return b'{"error":"epoch lost"}'
            if op == 'create':
                records = json.loads(self.path.read_text())['operations']
                assert any(r['operation']['id'] == p['id'] and r['state'] == 'pending' for r in records)
                self.registry[p['id']] = {'id': p['id'], 'name': p['name'], 'state': 'live', 'physical': False,
                    'fallback_usable': True, 'observation': {**{k:p[k] for k in ('width','height','refresh_millihz','scale','x','y','workspace')},
                    'enabled': True, 'dpms': True, 'mirrored': False, 'transform': 0,
                    'workspaces': [{'id':p['workspace'], 'name':p['workspace_name'], 'special':False}], 'windows': []}}
                if p['index'] == self.fail_index: self.registry[p['id']]['state'] = 'failed'
                if self.lost_reply: raise OSError('reply lost after creation')
            if p['id'] not in self.registry: return b'{"error":"unproven UUID"}'
            if op == 'evacuate_remove':
                assert p['fallback'] == 'physical-handle'
                self.registry[p['id']]['state'] = 'dead'
                self.registry[p['id']]['observation'] = None
            result = copy.deepcopy(self.registry[p['id']])
            if op == 'inspect' and self.bad_readback: result['observation']['refresh_millihz'] = 60000
        return json.dumps({**{k:j[k] for k in ('schema','session','run_id','request_id')}, 'epoch':self.epoch, 'result':result}).encode()

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'journal.json'
        self.j = Journal.open(self.path, 'run', 'session')
        self.transport = Transport(self.path)
        self.client = OwnershipBrokerClient(self.transport, 'run', 'session', 'DP-1')
        self.specs = plan_outputs([], [], 'run')
    def test_journal_before_mutation_and_readback_before_confirmation(self):
        self.assertEqual(len(create_outputs(self.specs, self.j, self.client)), 3)
        self.assertEqual(self.transport.events, ['hello','create','inspect','create','inspect','create','inspect'])
        self.assertTrue(all(r['state'] == 'confirmed' for r in Journal.open(self.path,'run','session').operations))
        self.assertTrue(recover(self.j, self.client).complete)
        self.assertTrue(recover(self.j, self.client).complete)
    def test_wrong_actual_refresh_never_confirms(self):
        self.transport.bad_readback = True
        with self.assertRaises(OwnershipBlocked): create_outputs(self.specs, self.j, self.client)
        self.assertEqual(self.j.operations[0]['state'], 'pending')
    def test_partial_failure_retains_intents_and_rolls_back_handles(self):
        for index in (1,2):
            with self.subTest(index=index):
                path = self.path.with_name(f'partial-{index}.json')
                journal = Journal.open(path, 'run', 'session')
                transport = Transport(path)
                transport.fail_index = index
                client = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1')
                specs = plan_outputs([], [], 'run')
                with self.assertRaises(OwnershipBlocked): create_outputs(specs, journal, client)
                self.assertEqual(len(journal.operations), index + 1)
                self.assertEqual(journal.operations[-1]['state'], 'pending')
                self.assertTrue(recover(journal, client).complete)
                self.assertTrue(all(r['state'] == 'dead' for r in transport.registry.values()))
    def test_lost_reply_restart_uses_surviving_registry(self):
        self.transport.lost_reply = True
        with self.assertRaises(OSError): create_outputs(self.specs, self.j, self.client)
        restarted = OwnershipBrokerClient(self.transport, 'run', 'session', 'DP-1')
        restarted.require_output_ownership()
        reopened = Journal.open(self.path,'run','session')
        self.assertTrue(recover(reopened, restarted).complete)
    def test_epoch_loss_unknown_is_not_absent(self):
        create_outputs(self.specs, self.j, self.client)
        self.transport.epoch = 'epoch-two'; self.transport.registry.clear()
        self.assertFalse(recover(self.j, self.client).complete)
        restarted = OwnershipBrokerClient(self.transport,'run','session','DP-1')
        restarted.require_output_ownership()
        self.assertFalse(recover(self.j,restarted).complete)
        self.assertTrue(all(r['state'] != 'done' for r in self.j.operations))
    def test_boolean_workspace_ids_do_not_pass_readback(self):
        create_outputs(self.specs, self.j, self.client)
        observed = self.client.inspect(self.specs[0]['id'])
        observed['observation']['workspaces'][0]['id'] = True
        with self.assertRaises(OwnershipBlocked): self.client.verify(observed, self.specs[0])

    def test_supervisor_exit_recovers_real_broker_journal(self):
        import os
        import socket
        from tools.native_spike.supervisor import run_presentation
        create_outputs(self.specs, self.j, self.client)
        self.transport._peer = (os.getpid(),os.getuid(),os.getgid(),'synthetic')
        peer, wayland = socket.socketpair()
        self.addCleanup(peer.close); self.addCleanup(wayland.close)
        class Child:
            def poll(self): return 0
        def spawn(argv, **kwargs):
            fd = int(argv[argv.index('--supervised-fd') + 1])
            endpoint = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return Child()
        result = run_presentation(self.j, self.client, 'DP-2', wayland, popen=spawn)
        self.assertTrue(result.complete)
        self.assertTrue(all(r['state']=='done' for r in self.j.operations))

    def test_python_requests_pass_actual_cpp_validator(self):
        import subprocess
        binary = Path(__file__).resolve().parents[2] / 'build/native-spike/ownership_test'
        if not binary.is_file(): self.skipTest('opt-in ownership native build not present')
        original = self.transport._request
        def checked(wire):
            checked = subprocess.run([str(binary), '--validate'], input=wire[2:].encode(), capture_output=True, timeout=5)
            self.assertEqual(checked.returncode, 0, wire)
            return original(wire)
        self.transport._request = checked
        create_outputs(self.specs, self.j, self.client)
        self.assertTrue(recover(self.j, self.client).complete)

    def test_bad_handshake_cannot_poison_retry(self):
        original = self.transport._request
        def invalid(wire):
            reply = json.loads(original(wire))
            reply['result'] = {'qualified':True, 'fallback':'invalid handle'}
            return json.dumps(reply).encode()
        self.transport._request = invalid
        with self.assertRaises(ValueError): self.client.require_output_ownership()
        self.assertIsNone(self.client.epoch)
        self.assertIsNone(self.client.fallback)

    def test_missing_plugin_fails_before_prepare(self):
        self.transport._request = lambda wire: b'unknown request'
        with self.assertRaises(ValueError): create_outputs(self.specs,self.j,self.client)
        self.assertEqual(self.j.operations, [])


class VerifyDiagnosticsTests(unittest.TestCase):
    """phys1: a bare 'workspace placement mismatch' was not diagnosable."""
    def setUp(self):
        self.spec = plan_outputs([], [], 'run')[0]
        self.result = {'id': self.spec['id'], 'name': self.spec['name'], 'state': 'live', 'physical': False, 'fallback_usable': True,
                       'observation': {**{k: self.spec[k] for k in ('width', 'height', 'refresh_millihz', 'scale', 'x', 'y', 'workspace')},
                                       'enabled': True, 'dpms': True, 'mirrored': False, 'transform': 0,
                                       'workspaces': [{'id': self.spec['workspace'], 'name': self.spec['workspace_name'], 'special': False}],
                                       'windows': []}}

    def test_mismatch_reports_bounded_expected_and_observed(self):
        bad = copy.deepcopy(self.result)
        bad['observation']['workspaces'] += [{'id': 21 + i, 'name': 'x\n‮' * 200, 'special': False} for i in range(40)]
        with self.assertRaises(OwnershipBlocked) as raised:
            OwnershipBrokerClient.verify(bad, self.spec)
        message = str(raised.exception)
        self.assertIn('workspace placement mismatch', message)
        self.assertIn(f"'id': {self.spec['workspace']}", message.replace('"', "'").replace(': ', ': '))
        self.assertIn('21', message)
        self.assertLessEqual(len(message), 700)
        self.assertTrue(message.isascii() and '\n' not in message)

    def test_window_mismatch_reports_observed(self):
        bad = copy.deepcopy(self.result)
        bad['observation']['windows'] = [{'workspace': -98, 'pinned': False}]
        with self.assertRaisesRegex(OwnershipBlocked, 'unsupported or unreachable mapped window.*-98'):
            OwnershipBrokerClient.verify(bad, self.spec)
        OwnershipBrokerClient.verify(self.result, self.spec)
