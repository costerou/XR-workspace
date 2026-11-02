"""Controller side of VR video: a mode of the running session, so no new consent; paths and keys are forwarded to the run."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.native_spike.video import KEY_OPS
from tools.xr_controller.state import Controller


class Store:
    def __init__(self): self.profile = copy.deepcopy(DEFAULT_PROFILE)
    def load(self, model): return copy.deepcopy(self.profile)
    def save(self, model, p): self.profile = copy.deepcopy(p)


class Units:
    def __init__(self): self.active, self.sent, self.reply = False, [], True
    def alive(self, run_id): return self.active
    def control(self, run_id, session, op, payload=None):
        if op not in (None, 'status'): self.sent.append((op, payload))
        if op == 'status':
            return {'capabilities': ['recenter'], 'view': {}, 'status': {'video': 'playing'},
                    'video': {'active': True, 'title': 'a_360.mp4', 'state': 'playing'}}
        return self.reply


class VideoControllerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name); self.fd = os.open(self.path, os.O_DIRECTORY); self.addCleanup(os.close, self.fd)
        self.observation = dict(model='luma-ultra', output='DP-2', reason=None, instance='instance',
                                monitors=[dict(name='DP-2'), dict(name='DP-1')], journals=[], plugins=[],
                                installed={k: dict(path='/p/' + k, sha256='a' * 64) for k in ('plugin', 'renderer')})
        self.now, self.spawns, self.units = 0, [], Units()
        self.c = Controller(lambda: self.now, lambda: copy.deepcopy(self.observation), lambda *a: self.spawns.append(a),
                            self.units, Store(), self.fd, self.path)

    def run_session(self):
        r = self.c.request(dict(op='begin_start')); self.c.request(dict(op='confirm_start', scope_id=r['scope_id']))
        self.now = 10
        self.observation['journals'] = [dict(run_id=r['scope']['run_id'], session='session', path='/runtime/journal.json',
                                             operations=[dict(state='pending')])]
        self.units.active = True; self.c.tick()
        self.assertEqual(self.c.snapshot()['state'], 'running')
        return len(self.spawns)

    def test_needs_a_running_session(self):
        reply = self.c.request(dict(op='video_open', path='/home/me/a_360.mp4'))
        self.assertFalse(reply['ok']); self.assertIn('start an XR session', reply['error'])
        self.assertEqual(self.units.sent, [])
        self.assertIsNone(self.c.snapshot()['video'])

    def test_open_is_forwarded_without_new_consent(self):
        spawns = self.run_session()
        consents = sorted(p.name for p in self.path.iterdir())
        reply = self.c.request(dict(op='video_open', path='/home/me/Videos/a_360.mp4'))
        self.assertTrue(reply['ok'], reply); self.assertTrue(reply['forwarded'])
        self.assertEqual(self.units.sent, [('video_open', {'path': '/home/me/Videos/a_360.mp4'})])
        chosen = {'projection': '180', 'stereo': 'sbs', 'fov': 180}
        self.assertTrue(self.c.request(dict(op='video_open', path='/v.mkv', format=chosen))['ok'])
        self.assertEqual(self.units.sent[-1], ('video_open', {'path': '/v.mkv', 'format': chosen}))
        self.assertEqual(len(self.spawns), spawns)                               # no new run
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), consents)  # no new consent written
        for bad in (dict(path='relative.mp4'), dict(path='/v.mp4', format={'projection': 'cubemap', 'stereo': 'mono', 'fov': 180}), {}):
            with self.subTest(bad=bad): self.assertFalse(self.c.request(dict(op='video_open', **bad))['ok'])
        self.units.reply = False
        self.assertFalse(self.c.request(dict(op='video_open', path='/v.mp4'))['ok'])

    def test_video_keys_and_snapshot(self):
        self.run_session()
        for op in KEY_OPS:
            self.assertTrue(self.c.request(dict(op='view_op', operation=op))['ok'], op)
        self.assertEqual([op for op, _ in self.units.sent], list(KEY_OPS))
        self.assertFalse(self.c.request(dict(op='view_op', operation='video_open'))['ok'])  # opening needs a path
        self.now += 2; self.c.tick()
        self.assertEqual(self.c.snapshot()['video'], {'active': True, 'title': 'a_360.mp4', 'state': 'playing'})


if __name__ == '__main__':
    unittest.main()
