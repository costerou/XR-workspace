"""Small joint bilateral refinement on the model grid, outside the render/pose path."""
import numpy as np


def refine(depth, luma):
    """Colour-guided 5x5 filter; constrain outlying depth steps without crossing strong colour edges.

    Runs only when inference publishes a map. No full-resolution pass or frame queue.
    """
    depth = np.nan_to_num(depth, nan=0., posinf=0., neginf=0.).astype(np.float32)
    lo, hi = np.percentile(depth, (2, 98))
    span = max(float(hi - lo), 1e-6)
    d = (depth - lo) / span
    pd, pc = np.pad(d, 2, mode='edge'), np.pad(luma, 2, mode='edge')
    total, weight = np.zeros_like(d), np.zeros_like(d)
    h, w = d.shape
    for y in range(5):
        for x in range(5):
            neighbour = pd[y:y+h, x:x+w]
            contrast = pc[y:y+h, x:x+w] - luma
            k = np.exp(-contrast * contrast / .0032 - ((x-2)**2 + (y-2)**2) / 4.)
            # A colour boundary can retain a large step; a flat region cannot hide a depth spike.
            limit = .12 + .88 * np.clip(np.abs(contrast) / .12, 0, 1)
            total += k * (d + np.clip(neighbour - d, -limit, limit))
            weight += k
    return ((total / weight) * span + lo).astype(np.float32)
