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
        self.assertRegex(header,r'max_predict_deg = 6\.f');self.assertRegex(header,r'snap_deg = 30\.f')


class Tilt(unittest.TestCase):
    def test_the_split_finds_each_cause(self):
        frames=[];times=[];rolls=[];pitches=[]
        for k in range(900):
            t=k*8.3333;yaw=25*math.sin(k/60);roll=2*math.sin(k/37);pitch=4*math.sin(k/23)
            times.append(t-A.READ_TO_PRESENT_MS);rolls.append(roll);pitches.append(pitch)
            persp=math.degrees(math.atan(math.sin(math.radians(pitch))*math.tan(math.radians(yaw))))
            frames.append(dict(present=t,yaw=yaw,roll=persp+roll+.3*yaw))
        split=A.explained_tilt(frames,(times,rolls,pitches),0,9e9)
        self.assertAlmostEqual(split['perspective'],1,places=1);self.assertAlmostEqual(split['head_roll'],1,places=1)
        self.assertAlmostEqual(split['roll_per_10deg_yaw_alone'],3,places=1)
    def test_the_replayed_tilt_passes_its_limit(self):
        tilt=json.loads((ROOT/'docs/pro2/smooth-evidence/replay-aggregates.json').read_text())['results']['directional']['tilt']
        self.assertGreater(tilt['unlevelled']['split']['roll_per_10deg_yaw_alone'],3)
        self.assertLess(abs(tilt['levelled']['split']['roll_per_10deg_yaw_alone']),A.TILT_LIMIT)


class Summary(unittest.TestCase):
    def line(self):
        return json.dumps(dict(kind='managed_output_capture',outputs=[dict(name='XR-1',frames=9)],tracking=dict(samples=5368,lost_events=0,fov_source='pro2-supervised-approximate',diagonal_fov_deg=44.5),
            latency=dict(read_to_present=dict(p50_ms=16.6),pose_age_relative=dict(p50_ms=9.4),prediction=dict(enabled=True,predicted_frames=9000,unpredicted_frames=0,last_horizon_ms=20.0,look_ahead_cfg=[0,0,0,40],smoothing=dict(tau_ms=35,frames=9000,snaps=0)))))
    def test_consent_for_keeps_one_desktop_and_sixty_unless_asked(self):
        from tools.pro2_runtime.supervised_parent import consent_for
        from tools.xr_controller.pro2_test import validate
        plain=consent_for('pro2-supervised-1','i');self.assertNotIn('pose_rate_hz',plain);self.assertEqual(len(plain['desktops']),1)
        validate(plain);self.assertEqual(validate(consent_for('pro2-supervised-1','i',pose_rate_hz=120))['pose_rate_hz'],120)
        with self.assertRaises(ValueError):validate({**plain,'pose_rate_hz':1000})
    def test_summarize_reads_aggregates_only(self):
        from tools.pro2_runtime.supervised_parent import summarize
        timing=json.dumps(dict(kind='generated_pattern_presentation_timing',presented=10000,interval_histogram=[0,0,0,9000],abandoned=2))
        s=summarize('PRO2 SUPERVISED TEST\n'+timing+'\n'+self.line()+'\n{"kind":"x"}\nnot json\n',dict(complete=True,provider=dict(samples=5400)))
        self.assertEqual((s['predicted_share'],s['desktops'],s['provider']['samples'],s['complete']),(.9,['XR-1'],5400,True))
        self.assertEqual(s['smoothing']['tau_ms'],35);self.assertEqual(s['look_ahead_cfg'],[0,0,0,40])
    def test_summarize_carries_why_a_run_stopped(self):
        from tools.pro2_runtime.supervised_parent import summarize
        s=summarize('xr-workspace: run guard: ValueError: gone\n',dict(complete=True,run=dict(stop_reason='guard-failed')))
        self.assertEqual((s['stop_reason'],s['guard_reason']),('guard-failed','ValueError: gone'))
    def test_summarize_survives_an_empty_log(self):
        from tools.pro2_runtime.supervised_parent import summarize
        self.assertEqual(summarize('',None)['presented'],0)


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
    def pitched_head(self,pitch):
        """Cody's report as a feed: a head starting `pitch` below level, turning right then left about gravity, with no roll of its own."""
        rows=[];t=0.0;i=0;p=math.radians(pitch)/2
        while t<6000:
            yaw=math.radians(25*math.sin(2*math.pi*t/3000))/2
            rows.append((t+.5,t,A.qmul((0.0,0.0,-math.sin(yaw),math.cos(yaw)),(0.0,math.sin(p),0.0,math.cos(p)))))
            t+=(15.0,15.0,20.0)[i%3];i+=1
        return rows
    def test_a_turn_about_gravity_stays_level_through_the_real_view(self):
        rows=self.pitched_head(19.5)
        before=A.tilt(A.replay(REPLAY,rows,mode='smooth',level=0)[1]);after=A.tilt(A.replay(REPLAY,rows,mode='smooth',level=1)[1])
        self.assertGreater(before['roll_per_10deg_yaw'],2.5)                     # right leans left, left leans right
        self.assertGreater(before['right']['roll'],0);self.assertLess(before['left']['roll'],0)
        self.assertLess(abs(after['roll_per_10deg_yaw']),.1)
        self.assertLess(abs(after['right']['roll']),.3);self.assertLess(abs(after['left']['roll']),.3)
    def test_levelling_leaves_the_motion_measures_alone(self):
        rows=self.pitched_head(19.5)
        self.assertLess(A.evaluate(REPLAY,rows,mode='smooth',level=1)['stuck_pct'],5)
    def test_private_capture_data_stays_out_of_the_summary(self):
        text=json.dumps(A.evaluate(REPLAY,A.synthetic(seconds=4),mode='smooth'))
        self.assertNotIn('sdk_values',text);self.assertNotRegex(text,r'"q"')


RENDERER=os.environ.get('PRO2_RENDERER')
@unittest.skipUnless(RENDERER,'set PRO2_RENDERER to a built xr-native-spike')
class RendererArguments(unittest.TestCase):
    def status(self,*extra):
        base=['--supervised-fd','3','--wayland-fd','4','--output','DP-2','--run-id','r','--session','s','--capture','XR-1','--pose-feed','xrld']
        env={'PATH':'/usr/bin'}
        return subprocess.run([RENDERER,*base,*extra],capture_output=True,timeout=5,env=env,stdin=subprocess.DEVNULL).returncode
    def test_smoothing_needs_the_private_feed_and_a_sane_time(self):
        pro2=['--pro2-test-pose-prefix','/private/pose_','--pro2-test-projection','44.5']
        self.assertEqual(self.status('--pro2-test-smooth-ms','35'),2)            # alone: refused
        for bad in ('4','81','nan','x'):
            with self.subTest(bad=bad):self.assertEqual(self.status(*pro2,'--pro2-test-smooth-ms',bad),2)
