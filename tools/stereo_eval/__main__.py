"""python -m tools.stereo_eval <command>; see tools/stereo_eval/README.md."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

from . import depth_source, metrics, preview, presets, run
from .dibr import Eye
from .geometry import Optics


def depth_for(frames, source: str, height: int):
    if source == 'model':
        return [depth_source.infer_content(f, height) for f in frames]
    path = Path(source)
    if path.is_dir():
        files = sorted(path.glob('*'))
        if len(files) < len(frames):
            raise SystemExit(f'{len(frames)} frames but {len(files)} depth maps in {path}')
        return [depth_source.load_map(p) for p in files[:len(frames)]]
    return [depth_source.load_map(path)] * len(frames)


def report(result: dict, preset: dict, as_json: bool) -> int:
    misses = metrics.check_targets(result, preset.get('targets', {}))
    result['target_misses'] = misses
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for section, values in result.items():
            if isinstance(values, dict):
                print(section)
                for k, v in values.items():
                    print(f'  {k:34s} {v:.4g}' if isinstance(v, float) else f'  {k:34s} {v}')
            elif section != 'target_misses':
                print(f'{section:36s} {values:.4g}')
        print('targets: ' + ('all met' if not misses else '; '.join(misses)))
    return 1 if misses else 0


def cmd_eval(a) -> int:
    frames = run.load_frames(a.frames, a.limit)
    preset = presets.get(a.preset)
    maps, result = run.convert(frames, depth_for(frames, a.depth, a.height), preset, a.fps)
    code = report(run.score(frames, maps, result, a.fps, use_ocr=a.ocr), preset, a.json)
    if a.out:
        out = Path(a.out)
        preview.save(out / 'sbs.png', preview.side_by_side(result.left.image, result.right.image))
        preview.save(out / 'anaglyph.png', preview.anaglyph(result.left.image, result.right.image))
    return code


def cmd_sbs(a) -> int:
    """Scores recorded side-by-side frames without a depth map: this is how a real renderer's output gets checked."""
    frames = run.load_frames(a.frames, a.limit, size=(3840, 1200))
    optics = Optics()
    px_per_arcmin = optics.image_px_per_arcmin(1920)
    lefts, rights, maps, last = [], [], [], None
    for frame in frames:
        left, right = frame[:, :1920], frame[:, 1920:]
        d_px, occluded = metrics.estimate_disparity(left, right, a.max_px)
        lefts.append(left)
        rights.append(right)
        maps.append(d_px / px_per_arcmin)
        last = occluded
    mask = metrics.text_mask(metrics.luma(lefts[-1]))
    out = {'disparity': metrics.disparity_stats(maps[-1], optics), 'occluded_share': float(last.mean()),
           'text': {**metrics.text_score(lefts[-1], rights[-1], mask), 'wobble_arcmin': metrics.text_wobble(maps[-1], mask)}}
    if len(lefts) >= 2:
        out['flicker'] = metrics.flicker(lefts, maps, a.fps)
    return report(out, presets.get(a.preset), a.json)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m tools.stereo_eval', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('fetch-model', help='download the Apache-2.0 depth weights (the only command that uses the network)')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('frames', help='a picture, a folder of pictures, or a video')
    common.add_argument('--preset', default='desktop', choices=sorted(presets.load()))
    common.add_argument('--fps', type=float, default=30)
    common.add_argument('--limit', type=int, default=0, help='use only the first N frames')
    common.add_argument('--json', action='store_true')
    e = sub.add_parser('eval', parents=[common], help='convert frames with the reference pipeline and score them')
    e.add_argument('--depth', default='model', help="'model', a saved map (.npy, 16-bit PNG), or a folder of maps")
    e.add_argument('--height', type=int, default=518, help='depth map height for the model')
    e.add_argument('--ocr', action='store_true', help='also compare tesseract readings')
    e.add_argument('--out', help='folder for sbs.png and anaglyph.png')
    s = sub.add_parser('sbs', parents=[common], help='score recorded 3840 x 1200 side-by-side frames')
    s.add_argument('--max-px', type=int, default=40, help='largest disparity to search for, px')
    sub.add_parser('sweep', help='the parameter sweeps behind docs/3d-comfort.md').add_argument('--out', required=True)
    sub.add_parser('gallery', help='sample pictures for every preset').add_argument('--out', required=True)
    a = parser.parse_args(argv)
    if a.command == 'fetch-model':
        print(depth_source.fetch_model())
        return 0
    if a.command == 'eval':
        return cmd_eval(a)
    if a.command == 'sbs':
        return cmd_sbs(a)
    from . import study
    return study.sweep(Path(a.out)) if a.command == 'sweep' else study.gallery(Path(a.out))


if __name__ == '__main__':
    sys.exit(main())
