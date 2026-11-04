"""Build identical worst-edge crops and a before/after metric report from edges.py recordings."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from tools.stereo_eval import metrics
from .edges import fixtures
from . import __main__ as proof


def worst_crop(source, eye, size=384):
    # Register patches at half size. The crop selection depends ONLY on baseline output,
    # so after gets the identical source rectangle (no cherry-picking a nicer after edge).
    a, b = source[::2, ::2].astype(np.float32), eye[::2, ::2].astype(np.float32)
    padded = np.pad(a, ((0, 0), (12, 12), (0, 0)), mode='edge')
    residual = np.full(a.shape[:2], np.inf, np.float32)
    for shift in range(-12, 13):
        residual = np.minimum(residual, metrics.box(np.abs(padded[:, 12+shift:12+shift+a.shape[1]] - b).mean(2), 2))
    heat = metrics.box(residual, size // 4)
    margin = size // 4 + 24
    heat[:margin] = heat[-margin:] = 0; heat[:, :margin] = heat[:, -margin:] = 0
    y, x = np.unravel_index(np.argmax(heat), heat.shape)
    return (int(x * 2 - size // 2), int(y * 2 - size // 2), int(x * 2 + size // 2), int(y * 2 + size // 2))


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('out', type=Path); args = p.parse_args()
    out = args.out
    before, after = [json.loads((out / f'edges-{s}-report.json').read_text()) for s in ('before', 'after')]
    result = {'baseline': '00c2328', 'strength_internal_percent': 100,
              'photo_source': 'https://raw.githubusercontent.com/scikit-image/scikit-image/v0.21.0/skimage/data/astronaut.png (NASA, public domain)',
              'scenes': {}, 'timing': {'before': before['timing'], 'after': after['timing']}}
    scenes = fixtures(out / "edges-portrait-source.png")
    sheets = []
    for name in before['scenes']:
        prefix = out / f'edges-before-{name}'
        source = np.asarray(Image.open(str(prefix) + '-source.png'))
        baseline = np.asarray(Image.open(str(prefix) + '-sbs.png'))
        box = worst_crop(source, baseline[:, :1920])
        row = Image.new('RGB', (1536, 414), (24, 24, 28)); d = ImageDraw.Draw(row)
        for col, stage in enumerate(('before', 'after')):
            pre = out / f'edges-{stage}-{name}'
            sbs = np.asarray(Image.open(str(pre) + '-sbs.png'))
            left = Image.fromarray(sbs[:, :1920]).crop(box)
            right = Image.fromarray(sbs[:, 1920:]).crop(box)
            crop = Image.new('RGB', (768, 384)); crop.paste(left, (0, 0)); crop.paste(right, (384, 0))
            crop.save(str(pre) + '-worst-sbs-crop.png')
            Image.open(str(pre) + '-anaglyph.png').crop(box).save(str(pre) + '-worst-anaglyph-crop.png')
            Image.open(str(pre) + '-eye-difference.png').crop(box).save(str(pre) + '-worst-eye-difference-crop.png')
            row.paste(crop, (col * 768, 30)); d.text((col*768+10, 8), f'{stage.upper()}: {name}', fill='white')
        sheets.append(row)
        result['scenes'][name] = {'crop_xyxy': box, 'before': before['scenes'][name], 'after': after['scenes'][name]}
        sources = scenes[name][0]
        if len(sources) > 1:
            tiny = [f[::12, ::12].astype(np.float32) for f in sources]
            for stage in ('before', 'after'):
                files = sorted((out / f'edges-{stage}-work' / name / 'frames').glob('*.png'))
                eyes = [np.asarray(Image.open(f))[:, :1920] for f in files]
                indices = [int(np.argmin([np.mean((eye[::12, ::12].astype(np.float32)-f)**2) for f in tiny])) for eye in eyes]
                result['scenes'][name][stage]['temporal_flicker'] = metrics.temporal_flicker([sources[i] for i in indices], eyes)
                result['scenes'][name][stage]['source_frame_indices'] = indices
    sheet = Image.new('RGB', (1536, len(sheets) * 414))
    for i, row in enumerate(sheets): sheet.paste(row, (0, i*414))
    sheet.save(out / 'edges-before-after-crops.png')
    (out / 'edges-comparison.json').write_text(json.dumps(result, indent=2))
    for stage in ('before', 'after'):
        folders = [out / f'edges-{stage}-work' / name / 'frames' for name in before['scenes']]
        proof.clip(folders, out / f'edges-{stage}-clip.mp4')


if __name__ == '__main__': main()
