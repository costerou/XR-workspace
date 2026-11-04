"""The pipeline end to end: frames plus depth, through a preset, to eyes and scores."""
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image

from . import metrics, samples
from .conditioning import Conditioned, Conditioner
from .dibr import Eye, render_pair
from .geometry import Optics


@dataclass
class Result:
    frame: np.ndarray
    conditioned: Conditioned
    left: Eye
    right: Eye


def convert(frames, depths, preset: dict, fps: float = 30.0, optics: Optics = Optics(), fill: str = 'mirror', split: float = 0.5,
            render: str = 'last') -> tuple[list[np.ndarray], Result]:
    """Runs the conditioner over a sequence. Returns every disparity map, and the fully rendered pair of the `render` frame
    ('last', after the temporal state has settled, or 'middle')."""
    conditioner = Conditioner(preset, optics, fps)
    maps, chosen = [], len(frames) - 1 if render == 'last' else len(frames) // 2
    out = None
    for t, (frame, depth) in enumerate(zip(frames, depths)):
        c = conditioner.step(frame, depth)
        maps.append(c.disparity_arcmin)
        if t == chosen:
            d_px = c.disparity_arcmin * optics.image_px_per_arcmin(frame.shape[1])
            left, right = render_pair(frame, d_px, fill, split)
            out = Result(frame, c, left, right)
    return maps, out


def score(frames, maps, result: Result, fps: float, optics: Optics = Optics(), use_ocr: bool = False) -> dict:
    out = metrics.evaluate_pair(result.frame, result.left, result.right, result.conditioned.disparity_arcmin, optics, use_ocr)
    if len(frames) >= 2:
        out['flicker'] = metrics.flicker(frames, maps, fps)
    return out


def load_frames(path: str | Path, limit: int = 0, size=(samples.W, samples.H)) -> list[np.ndarray]:
    """A picture, a folder of pictures (sorted by name) or a video, each letterboxed to `size`."""
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
        files = files[:limit] if limit else files
        return [samples.fit(np.asarray(Image.open(p).convert('RGB')), *size) for p in files]
    if path.suffix.lower() in ('.png', '.jpg', '.jpeg'):
        return [samples.fit(np.asarray(Image.open(path).convert('RGB')), *size)]
    if not shutil.which('ffmpeg'):
        raise SystemExit('reading a video needs ffmpeg')
    w, h = size
    chain = f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
    cmd = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', str(path), '-vf', chain, '-f', 'rawvideo', '-pix_fmt', 'rgb24']
    cmd += (['-frames:v', str(limit)] if limit else []) + ['-']
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return [f.copy() for f in np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)]
