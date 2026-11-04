"""Linear-time colour-guided depth refinement, outside the render/pose path."""
import numpy as np


def _box(a, radius=3):
    """Separable box mean, repeated edge boundary, float64 accumulators."""
    n = 2 * radius + 1
    p = np.pad(a, ((0, 0), (radius + 1, radius)), mode='edge')
    c = np.cumsum(p, axis=1, dtype=np.float64)
    out = (c[:, n:] - c[:, :-n]) / n
    p = np.pad(out, ((radius + 1, radius), (0, 0)), mode='edge')
    c = np.cumsum(p, axis=0, dtype=np.float64)
    return ((c[n:] - c[:-n]) / n).astype(np.float32)


def refine(depth, luma):
    """He-style guided filter on the inference grid; colour snaps a blurred depth edge.

    Covariance fits depth to the local picture, then averages the coefficients rather
    than averaging across the visible edge. Flat regions lose depth spikes; colour
    detail with no depth support cannot invent a new depth range. O(pixels), no GPU
    pass, no full-size image and no frame queue.
    """
    depth = np.nan_to_num(depth, nan=0., posinf=0., neginf=0.).astype(np.float32)
    guide = np.asarray(luma, np.float32)
    mi, mp = _box(guide), _box(depth)
    variance = np.maximum(_box(guide * guide) - mi * mi, 0.)
    covariance = _box(guide * depth) - mi * mp
    a = covariance / (variance + .001)
    b = mp - a * mi
    refined = _box(a) * guide + _box(b)
    return np.clip(refined, float(depth.min()), float(depth.max())).astype(np.float32)


def limit_steps(depth, step=.3):
    """Symmetric Lipschitz envelopes: normalized depth steps <= .3 per model pixel.

    Four Manhattan relaxations cover the complete 0..1 depth range at this ceiling.
    Averaging the upper/lower envelopes keeps the edge centred instead of eroding
    only foreground or background. Constant planes and broad gradients are unchanged.
    """
    low, high = depth.copy(), depth.copy()
    for _ in range(int(np.ceil(1 / step))):
        pl, ph = np.pad(low, 1, mode='edge'), np.pad(high, 1, mode='edge')
        low = np.minimum.reduce([low, pl[:-2, 1:-1] + step, pl[2:, 1:-1] + step,
                                 pl[1:-1, :-2] + step, pl[1:-1, 2:] + step])
        high = np.maximum.reduce([high, ph[:-2, 1:-1] - step, ph[2:, 1:-1] - step,
                                  ph[1:-1, :-2] - step, ph[1:-1, 2:] - step])
    return (low + high) * .5
