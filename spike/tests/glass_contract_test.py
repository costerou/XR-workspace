"""The supervisor's glass pill and level bar hit testing (tools/native_spike/free_pointer.py) uses the renderer's geometry (glass.hpp).

usage: glass_contract_test.py GLASS_GEOMETRY_DUMP REPO_ROOT
"""
import json
import subprocess
import sys

dump, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from tools.native_spike import free_pointer as fp, geometry, view  # noqa: E402

data = json.loads(subprocess.run([dump], check=True, capture_output=True, text=True).stdout)
c = data['constants']
c_cap = data['cap']
mirror = {'pill_share': fp.PILL_SHARE, 'pill_min_w': fp.PILL_MIN, 'pill_max_w': fp.PILL_MAX, 'pill_h': fp.PILL_THICKNESS,
          'pill_gap': fp.PILL_GAP, 'panel_w': fp.PANEL_W, 'panel_h': fp.PANEL_H, 'header_h': fp.PANEL_HEADER_H,
          'rows_top': fp.PANEL_ROWS_TOP, 'row_h': fp.PANEL_ROW_H, 'knob_lo': fp.PANEL_KNOB_LO, 'knob_hi': fp.PANEL_KNOB_HI,
          'bar_w': fp.LEVEL_BAR_W, 'bar_h': fp.LEVEL_BAR_H, 'bar_gap': fp.LEVEL_BAR_GAP, 'bar_pad': fp.LEVEL_BAR_PAD,
          'bar_share': fp.LEVEL_BAR_SHARE, 'bar_min_w': fp.LEVEL_BAR_MIN_W, 'edge_clear': fp.LEVEL_EDGE_CLEAR,
          'actions_top': fp.PANEL_ACTIONS_TOP, 'action_h': fp.PANEL_ACTION_H, 'action_w': fp.PANEL_ACTION_W,
          'action_gap': fp.PANEL_ACTION_GAP, 'actions': len(fp.PANEL_ACTIONS), 'max_desktops': fp.MAX_DESKTOPS,
          'scale_top': fp.PANEL_SCALE_TOP, 'scale_item_h': fp.PANEL_SCALE_ITEM_H, 'scale_list_gap': fp.PANEL_SCALE_LIST_GAP,
          'resolution_top': fp.PANEL_RES_TOP, 'resolution_item_h': fp.PANEL_RES_ITEM_H,
          'resolution_list_gap': fp.PANEL_RES_LIST_GAP}
assert set(mirror) == set(c), (sorted(mirror), sorted(c))
for name, value in mirror.items():
    assert abs(value - c[name]) < 1e-3, (name, value, c[name])
for i, want in enumerate(data['actions']):  # the desktop row's buttons (panel px)
    got = fp.action_rect(i)
    assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (i, got, want)
# The Scale dropdown: its box, its list's items and the steps they stand for (geometry.SCALES, view.SCALE_PERCENTS).
assert all(abs(a - b) < 1e-3 for a, b in zip(fp.PANEL_SCALE_BOX, data['scale_box'])), (fp.PANEL_SCALE_BOX, data['scale_box'])
assert len(data['scale_items']) == len(view.SCALE_PERCENTS)
for i, want in enumerate(data['scale_items']):
    got = fp.scale_item_rect(i)
    assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (i, got, want)
assert data['scale_percents'] == list(view.SCALE_PERCENTS) == [round(s * 100) for s in geometry.SCALES], data['scale_percents']
# The Resolution dropdown: its box, its open list and items, and the sizes and shapes they stand for (geometry.RESOLUTIONS,
# RESOLUTION_GROUPS).
assert all(abs(a - b) < 1e-3 for a, b in zip(fp.PANEL_RES_BOX, data['resolution_box'])), (fp.PANEL_RES_BOX, data['resolution_box'])
assert all(abs(a - b) < 1e-3 for a, b in zip(fp.resolution_list_rect(), data['resolution_list'])), data['resolution_list']
assert len(data['resolution_items']) == len(geometry.RESOLUTIONS)
for i, want in enumerate(data['resolution_items']):
    got = fp.resolution_item_rect(i)
    assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (i, got, want)
assert [tuple(r) for r in data['resolutions']] == list(geometry.RESOLUTIONS), data['resolutions']
assert [(n, c) for n, c in data['resolution_groups']] == [(n, len(s)) for n, s in geometry.RESOLUTION_GROUPS], data['resolution_groups']
probes = hits = 0
for case in data['cases']:
    tile = tuple(case['tile'])
    for got, want in ((fp.pill_rect(tile), case['pill']), (fp.panel_rect(tile), case['panel'])):
        assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (tile, got, want)
    # Level bars (glass.hpp level_bar_scale / level_bar_rect / level_bar_segment): the same scale and rect, and over and round
    # the bar the same segment, where the point is not within float error of the hit region's edge or a third's.
    bar = case['bar']
    assert abs(fp.level_bar_scale(tile) - bar['scale']) < 1e-4, (tile, bar['scale'])
    rect = fp.level_bar_rect(tile)
    assert all(abs(a - b) < 1e-3 for a, b in zip(rect, bar['rect'])), (tile, rect, bar['rect'])
    x, y, w, h = rect
    pad = fp.LEVEL_BAR_PAD * fp.level_bar_scale(tile)
    for px, py, segment in bar['probes']:
        want = fp.LEVEL_SEGMENTS[segment] if segment >= 0 else None
        edge = min(abs(px - x + pad), abs(px - x - w - pad), abs(py - y + pad), abs(py - y - h - pad), abs(px - x - w / 3), abs(px - x - 2 * w / 3))
        if edge > 1e-2:
            got = fp.level_bar_segment(tile, (px, py))
            assert got == want, (tile, px, py, got, want)
        probes += 1; hits += want is not None
assert probes > 1000 and 100 < hits < probes, (probes, hits)
# The corner caps sit in the corners' resize zones, so what you see is what you can press: an idle cap lies inside the band
# EDGE_OUT_PX outside the edges and within the smallest corner_zone along them.
assert c_cap['gap'] + c_cap['stroke'] / 2 <= fp.EDGE_OUT_PX and c_cap['leg'] - c_cap['gap'] + c_cap['stroke'] / 2 <= fp.CORNER_MIN, c_cap
print(f"glass contract: {len(mirror)} constants, {len(data['cases'])} layouts and {probes} level bar probes ({hits} hits) match")
