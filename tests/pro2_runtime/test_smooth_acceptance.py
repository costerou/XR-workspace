"""The acceptance arithmetic, and the synthetic replay of the real pose path (needs a built pro2_smooth_replay)."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from tools.pro2_runtime import smooth_acceptance as A

ROOT=Path(__file__).resolve().parents[2]


class Metrics(unittest.TestCase):
    def rows(self,dps=30.0):
        out=[];t=0.0;i=0
        while t<3000:
            half=math.radians(dps*t/1000)/2
            out.append((t+.5,t,(0.0,0.0,math.sin(half),math.cos(half))))
            t+=(15.0,15.0,20.0)[i%3];i+=1
        return out
    def frames(self,rows,shown):
        return [dict(present=t,device=0.0,q=shown(t),horizon=0.0,predicted=0.0) for t in [40+8.3333*k for k in range(300)]]
    def quat(self,deg):return (0.0,0.0,math.sin(math.radians(deg)/2),math.cos(math.radians(deg)/2))
    def test_a_perfect_view_scores_zero(self):
        rows=self.rows();s=A.summary(A.pool([A.measure(rows,(0,0,0,1),self.frames(rows,lambda t:self.quat(30*(t-.5)/1000)))]))
        self.assertEqual(s['stuck_pct'],0);self.assertLess(s['step_err_p95'],.03)  # the reference's own floor: about a pixel;self.assertLess(abs(s['lag_ms']),.2)
    def test_a_held_view_scores_as_stuck_and_late(self):
        rows=self.rows()
        def held(t):
            d=max(r[1] for r in rows if r[1]<=t-25);return self.quat(30*d/1000)
        s=A.summary(A.pool([A.measure(rows,(0,0,0,1),self.frames(rows,held))]))
        self.assertGreater(s['stuck_pct'],40);self.assertGreater(s['lag_ms'],20)
    def test_overshoot_is_past_the_furthest_the_head_went(self):
        rows=[];t=0.0;angle=0.0;i=0
        while t<4000:
            angle+=(30*(t<1000)+0)*(15.0,15.0,20.0)[i%3]/1000
            half=math.radians(angle)/2;rows.append((t+.5,t,(0.0,0.0,math.sin(half),math.cos(half))))
            t+=(15.0,15.0,20.0)[i%3];i+=1
        end=angle
        over=lambda t:self.quat(min(30*t/1000,end)+(.5 if 1000<t<1100 else 0))
        s=A.summary(A.pool([A.measure(rows,(0,0,0,1),[dict(present=40+8.3333*k,device=0.0,q=over(40+8.3333*k),horizon=0.0,predicted=0.0) for k in range(400)])]))
        self.assertEqual(s['stops'],1);self.assertGreater(s['overshoot_max'],.4)
    def test_capture_loader_takes_only_poses_and_applies_the_bridge_axes(self):
        with tempfile.NamedTemporaryFile('w',suffix='.jsonl') as f:
            f.write(json.dumps(dict(kind='contract'))+'\n')
            for i in range(5):f.write(json.dumps(dict(kind='pose',host_monotonic_ns=1000000*(i+1),timestamp_raw=2000000*i,sdk_values=[0,0,0,1,.1,.2,.3]))+'\n')
            f.flush();rows=A.load_capture(f.name)
        self.assertEqual(len(rows),5);self.assertEqual(rows[1][:2],(1.0,2.0))
        n=math.sqrt(1+.01+.04+.09);self.assertAlmostEqual(rows[0][2][0],-.3/n);self.assertAlmostEqual(rows[0][2][1],-.1/n);self.assertAlmostEqual(rows[0][2][2],.2/n);self.assertAlmostEqual(rows[0][2][3],1/n)
    def test_shipped_values_are_the_ones_the_route_passes(self):
        from tools.pro2_runtime import scope
        self.assertEqual(A.SHIPPED['tau'],scope.SMOOTH_MS)
        bridge=(ROOT/'tools/pro2_runtime/bridge.cpp').read_text()
        self.assertIn('kLookAhead{0,0,0,40}',bridge);self.assertEqual((A.SHIPPED['constant'],A.SHIPPED['cap']),(0.0,40.0))
        header=(ROOT/'spike/include/xr/motion_smooth.hpp').read_text()
        self.assertRegex(header,r'tau_ms = 35\.f');self.assertRegex(header,r'brake_power = 3\.f');self.assertRegex(header,r'brake_floor_rad_per_ms = 4\.f')
        self.assertRegex(header,r'max_predict_deg = 6\.f');self.assertRegex(header,r'snap_deg = 8\.f')


REPLAY=os.environ.get('PRO2_SMOOTH_REPLAY')
@unittest.skipUnless(REPLAY,'set PRO2_SMOOTH_REPLAY to a built pro2_smooth_replay')
class Replay(unittest.TestCase):
    def test_synthetic_head_through_the_renderer_path(self):
        rows=A.synthetic()
        hold=A.evaluate(REPLAY,rows,mode='hold');smooth=A.evaluate(REPLAY,rows,mode='smooth')
        self.assertGreater(hold['stuck_pct'],45)        # the live symptom: one frame in two does not move
        self.assertLess(smooth['stuck_pct'],hold['stuck_pct']/5)
        self.assertLess(smooth['jerk_p95']*2,hold['jerk_p95'])
        self.assertLess(smooth['lag_ms'],hold['lag_ms']/2)
        self.assertLessEqual(smooth['predicted_max_deg'],6.0);self.assertLessEqual(smooth['horizon_max_ms'],40.0)
    def test_private_capture_data_stays_out_of_the_summary(self):
        text=json.dumps(A.evaluate(REPLAY,A.synthetic(seconds=4),mode='smooth'))
        self.assertNotIn('sdk_values',text);self.assertNotRegex(text,r'"q"')
