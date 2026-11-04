"""What `stereo_eval sbs` reads from output that is right by construction: the comfort study's own pipeline (tools/stereo_eval), which
meets its presets' targets on the disparity it knows exactly, drawn to two eyes and put through the same scorer as the real renderer.

`sbs` has no depth map to read: it block-matches the eyes at half resolution, in steps of about 3 arcminutes. So it cannot see a 0.3
arcminute target directly, and the difference between these numbers and the renderer's is what means something. Needs the model:

    ~/.cache/viture-stereo-eval/venv/bin/python -m tools.immersive_proof.yardstick --out FILE [--frames 30]
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from tools.immersive_proof.__main__ import ROOT, scenes
from tools.stereo_eval import depth_source, presets, preview
from tools.stereo_eval.conditioning import Conditioner
from tools.stereo_eval.dibr import render_pair
from tools.stereo_eval.geometry import Optics


def draw(frames, preset, folder):
    optics, conditioner = Optics(), Conditioner(preset, Optics(), 30.0)
    for i, frame in enumerate(frames):
        c = conditioner.step(frame, depth_source.infer_content(frame, 266))
        left, right = render_pair(frame, c.disparity_arcmin * optics.image_px_per_arcmin(frame.shape[1]), 'mirror', 0.5)
        preview.save(folder / f'{i:04d}.png', preview.side_by_side(left.image, right.image))


def main(argv=None):
    p = argparse.ArgumentParser(prog='yardstick')
    p.add_argument('--out', required=True)
    p.add_argument('--frames', type=int, default=30)
    a = p.parse_args(argv)
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, (frames, preset) in scenes(a.frames).items():
            folder = Path(tmp) / name
            folder.mkdir()
            draw(frames if len(frames) > 1 else frames * 4, presets.get(preset), folder)
            done = subprocess.run([sys.executable, '-m', 'tools.stereo_eval', 'sbs', str(folder), '--preset', preset, '--json'], cwd=ROOT,
                                  capture_output=True, text=True, check=False)
            out[name] = json.loads(done.stdout)
            print(name, done.stdout[:0], json.dumps(out[name].get('target_misses')), flush=True)
    Path(a.out).write_text(json.dumps(out, indent=1) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
