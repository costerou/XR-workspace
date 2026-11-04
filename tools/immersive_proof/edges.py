"""Offline A/B proof. No display, XR service, driver or running engine is contacted.

Run at nice 19. --engine-root allows the same fixtures to be rendered through the
unchanged baseline engine and binary. Output edges-{before,after}-* is durable.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image, ImageDraw
from . import __main__ as proof
from tools.stereo_eval import metrics, samples


def fixtures(photo, count=30):
    portrait = samples.fit(np.asarray(Image.open(photo).convert('RGB')))
    video = proof.video_frames(proof.SAMPLES / 'tos-410.mov', count, 60)
    game = samples.game_pan(count)
    ui = Image.fromarray(samples.desktop(picture=video[0]))
    d = ImageDraw.Draw(ui)
    d.rectangle((70, 120, 900, 790), fill=(238, 241, 245))
    for x in range(90, 880, 13): d.line((x, 140, x, 770), fill=(40, 60, 90), width=1)
    for y in range(140, 770, 19): d.line((90, y, 880, y), fill=(40, 60, 90), width=1)
    rng = np.random.default_rng(20261104)
    noisy = [np.clip(portrait.astype(np.int16) + rng.integers(-1, 2, portrait.shape), 0, 255).astype(np.uint8) for _ in range(count)]
    return {'noise-hair': (noisy, 'video'), 'text': ([samples.desktop(picture=video[0])], 'desktop'),
            'hair': ([portrait], 'video'), 'thin-ui': ([np.asarray(ui)], 'desktop'),
            'video': ([video[0]], 'video'), 'game': ([game[0]], 'games'),
            'motion-video': (video, 'video'), 'motion-game': (game, 'games')}


def render(binary, engine, pictures, preset, folder, count=12):
    folder.mkdir(parents=True, exist_ok=True)
    listing = proof.write_pictures(pictures, folder / 'input')
    raw = folder / 'raw'; raw.mkdir(exist_ok=True)
    result = proof.bench(binary, listing, proof.DGPU, engine=engine, desktops=1, layout='full', mode='3d',
                         hz=30, capture_hz=0 if len(pictures) == 1 else 30, settle=3,
                         record=raw, record_frames=count, **proof.look_options(preset))
    png = folder / 'frames'; png.mkdir(exist_ok=True)
    for ppm in sorted(raw.glob('*.ppm')):
        Image.open(ppm).save(png / (ppm.stem + '.png'), compress_level=1)
        ppm.unlink()
    return png, result


def score(source, frames, preset):
    files = sorted(frames.glob('*.png'))
    eyes = [np.asarray(Image.open(f))[:, :1920] for f in files]
    # Static scenes are scored against their exact known source, motion by the existing SBS disparity scorer.
    result = {'comfort': proof.comfort(frames, preset)}
    if len(source) == 1:
        # Crop away the compositor's desktop border before the local registration metric.
        result['edge_artifact'] = metrics.edge_artifact(source[0][12:-12, 12:-12], eyes[-1][12:-12, 12:-12])
        result['temporal_flicker'] = metrics.temporal_flicker([source[0]] * len(eyes), eyes)
    if len(source) > 1:
        # Register to the nearest supplied source frame before masking scene motion.
        # This avoids assuming that GPU scheduling kept capture and recording clocks in lockstep.
        tiny = [f[::12, ::12].astype(np.float32) for f in source]
        indices = [int(np.argmin([np.mean((e[::12, ::12].astype(np.float32)-f)**2) for f in tiny])) for e in eyes]
        result['source_frame_indices'] = indices
        result['temporal_flicker'] = metrics.temporal_flicker([source[i] for i in indices], eyes)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['before', 'after'], required=True)
    parser.add_argument('--engine-root', type=Path, default=proof.ROOT)
    parser.add_argument('--bench', type=Path, required=True)
    parser.add_argument('--photo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--record-only', action='store_true')
    parser.add_argument('--only', help='one scene, updating the existing report')
    args = parser.parse_args()
    os.nice(19 - os.getpriority(os.PRIO_PROCESS, 0))
    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / f'edges-{args.stage}-work'; work.mkdir(exist_ok=True)
    scenes = fixtures(args.photo)
    report = {'stage': args.stage, 'engine_root': str(args.engine_root), 'binary': str(args.bench), 'scenes': {}}
    if args.only:
        report_path = args.out / f'edges-{args.stage}-report.json'
        if report_path.exists(): report = json.loads(report_path.read_text())
        scenes = {args.only: scenes[args.only]}
    # Engine.__init__ uses ROOT only as the subprocess cwd. Each process owns a new memfd.
    previous = proof.ROOT
    proof.ROOT = args.engine_root
    engine = proof.Engine('rocm')
    proof.ROOT = previous
    folders = []
    try:
        for name, (pictures, preset) in scenes.items():
            folder, info = render(args.bench, engine, pictures, preset, work / name, 30 if len(pictures) > 1 else 12)
            folders.append(folder)
            prefix = args.out / f'edges-{args.stage}-{name}'
            last = np.asarray(Image.open(sorted(folder.glob('*.png'))[-1]))
            Image.fromarray(last).save(str(prefix) + '-sbs.png')
            Image.fromarray(proof.anaglyph(last)).save(str(prefix) + '-anaglyph.png')
            diff = np.abs(last[:, :1920].astype(np.int16) - last[:, 1920:].astype(np.int16))
            Image.fromarray(np.minimum(diff * 3, 255).astype(np.uint8)).save(str(prefix) + '-eye-difference.png')
            Image.fromarray(pictures[0]).save(str(prefix) + '-source.png')
            report['scenes'][name] = score(pictures, folder, preset)
            report['scenes'][name]['renderer'] = info
            print(args.stage, name, json.dumps(report['scenes'][name].get('edge_artifact')), flush=True)
            (args.out / f'edges-{args.stage}-report.json').write_text(json.dumps(report, indent=2))
        if not args.only: proof.clip(folders, args.out / f'edges-{args.stage}-clip.mp4')
        if not args.record_only and not args.only:
            listing = proof.write_pictures([samples.desktop(scroll=i*3, picture=scenes['video'][0][0]) for i in range(30)], work / 'timing')
            report['timing'] = []
            for n in (1, 2, 3, 4):
                data = proof.bench(args.bench, listing, proof.DGPU, engine=engine, desktops=n, layout='overview', mode='3d',
                                   hz=60, capture_hz=60, settle=3, seconds=10, **proof.look_options('desktop'))
                report['timing'].append(data)
                print(args.stage, 'timing', n, data['frame_ms'], data.get('maps_per_desktop_per_s'), flush=True)
    finally:
        engine.stop()
    (args.out / f'edges-{args.stage}-report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
