"""What a mockup must pass: no clipped text, no overlapping hit zones, every row drawn, the OLED cap, a centred icon, and a Restore
that the real pointer code can (or provably cannot) reach. make_mockups.py writes the results; the tests assert them."""
import json
import math

import numpy as np

from tools.native_spike import free_pointer as fp, neighbors, options_geometry as og
from tools.native_spike.unified_cursor import NUDGE_PX
from . import harness as h, maximize_options as X, menu_options as M


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


def check_design(d, snaps, children, fonts):
    """Every page of every tab in both scopes and the child screens: returns (problems, pages drawn)."""
    problems, count = [], 0
    for snap in snaps:
        drawn = []
        pages = d.page_count(snap, fonts)
        for page in range(pages):
            out = d.draw(snap, fonts, page)
            problems += panel_problems(out.panel)
            drawn += out.drawn
            count += 1
        want = [r['id'] for r in snap['rows']]
        if drawn != want: problems.append(f'{d.name}: {snap["category"]}/{snap["scope"]} drew {drawn}, not {want}')
        for r in snap['rows']:
            if not any(t['text'] == r['label'] for page in range(pages) for t in d.draw(snap, fonts, page).panel.texts) and not long_label_ok(d, snap, r, fonts, pages):
                problems.append(f'{d.name}: {snap["category"]}/{snap["scope"]} lost label {r["label"]!r}')
    for name, snap in children.items():
        for page in range(d.page_count(snap, fonts)):
            problems += panel_problems(d.draw(snap, fonts, page).panel)
            count += 1
    return problems, count


def long_label_ok(d, snap, row, fonts, pages):
    """A label wrapped onto two button lines is still whole."""
    words = ' '.join(t['text'] for page in range(pages) for t in d.draw(snap, fonts, page).panel.texts)
    return row['label'] in words


def tabs_reachable(d, snap, fonts):
    """Each of the 14 tabs has a hit zone: on show, in the open Section list, or on its own page of four."""
    found = set()
    for c in M.CATEGORIES:
        panel = d.draw(dict(snap, category=c, rows=[]), fonts, open_nav=True).panel
        found |= {x['name'][4:] for x in panel.hits if x['name'].startswith('tab:')}
    return set(M.CATEGORIES) - found


def real_geometry_kept(d, fonts):
    """Drop-in: its rows, tabs and header against the rects free_pointer.py and options_geometry.py use today."""
    bad = []
    if (d.w, d.h, d.grid.row_h, d.grid.x0, d.grid.top) != (og.W, og.H, og.ROW_H, og.PAD, og.BODY_TOP): bad.append('panel or row grid')
    if d.grid.top + d.grid.rows * d.grid.row_h != og.BODY_BOTTOM: bad.append('body bottom')
    return bad


def inventory_cover(snaps):
    """Rows the menu shows against tests/xr_options_acceptance/inventory.json: which inventory rows are not in-menu rows, and why."""
    rows = json.loads((h.REPO / 'tests/xr_options_acceptance/inventory.json').read_text())['rows']
    shown = {r['id'] for s in snaps for r in s['rows']}
    from tools.native_spike.options_actions import ALIASES
    missing = [r for r in rows if r['identifier'] not in shown and ALIASES.get(r['identifier']) not in shown]
    return shown, missing


def max_channel(path, w, hgt):
    a = np.frombuffer(open(path, 'rb').read(), np.uint8).reshape(hgt, w, 4)
    return int(a[..., :3].max())


# --- Maximize ---------------------------------------------------------------------------------------------------------
def tile_problems(look, frame):
    """A look on every tile of a real layout: clear of the bar, the top-left cap, its own desktop's width, the neighbour."""
    out = []
    for i, tile in enumerate(frame['tiles']):
        bar, s = tuple(frame['bars'][i]), frame['scale'][i]
        r, hit = look.rect(bar, s), look.hit(bar, s)
        if not bar[2]: continue
        if r[0] + r[2] > bar[0] + 1e-3: out.append(f'{look.name} touches the bar on tile {i}')
        bar_hit = (bar[0] - 4 * s, bar[1] - 4 * s, bar[2] + 8 * s, bar[3] + 8 * s)
        # The contract of immersive_part: padding gives way in a gap too narrow for it, and the button asks first.
        if look.gap * s < 8 * s:
            mid = bar[0] - look.gap * s / 2
            if abs(hit[0] + hit[2] - mid) > 1e-3: out.append(f'{look.name} hit does not stop at the gap\'s middle on tile {i}')
        elif overlap(hit, bar_hit): out.append(f'{look.name} hit overlaps the bar on tile {i}')
        if r[0] < tile[0] + 16 * s: out.append(f'{look.name} reaches the top-left cap on tile {i}')
        if hit[1] + hit[3] > tile[1] - 6: out.append(f'{look.name} hit enters the band along the edge on tile {i}')
        for j, other in enumerate(frame['tiles']):
            if j != i and overlap(hit, other): out.append(f'{look.name} hit is on tile {j} from tile {i}')
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


def x_of(look, m, d):
    """A pixel of pure fill on the centre line: just left of the glyph, inside the shape."""
    return int((m + look.w * .5 - 8) * d)


def centred(look, restore, fonts, stage_accent, work):
    """The drawn button's ink centre against the shape's: restyle_preview raster at density 6, ink = pixels unlike the fill."""
    p = look.draw(restore, False, fonts)
    m = X.MARGIN
    d = 6
    path = f'{work}/ink-{look.slug}-{restore}.rgba'
    h.job_call('raster', dict(accent=stage_accent, images=[dict(p.overlay(), density=d, out=path)]))
    W, H = int((look.w + 2 * m) * d), int((look.h + 2 * m) * d)
    a = np.frombuffer(open(path, 'rb').read(), np.uint8).reshape(H, W, 4).astype(int)
    cx, cy = W // 2, H // 2
    fill = a[cy, x_of(look, m, d)]
    y0, y1 = int((m + look.h * .5 - min(look.h * .5, 7.5)) * d), int((m + look.h * .5 + min(look.h * .5, 7.5)) * d)   # the glyph's own square,
    x0, x1 = int((m + look.w * .5 - 8.5) * d), int((m + look.w * .5 + 8.5) * d)                                      # clear of a capsule's corners
    sub = a[y0:y1, x0:x1]
    ink = np.abs(sub - fill)[..., :3].sum(axis=2) > 40
    ys, xs = np.nonzero(ink)
    return ((xs.min() + xs.max() + 1) / 2 + x0 - W / 2) / d, ((ys.min() + ys.max() + 1) / 2 + y0 - H / 2) / d
