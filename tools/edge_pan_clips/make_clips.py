#!/usr/bin/env python3
"""Render the edge pan on the real compositor for every layout in motion.named_layouts, with its per-frame metrics.

    make_clips.py --tag before --pan-clip PATH --trace PATH     seamless-before-<layout>.mp4 and .json
    make_clips.py --compare                                     seamless-compare-<layout>.mp4 (before beside after) and the plots

Runs on llvmpipe, so a render uses neither GPU; run it under `nice -n 19`. Touches nothing on the desktop: the desktops in the frames
are synthetic, nothing is captured, and nothing here starts, stops or talks to a run. See README.md.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cairo
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.edge_pan_clips import motion  # noqa: E402

DEFAULT_OUT = Path.home() / '.hermes/profiles/coder/previews/switch-anim'
ACCENT = '7aa2f7'
FONT = 'JetBrainsMono Nerd Font'
W, H, FPS = 1920, 1200, 60
INK, DIM, SURFACE, LOW, TOP = (0.75, 0.78, 0.86), (0.45, 0.48, 0.58), (0.09, 0.10, 0.14), (0.06, 0.065, 0.09), (0.16, 0.17, 0.23)
PRIMARY = (0.48, 0.64, 0.97)
LINES = [
    ['~/code/viture-workspace  master', '$ ctest --test-dir build/release -j 1', '100% tests passed, 0 tests failed out of 70', '$ git status', 'On branch master',
     'nothing to commit, working tree clean', '', '~/code/viture-workspace  master', '$ _'],
    ['fn next_desktop(from: Desktop, push: Edge) {', '    let target = nearest_within(from, push, 45.0);', '    match target {', '        Some(t) => view.turn_toward(t),',
     '        None => cursor.stop_at_edge(),', '    }', '}', '', '// one smooth path, whatever the layout'],
    ['Tuesday', '', '- try the pan in the glasses', '- resized and moved desktops', '- XR-3 is out of the row', '', 'Groceries', '- oat milk, coffee, limes'],
]


def desktop_pixels(index, w, h, path):
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surface)
    cr.set_source_rgb(*SURFACE); cr.paint()
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(19); cr.set_source_rgb(*DIM)
    cr.move_to(26, 28); cr.show_text('1  2  3')
    cr.move_to(w - 92, 28); cr.show_text('01:05')
    x, y, bw, bh = 70, 70, w - 140, h - 130
    cr.set_source_rgb(*LOW); cr.rectangle(x, y, bw, bh); cr.fill()
    cr.set_source_rgb(*PRIMARY); cr.set_line_width(2); cr.rectangle(x, y, bw, bh); cr.stroke()
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(240); cr.set_source_rgb(*TOP)
    extent = cr.text_extents(f'XR-{index + 1}')
    cr.move_to(x + bw - extent.width - 60, y + bh - 60); cr.show_text(f'XR-{index + 1}')
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(34)
    for i, text in enumerate(LINES[index % 3]):
        cr.set_source_rgb(*(PRIMARY if text.startswith(('~', '//', 'Tuesday', 'Groceries')) else INK))
        cr.move_to(x + 48, y + 80 + i * 50); cr.show_text(text)
    surface.flush()
    path.write_bytes(bytes(surface.get_data()))


def font(style, size):
    file = subprocess.run(['fc-match', '-f', '%{file}', f'{FONT}:{style}'], capture_output=True, text=True, check=True).stdout
    return ImageFont.truetype(file, size)


def caption(text, w=W, h=44):
    band = Image.new('RGB', (w, h), (0, 0, 0))
    ImageDraw.Draw(band).text((20, 8), text, font=font('bold', 24), fill=(170, 174, 190))
    return np.array(band)


def encoder(path):
    return subprocess.Popen(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                             '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-tune', 'animation', '-g', '30', '-threads', '4', '-pix_fmt', 'yuv420p',
                             '-movflags', '+faststart', str(path)], stdin=subprocess.PIPE)


def render(name, layout, home, side, along, args, out):
    target = layout.target(home, side, along)
    plan = layout.plan(home, target, side, along) if target is not None else None
    if plan is None: raise SystemExit(f'{name}: no pan that way')
    lines = motion.script(layout, plan, fps=FPS, lead=0.35, tail=1.05)
    frames = motion.trace(lines, args.trace)
    summary = motion.Motion(motion.trace(motion.script(layout, plan, fps=120), args.trace), home, target).summary()
    data = {'name': name, 'home': home, 'target': target, 'plan': {k: getattr(plan, k) for k in ('zoom', 'rate', 'peek_px', 'gap_px', 'total_px')}, 'summary': summary,
            'frames': [{k: f[k] for k in ('t', 'zoom', 'landing')} | {'aim': f['aim'], 'home_h': f['desktops'][home]['seen'][1], 'target_h': f['desktops'][target]['seen'][1],
                                                                      'home_roll': f['desktops'][home]['roll'], 'target_roll': f['desktops'][target]['roll']} for f in frames]}
    (out / f'seamless-{args.tag}-{name}.json').write_text(json.dumps(data))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for i, (w, h) in enumerate(layout.sources): desktop_pixels(i, w, h, tmp / f'desktop-{i}.bgra')
        env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', LP_NUM_THREADS='4', EGL_LOG_LEVEL='fatal')
        p = subprocess.Popen([str(args.pan_clip), 'render', ACCENT, str(tmp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        mp4 = encoder(out / f'seamless-{args.tag}-{name}.mp4')
        band = caption(f'{name}   XR-{home + 1} to XR-{target + 1}, pushing {side}   {args.tag}')
        count = 0
        for line in lines:
            p.stdin.write((line + '\n').encode()); p.stdin.flush()
            if not line.startswith('frame '): continue
            raw = p.stdout.read(W * H * 4)
            if len(raw) != W * H * 4: raise SystemExit(f'{name}: short frame')
            px = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 4).copy()
            px[:band.shape[0], :, :3] = band
            mp4.stdin.write(px.tobytes())
            count += 1
        p.stdin.close(); p.wait()
        mp4.stdin.close(); mp4.wait()
    print(f'{name}: {count} frames', flush=True)


def compare(out, names):
    for name in names:
        a, b = out / f'seamless-before-{name}.mp4', out / f'seamless-after-{name}.mp4'
        if not a.exists() or not b.exists(): continue
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-threads', '4', '-i', str(a), '-i', str(b), '-filter_complex',
                        '[0:v]scale=960:-2[l];[1:v]scale=960:-2[r];[l][r]hstack=inputs=2,tpad=stop_mode=clone:stop_duration=0.6',
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-tune', 'animation', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                        str(out / f'seamless-compare-{name}.mp4')], check=True)
        print('compared', name, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='after')
    ap.add_argument('--pan-clip', type=Path, default=motion.REPO / 'build/clips/pan_clip')
    ap.add_argument('--trace', type=Path, default=motion.DEFAULT_TRACE)
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--only', default='')
    ap.add_argument('--compare', action='store_true')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    layouts = motion.named_layouts()
    names = [n for n in args.only.split(',') if n] or list(layouts)
    if args.compare: return compare(args.out, names)
    for name in names: render(name, *layouts[name], args, args.out)


if __name__ == '__main__':
    main()
