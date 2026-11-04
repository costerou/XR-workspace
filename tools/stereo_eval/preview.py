"""Pictures of a result: full side-by-side, red-cyan anaglyph, depth views and contact sheets."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Dubois least-squares red-cyan matrices: less retinal rivalry and ghosting than the simple channel swap.
DUBOIS_L = np.array([[0.456, 0.500, 0.176], [-0.040, -0.038, -0.016], [-0.015, -0.021, -0.005]], np.float32)
DUBOIS_R = np.array([[-0.043, -0.088, -0.002], [0.378, 0.734, -0.018], [-0.072, -0.113, 1.226]], np.float32)
FONT = '/usr/share/fonts/liberation/LiberationSans-Bold.ttf'


def to_u8(a: np.ndarray) -> np.ndarray:
    return np.clip(a + 0.5, 0, 255).astype(np.uint8)


def side_by_side(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Full width per eye: 3840 x 1200 from two 1920 x 1200 eyes. Left eye on the left."""
    return np.concatenate([to_u8(left), to_u8(right)], axis=1)


def anaglyph(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return to_u8(np.clip(left.astype(np.float32) @ DUBOIS_L.T + right.astype(np.float32) @ DUBOIS_R.T, 0, 255))


def disparity_view(d_arcmin: np.ndarray, near: float, far: float) -> np.ndarray:
    """Grey is the plane, warm is in front of it, cool is behind it, full colour at the given limits."""
    t = np.clip(d_arcmin / np.where(d_arcmin < 0, max(near, 1e-3), max(far, 1e-3)), -1, 1)[..., None]
    grey, warm, cool = np.array([58, 58, 62.]), np.array([255, 150, 60.]), np.array([70, 150, 255.])
    return to_u8(grey + np.maximum(-t, 0) * (warm - grey) + np.maximum(t, 0) * (cool - grey))


def label(img: np.ndarray, text: str, size: int = 28) -> np.ndarray:
    image = Image.fromarray(img.astype(np.uint8)).convert('RGB')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, size) if Path(FONT).exists() else ImageFont.load_default()
    box = draw.textbbox((12, 8), text, font=font)
    draw.rectangle((box[0] - 8, box[1] - 6, box[2] + 8, box[3] + 6), fill=(0, 0, 0))
    draw.text((12, 8), text, fill=(255, 255, 255), font=font)
    return np.asarray(image)


def sheet(rows: list[list[np.ndarray]], tile_w: int = 640, gap: int = 6) -> np.ndarray:
    """A grid of images, each scaled to tile_w wide."""
    def fit(a):
        return np.asarray(Image.fromarray(a.astype(np.uint8)).convert('RGB').resize((tile_w, round(a.shape[0] * tile_w / a.shape[1])), Image.LANCZOS))
    made = [[fit(a) for a in row] for row in rows]
    heights = [max(a.shape[0] for a in row) for row in made]
    cols = max(len(r) for r in made)
    out = np.full((sum(heights) + gap * (len(made) + 1), cols * tile_w + gap * (cols + 1), 3), 24, np.uint8)
    y = gap
    for row, height in zip(made, heights):
        x = gap
        for a in row:
            out[y:y + a.shape[0], x:x + a.shape[1]] = a
            x += tile_w + gap
        y += height + gap
    return out


def save(path: str | Path, a: np.ndarray) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a.astype(np.uint8)).save(path, optimize=False, compress_level=6)
    return path
