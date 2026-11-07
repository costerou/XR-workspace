import math
import unittest
from tools.pro2_diagnostic.adapter import ReplayAdapter, basis

class AdapterTests(unittest.TestCase):
    def test_offline_gate(self):
        with self.assertRaises(ValueError): ReplayAdapter()
    def test_signed_axes_and_composition(self):
        a=.15
        self.assertEqual(basis([1,0,0,0]),[1,0,0,0])
        self.assertEqual(basis([math.cos(a),0,math.sin(a),0]),[math.cos(a),0,0,math.sin(a)])
        self.assertEqual(basis([math.cos(a),math.sin(a),0,0]),[math.cos(a),0,-math.sin(a),0])
    def test_packet_history_units_and_no_pitch(self):
        a=ReplayAdapter(replay_only=True)
        for i in range(3): packet=a.push([0,0,0,1,0,0,0],10**12+i*16666667,i*16666667)
        self.assertEqual(len(packet),16)
        self.assertEqual(packet[:12],[0,0,0,1]*3)
        self.assertAlmostEqual(packet[12],33.333334,places=4)
        self.assertEqual(packet[15],0)
    def test_reset_wrap_stale_and_teardown(self):
        a=ReplayAdapter(replay_only=True);v=[0,0,0,1,0,0,0]
        a.push(v,2**64-2,0)
        with self.assertRaises(ValueError): a.push(v,0,10)
        self.assertFalse(a.active)
        a=ReplayAdapter(replay_only=True);a.push(v,0,0)
        with self.assertRaises(ValueError): a.push(v,1,300000001)
        a=ReplayAdapter(replay_only=True);a.push(v,0,0)
        packet=a.push(v,(2**24-1000)*1000000,1)
        self.assertEqual(packet[12:15],[0,0,0])
        a.close();a.close()
        with self.assertRaises(ValueError):a.push(v,1,2)
    def test_invalid_and_nonincreasing_fail_closed(self):
        for bad in ([0]*6,[float('nan')]*7,[0]*7,[0,0,0,2,0,0,0]):
            a=ReplayAdapter(replay_only=True)
            with self.assertRaises(ValueError):a.push(bad,0,0)
            self.assertFalse(a.active)
        for ts in (-1,2**64,True):
            with self.assertRaises(ValueError):ReplayAdapter(replay_only=True).push([0,0,0,1,0,0,0],ts,0)

class CompositionTests(unittest.TestCase):
    def test_basis_commutes_with_quaternion_composition(self):
        from tools.pro2_diagnostic.directional import mul
        a=[math.cos(.2),math.sin(.2),0,0];b=[math.cos(.3),0,math.sin(.3),0]
        for x,y in zip(basis(mul(a,b)),mul(basis(a),basis(b))):self.assertAlmostEqual(x,y)
    def test_cues_truncated_return_and_tilted_neutral(self):
        from tools.pro2_diagnostic.directional import aggregate,mul
        rows=[];cues=[dict(label=n,host_monotonic_ns=int(t*1e9)) for n,t in zip(['center','left','right','up','down','return'],[0,5,10,15,20,25.02])]
        neutral=[math.cos(.15),math.sin(.15),0,0]
        for i in range(1800):
            t=i/60;q=neutral
            if 5<=t<10:q=mul([math.cos(.1),0,math.sin(.1),0],neutral)
            rows.append(dict(kind='pose',host_monotonic_ns=int(t*1e9),sdk_values=[0,0,0]+q,issues=[]))
        out=aggregate(rows,cues)
        self.assertLess(out['holds']['return']['actual_sample_span_s'],3)
        self.assertLess(out['holds']['left']['compositions']['reference']['H1_upstream_basis']['off_expected_axis_fraction'],1e-6)
        self.assertGreater(out['holds']['left']['compositions']['body']['H1_upstream_basis']['off_expected_axis_fraction'],.2)
        with self.assertRaises(ValueError):aggregate(rows,list(reversed(cues)))


class ReplayScopeTests(unittest.TestCase):
    def test_capture_scope_failures(self):
        from tools.pro2_diagnostic.adapter import replay
        from tools.pro2_diagnostic.candidate import SHA256
        rows=[dict(kind='contract',model='pro2',library_sha256=SHA256),dict(kind='firmware_query',status=0,version='30.0.00.002_20260804'),dict(kind='cleanup',errors=[],queue_dropped=0)]
        for key,value in [('model','beast'),('library_sha256','wrong')]:
            bad=[dict(r) for r in rows];bad[0][key]=value
            with self.assertRaises(ValueError):replay(bad)
        with self.assertRaisesRegex(ValueError,'empty'):replay(rows)
        rows[-1]['queue_dropped']=1
        with self.assertRaisesRegex(ValueError,'unclean'):replay(rows)

class RollEvidenceTests(unittest.TestCase):
    def test_left_ear_down_and_right_ear_down_candidate_signs(self):
        from tools.pro2_diagnostic.directional import aggregate,mul
        rows=[];cues=[dict(label=n,host_monotonic_ns=int(t*1e9)) for n,t in zip(['center','roll_left','roll_right','return'],[.004,5.003,10.001,15])]
        for i in range(1200):
            t=i/60;a=.2 if 5<=t<10 else -.2 if 10<=t<15 else 0
            rows.append(dict(kind='pose',host_monotonic_ns=int(t*1e9),sdk_values=[0,0,0,math.cos(a),0,0,math.sin(a)],issues=[]))
        result=aggregate(rows,cues,roll=True)
        self.assertLess(result['holds']['roll_left']['compositions']['reference']['H1_upstream_basis']['axis_xyz'][0],-.99)
        self.assertGreater(result['holds']['roll_right']['compositions']['reference']['H1_upstream_basis']['axis_xyz'][0],.99)
        self.assertLess(result['pair_metrics']['reference']['roll_left_right_axis_dot'],-.99)
        self.assertLess(result['holds']['return']['actual_sample_span_s'],3)
