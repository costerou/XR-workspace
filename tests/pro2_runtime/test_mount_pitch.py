"""Why the Pro 2 has a fixed mount pitch, not a gravity level: Cody's head turns about an axis fixed to the head."""
import json
import math
import os
from pathlib import Path
import unittest
from tools.pro2_runtime import smooth_acceptance as A
from tools.native_spike.display_policy import PRO2_MOUNT_ALTERNATIVE,PRO2_MOUNT_PITCH
from tools.pro2_runtime.launch import validate_mount_pitch

REPLAY=os.environ.get('PRO2_SMOOTH_REPLAY')


def head(start_pitch,axis_tilt=-11.0):
    """A head at `start_pitch` against gravity, turning about an axis fixed to it, `axis_tilt` from the sensor's up, as Luma's 28 turns show."""
    rows=[];t=0.0;i=0;p=math.radians(start_pitch)/2;a=math.radians(axis_tilt)
    axis=(math.sin(a),0.0,math.cos(a))
    while t<6000:
        half=math.radians(25*math.sin(2*math.pi*t/3000))/2;s=math.sin(half)
        q=A.qmul((0.0,math.sin(p),0.0,math.cos(p)),(axis[0]*s,axis[1]*s,axis[2]*s,math.cos(half)))
        rows.append((t+.5,t,q));t+=(15.0,15.0,20.0)[i%3];i+=1
    return rows


class Default(unittest.TestCase):
    def test_the_default_is_the_one_cody_wore_and_the_fit_is_the_alternative(self):
        self.assertEqual(PRO2_MOUNT_PITCH,-8.5);self.assertEqual(PRO2_MOUNT_ALTERNATIVE,-11.0)
    def test_bounds(self):
        self.assertEqual(validate_mount_pitch(-11.0),-11.0);self.assertEqual(validate_mount_pitch(0),0.0)
        for bad in (-31,31,float('nan'),True,'x',None):
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_mount_pitch(bad)
    def test_consent_takes_it_and_defaults_to_it(self):
        from tools.pro2_runtime.supervised_parent import consent_for
        from tools.xr_controller.pro2_test import validate
        self.assertEqual(validate(consent_for('pro2-supervised-1','i'))['mount_pitch_deg'],-8.5)
        self.assertEqual(validate(consent_for('pro2-supervised-1','i',mount_pitch_deg=-8.0))['mount_pitch_deg'],-8.0)
        with self.assertRaises(ValueError):validate(consent_for('pro2-supervised-1','i',mount_pitch_deg=-40.0))


@unittest.skipUnless(REPLAY,'set PRO2_SMOOTH_REPLAY to a built pro2_smooth_replay')
class Reference(unittest.TestCase):
    def lean(self,rows,level):return abs(A.tilt(A.replay(REPLAY,rows,mode='hold',level=level,stabilization=0)[1])['roll_per_10deg_yaw'])
    def test_a_fixed_mount_holds_at_any_start_a_gravity_level_does_not(self):
        for start in (5.0,14.47,19.5,27.0):
            with self.subTest(start=start):
                rows=head(start)
                self.assertLess(self.lean(A.mounted(rows,PRO2_MOUNT_ALTERNATIVE),0),.08)  # head()'s axis is the -11 fit
        self.assertGreater(self.lean(head(14.47),1),.4)      # Run B's start
        self.assertGreater(self.lean(head(19.5),1),1.2)      # the directional capture's start
    def test_the_lean_of_a_gravity_level_has_the_sign_cody_reported(self):
        lean=A.tilt(A.replay(REPLAY,head(14.47),mode='hold',level=1,stabilization=0)[1])
        self.assertLess(lean['right']['roll'],0);self.assertGreater(lean['left']['roll'],0)   # turn right leans right, turn left leans left
