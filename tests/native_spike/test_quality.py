"""VR video quality menu (tools/native_spike/quality.py, video.VideoControl): the settings accessor and its defaults,
the menu keys while a video plays, live renderer frames, and saving to layout.json and the glasses profile."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.native_spike import profiles, quality, video, view
from tools.native_spike.profiles import LayoutStore, ProfileStore

from test_video import Clock, FakeSupervisor, frames, info

NAMES = ['XR-1', 'XR-2', 'XR-3']
SHARP = {'filter': 'sharp', 'deband': False}  # one step right of the default (Catmull-Rom)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_the_renderers_own(self):
        # What the renderer does with no quality frame (Catmull-Rom from the filtering work, mpv's deband off:
        # spike/include/xr/video.hpp VideoQuality). Other work owns these values; the menu must not move them.
        self.assertEqual(quality.DEFAULTS, {'filter': 'catmull-rom', 'deband': False})
        self.assertEqual(view.DEFAULT_SETTINGS['video_quality'], quality.DEFAULTS)
        self.assertEqual(profiles.DEFAULT_PROFILE['video_quality'], quality.DEFAULTS)
        self.assertEqual(quality.KEYS, tuple(quality.DEFAULTS))
        for s in quality.SETTINGS:
            self.assertIn(quality.DEFAULTS[s.key], [v for v, _ in s.choices])

    def test_the_accessor_cleans_each_field_on_its_own(self):
        self.assertEqual(quality.video_quality({}), quality.DEFAULTS)
        self.assertEqual(quality.video_quality({'video_quality': 'sharp'}), quality.DEFAULTS)
        self.assertEqual(quality.video_quality({'video_quality': {'filter': 'sharp', 'deband': 1}}), SHARP)  # 1 is not True
        self.assertEqual(quality.video_quality({'video_quality': {'filter': 'cubic', 'deband': True}}),
                         {'filter': 'catmull-rom', 'deband': True})
        # 'linear', the first menu's name for bilinear, still loads as bilinear
        self.assertEqual(quality.video_quality({'video_quality': {'filter': 'linear', 'deband': False}}),
                         {'filter': 'bilinear', 'deband': False})
        self.assertEqual(quality.video_quality(view.ViewState(NAMES, video_quality=SHARP)), SHARP)
        self.assertEqual(quality.video_quality(object()), quality.DEFAULTS)
        state = view.ViewState(NAMES, video_quality={'filter': 'bogus'})
        self.assertEqual(state.video_quality, quality.DEFAULTS)
        self.assertEqual(state.settings()['video_quality'], quality.DEFAULTS)

    def test_step_wraps_and_payloads_validate(self):
        q = dict(quality.DEFAULTS)
        # the XR_VIDEO_FILTER names, Catmull-Rom first (the default), then the first menu's nearest
        self.assertEqual([v for v, _ in quality.SETTINGS[0].choices], ['catmull-rom', 'sharp', 'bilinear', 'nearest'])
        self.assertEqual(quality.step(q, 0, 1), {**q, 'filter': 'sharp'})
        self.assertEqual(quality.step(q, 0, -1), {**q, 'filter': 'nearest'})  # wraps round
        self.assertEqual(quality.step(quality.step(q, 0, 2), 0, 2), q)
        self.assertEqual(quality.step(quality.step(q, 1, 1), 1, 1), q)
        self.assertEqual(q, quality.DEFAULTS)  # never changed in place
        video.validate_renderer_payload(quality.renderer_payload(q))
        menu = quality.menu_payload(q, True, 1)
        video.validate_renderer_payload(menu)
        self.assertEqual(menu['rows'], [{'label': 'Texture filter', 'value': 'Catmull-Rom'}, {'label': 'Deband', 'value': 'Off'}])
        self.assertEqual(menu['selected'], 1)
        for s in quality.SETTINGS:
            self.assertLessEqual(len(s.label), quality.MAX_LABEL)
            for _, label in s.choices: self.assertLessEqual(len(label), quality.MAX_LABEL)
        with self.assertRaises(ValueError): quality.renderer_payload({'filter': 'nearest'})
        with self.assertRaises(ValueError): video.validate_renderer_payload({'action': 'quality', 'filter': 'bilinear', 'deband': 0})

    def test_layout_json_keeps_it_and_old_files_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'layout.json'
            path.write_text('{"spacing": 60, "mode": "follow", "prediction": true}')  # written before the menu existed
            self.assertEqual(view.load_settings(path)['video_quality'], quality.DEFAULTS)
            view.save_settings(path, {**view.load_settings(path), 'video_quality': SHARP})
            self.assertEqual(json.loads(path.read_text())['video_quality'], SHARP)
            self.assertEqual(view.load_settings(path)['video_quality'], SHARP)
            self.assertEqual(view.load_settings(path)['spacing'], 60)
            with self.assertRaises(ValueError): view.save_settings(path, {**view.load_settings(path), 'video_quality': {'filter': 'x'}})

    def test_profile_keeps_it_per_glasses_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            luma, beast = ProfileStore(path, 'luma-ultra'), ProfileStore(path, 'beast')
            luma.save({**luma.load(), 'video_quality': SHARP})
            self.assertEqual(luma.load()['video_quality'], SHARP)
            self.assertEqual(beast.load()['video_quality'], quality.DEFAULTS)
            self.assertEqual(profiles.load_profile(path, 'luma-ultra')['video_quality'], SHARP)
            # A profile written before the menu (no key) or with a broken value loads the defaults, keeping the rest.
            data = json.loads(path.read_text())
            del data['profiles']['luma-ultra']['video_quality']
            data['profiles']['beast'] = {**data['profiles']['luma-ultra'], 'video_quality': [1], 'spacing': 80}
            path.write_text(json.dumps(data))
            self.assertEqual(luma.load()['video_quality'], quality.DEFAULTS)
            self.assertEqual(beast.load()['video_quality'], quality.DEFAULTS)
            self.assertEqual(beast.load()['spacing'], 80)


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clip = Path(self.tmp.name) / 'Beach_360_TB.mp4'; self.clip.write_bytes(b'v' * 100)
        self.layout = Path(self.tmp.name) / 'layout.json'
        self.clock = Clock()
        self.v = self.control(LayoutStore(self.layout))

    def control(self, store, state=None):
        vc = view.ViewControl(state or view.ViewState(NAMES), 'run1', 's1', lambda name: None, store, None, clock=self.clock)
        v = video.VideoControl(vc, probe=lambda fd: info(w=4096, h=4096), threads=False, clock=self.clock)
        v.attach_supervisor(FakeSupervisor())
        return v

    def op(self, op, v=None):
        return frames((v or self.v).on_message({'op': op, 'payload': {}}))

    def open(self, v=None):
        v = v or self.v
        v.on_message({'op': 'video_open', 'payload': {'path': str(self.clip)}})
        out = v.poll(self.clock())
        self.addCleanup(out.close_fds)
        return frames(out)

    def payloads(self, op, v=None):
        return [f['payload'] for f in self.op(op, v)]

    def test_q_opens_the_menu_on_the_card(self):
        self.open()
        self.v.controls = False  # as if the card had closed
        out = self.payloads('video_quality')
        self.assertEqual(out[0], {'action': 'controls', 'visible': True})
        self.assertEqual(out[1], {'action': 'menu', 'visible': True, 'selected': 0,
                                  'rows': [{'label': 'Texture filter', 'value': 'Catmull-Rom'}, {'label': 'Deband', 'value': 'Off'},
                                           {'label': 'Background', 'value': 'None'}]})  # environment.py's row
        self.assertTrue(self.v.snapshot()['quality_menu'])
        for p in out: video.validate_renderer_payload(p)
        self.assertEqual(self.payloads('video_quality'), [{**out[1], 'visible': False, 'selected': 0}])  # Q again closes
        self.assertTrue(self.v.active and self.v.controls)  # the video and its card stay

    def test_left_right_change_live_and_save(self):
        self.open()
        self.op('video_quality')
        out = self.payloads('video_seek_fwd')
        self.assertEqual(out[0], {'action': 'quality', 'filter': 'sharp', 'deband': False})  # to the renderer at once
        self.assertEqual(out[1]['rows'][0], {'label': 'Texture filter', 'value': 'Sharp'})
        self.assertEqual(view.load_settings(self.layout)['video_quality'], {'filter': 'sharp', 'deband': False})  # saved in layout.json
        self.assertEqual(self.payloads('video_down')[0]['selected'], 1)
        out = self.payloads('video_seek_back_big')  # Shift+Left: the same one step
        self.assertEqual(out[0], {'action': 'quality', 'filter': 'sharp', 'deband': True})
        self.assertEqual(out[1]['rows'][1], {'label': 'Deband', 'value': 'On'})
        self.assertEqual(view.load_settings(self.layout)['video_quality'], {'filter': 'sharp', 'deband': True})
        self.assertEqual(self.v.snapshot()['quality'], {'filter': 'sharp', 'deband': True})
        self.assertEqual(self.v.quality(), quality.video_quality(self.v.view.state))  # one accessor

    def test_menu_keys_do_not_seek_or_leave(self):
        self.open()
        self.op('video_quality')
        for op in ('video_seek_back', 'video_seek_fwd', 'video_seek_back_big', 'video_seek_fwd_big'):
            self.assertNotIn('seek', [p['action'] for p in self.payloads(op)])
        self.assertEqual([p['selected'] for p in (self.payloads('video_up')[0], self.payloads('video_up')[0])], [2, 1])  # wraps
        self.payloads('video_up')  # back on the first row
        self.assertEqual(self.payloads('video_format')[0]['selected'], 1)  # Tab: next setting, not the next format
        self.assertEqual(self.v.format['stereo'], 'tb')
        self.assertEqual([p['action'] for p in self.payloads('video_pause')], ['pause'])  # Space still plays / pauses
        out = self.payloads('video_exit')  # Escape closes the menu, not the video
        self.assertEqual([(p['action'], p.get('visible')) for p in out], [('menu', False)])
        self.assertTrue(self.v.active)
        self.assertEqual(self.payloads('video_exit'), [{'action': 'close'}])  # and then leaves as before

    def test_up_down_without_the_menu_still_toggle_the_card(self):
        self.open()
        self.assertEqual(self.payloads('video_up'), [{'action': 'controls', 'visible': False}])
        self.assertEqual(self.payloads('video_down'), [{'action': 'controls', 'visible': True}])
        self.assertEqual(self.payloads('video_seek_fwd')[-1], {'action': 'seek', 'seconds': 10})

    def test_hiding_the_card_closes_the_menu(self):
        self.open()
        self.op('video_quality')
        out = self.payloads('help')  # Ctrl+Alt+H
        self.assertEqual([(p['action'], p['visible']) for p in out], [('menu', False), ('controls', False)])
        self.assertFalse(self.v.menu)
        self.op('video_quality')
        self.v.supervisor.status = {'video': 'playing'}
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S
        out = [f['payload'] for f in frames(self.v.poll(self.clock()))]
        self.assertEqual([(p['action'], p['visible']) for p in out], [('menu', False), ('controls', False)])

    def test_saved_settings_reach_the_renderer_on_every_open(self):
        self.open()
        self.op('video_quality'); self.op('video_seek_fwd')
        self.op('video_exit')  # closes the menu
        self.assertEqual(self.payloads('video_exit')[-1], {'action': 'close'})
        self.assertFalse(self.v.menu)
        # A new run reads layout.json back (managed.py: ViewState(..., video_quality(saved))) and sends it after the open.
        saved = view.load_settings(self.layout)
        v2 = self.control(LayoutStore(self.layout), view.ViewState(NAMES, video_quality=quality.video_quality(saved)))
        out = [f['payload'] for f in self.open(v2)]
        self.assertEqual([p['action'] for p in out], ['open', 'quality'])
        self.assertEqual(out[1], {'action': 'quality', **SHARP})
        self.assertFalse(v2.snapshot()['quality_menu'])

    def test_profile_store_saves_the_menus_changes(self):
        path = Path(self.tmp.name) / 'profiles.json'
        v = self.control(ProfileStore(path, 'luma-ultra'))
        self.open(v)
        self.op('video_quality', v); self.op('video_seek_fwd', v)
        self.assertEqual(profiles.load_profile(path, 'luma-ultra')['video_quality'], SHARP)
        self.assertFalse(self.layout.exists())

    def test_a_failed_save_still_applies(self):
        class Broken:
            def save(self, settings): raise OSError('read-only')
        v = self.control(Broken())
        self.open(v)
        self.op('video_quality', v)
        self.assertEqual(self.payloads('video_seek_fwd', v)[0], {'action': 'quality', **SHARP})
        self.assertEqual(v.quality(), SHARP)

    def test_menu_keys_do_nothing_without_a_video(self):
        for op in video.QUALITY_OPS:
            self.assertEqual(self.op(op), [])
        self.assertFalse(self.v.menu)


if __name__ == '__main__':
    unittest.main()
