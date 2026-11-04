"""The offline proof of immersive 3D (docs/immersive-3d.md): the whole chain on real content, off screen.

Real pictures go in as a desktop's captures; the real renderer code (spike/bench/stereo_chain_bench.cpp: GlCompositor, DepthLink)
draws both eyes from the real depth engine (tools/depth_engine, in its own process, over a real memfd). Out come side-by-side
stills, a clip, frame times against 2D per desktop count, and the comfort scores of tools/stereo_eval on the recorded output.

It uses no display, no compositor and not the glasses, but it does load the GPU, so it refuses to run while an XR session is
up, and asks the operating system for the lowest priority.

    python3 -m tools.immersive_proof --out DIR          # everything, on the RX 7800 XT
    python3 -m tools.immersive_proof --out DIR --backend fake --quick   # the plumbing only: no model, a few seconds
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image

from tools.depth_engine import shm as S
from tools.native_spike.immersive import DepthMemory, PRESETS, wire_params
from tools.stereo_eval import samples as content

ROOT = Path(__file__).resolve().parents[2]
ENGINE_PYTHON = Path.home() / '.cache/viture-depth/venv/bin/python'
EVAL_PYTHON = Path.home() / '.cache/viture-stereo-eval/venv/bin/python'
SAMPLES = Path.home() / '.cache/viture-stereo-eval/samples'
PHOTO = Path('/usr/share/omarchy/themes/starship/backgrounds/3-engine-cluster.webp')
DGPU, IGPU = 'pci-0000_03_00_0', 'pci-0000_12_00_0'
W, H = content.W, content.H


def xr_is_up():
    """A session on this machine, whichever user unit it runs in."""
    units = subprocess.run(['systemctl', '--user', 'list-units', '--type=service', '--state=running', '--no-legend', 'viture-xr-*'],
                           capture_output=True, text=True, check=False).stdout
    return bool(units.strip())


def video_frames(path, count, start=0):
    """Frames of a clip, shown as a fullscreen player shows them: letterboxed on a desktop, black bars and all."""
    chain = f'select=gte(n\\,{start}),scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2'
    raw = subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', str(path), '-vf', chain, '-fps_mode', 'passthrough', '-frames:v', str(count),
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
    return [f.copy() for f in np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)]


def scenes(frames):
    """name: (frames, preset). Text scrolls, a photo sits still as a wallpaper does, video and the game move."""
    still = None
    clip = SAMPLES / 'tos-410.mov'
    if clip.exists(): still = video_frames(clip, 1, 60)[0]
    out = {'text': ([content.desktop(scroll=(i * 3) % 90, picture=still) for i in range(frames)], 'desktop')}
    if PHOTO.exists():
        out['photo'] = ([content.fit(np.asarray(Image.open(PHOTO).convert('RGB')))], 'video')
    if clip.exists(): out['video'] = (video_frames(clip, frames, 20), 'video')
    if (SAMPLES / 'game-0ad.jpg').exists():
        out['game'] = (content.game_pan(frames), 'games')
    return out


def write_pictures(frames, folder):
    """The bench's picture list, of raw RGBA."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, rgb in enumerate(frames):
        rgba = np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])
        path = folder / f'{i:04d}.rgba'
        path.write_bytes(rgba.tobytes())
        lines.append(f'{rgb.shape[1]} {rgb.shape[0]} {path}')
    lines += lines[-2:0:-1]  # there and back: a clip that jumped to its start would be a scene cut every second, which is not the clip
    listing = folder / 'list.txt'
    listing.write_text('\n'.join(lines) + '\n')
    return listing


class Engine:
    """The depth engine as a run has it: its own process, the memfd inherited, `ready` on stdout, end of input to stop."""
    def __init__(self, backend):
        self.memory = DepthMemory()
        S.DepthShm.create(f'/proc/{os.getpid()}/fd/{self.memory.fd}', DepthMemory.CHANNELS, DepthMemory.IN_W, DepthMemory.IN_H, DepthMemory.IN_W, DepthMemory.IN_H).close()
        python = str(ENGINE_PYTHON) if backend != 'fake' else sys.executable
        started = time.monotonic()
        self.child = subprocess.Popen(['nice', '-n', '19', python, '-m', 'tools.depth_engine', 'serve', '--supervised', '--backend', backend,
                                       '--shm-fd', str(self.memory.fd)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      pass_fds=(self.memory.fd,), text=True)
        for line in self.child.stdout:
            if line.strip() == 'ready': break
        else:
            raise SystemExit('the depth engine did not come up')
        self.ready_s = round(time.monotonic() - started, 2)

    def rss_mb(self):
        """The whole process tree's resident memory."""
        total = 0
        for pid in (self.child.pid, *map(int, subprocess.run(['pgrep', '-P', str(self.child.pid)], capture_output=True, text=True).stdout.split())):
            try: total += int(next(l for l in Path(f'/proc/{pid}/status').read_text().splitlines() if l.startswith('VmRSS')).split()[1])
            except (OSError, StopIteration): pass
        return total // 1024

    def stop(self):
        self.child.stdin.close()
        try: self.child.wait(10)
        except subprocess.TimeoutExpired: self.child.kill()
        self.memory.close()


def bench(binary, listing, gpu, *, engine=None, **options):
    env = dict(os.environ, DRI_PRIME=gpu, MESA_SHADER_CACHE_DISABLE='true')
    argv = ['nice', '-n', '19', str(binary), '--pictures', str(listing)]
    for key, value in options.items(): argv += ['--' + key.replace('_', '-'), str(value)]
    passed = ()
    if engine is not None:
        argv += ['--depth-fd', str(engine.memory.fd)]
        passed = (engine.memory.fd,)
    done = subprocess.run(argv, capture_output=True, text=True, env=env, pass_fds=passed, timeout=600, check=False)
    if done.returncode: raise SystemExit(f'bench failed ({done.returncode}): {done.stderr[-400:]}')
    return json.loads(done.stdout)


def look_options(preset, strength=100):
    p = wire_params({'preset': preset, 'strength': strength, 'convergence': PRESETS[preset]['convergence']})
    return {'strength': p['strength'], 'convergence': p['convergence'], 'near': p['near'], 'far': p['far'], 'text': p['text'], 'range_ms': p['range_ms']}


def anaglyph(sbs):
    """Red-cyan, for a look without the glasses."""
    half = sbs.shape[1] // 2
    return np.dstack([sbs[:, :half, 0], sbs[:, half:, 1], sbs[:, half:, 2]])


def record(binary, engine, name, frames, preset, gpu, work, out, record_frames, settle, capture_hz):
    """`record_frames` side-by-side frames of a scene filling the view, as if maximized: stills, a clip, and what the scores read."""
    listing = write_pictures(frames, work / 'in' / name)
    folder = work / 'out' / name
    folder.mkdir(parents=True, exist_ok=True)
    bench(binary, listing, gpu, engine=engine, desktops=1, layout='full', mode='3d', hz=30, capture_hz=capture_hz, settle=settle,
          record=folder, record_frames=record_frames, **look_options(preset))
    pngs = work / 'png' / name
    pngs.mkdir(parents=True, exist_ok=True)
    for ppm in sorted(folder.glob('*.ppm')):
        Image.open(ppm).save(pngs / (ppm.stem + '.png'), compress_level=1)
        ppm.unlink()
    last = sorted(pngs.glob('*.png'))[-1]
    sbs = np.asarray(Image.open(last).convert('RGB'))
    Image.fromarray(sbs).save(out / f'{name}-sbs.png')
    Image.fromarray(anaglyph(sbs)).save(out / f'{name}-anaglyph.png')
    half = sbs.shape[1] // 2
    Image.fromarray((np.abs(sbs[:, :half].astype(np.int16) - sbs[:, half:]).clip(0, 85) * 3).astype(np.uint8)).save(out / f'{name}-eye-difference.png')
    return pngs


def clip(folders, out):
    """All the scenes, one after another, side by side: what the glasses would be sent."""
    listing = out.parent / 'clip-frames.txt'
    lines = []
    for folder in folders:
        lines += [f"file '{p}'\nduration 0.0333" for p in sorted(folder.glob('*.png'))]
    listing.write_text('\n'.join(lines) + '\n')
    subprocess.run(['nice', '-n', '19', 'ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c:v', 'libx264',
                    '-crf', '18', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-r', '30', str(out)], check=True)
    listing.unlink()


def comfort(folder, preset):
    """tools/stereo_eval's score of what was really drawn: it reads the disparity from the two eyes, with no depth map."""
    python = str(EVAL_PYTHON) if EVAL_PYTHON.exists() else sys.executable
    done = subprocess.run(['nice', '-n', '19', python, '-m', 'tools.stereo_eval', 'sbs', str(folder), '--preset', preset, '--json'],
                          cwd=ROOT, capture_output=True, text=True, check=False)
    try: result = json.loads(done.stdout)
    except ValueError: return {'failed': (done.stderr or done.stdout)[-300:]}
    return result


def timing(binary, engine, listing, gpu, seconds, settle, label):
    """Frame times per desktop count: 2D, then glasses side by side with nothing 3D, then 3D on every desktop."""
    rows = []
    for desktops in (1, 2, 3, 4):
        base = dict(desktops=desktops, layout='overview', hz=90, capture_hz=60, seconds=seconds, settle=settle)
        two_d = bench(binary, listing, gpu, mode='2d', **{**base, 'hz': 120})
        flat = bench(binary, listing, gpu, mode='flat', **base)
        three_d = bench(binary, listing, gpu, engine=engine, mode='3d', **base, **look_options('desktop'))
        depth = three_d.get('depth', {})
        rows.append({'desktops': desktops, 'gpu': label, 'frame_2d_ms': two_d['frame_ms'], 'frame_flat_ms': flat['frame_ms'], 'frame_3d_ms': three_d['frame_ms'],
                     'link_ms': three_d['link_ms'], 'over_budget_90hz': three_d['over_budget'], 'frames_3d': three_d['frames_drawn'],
                     'maps_per_desktop_per_s': three_d.get('maps_per_desktop_per_s'), 'feeds_per_desktop_per_s': three_d.get('feeds_per_desktop_per_s'),
                     'depth_age_p50_ms': depth.get('age_p50_ms'), 'depth_age_p99_ms': depth.get('age_p99_ms'), 'draw_failures': three_d['draw_failures']})
        print(json.dumps(rows[-1]), flush=True)
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(prog='immersive_proof')
    p.add_argument('--out', required=True)
    p.add_argument('--backend', default='rocm', choices=['rocm', 'ncnn', 'fake'])
    p.add_argument('--bench', default=str(ROOT / 'build/release/stereo_chain_bench'))
    p.add_argument('--gpu', default=DGPU)
    p.add_argument('--quick', action='store_true', help='short runs, for checking the tool')
    p.add_argument('--skip-timing', action='store_true')
    p.add_argument('--igpu-timing', action='store_true', help='the same frame times on the integrated GPU, as well')
    a = p.parse_args(argv)
    if xr_is_up(): raise SystemExit('an XR session is running: this loads the GPU, so it waits for that to end')
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    frames = 8 if a.quick else 30
    report = {'backend': a.backend, 'gpu': a.gpu, 'presets': {}}
    with tempfile.TemporaryDirectory(dir=os.environ.get('XR_PROOF_WORK')) as tmp:
        work = Path(tmp)
        chosen = scenes(frames)
        engine = Engine(a.backend)
        report['engine_ready_s'] = engine.ready_s
        report['engine_rss_mb'] = engine.rss_mb()
        print(f"engine up in {engine.ready_s} s, {report['engine_rss_mb']} MB", flush=True)
        try:
            recorded = []
            settle = 1 if a.quick else 3
            for name, (pictures, preset) in chosen.items():
                still = len(pictures) == 1
                folder = record(a.bench, engine, name, pictures, preset, a.gpu, work, out, 4 if a.quick else frames, settle, 0 if still else 30)
                recorded.append(folder)
                report['presets'][name] = {'preset': preset, 'comfort': comfort(folder, preset)}
                print(name, json.dumps(report['presets'][name])[:300], flush=True)
            if shutil.which('ffmpeg') and recorded: clip(recorded, out / '3d-chain.mp4')
            if not a.skip_timing and 'text' in chosen:
                listing = write_pictures(chosen['text'][0], work / 'in' / 'timing')
                seconds, settle = (1, 0.5) if a.quick else (10, 2)
                report['timing'] = timing(a.bench, engine, listing, a.gpu, seconds, settle, 'dGPU')
                if a.igpu_timing: report['timing_igpu'] = timing(a.bench, engine, listing, IGPU, seconds, settle, 'iGPU')
            report['engine_rss_mb_end'] = engine.rss_mb()
        finally:
            engine.stop()
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print(f'written to {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
