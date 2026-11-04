"""The four scores: disparity range, flicker, halo, text legibility. Each works from what a recording can give: frames, a
disparity map (known from the pipeline, or estimated from an SBS pair by `estimate_disparity`), and the rendered eyes.

The numbers are engineering measures, not human data: "targets" in docs/3d-comfort.md are chosen against literature limits and
the sample sweep, and need a check in the glasses.
"""
from difflib import SequenceMatcher
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .conditioning import box, luma, text_mask
from .dibr import Eye
from .geometry import Optics, shibata_limits


# Disparity range ------------------------------------------------------------------------------------------------------

def disparity_stats(d_arcmin: np.ndarray, optics: Optics = Optics()) -> dict:
    """d in arcminutes, d < 0 in front of the plane. Border = the outer 2 % of the width: a pixel in front of the plane
    there is cut off by the frame that stands behind it, which the eyes read as a conflict (a window violation)."""
    p = np.percentile(d_arcmin, [0.1, 1, 50, 99, 99.9])
    limits = shibata_limits(optics)
    h, w = d_arcmin.shape
    b = max(1, int(0.02 * w))
    border = np.concatenate([d_arcmin[:, :b].ravel(), d_arcmin[:, -b:].ravel()])
    gy, gx = np.gradient(d_arcmin)
    ppd = optics.image_px_per_arcmin(w) * 60
    return {
        'near_arcmin': float(-min(p[0], 0)),  # p0.1, so a few stray pixels do not set the number
        'far_arcmin': float(max(p[4], 0)),
        'range_arcmin': float(p[4] - p[0]),
        'median_arcmin': float(p[2]),
        'percent_width_near': optics.arcmin_to_percent_width(float(-min(p[0], 0))),
        'percent_width_far': optics.arcmin_to_percent_width(float(max(p[4], 0))),
        'on_plane_share': float((np.abs(d_arcmin) < 1).mean()),
        'in_front_share': float((d_arcmin < -1).mean()),
        'outside_zone_share': float(((d_arcmin < -limits.near_arcmin) | (d_arcmin > limits.far_arcmin)).mean()),
        'border_violation_share': float((border < -1).mean()),
        'gradient_p99_arcmin_per_deg': float(np.percentile(np.abs(gx), 99) * ppd),
    }


# Flicker --------------------------------------------------------------------------------------------------------------

def flicker(frames: list[np.ndarray], disparities: list[np.ndarray], fps: float, still_luma: float = 2.0) -> dict:
    """Depth that changes while the picture does not, in arcminutes per frame, plus two views of the same fault:
    pump: the whole picture's median sliding in depth from frame to frame.
    jitter: the second difference d[t+1] - 2 d[t] + d[t-1], which ignores steady motion and so also works on panning content.
    A still pixel is one whose neighbourhood changed by under `still_luma` levels."""
    if len(frames) < 2:
        raise ValueError('flicker needs at least two frames')
    still_change, still_p95, still_share, pump = [], [], [], []
    grays = [luma(f) for f in frames]
    for t in range(1, len(frames)):
        delta = np.abs(disparities[t] - disparities[t - 1])
        still = box(np.abs(grays[t] - grays[t - 1]), 3) < still_luma
        still_share.append(float(still.mean()))
        if still.any():
            still_change.append(float(delta[still].mean()))
            still_p95.append(float(np.percentile(delta[still], 95)))
        pump.append(abs(float(np.median(disparities[t]) - np.median(disparities[t - 1]))))
    jitter = [float(np.abs(disparities[t + 1] - 2 * disparities[t] + disparities[t - 1]).mean())
              for t in range(1, len(frames) - 1)]
    return {
        'frames': len(frames),
        'still_share': float(np.mean(still_share)),
        'still_flicker_arcmin': float(np.mean(still_change)) if still_change else 0.0,
        'still_flicker_p95_arcmin': float(np.mean(still_p95)) if still_p95 else 0.0,
        'pump_arcmin': float(np.mean(pump)),
        'pump_max_arcmin': float(np.max(pump)),
        'jitter_arcmin': float(np.mean(jitter)) if jitter else 0.0,
        'peak_speed_arcmin_per_s': float(np.max(pump) * fps),
    }


# Halo -----------------------------------------------------------------------------------------------------------------

def _runs(row: np.ndarray):
    """(start, end) of each True run, end exclusive."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])))
    return edges[::2], edges[1::2]


def halo(eye: Eye, ring_px: int = 12) -> dict:
    """How visible the filled areas are. Each hole's fill is compared with the real background beside it, taken from the far
    side of the hole and clear of the seam: a fill of the wrong colour (foreground smear, black) or a fill that loses the
    background's texture (a flat streak) both count, weighted by area. It needs no ground truth, so it scores any renderer."""
    h, w = eye.holes.shape
    gray = luma(eye.image)
    widths, total = [], 0.0
    for y in np.flatnonzero(eye.holes.any(axis=1)):
        starts, ends = _runs(eye.holes[y])
        for a, b in zip(starts, ends):
            n = b - a
            widths.append(n)
            left = eye.disparity[y, a - 1] if a > 0 else -np.inf
            right = eye.disparity[y, b] if b < w else -np.inf
            if right >= left:
                ring = gray[y, min(b + 2, w - 1):min(b + 2 + ring_px, w)]
            else:
                ring = gray[y, max(a - 2 - ring_px, 0):max(a - 2, 1)]
            if ring.size == 0:
                continue
            fill = gray[y, a:b]
            wrong = abs(float(fill.mean()) - float(ring.mean()))
            lost = max(float(ring.std()) - float(fill.std()), 0.0) if n > 2 else 0.0
            total += n * (wrong + 0.5 * lost) / 255
    widths = np.array(widths) if widths else np.zeros(1)
    return {
        'halo_score': 1000 * total / (h * w),
        'hole_share': float(eye.holes.mean()),
        'hole_width_p95_px': float(np.percentile(widths, 95)),
        'hole_width_max_px': float(widths.max()),
    }


def edge_misalignment(disparity: np.ndarray, gray: np.ndarray, step_arcmin: float = 2.0) -> float:
    """Mean distance in px from a depth edge to the nearest picture edge (capped at 16). A depth edge off the object's edge
    carries a band of background along with the object: the classic halo at its root."""
    gx = np.abs(np.diff(disparity, axis=1, prepend=disparity[:, :1]))
    edges = gx > step_arcmin
    if not edges.any():
        return 0.0
    picture = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1])) > 12
    dist = np.full(edges.shape, 16.0, np.float32)
    for r in (8, 4, 2, 1, 0):
        reach = box(picture.astype(np.float32), r, 0) > 1e-4 if r else picture
        dist[reach] = r
    return float(dist[edges].mean())


# Text -----------------------------------------------------------------------------------------------------------------

def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM with a 7 x 7 box window, grey 0..255."""
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = box(a, 3), box(b, 3)
    va, vb = box(a * a, 3) - ma * ma, box(b * b, 3) - mb * mb
    cov = box(a * b, 3) - ma * mb
    return float((((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))).mean())


def _best_shift(ref: np.ndarray, eye: np.ndarray, y0: int, x0: int, th: int, tw: int, search: int) -> int:
    best, best_err = 0, float('inf')
    for s in sorted(range(-search, search + 1), key=abs):
        if x0 + s < 0 or x0 + s + tw > eye.shape[1]:
            continue
        err = float(np.abs(ref[y0:y0 + th, x0:x0 + tw] - eye[y0:y0 + th, x0 + s:x0 + s + tw]).mean())
        if err < best_err - 1e-3:
            best, best_err = s, err
    return best


def text_score(reference: np.ndarray, eye: np.ndarray, mask: np.ndarray, search: int = 12, tile=(32, 96)) -> dict:
    """SSIM of each text tile against the reference, after sliding the tile sideways to its best match. A block of text
    shifted whole in depth reads as well as ever, so a pure shift costs nothing; warped, torn or blurred glyphs do. Works for
    source vs eye, and left vs right (where a low score means the eyes see different glyphs)."""
    a, b = luma(reference), luma(eye)
    th, tw = tile
    scores = []
    for y0 in range(0, a.shape[0] - th + 1, th):
        for x0 in range(search, a.shape[1] - tw - search + 1, tw):  # every tile can slide the whole search
            if mask[y0:y0 + th, x0:x0 + tw].mean() < 0.5 or a[y0:y0 + th, x0:x0 + tw].std() < 8:
                continue
            s = _best_shift(a, b, y0, x0, th, tw, search)
            scores.append(_ssim(a[y0:y0 + th, x0:x0 + tw], b[y0:y0 + th, x0 + s:x0 + s + tw]))
    if not scores:
        return {'tiles': 0, 'text_ssim': 1.0, 'text_ssim_p10': 1.0}
    return {'tiles': len(scores), 'text_ssim': float(np.mean(scores)), 'text_ssim_p10': float(np.percentile(scores, 10))}


def text_wobble(d_arcmin: np.ndarray, mask: np.ndarray, r: int = 24) -> float:
    """Arcminutes of disparity varying within a line of text (local deviation over a 49 px wide, 9 px tall window): text
    that wobbles or leans in depth, even when each glyph stays sharp."""
    if not mask.any():
        return 0.0
    local = box(d_arcmin, r, 4)
    return float(np.abs(d_arcmin - local)[mask].mean())


def ocr(image: np.ndarray, box_xywh=None) -> str:
    """Tesseract's reading of an image, or '' without it. Slow, so it is only an optional second opinion on text_ssim."""
    if not shutil.which('tesseract'):
        return ''
    crop = image if box_xywh is None else image[box_xywh[1]:box_xywh[1] + box_xywh[3], box_xywh[0]:box_xywh[0] + box_xywh[2]]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'f.png'
        Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8)).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.BICUBIC).save(path)
        out = subprocess.run(['tesseract', str(path), 'stdout', '--psm', '6'], capture_output=True, text=True, timeout=120,
                             env={'OMP_THREAD_LIMIT': '2', 'PATH': '/usr/bin'})
    return out.stdout


def ocr_match(source: np.ndarray, eye: np.ndarray, box_xywh=None) -> float | None:
    """How much of the source's OCR text survives in the eye, 0..1; None when tesseract is missing or finds nothing."""
    a = ocr(source, box_xywh)
    if not a.strip():
        return None
    return SequenceMatcher(None, ' '.join(a.split()), ' '.join(ocr(eye, box_xywh).split()), autojunk=False).ratio()


# Brightness -----------------------------------------------------------------------------------------------------------

def brightness_excess(source: np.ndarray, *eyes: np.ndarray) -> float:
    """How far any eye goes above the source's brightest channel value. Must be 0: 3D may not raise the OLED's peak."""
    return max(0.0, max(float(e.max()) for e in eyes) - float(source.max()))


# Scoring a pair without a depth map -----------------------------------------------------------------------------------

def estimate_disparity(left: np.ndarray, right: np.ndarray, max_px: int = 40, r: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Block matching at half resolution: disparity (x_right - x_left, full-res px, from the left eye) and a bool mask of
    pixels whose left-right check fails (occlusions, which is where a renderer has filled). Flat areas read as 0."""
    a, b = luma(left)[::2, ::2], luma(right)[::2, ::2]
    half = max(1, max_px // 2)
    shifts = np.arange(-half, half + 1)

    def match(ref, other, sign):
        cost = np.stack([box(np.abs(ref - np.roll(other, -sign * int(s), axis=1)), r) for s in shifts])
        best = cost.argmin(axis=0)
        low = np.take_along_axis(cost, best[None], axis=0)[0]
        for offset in (-1, 0, 1):  # the runner-up has to be a different match, not the same valley
            np.put_along_axis(cost, np.clip(best + offset, 0, len(shifts) - 1)[None], np.inf, axis=0)
        return shifts[best], cost.min(axis=0) > low * 1.25 + 0.5  # a clear winner only

    dl, sure = match(a, b, 1)
    dr, _ = match(b, a, -1)
    dl, dr = dl.astype(np.float32), dr.astype(np.float32)
    h, w = a.shape
    xs = np.clip(np.arange(w)[None, :] + dl.astype(int), 0, w - 1)
    bad = np.abs(dl - dr[np.arange(h)[:, None], xs]) > 1.5
    texture = box(np.abs(np.diff(a, axis=1, prepend=a[:, :1])), r) > 1.0
    bad &= texture
    # Repeating glyphs match at the wrong letter; those fail the check too, so an unconfirmed match does not count as depth.
    dl = np.where(texture & ~bad & sure, dl, 0.0)
    H, W = left.shape[:2]
    up = lambda m: np.repeat(np.repeat(m, 2, axis=0), 2, axis=1)[:H, :W]
    return (up(dl) * 2).astype(np.float32), up(bad)


def evaluate_pair(source: np.ndarray, left: Eye, right: Eye, d_arcmin: np.ndarray, optics: Optics = Optics(),
                  use_ocr: bool = False) -> dict:
    """All the single-frame scores for one rendered pair. `d_arcmin` is on the source's grid."""
    gray = luma(source)
    mask = text_mask(gray)
    worst = lambda f: max((f(left), f(right)), key=lambda m: m['halo_score'])
    out = {'disparity': disparity_stats(d_arcmin, optics), 'halo': worst(halo),
           'edge_misalignment_px': edge_misalignment(d_arcmin, gray),
           'brightness_excess': brightness_excess(source, left.image, right.image)}
    tl, tr = text_score(source, left.image, mask), text_score(source, right.image, mask)
    pair = text_score(left.image, right.image, mask)
    out['text'] = {'tiles': tl['tiles'], 'text_mask_share': float(mask.mean()),
                   'text_ssim': min(tl['text_ssim'], tr['text_ssim']), 'text_ssim_p10': min(tl['text_ssim_p10'], tr['text_ssim_p10']),
                   'binocular_ssim': pair['text_ssim'], 'wobble_arcmin': text_wobble(d_arcmin, mask)}
    if use_ocr:
        out['text']['ocr_match'] = ocr_match(source, left.image)
    return out


def check_targets(result: dict, targets: dict) -> list[str]:
    """Names each target missed. `targets` maps 'section.key' to {'max': x} or {'min': x}."""
    misses = []
    for key, bound in targets.items():
        node = result
        for part in key.split('.'):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            continue
        if 'max' in bound and node > bound['max']:
            misses.append(f"{key} {node:.3g} > {bound['max']}")
        if 'min' in bound and node < bound['min']:
            misses.append(f"{key} {node:.3g} < {bound['min']}")
    return misses
