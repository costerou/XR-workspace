import unittest
from unittest.mock import Mock
from tools.pro2_diagnostic.candidate import admit, PoseLog, capture

class CandidateTests(unittest.TestCase):
    def test_admission_before_sdk_access(self):
        for rows in ([], [('35ca','1104','VITURE PRO 2 XR Glasses')], [('35ca','1301','VITURE Pro')], [('35ca','1301','VITURE PRO 2 XR Glasses')]*2):
            sdk = Mock()
            with self.assertRaises(ValueError): capture(rows, lambda: sdk, 0, lambda x: None)
            self.assertEqual(sdk.mock_calls, [])

    def test_pose_validation_and_preservation(self):
        log=PoseLog(); raw=[1,2,3,1,0,0,0]
        result=log.sample(raw,10)
        self.assertEqual(result['sdk_values'],raw)
        self.assertEqual(result['timestamp_raw'],10)
        self.assertEqual(result['issues'],[])
        self.assertIn('timestamp-not-increasing',log.sample(raw,10)['issues'])
        self.assertIn('quaternion-norm',log.sample([0,0,0,2,0,0,0],11)['issues'])
        for values in (raw[:6], raw+[0], [float('nan')]*7):
            self.assertTrue(log.sample(values,12)['issues'])
        for stamp in (-1,2**64,True):
            self.assertIn('timestamp-range',log.sample(raw,stamp)['issues'])

    def test_lifecycle_and_failure_cleanup(self):
        for fail in (None,'initialize','start','register','open'):
            sdk=Fake(fail); out=[]
            if fail:
                with self.assertRaises(RuntimeError): capture(EXACT,lambda:sdk,0,out.append)
            else: capture(EXACT,lambda:sdk,0,out.append)
            self.assertEqual(sdk.calls[-3:],['stop','shutdown','destroy'])
            if 'open' in sdk.calls: self.assertIn('close',sdk.calls)
            self.assertLess(sdk.calls.index('register'), sdk.calls.index('open')) if 'open' in sdk.calls else None

    def test_unsupported_rate_prevents_create(self):
        sdk=Fake(); sdk.support=False
        with self.assertRaises(ValueError): capture(EXACT,lambda:sdk,0,lambda x:None)
        self.assertNotIn('create',sdk.calls)

    def test_rate_reaches_capability_and_open(self):
        from tools.pro2_diagnostic.candidate import SDK, RATES
        self.assertEqual(RATES, {60: 0, 90: 1, 120: 2, 240: 3})  # xr-pro2-bridge rate_code
        sdk=SDK.__new__(SDK); sdk.rate=RATES[240]; sdk.h=7; seen=[]
        sdk.functions={'is_product_id_valid':lambda pid:1,
                       'is_product_support_imu_frequency':lambda pid,mode,rate:seen.append(('support',rate)) or 1,
                       'open_imu':lambda h,mode,rate:seen.append(('open',rate)) or 0}
        self.assertTrue(sdk.capabilities()); sdk.call('open')
        self.assertEqual(seen,[('support',3),('open',3)])

EXACT=[('35ca','1301','VITURE PRO 2 XR Glasses')]
class Fake:
    support=True
    def __init__(self,fail=None): self.calls=[]; self.fail=fail
    def family(self): return 1
    def capabilities(self): return self.support
    def create(self): self.calls.append('create')
    def call(self,name):
        self.calls.append(name)
        if name==self.fail: raise RuntimeError(name)
    def register(self,callback): self.call('register'); self.callback=callback
    def firmware(self): return {'status':0,'version':'simulated'}

class CAbiTests(unittest.TestCase):
    def test_compiled_simulated_sdk_order_and_capture(self):
        import hashlib, subprocess, tempfile
        from pathlib import Path
        from tools.pro2_diagnostic.candidate import SDK
        with tempfile.TemporaryDirectory() as d:
            lib=Path(d)/'fake.so'
            subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(Path(__file__).with_name('fake_sdk.c')),'-o',str(lib)],check=True)
            digest=hashlib.sha256(lib.read_bytes()).hexdigest()
            sdk=SDK(str(lib),digest); output=[]
            result=capture(EXACT,lambda:sdk,.01,output.append)
            self.assertEqual(result,(1,0,0))
            sample=next(x for x in output if x['kind']=='pose')
            self.assertEqual(sample['sdk_values'],[1,2,3,1,0,0,0])
            self.assertEqual(sample['timestamp_raw'],1234567890123)
            self.assertEqual(sdk.lib.fixture_stage(),8)
            with self.assertRaises(ValueError): SDK(str(lib),'0'*64)

    def test_identity_changed_before_create(self):
        sdk=Fake()
        with self.assertRaises(ValueError): capture(EXACT,lambda:sdk,0,lambda x:None,recheck=lambda:[])
        self.assertEqual(sdk.calls,[])

    def test_cleanup_continues_after_close_failure(self):
        sdk=Fake('close')
        with self.assertRaisesRegex(RuntimeError,'cleanup failed'):
            capture(EXACT,lambda:sdk,0,lambda x:None)
        self.assertEqual(sdk.calls[-3:],['stop','shutdown','destroy'])

class ComparisonTests(unittest.TestCase):
    def test_receipt_time_windows_and_sign_equivalence(self):
        from tools.pro2_diagnostic.analyze import compare
        samples=[dict(kind='pose',host_monotonic_ns=i*100000000,sdk_values=[0,0,0,(-1 if i%2 else 1),0,0,0],issues=[]) for i in range(301)]
        result=compare(samples)
        for r in result['segments'].values():
            self.assertEqual(r['relative_wxyz'],[1,0,0,0])
        with self.assertRaises(ValueError): compare(samples[:20])

class FinalBoundaryTests(unittest.TestCase):
    def test_wrong_family_rejects_before_initialization(self):
        sdk=Fake(); sdk.family=lambda:2
        with self.assertRaises(ValueError): capture(EXACT,lambda:sdk,0,lambda x:None)
        self.assertNotIn('initialize',sdk.calls)
        self.assertEqual(sdk.calls[-3:],['stop','shutdown','destroy'])

    def test_stop_event_uses_cleanup(self):
        import threading
        stop=threading.Event();stop.set();sdk=Fake()
        self.assertEqual(capture(EXACT,lambda:sdk,30,lambda x:None,stop),(0,0,0))
        self.assertEqual(sdk.calls[-4:],['close','stop','shutdown','destroy'])

    def test_cli_requires_authorization_before_inventory_or_load(self):
        from unittest.mock import patch
        from tools.pro2_diagnostic.candidate import main
        with patch('sys.argv',['candidate','--output','/nonexistent/test']), \
             patch('tools.pro2_diagnostic.candidate.inventory') as scan, \
             patch('tools.pro2_diagnostic.candidate.SDK') as sdk:
            with self.assertRaises(SystemExit): main()
            scan.assert_not_called();sdk.assert_not_called()


class HypothesisTests(unittest.TestCase):
    def test_directional_hypotheses_remain_unselected(self):
        import math
        from tools.pro2_diagnostic.analyze import compare
        rows=[]
        for i in range(301):
            t=i/10; q=[1,0,0,0]; a=.1
            if 5<=t<10:q=[math.cos(a),0,0,math.sin(a)]
            elif 10<=t<15:q=[math.cos(a),0,0,-math.sin(a)]
            elif 15<=t<20:q=[math.cos(a),0,math.sin(a),0]
            elif 20<=t<25:q=[math.cos(a),0,-math.sin(a),0]
            rows.append(dict(kind='pose',host_monotonic_ns=i*100000000,sdk_values=[0,0,0]+q,issues=[]))
        result=compare(rows); h=result['analysis_only_hypotheses']['left']
        self.assertIsNone(result['selected_mapping'])
        self.assertAlmostEqual(h['documented_NWU_identity_wxyz'][3],math.sin(.1))
        self.assertAlmostEqual(h['upstream_EUS_to_NWU_only_wxyz'][1],-math.sin(.1))
