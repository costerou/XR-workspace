"""Sample content. The desktop is drawn here, so it holds nothing private and scrolls on demand. The game shot and the film
clip are downloaded by hand into the cache (the URLs and licences are in docs/3d-comfort.md); tests never need them."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .depth_source import CACHE

W, H = 1920, 1200
ROOT = Path(__file__).resolve().parents[2]
SANS, MONO = '/usr/share/fonts/liberation/LiberationSans-Regular.ttf', '/usr/share/fonts/noto/NotoSansMono-Light.ttf'
GAME = CACHE / 'samples/game-0ad.jpg'           # 0 A.D. alpha 25, Wikimedia Commons, CC BY-SA 3.0
CLIP = CACHE / 'samples/tos-230.mov'            # Tears of Steel, (c) Blender Foundation, CC BY 3.0


def _font(path: str, size: int):
    return ImageFont.truetype(path, size) if Path(path).exists() else ImageFont.load_default()


def _window(draw, x, y, w, h, title, focused=False):
    draw.rectangle((x - 2, y - 2, x + w + 2, y + h + 2), outline=(122, 162, 247) if focused else (65, 72, 104), width=2)
    draw.rectangle((x, y, x + w, y + h), fill=(26, 27, 38))
    draw.rectangle((x, y, x + w, y + 28), fill=(36, 40, 59))
    draw.text((x + 12, y + 6), title, fill=(192, 202, 245), font=_font(SANS, 14))


def desktop(scroll: int = 0, picture: np.ndarray | None = None) -> np.ndarray:
    """A 1920 x 1200 desktop: wallpaper, bar, editor, terminal, document, and an image window. `scroll` moves the document."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    wall = np.stack([26 + 40 * yy / H + 18 * np.sin(xx / 170), 30 + 22 * yy / H, 60 + 70 * xx / W], -1)
    im = Image.fromarray(np.clip(wall, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 30), fill=(22, 22, 30))
    d.text((14, 7), '1  2  3  4        XR-1                    23:40        wifi  vol 62%', fill=(169, 177, 214), font=_font(SANS, 14))

    _window(d, 40, 60, 900, 760, 'dibr.py - editor', True)
    code = (ROOT / 'tools/stereo_eval/conditioning.py').read_text().splitlines()
    for i, line in enumerate(code[60:96]):
        d.text((56, 100 + i * 19), f'{i + 60:4d}  {line[:96]}', fill=(158, 206, 106) if line.strip().startswith('def') else (192, 202, 245), font=_font(MONO, 13))

    _window(d, 40, 850, 900, 310, 'terminal')
    term = ['$ git log --oneline -5', 'c5787ac Merge origin/cursor-switch-animation: a maximized edge push pans the view by hand',
            '9bbef80 Merge origin/text-cursor-snappier: the Serif Beam follows the app at once, both ways',
            '$ python -m unittest discover -s tests/stereo_eval', 'Ran 40 tests in 12.031s', 'OK', '$ _']
    for i, line in enumerate(term):
        d.text((56, 892 + i * 22), line, fill=(169, 177, 214), font=_font(MONO, 14))

    _window(d, 980, 60, 900, 640, 'docs - technical.md')
    page = Image.new('RGB', (884, 604), (250, 250, 247))
    pd = ImageDraw.Draw(page)
    text = (ROOT / 'docs/vr-video.md').read_text().splitlines()
    wrapped = []
    for line in text[:80]:
        while line:
            wrapped.append(line[:104])
            line = line[104:]
    for i, line in enumerate(wrapped):
        y = 12 + i * 21 - scroll
        if -24 < y < 604:
            pd.text((16, y), line, fill=(30, 30, 34), font=_font(SANS, 15))
    im.paste(page, (988, 92))

    _window(d, 980, 730, 900, 430, 'viewer - still.jpg')
    if picture is not None:
        tile = Image.fromarray(picture.astype(np.uint8)).resize((884, 394), Image.BICUBIC)
        im.paste(tile, (988, 762))
    return np.asarray(im, dtype=np.uint8).copy()


def fit(rgb: np.ndarray, w: int = W, h: int = H) -> np.ndarray:
    """Letterbox into the desktop's shape, the way a fullscreen player shows it."""
    im = Image.fromarray(rgb.astype(np.uint8))
    scale = min(w / im.width, h / im.height)
    small = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    out = Image.new('RGB', (w, h))
    out.paste(small, ((w - small.width) // 2, (h - small.height) // 2))
    return np.asarray(out, dtype=np.uint8).copy()


def game() -> np.ndarray:
    return fit(np.asarray(Image.open(GAME).convert('RGB')))


def game_pan(frames: int, px_per_frame: float = 6.0) -> list[np.ndarray]:
    """The camera slides over the world while the HUD stays. The screenshot's HUD is laid over a panning copy of its world, so
    the world moves as one flat sheet: a simulation of panning, kept honest in the doc."""
    base = game()
    # Measured on the fitted frame: 60 px letterbox, 45 px top bar, bottom panels from y = 915 over x = 384..1536.
    top, bottom = 105, 1140
    hud = np.zeros(base.shape[:2], bool)
    hud[:top] = True
    hud[bottom:] = True
    hud[915:, 384:1536] = True
    world = base[top:bottom]
    out = []
    for t in range(frames):
        frame = base.copy()
        dx = int(min(t, frames - 1 - t) * px_per_frame)  # out and back: the last frame is the screenshot itself
        moved = np.roll(world, -dx, axis=1)
        frame[top:bottom] = moved
        frame[hud] = base[hud]
        out.append(frame)
    return out
