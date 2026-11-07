"""Options commands traverse the real private control decoder, never an alternate channel."""
import json
import unittest
from tools.native_spike.ipc import decode_control, ControlQueue


def wire(op, payload, request_id=1, session='session'):
    return (json.dumps(dict(schema=1, request_id=request_id,run_id='run',session=session,op=op,payload=payload))+'\n').encode()

class OptionsTransportTests(unittest.TestCase):
    def test_every_intent_roundtrips_through_private_decoder(self):
        cases={'open':{'monitor':'XR-3'},'close':{},'scope':{'scope':'workspace'},'category':{'category':'video'},'focus':{'focus':2},'scroll':{'delta':-38},'cancel':{}}
        for op in ('activate','set','confirm'):
            cases[op]=dict(row_id='view.mode',monitor='XR-3',scope='monitor',revision=2,value='locked')
        for op,payload in cases.items():
            with self.subTest(op=op): self.assertEqual(decode_control(wire('options_'+op,payload))['payload'],payload)
    def test_malformed_and_stale_envelopes_stay_rejected(self):
        for payload in ({'monitor':'XR-3','extra':1},{'monitor':'XR-0'},{'monitor':'XR-1;exec x'}):
            with self.assertRaises(ValueError): decode_control(wire('options_open',payload))
        q=ControlQueue('run','session')
        q.push(wire('options_close',{}))
        with self.assertRaises(ValueError): q.push(wire('options_close',{}))
        with self.assertRaises(ValueError): q.push(wire('options_close',{},2,'old'))

class OptionsHelperTests(unittest.TestCase):
    def test_signed_wheel_helper_payload(self):
        from unittest.mock import patch
        from tools.native_spike import xr_control
        with patch.object(xr_control, 'send') as send:
            self.assertEqual(xr_control.main(['xr_control','/tmp/private.sock','run','session','options_scroll','-38']),0)
            self.assertEqual(send.call_args.args[-1], {'delta':-38})
