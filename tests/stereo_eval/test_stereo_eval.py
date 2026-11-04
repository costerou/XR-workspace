"""tools/stereo_eval: the geometry, the conversion stages and above all the metrics. A metric that cannot tell a bad result
from a good one is worse than none, so each test builds both from a scene with a known answer. No model, no network, no GPU."""
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from tools.stereo_eval import conditioning as c, dibr, geometry as g, metrics as m, preview, presets, run, samples
from tools.stereo_eval.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
W, H = 480, 300


def texture(w=W, h=H, seed=1):
    """Natural-looking noise: not flat, so nothing here reads as text."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 40, (h, w, 3)).astype(np.float32)
    for i in range(3):
        noise[..., i] = c.box(noise[..., i], 2)
    return np.clip(120 + noise * 3, 0, 255).astype(np.uint8)


def scene():
    """Background, a nearer square, and a nearness map that matches."""
    rgb, near = texture(), np.zeros((H, W), np.float32)
    rgb[100:200, 180:300] = texture(120, 100, 2)
    near[100:200, 180:300] = 1
    return rgb, near


def page(w=480, h=300):
    """Black strokes on white, like a block of text."""
    rng = np.random.default_rng(3)
    out = np.full((h, w, 3), 245, np.uint8)
    for y in range(20, h - 20, 14):
        x = 20
        while x < w - 30:
            n = int(rng.integers(2, 7))
            for i in range(n):
                out[y:y + 8, x:x + 2] = 20
                out[y + (i % 3) * 3:y + (i % 3) * 3 + 2, x:x + 5] = 20
                x += 6
            x += 6
    return out


class Geometry(unittest.TestCase):
    def test_luma_ultra_numbers(self):
        o = g.Optics()
        self.assertAlmostEqual(o.horizontal_fov_deg, 44.94, 1)
        self.assertAlmostEqual(o.px_per_degree, 40.5, 0)
        self.assertAlmostEqual(o.arcmin_to_percent_width(27), 1.0, 1)

    def test_shibata_at_3m_stops_at_parallel_eyes(self):
        z = g.shibata_limits(g.Optics(focal_distance_m=3))
        self.assertAlmostEqual(z.near_arcmin, 128.6, 0)
        self.assertAlmostEqual(z.far_arcmin, 72.2, 0)
        self.assertEqual(z.far_vergence_m, float('inf'))
        self.assertAlmostEqual(z.near_vergence_m, 1.08, 1)

    def test_shibata_matches_the_papers_worked_example(self):
        z = g.shibata_limits(g.Optics(focal_distance_m=1 / 1.3))
        self.assertAlmostEqual(z.near_vergence_m, 0.54, 2)
        self.assertAlmostEqual(z.far_vergence_m, 1.3, 1)

    def test_every_preset_is_inside_the_zone_and_within_the_sky_limits(self):
        z, o = g.shibata_limits(), g.Optics()
        for name in presets.CONTENT:
            p = presets.get(name)
            self.assertLess(p['near_arcmin'], z.near_arcmin / 2, name)
            self.assertLess(p['far_arcmin'], z.far_arcmin * 0.75, name)
            self.assertLessEqual(o.arcmin_to_percent_width(p['near_arcmin']), 1.0, name)
            self.assertLessEqual(o.arcmin_to_percent_width(p['far_arcmin']), 2.0, name)
            self.assertLess(p['near_arcmin'], p['far_arcmin'], name)


class Dibr(unittest.TestCase):
    def test_zero_disparity_is_identity(self):
        rgb, _ = scene()
        for eye in dibr.render_pair(rgb, np.zeros((H, W), np.float32)):
            np.testing.assert_array_equal(eye.image, rgb.astype(np.float32))
            self.assertFalse(eye.holes.any())

    def test_a_pinned_row_stays_exact_while_others_shift(self):
        rgb, _ = scene()
        d = np.full((H, W), 8, np.float32)
        d[50:60] = 0
        left, right = dibr.render_pair(rgb, d)
        np.testing.assert_array_equal(left.image[50:60], rgb[50:60].astype(np.float32))

    def test_near_object_moves_toward_the_nose_in_each_eye(self):
        rgb, near = scene()
        d = -near * 16  # in front of the plane
        left, right = dibr.render_pair(rgb, d)
        # Each eye moves it 8 px: right in the left eye, left in the right eye.
        np.testing.assert_allclose(left.image[150, 240], rgb[150, 232], atol=0.5)
        np.testing.assert_allclose(right.image[150, 240], rgb[150, 248], atol=0.5)

    def test_foreground_covers_background_and_holes_open_on_the_far_side_only(self):
        rgb, near = scene()
        left, right = dibr.render_pair(rgb, -near * 16)
        self.assertTrue(left.holes[150, 180:188].all())   # left eye: object moved right, gap on its left
        self.assertFalse(left.holes[150, 188:320].any())
        self.assertTrue(right.holes[150, 292:300].all())
        self.assertEqual(left.holes.sum(), 100 * 8)

    def test_fill_comes_from_the_background_in_both_eyes(self):
        rgb, near = scene()
        rgb = rgb.copy()
        rgb[100:200, 180:300] = 5  # a black object on a bright background: any smear of it into a hole is obvious
        for eye in dibr.render_pair(rgb, -near * 16, 'stretch'):
            self.assertGreater(eye.image[..., 1][eye.holes].min(), 40)

    def test_split_puts_all_the_work_on_one_eye(self):
        rgb, near = scene()
        left, right = dibr.render_pair(rgb, -near * 16, split=0.0)
        np.testing.assert_array_equal(left.image, rgb.astype(np.float32))
        self.assertTrue(right.holes.any())

    def test_never_brighter_than_the_source(self):
        rgb, near = scene()
        for fill in ('black', 'foreground', 'stretch', 'mirror'):
            left, right = dibr.render_pair(rgb, -near * 16 + 4, fill)
            self.assertEqual(m.brightness_excess(rgb, left.image, right.image), 0, fill)

    def test_bad_fill_name(self):
        rgb, near = scene()
        with self.assertRaises(ValueError):
            dibr.render_pair(rgb, -near * 16, 'nope')


class Conditioning(unittest.TestCase):
    def preset(self, **change):
        return {**presets.get('video'), 'edge_fade': 0, 'soften_px': 0, 'guided_radius': 0, 'cut_ease_ms': 0, **change}

    def test_box_is_a_mean_and_keeps_the_shape(self):
        a = np.arange(30, dtype=np.float32).reshape(5, 6)
        out = c.box(a, 1)
        self.assertEqual(out.shape, a.shape)
        self.assertAlmostEqual(float(out[2, 2]), float(a[1:4, 1:4].mean()), 4)

    def test_soft_clip_never_passes_the_limits_and_is_monotonic(self):
        x = np.linspace(-500, 500, 2001, dtype=np.float32)
        y = c.soft_clip(x, 10, 30)
        self.assertGreaterEqual(y.min(), -10)
        self.assertLessEqual(y.max(), 30)
        self.assertTrue((np.diff(y) >= 0).all())
        self.assertEqual(float(c.soft_clip(np.array([5.0]), 10, 30)[0]), 5.0)

    def test_nearer_is_in_front(self):
        rgb, near = scene()
        out = c.Conditioner(self.preset()).step(rgb, near + np.linspace(0, 0.5, H)[:, None])
        self.assertLess(out.disparity_arcmin[150, 240], out.disparity_arcmin[20, 40])

    def test_budget_is_held_whatever_the_models_scale(self):
        rgb, near = scene()
        for scale in (0.01, 1, 1000):
            d = c.Conditioner(self.preset()).step(rgb, near * scale).disparity_arcmin
            self.assertGreaterEqual(float(d.min()), -14.001)
            self.assertLessEqual(float(d.max()), 36.001)

    def test_a_flat_scene_stays_flat_despite_model_noise(self):
        rgb, _ = scene()
        rng = np.random.default_rng(1)
        noise = c.box(rng.normal(0, 0.1, (H, W)).astype(np.float32), 8)
        out = c.Conditioner(self.preset()).step(rgb, 5.0 + noise)
        self.assertEqual(out.gain, 0)
        self.assertEqual(float(np.abs(out.disparity_arcmin).max()), 0)

    def test_frame_edges_sit_on_the_plane(self):
        rgb, near = scene()
        d = c.Conditioner(self.preset(edge_fade=0.05)).step(rgb, near + 0.5 * np.linspace(0, 1, W)).disparity_arcmin
        self.assertTrue((d[:, 0] == 0).all() and (d[:, -1] == 0).all() and (d[0] == 0).all())

    def test_letterbox_bars_stay_on_the_plane_and_do_not_set_the_range(self):
        rgb, near = scene()
        boxed = rgb.copy()
        boxed[:40] = 0
        boxed[-40:] = 0
        depth = near.copy()
        depth[:40] = 3  # what the model does: bars read as the nearest thing there is
        depth[-40:] = 3
        d = c.Conditioner(self.preset()).step(boxed, depth).disparity_arcmin
        self.assertTrue((d[:40] == 0).all() and (d[-40:] == 0).all())
        self.assertLess(d[150, 240], -5)  # the object still comes forward

        off = c.Conditioner(self.preset(letterbox=False)).step(boxed, depth).disparity_arcmin
        self.assertLess(off[10, 200], -5)  # and this is the fault it prevents

    def test_content_box(self):
        gray = np.zeros((100, 200), np.float32)
        gray[10:90, 20:180] = 100
        self.assertEqual(c.content_box(gray), (20, 10, 180, 90))
        self.assertIsNone(c.content_box(np.zeros((10, 10))))

    def test_text_is_found_on_a_page_but_not_in_a_textured_scene(self):
        self.assertGreater(c.text_mask(c.luma(page())).mean(), 0.6)
        self.assertLess(c.text_mask(c.luma(texture())).mean(), 0.02)

    def test_pinned_text_is_pixel_identical_in_both_eyes(self):
        rgb = page()
        rgb[:, :200] = texture(200, 300)
        near = np.linspace(0, 1, 480, dtype=np.float32)[None, :].repeat(300, 0)
        p = presets.get('desktop', edge_fade=0, soften_px=0, guided_radius=0)
        out = c.Conditioner(p).step(rgb, near)
        mask = c.text_mask(c.luma(rgb))
        self.assertGreater(mask.mean(), 0.2)
        d_px = out.disparity_arcmin * 1.0
        left, right = dibr.render_pair(rgb, d_px)
        core = c.grow(~mask, 8)
        self.assertTrue((~core).any())
        np.testing.assert_array_equal(left.image[~core], rgb[~core].astype(np.float32))
        np.testing.assert_array_equal(right.image[~core], rgb[~core].astype(np.float32))

    def test_temporal_smoothing_cuts_flicker_on_a_still_picture(self):
        rgb, near = scene()
        rng = np.random.default_rng(5)
        noisy = [near + c.box(rng.normal(0, 0.3, near.shape).astype(np.float32), 6) for _ in range(30)]
        frames = [rgb] * 30
        off, _ = run.convert(frames, noisy, self.preset(temporal_still_ms=0, temporal_motion_ms=0), 30)
        on, _ = run.convert(frames, noisy, self.preset(), 30)
        self.assertLess(m.flicker(frames, on, 30)['still_flicker_arcmin'] * 3, m.flicker(frames, off, 30)['still_flicker_arcmin'])

    def test_a_cut_is_taken_at_once_not_smeared(self):
        a, near = scene()
        b = (texture(seed=9) // 3 + 180).astype(np.uint8)
        cond = c.Conditioner(self.preset(cut_ease_ms=0))
        for _ in range(10):
            cond.step(a, near)
        after = cond.step(b, 1 - near)
        self.assertTrue(after.cut)
        self.assertGreater(after.disparity_arcmin[150, 240], after.disparity_arcmin[20, 40])

    def test_cut_ease_ramps_the_depth_back(self):
        a, near = scene()
        b = (texture(seed=9) // 3 + 180).astype(np.uint8)
        cond = c.Conditioner(self.preset(cut_ease_ms=200))
        cond.step(a, near)
        first = np.abs(cond.step(b, near).disparity_arcmin).max()
        later = [np.abs(cond.step(b, near).disparity_arcmin).max() for _ in range(8)]
        self.assertEqual(first, 0)
        self.assertGreater(later[-1], 5)

    def test_hud_lock_pins_what_stays_still_while_the_world_moves(self):
        world, near = texture(), np.linspace(0, 1, H, dtype=np.float32)[:, None].repeat(W, 1)
        frames = []
        for t in range(30):
            f = np.roll(world, -t * 6, axis=1)
            f[220:290, 40:200] = 90
            f[220:290, 40:200, 1] = 90 + (np.arange(160) % 7)
            frames.append(f)
        p = presets.get('games', edge_fade=0, soften_px=0, guided_radius=0, text_pin=0)
        cond = c.Conditioner(p, fps=30)
        for f in frames:
            out = cond.step(f, near)
        self.assertEqual(float(out.disparity_arcmin[250:270, 90:150].max()), 0)
        self.assertNotEqual(float(out.disparity_arcmin[120:150, 90:150].mean()), 0)

    def test_time_constant_does_not_depend_on_the_depth_rate(self):
        slow = 1 - (1 - c.time_constant(33.3, 300)) ** 1
        fast = 1 - (1 - c.time_constant(16.65, 300)) ** 2
        self.assertAlmostEqual(slow, fast, 6)


class Metrics(unittest.TestCase):
    def test_disparity_stats_reads_a_known_map(self):
        d = np.zeros((H, W), np.float32)
        d[:, 100:200] = -20
        d[:, 200:300] = 40
        s = m.disparity_stats(d)
        self.assertAlmostEqual(s['near_arcmin'], 20, 1)
        self.assertAlmostEqual(s['far_arcmin'], 40, 1)
        self.assertAlmostEqual(s['in_front_share'], 100 / W, 2)
        self.assertEqual(s['outside_zone_share'], 0)
        self.assertEqual(s['border_violation_share'], 0)

    def test_disparity_stats_flags_a_window_violation_and_leaving_the_zone(self):
        d = np.zeros((H, W), np.float32)
        d[:, :5] = -20
        d[:, 300:400] = 200
        s = m.disparity_stats(d)
        self.assertGreater(s['border_violation_share'], 0.25)
        self.assertGreater(s['outside_zone_share'], 0.15)

    def test_flicker_sees_noise_on_a_still_picture(self):
        rgb, _ = scene()
        rng = np.random.default_rng(1)
        steady = [np.ones((H, W), np.float32)] * 5
        noisy = [np.ones((H, W), np.float32) + rng.normal(0, 2, (H, W)).astype(np.float32) for _ in range(5)]
        self.assertEqual(m.flicker([rgb] * 5, steady, 30)['still_flicker_arcmin'], 0)
        self.assertGreater(m.flicker([rgb] * 5, noisy, 30)['still_flicker_arcmin'], 1.5)

    def test_flicker_does_not_blame_a_moving_picture_or_a_steady_ramp(self):
        frames = [np.roll(texture(), t * 8, axis=1) for t in range(6)]
        maps = [np.full((H, W), t * 1.0, np.float32) for t in range(6)]
        f = m.flicker(frames, maps, 30)
        self.assertLess(f['still_share'], 0.1)
        self.assertAlmostEqual(f['jitter_arcmin'], 0, 5)
        self.assertAlmostEqual(f['pump_arcmin'], 1, 5)

    def test_flicker_needs_two_frames(self):
        with self.assertRaises(ValueError):
            m.flicker([np.zeros((4, 4, 3))], [np.zeros((4, 4))], 30)

    def test_halo_ranks_fills_from_worst_to_best(self):
        rgb, near = scene()
        rgb = rgb.copy()
        rgb[100:200, 180:300] = 15  # a dark object, so a foreground smear shows
        d = -near * 40
        scores = {f: m.halo(dibr.render_eye(rgb, d, 'left', f))['halo_score'] for f in ('black', 'foreground', 'stretch', 'mirror')}
        self.assertGreater(scores['black'], scores['stretch'])
        self.assertGreater(scores['foreground'], scores['stretch'])
        self.assertGreater(scores['stretch'], scores['mirror'])

    def test_halo_is_zero_without_holes_and_grows_with_disparity(self):
        rgb, near = scene()
        none = m.halo(dibr.render_eye(rgb, np.zeros((H, W), np.float32), 'left'))
        self.assertEqual(none['halo_score'], 0)
        small = m.halo(dibr.render_eye(rgb, -near * 10, 'left', 'stretch'))
        big = m.halo(dibr.render_eye(rgb, -near * 40, 'left', 'stretch'))
        self.assertGreater(big['halo_score'], small['halo_score'])
        self.assertAlmostEqual(big['hole_width_p95_px'], 20, delta=1.5)

    def test_edge_misalignment_sees_a_depth_edge_off_the_picture_edge(self):
        rgb, near = scene()
        gray = c.luma(rgb * 0 + 40)
        gray[100:200, 180:300] = 200
        d = -near * 20
        self.assertEqual(m.edge_misalignment(d, gray), 0)
        self.assertGreater(m.edge_misalignment(np.roll(d, 7, axis=1), gray), 3)

    def test_text_score_forgives_a_whole_shift_and_punishes_damage(self):
        rgb = page()
        mask = np.ones(rgb.shape[:2], bool)
        self.assertGreater(m.text_score(rgb, rgb, mask)['text_ssim'], 0.999)
        shifted = np.roll(rgb, 5, axis=1)
        self.assertGreater(m.text_score(rgb, shifted, mask)['text_ssim'], 0.98)
        half = dibr.render_eye(rgb, np.full(rgb.shape[:2], 0.5, np.float32), 'right', split=0.0).image
        blurred = m.text_score(rgb, half, mask)['text_ssim']
        wavy = -np.sin(np.arange(rgb.shape[1]) / 3)[None, :].repeat(rgb.shape[0], 0).astype(np.float32) * 3
        warped = m.text_score(rgb, dibr.render_eye(rgb, wavy, 'right', split=0.0).image, mask)['text_ssim']
        self.assertLess(blurred, 0.95)
        self.assertLess(warped, blurred)

    def test_text_wobble(self):
        mask = np.ones((H, W), bool)
        self.assertEqual(m.text_wobble(np.full((H, W), 7, np.float32), mask), 0)
        wavy = np.sin(np.arange(W) / 5)[None, :].repeat(H, 0).astype(np.float32) * 10
        self.assertGreater(m.text_wobble(wavy, mask), 3)
        self.assertEqual(m.text_wobble(wavy, np.zeros((H, W), bool)), 0)

    def test_estimate_disparity_recovers_a_known_pair(self):
        rgb, near = scene()
        d = -near * 16 + 6
        left, right = dibr.render_pair(rgb, d)
        estimate, occluded = m.estimate_disparity(left.image, right.image, 30)
        self.assertAlmostEqual(float(np.median(estimate[30:70, 60:400])), 6, delta=2)
        self.assertAlmostEqual(float(np.median(estimate[130:170, 215:265])), -10, delta=2)
        self.assertGreater(occluded[95:205, 160:320].mean(), occluded[:80].mean())

    def test_estimate_disparity_is_not_fooled_by_repeating_glyphs(self):
        rgb = page(480, 300)
        estimate, _ = m.estimate_disparity(rgb.astype(np.float32), rgb.astype(np.float32), 30)
        self.assertEqual(float(np.abs(estimate).max()), 0)

    def test_targets(self):
        result = {'a': {'b': 3.0}, 'c': 1.0}
        self.assertEqual(m.check_targets(result, {'a.b': {'max': 5}, 'c': {'min': 0.5}, 'missing.key': {'max': 1}}), [])
        self.assertEqual(len(m.check_targets(result, {'a.b': {'max': 2}, 'c': {'min': 2}})), 2)


class Previews(unittest.TestCase):
    def test_side_by_side_is_left_then_right(self):
        l, r = np.zeros((4, 8, 3), np.float32), np.full((4, 8, 3), 200, np.float32)
        out = preview.side_by_side(l, r)
        self.assertEqual(out.shape, (4, 16, 3))
        self.assertEqual(out[0, 0, 0], 0)
        self.assertEqual(out[0, 15, 0], 200)

    def test_anaglyph_puts_left_in_red_and_a_flat_image_stays_neutralish(self):
        l = np.zeros((2, 2, 3), np.float32)
        l[..., 1] = 255
        out = preview.anaglyph(l, np.zeros_like(l))
        self.assertGreater(out[0, 0, 0], 100)
        self.assertEqual(out[0, 0, 1], 0)

    def test_disparity_view_colours(self):
        v = preview.disparity_view(np.array([[-10.0, 0.0, 30.0]]), 10, 30)
        self.assertGreater(v[0, 0, 0], v[0, 0, 2])
        self.assertEqual(v[0, 1, 0], v[0, 1, 1])
        self.assertGreater(v[0, 2, 2], v[0, 2, 0])


class Samples(unittest.TestCase):
    def test_desktop_is_the_desktop_size_and_scrolls(self):
        a, b = samples.desktop(0), samples.desktop(40)
        self.assertEqual(a.shape, (samples.H, samples.W, 3))
        self.assertTrue((a[300:500, 1000:1800] != b[300:500, 1000:1800]).any())
        np.testing.assert_array_equal(a[100:800, 40:900], b[100:800, 40:900])

    def test_fit_letterboxes(self):
        out = samples.fit(np.full((534, 1280, 3), 200, np.uint8))
        self.assertEqual(out.shape, (1200, 1920, 3))
        self.assertEqual(c.content_box(c.luma(out)), (0, 199, 1920, 1000))


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def run_main(self, *args):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(a) for a in args])
        return code, out.getvalue()

    def test_eval_with_a_saved_map_writes_pictures_and_json(self):
        rgb, near = scene()
        Image.fromarray(rgb).save(self.dir / 'f.png')
        np.save(self.dir / 'd.npy', near)
        code, text = self.run_main('eval', self.dir / 'f.png', '--depth', self.dir / 'd.npy', '--preset', 'video', '--json', '--out', self.dir / 'o')
        self.assertIn('halo', json.loads(text))
        self.assertTrue((self.dir / 'o/sbs.png').exists())
        self.assertEqual(Image.open(self.dir / 'o/sbs.png').size, (3840, 1200))

    def test_a_folder_of_frames_gets_flicker(self):
        rgb, near = scene()
        (self.dir / 'f').mkdir()
        for i in range(4):
            Image.fromarray(rgb).save(self.dir / f'f/{i}.png')
        np.save(self.dir / 'd.npy', near)
        _, text = self.run_main('eval', self.dir / 'f', '--depth', self.dir / 'd.npy', '--preset', 'video', '--json')
        self.assertEqual(json.loads(text)['flicker']['frames'], 4)

    def test_sbs_scores_recorded_pairs_without_depth(self):
        rgb, near = scene()
        big = np.asarray(Image.fromarray(rgb).resize((1920, 1200), Image.BICUBIC))
        d = np.asarray(Image.fromarray(near).resize((1920, 1200)), np.float32)
        left, right = dibr.render_pair(big, -d * 12 + 4)
        Image.fromarray(preview.side_by_side(left.image, right.image)).save(self.dir / 's.png')
        _, text = self.run_main('sbs', self.dir / 's.png', '--json')
        out = json.loads(text)
        self.assertGreater(out['disparity']['far_arcmin'], 2)
        self.assertIn('occluded_share', out)


class Docs(unittest.TestCase):
    def test_the_doc_table_matches_presets_json(self):
        doc = (ROOT / 'docs/3d-comfort.md').read_text()
        table = doc[doc.index('<!-- presets -->'):doc.index('<!-- /presets -->')]
        fields = {'Near limit': 'near_arcmin', 'Far limit': 'far_arcmin', 'Plane at percentile': 'anchor_pct',
                  'Depth smoothing, still': 'temporal_still_ms', 'Depth smoothing, moving': 'temporal_motion_ms',
                  'Range smoothing': 'range_ms', 'Edge fade': 'edge_fade', 'Soften': 'soften_px'}
        for label, key in fields.items():
            row = re.search(rf'^\| {label}[^|]*\|(.*)\|$', table, re.M)
            self.assertIsNotNone(row, label)
            cells = [re.sub(r'[^0-9.]', '', x) for x in row.group(1).split('|')]
            for name, cell in zip(presets.CONTENT, cells):
                self.assertAlmostEqual(float(cell), float(presets.get(name)[key]) * (100 if key == 'edge_fade' else 1), msg=f'{label} {name}')


if __name__ == '__main__':
    unittest.main()
