"""What a mockup must pass: no clipped text, no overlapping hit zones, every row drawn, the OLED cap, a centred icon, and a Restore
that the real pointer code can (or provably cannot) reach. make_mockups.py writes the results; the tests assert them."""
import json

from tools.native_spike import free_pointer as fp, neighbors, options_geometry as og
from tools.native_spike.unified_cursor import NUDGE_PX
from . import harness as h, maximize_options as X


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def inside(a, x0, y0, x1, y1):
    return a[0] >= x0 and a[1] >= y0 and a[0] + a[2] <= x1 and a[1] + a[3] <= y1


def panel_problems(p, covered=False):
    """Text and hit zones of one drawn panel. `covered`: a list is open over the rows, so what it hides is not compared."""
    out = []
    for t in p.texts:
        x0, x1 = t['cell']
        if t['x'] < x0 - .5 or t['x'] + t['w'] > x1 + .5: out.append(f'{p.name}: text {t["text"]!r} leaves its cell ({t["x"]:.0f}..{t["x"] + t["w"]:.0f} in {x0:.0f}..{x1:.0f})')
        if t['x'] < 0 or t['x'] + t['w'] > p.w or t['cy'] - t['px'] / 2 < 0 or t['cy'] + t['px'] / 2 > p.h: out.append(f'{p.name}: text {t["text"]!r} leaves the panel')
    if not covered:
        box = lambda t: (t['x'], t['cy'] - .4 * t['px'], t['w'], .8 * t['px'])
        for i, a in enumerate(p.texts):
            for b in p.texts[i + 1:]:
                if overlap(box(a), box(b)): out.append(f'{p.name}: text {a["text"]!r} on {b["text"]!r}')
    for i, a in enumerate(p.hits):
        if not inside(a['rect'], 0, 0, p.w, p.h): out.append(f'{p.name}: hit {a["name"]} leaves the panel')
        if covered: continue
        for b in p.hits[i + 1:]:
            if overlap(a['rect'], b['rect']): out.append(f'{p.name}: hits {a["name"]} and {b["name"]} overlap')
    return out


def corner_table(setup=h.REAL):
    """For each desktop and corner, the desktops a push into that corner would pan to: real neighbors.pick_at on the saved layout."""
    frame = h.layout([dict(desktops=[dict(w=d['w'], h=d['h'], placement=d['placement']) for d in setup], spacing=h.REAL_SPACING, selected=0, zoom=0.0)])[0]
    c = frame['canvas']
    g = dict(w=c[0], h=c[1], half=(c[2], c[3]), tiles=frame['tiles'])
    placements = [{k: d['placement'][k] for k in ('x', 'y', 'distance', 'curvature')} for d in setup]
    out = {}
    for home in range(len(setup)):
        for name, ve, ha, along_h, along_v in (('top-left', 'up', 'left', 0, 0), ('top-right', 'up', 'right', 1, 0), ('bottom-left', 'down', 'left', 0, 1), ('bottom-right', 'down', 'right', 1, 1)):
            out[f'XR-{home + 1} {name}'] = dict(up_down=neighbors.pick_at(g, placements, home, ve, along_h), left_right=neighbors.pick_at(g, placements, home, ha, along_v))
    return out


def geometry_for(frame, mesh):
    return fp.parse_maximize_geometry(dict(monitor='XR-1', maximized=True, canvas=frame['canvas'], tile=frame['tiles'][frame['selected']], placement=frame['placement'],
                                           rect=[1, 1, 1, 1], hit_mesh=mesh, view=frame['view']))


def output_of(d):
    return dict(x=0, y=0, width=d['w'] / d['scale'], height=d['h'] / d['scale'])


def reach(geometry, output, cursor):
    return fp.restore_mesh_hit(geometry, fp.restore_cursor_ray(geometry, output, cursor))


def rect_meshes(setup, selected, rects):
    frame = h.layout([dict(desktops=[dict(w=d['w'], h=d['h'], placement=d['placement']) for d in setup], spacing=h.REAL_SPACING, selected=selected, zoom=1.0, meshes=rects)])[0]
    frame['selected'] = selected
    return frame


def sweep(geometry, output, step=4):
    """Every `step`th logical pixel of the output, and the outermost rows and columns: does the real pointer code ever reach the mesh?"""
    w, h_ = int(output['width']), int(output['height'])
    xs = sorted({*range(0, w, step), w - 1})
    ys = sorted({*range(0, h_, step), h_ - 1})
    return sum(reach(geometry, output, (x, y)) for x in xs for y in ys)


def corner_rect(tile, corner, w, hgt, inset=X.CORNER_INSET):
    """`corner` of the tile, `inset` in: (x, y, w, h)."""
    x = tile[0] + tile[2] - w - inset if 'right' in corner else tile[0] + inset
    y = tile[1] + tile[3] - hgt - inset if 'bottom' in corner else tile[1] + inset
    return [x, y, w, hgt]


def flush(r, tile):
    """Rect `r` grown to the tile's nearest corner, plus a pixel beyond it."""
    x0 = tile[0] - 1 if r[0] - tile[0] < tile[0] + tile[2] - r[0] - r[2] else r[0] - 4
    x1 = tile[0] + tile[2] + 1 if x0 == r[0] - 4 else r[0] + r[2] + 4
    y0 = tile[1] - 1 if r[1] - tile[1] < tile[1] + tile[3] - r[1] - r[3] else r[1] - 4
    y1 = tile[1] + tile[3] + 1 if y0 == r[1] - 4 else r[1] + r[3] + 4
    return [x0, y0, x1 - x0, y1 - y0]


def restore_reach(corner, setup=h.REAL):
    """Run R1, R2 and R3's meshes through free_pointer's real parser, ray and hit test on each saved desktop."""
    out = {}
    for i, d in enumerate(setup):
        tile = h.layout([dict(desktops=[dict(w=q['w'], h=q['h'], placement=q['placement']) for q in setup], spacing=h.REAL_SPACING, selected=i, zoom=1.0)])[0]['tiles'][i]
        o = output_of(d)
        pad = 4
        scale_x = tile[2] / o['width']
        zone = X.ZONE_LOGICAL * scale_x
        # The hit zone runs flush into the corner, and a pixel past it: the cursor stops on the output's last pixel, so a target
        # that stops at the drawing's inset can never be reached.
        pill = flush(corner_rect(tile, corner, X.PILL_W, X.PILL_H), tile)
        chip = flush(corner_rect(tile, corner, 26, 26), tile)
        band = [tile[0] + (tile[2] - X.PILL_W) / 2, tile[1] - X.PILL_H - X.BAND_PILL_GAP, X.PILL_W, X.PILL_H]
        trigger = corner_rect(tile, corner, zone, zone, 0)
        inset = corner_rect(tile, corner, X.PILL_W, X.PILL_H)   # the drawing's own rect, padded: the trap a corner target falls in
        rects = [pill, chip, trigger, band, [inset[0] - pad, inset[1] - pad, inset[2] + 2 * pad, inset[3] + 2 * pad]]
        frame = rect_meshes(setup, i, rects)
        meshes = [geometry_for(frame, m) for m in frame['meshes']]
        cx = o['width'] - 1 if 'right' in corner else 0
        cy = o['height'] - 1 if 'bottom' in corner else 0
        cursor = (cx, cy)
        back = (cx - NUDGE_PX if 'right' in corner else cx + NUDGE_PX, cy - NUDGE_PX if 'bottom' in corner else cy + NUDGE_PX)   # after a nudge
        out[f'XR-{i + 1}'] = dict(tile=tile, logical=[o['width'], o['height']], pill_hit=reach(meshes[0], o, cursor), chip_hit=reach(meshes[1], o, cursor),
                                  inset_rect_hit=reach(meshes[4], o, cursor),
                                  zone_at_corner=reach(meshes[2], o, cursor), zone_after_nudge=reach(meshes[2], o, back),
                                  band_hits_any_output_pixel=sweep(meshes[3], o), zone_px_of_output=sweep(meshes[2], o, 1))
    return out


def letterbox(sizes):
    """Px of view left above a maximized picture, per size: the real layout's tile against the canvas."""
    out = {}
    for name, w, hgt in sizes:
        frame = h.layout([dict(desktops=[dict(w=w, h=hgt, placement=dict(x=0, y=0, distance=100, curvature=0))], spacing=0, selected=0, zoom=1.0)])[0]
        out[name] = dict(size=f'{w}x{hgt}', above=frame['tiles'][0][1], side=frame['tiles'][0][0], tile=frame['tiles'][0])
    return out
