"""The sweeps behind docs/3d-comfort.md, and the sample gallery. Needs the weights, the samples and onnxruntime; CPU only."""
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from . import depth_source as ds, metrics, preview, presets, run, samples
from .conditioning import luma, text_mask
from .dibr import render_pair
from .geometry import Optics, shibata_limits

OPTICS = Optics()
STRENGTHS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
STAGES = {'all on': {}, 'no letterbox': {'letterbox': False}, 'no guided filter': {'guided_radius': 0},
          'no temporal': {'temporal_still_ms': 0, 'temporal_motion_ms': 0}, 'no text pin': {'text_pin': 0},
          'no HUD lock': {'hud_lock': False}, 'no soften': {'soften_px': 0}, 'no edge fade': {'edge_fade': 0}}


def sequences(n: int = 30) -> dict:
    """name: (frames, fps, preset). The video clip is a real shot; the desktop scrolls; the game pans (see samples.game_pan)."""
    clip = run.load_frames(samples.CLIP, 40)
    still = clip[0][120:1080]
    return {'desktop': ([samples.desktop(scroll=(i * 3) % 90, picture=still) for i in range(n)], 30.0, 'desktop'),
            'game': (samples.game_pan(n), 30.0, 'games'),
            'video': (clip[:n], 24.0, 'video')}


def depths(frames, height=518):
    return [ds.infer_content(f, height) for f in frames]


def flat(result: dict) -> dict:
    """The numbers the doc quotes."""
    d, h, t, f = result['disparity'], result['halo'], result['text'], result.get('flicker', {})
    return {'near': d['near_arcmin'], 'far': d['far_arcmin'], 'on_plane': d['on_plane_share'], 'border': d['border_violation_share'],
            'halo': h['halo_score'], 'holes': h['hole_share'], 'hole_p95': h['hole_width_p95_px'],
            'misalign': result['edge_misalignment_px'], 'text_ssim': t['text_ssim'], 'text_p10': t['text_ssim_p10'],
            'binocular': t['binocular_ssim'], 'wobble': t['wobble_arcmin'], 'still_flicker': f.get('still_flicker_arcmin'),
            'still_share': f.get('still_share'), 'pump': f.get('pump_arcmin'), 'jitter': f.get('jitter_arcmin')}


def measure(frames, maps_in, preset, fps, fill='mirror', split=0.5):
    maps, result = run.convert(frames, maps_in, preset, fps, fill=fill, split=split)
    return flat(run.score(frames, maps, result, fps))


def text_shift_study(frame: np.ndarray) -> list[dict]:
    """A whole page moved by a fraction of a pixel: what bilinear resampling alone costs text."""
    h, w = frame.shape[:2]
    mask = text_mask(luma(frame))
    box = (988, 92, 884, 604)
    rows = []
    for shift in (0.0, 0.25, 0.5, 1.0):
        d = np.full((h, w), shift * 2, np.float32)
        left, right = render_pair(frame, d, 'mirror', 0.5)
        t = metrics.text_score(frame, right.image, mask)
        rows.append({'shift_px': shift, 'text_ssim': t['text_ssim'], 'ocr': metrics.ocr_match(frame, right.image, box)})
    return rows


def extras(seqs: dict) -> dict:
    """One-off questions: leftover parallax on text, bars reaching the model, a page of nothing but text, and model speed."""
    out = {}
    frames, fps, _ = seqs['desktop']
    maps = depths(frames)
    box = (988, 92, 884, 604)
    # stereo.hpp's defaults, with the share of parallax text keeps as the variable.
    wide = presets.get('desktop', near_arcmin=60, far_arcmin=45, strength=0.6, anchor_pct=40)
    out['text_keep'] = {}
    for keep in (0.0, 0.15, 0.5, 1.0):
        d, r = run.convert(frames, maps, {**wide, 'text_keep': keep}, fps)
        row = flat(run.score(frames, d, r, fps))
        row['ocr'] = metrics.ocr_match(r.frame, r.right.image, box)
        out['text_keep'][str(keep)] = row

    frame = seqs['video'][0][0]
    x0, y0, x1, y1 = 0, 199, 1920, 1000
    bars_seen = ds.infer(frame)
    p = presets.get('video', letterbox=False)
    seen = run.convert([frame], [bars_seen], p)[1].conditioned.disparity_arcmin
    p = presets.get('video')
    cropped = run.convert([frame], [ds.infer_content(frame)], p)[1].conditioned.disparity_arcmin
    subject = (y0 + 200, y0 + 500, 700, 1100)  # the man in the middle
    area = lambda d: float(np.median(d[subject[0]:subject[1], subject[2]:subject[3]]))
    out['bars'] = {'reach_model': {'bars_median': float(np.median(seen[:y0 - 40])), 'subject_median': area(seen)},
                   'cropped': {'bars_median': float(np.median(cropped[:y0 - 40])), 'subject_median': area(cropped)}}

    page = Image.fromarray(samples.desktop()).crop((988, 92, 1872, 696)).resize((1920, 1200), Image.BICUBIC)
    page = np.asarray(page, dtype=np.uint8).copy()
    depth = ds.infer_content(page)
    naive = run.convert([page], [depth], presets.get('naive'))[1]
    tuned = run.convert([page], [depth], presets.get('desktop', text_pin=0))[1]
    out['full_page'] = {'naive_range': metrics.disparity_stats(naive.conditioned.disparity_arcmin)['range_arcmin'],
                        'desktop_no_pin_range': metrics.disparity_stats(tuned.conditioned.disparity_arcmin)['range_arcmin'],
                        'gain': tuned.conditioned.gain}

    timing = {}
    still = seqs['video'][0][0]
    for height in (140, 252, 364, 518):
        start = time.perf_counter()
        for _ in range(5):
            ds.infer(still, height, cache=False)
        timing[str(height)] = (time.perf_counter() - start) / 5 * 1000
    out['cpu_ms_per_frame'] = timing
    return out


def sweep(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    data = {'optics': {'horizontal_fov_deg': OPTICS.horizontal_fov_deg, 'px_per_arcmin': OPTICS.px_per_arcmin,
                       'shibata': vars(shibata_limits(OPTICS))}}
    seqs = sequences()
    for name, (frames, fps, preset_name) in seqs.items():
        maps = depths(frames)
        base = presets.get(preset_name)
        entry = {'preset': preset_name, 'naive': measure(frames, maps, presets.get('naive'), fps), 'stages': {}, 'strength': {}, 'fill': {}, 'split': {},
                 'temporal': {}, 'depth_height': {}}
        for label, change in STAGES.items():
            entry['stages'][label] = measure(frames, maps, {**base, **change}, fps)
        for s in STRENGTHS:
            entry['strength'][str(s)] = measure(frames, maps, {**base, 'strength': s}, fps)
        for fill in ('black', 'foreground', 'stretch', 'mirror'):
            # Fill matters when there are holes, so these run at the naive budget.
            entry['fill'][fill] = measure(frames, maps, {**base, 'near_arcmin': 40, 'far_arcmin': 60, 'soften_px': 0}, fps, fill)
        for split in (0.5, 1.0):
            entry['split'][str(split)] = measure(frames, maps, base, fps, split=split)
        for scale in (0, 0.5, 1, 2, 4):
            entry['temporal'][str(scale)] = measure(frames, maps, {**base, 'temporal_still_ms': base['temporal_still_ms'] * scale,
                                                                   'temporal_motion_ms': base['temporal_motion_ms'] * scale}, fps)
        for height in (140, 252, 364):
            entry['depth_height'][str(height)] = measure(frames, depths(frames, height), base, fps)
        entry['depth_height']['518'] = entry['stages']['all on']
        data[name] = entry
        print(name, 'done', flush=True)
    data['desktop']['text_shift'] = text_shift_study(seqs['desktop'][0][0])
    data['extras'] = extras(seqs)
    (out / 'sweep.json').write_text(json.dumps(data, indent=1))
    print(out / 'sweep.json')
    return 0


def gallery(out: Path) -> int:
    """Per sample: side-by-side (put it on the glasses in their 3D mode), anaglyph (red-cyan glasses), and naive vs preset."""
    out.mkdir(parents=True, exist_ok=True)
    seqs = sequences(24)
    rows, report = [], {}
    names = {'desktop': 'desktop-text', 'game': 'game-screenshot', 'video': 'video-frame'}
    for key, (frames, fps, preset_name) in seqs.items():
        maps = depths(frames)
        preset = presets.get(preset_name)
        d, r = run.convert(frames, maps, preset, fps)
        nd, nr = run.convert(frames, maps, presets.get('naive'), fps)
        report[key] = {'preset': flat(run.score(frames, d, r, fps)), 'naive': flat(run.score(frames, nd, nr, fps))}
        stem = names[key]
        preview.save(out / f'{stem}-source.png', r.frame)
        preview.save(out / f'{stem}-sbs.png', preview.side_by_side(r.left.image, r.right.image))
        preview.save(out / f'{stem}-anaglyph.png', preview.anaglyph(r.left.image, r.right.image))
        preview.save(out / f'{stem}-naive-anaglyph.png', preview.anaglyph(nr.left.image, nr.right.image))
        depth_view = preview.disparity_view(r.conditioned.disparity_arcmin, preset['near_arcmin'], preset['far_arcmin'])
        preview.save(out / f'{stem}-disparity.png', depth_view)
        rows.append([preview.label(r.frame, f'{key}: source'),
                     preview.label(preview.anaglyph(r.left.image, r.right.image), f'{preset_name} preset'),
                     preview.label(depth_view, 'disparity: orange in front, blue behind, grey on the plane'),
                     preview.label(preview.anaglyph(nr.left.image, nr.right.image), 'naive, for comparison')])
    preview.save(out / 'contact-sheet.png', preview.sheet(rows, 640))
    (out / 'report.json').write_text(json.dumps(report, indent=1))
    (out / 'README.txt').write_text(
        'Samples for the AI 3D filter. Made offline by tools/stereo_eval (python -m tools.stereo_eval gallery).\n'
        'Nothing here has been seen in the glasses.\n\n'
        '*-sbs.png        3840x1200, left eye on the left: for the glasses\' side-by-side 3D mode\n'
        '*-anaglyph.png   red-cyan glasses, red on the left eye\n'
        '*-naive-*        what a straight conversion does, for comparison\n'
        '*-disparity.png  grey is the screen plane, orange in front of it, blue behind it\n'
        'contact-sheet.png  all of it at a glance\n\n'
        'Credits: Tears of Steel (c) Blender Foundation, mango.blender.org, CC BY 3.0. '
        '0 A.D. screenshot, Wikimedia Commons, CC BY-SA 3.0. Depth: Depth Anything V2 Small, Apache-2.0.\n')
    print(out)
    return 0
