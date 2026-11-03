"""The supervisor's glass pill and roll bracket hit testing (tools/native_spike/free_pointer.py) uses the renderer's geometry (glass.hpp).

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
mirror = {'pill_share': fp.PILL_SHARE, 'pill_min_w': fp.PILL_MIN, 'pill_max_w': fp.PILL_MAX, 'pill_h': fp.PILL_THICKNESS,
          'pill_gap': fp.PILL_GAP, 'panel_w': fp.PANEL_W, 'panel_h': fp.PANEL_H, 'header_h': fp.PANEL_HEADER_H,
          'rows_top': fp.PANEL_ROWS_TOP, 'row_h': fp.PANEL_ROW_H, 'knob_lo': fp.PANEL_KNOB_LO, 'knob_hi': fp.PANEL_KNOB_HI,
          'roll_gap': fp.ROLL_GAP, 'roll_stroke': fp.ROLL_STROKE, 'roll_head_len': fp.ROLL_HEAD_LEN,
          'roll_head_half': fp.ROLL_HEAD_HALF, 'roll_pad': fp.ROLL_PAD, 'roll_arm_share': fp.ROLL_ARM_SHARE,
          'roll_arm_min': fp.ROLL_ARM_MIN, 'roll_arm_max_share': fp.ROLL_ARM_MAX_SHARE, 'roll_edge_clear': fp.ROLL_EDGE_CLEAR,
          'actions_top': fp.PANEL_ACTIONS_TOP, 'action_h': fp.PANEL_ACTION_H, 'action_w': fp.PANEL_ACTION_W,
          'action_gap': fp.PANEL_ACTION_GAP, 'actions': len(fp.PANEL_ACTIONS), 'max_desktops': fp.MAX_DESKTOPS,
          'scale_top': fp.PANEL_SCALE_TOP, 'scale_item_h': fp.PANEL_SCALE_ITEM_H, 'scale_list_gap': fp.PANEL_SCALE_LIST_GAP}
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
probes = hits = 0
for case in data['cases']:
    tile = tuple(case['tile'])
    for got, want in ((fp.pill_rect(tile), case['pill']), (fp.panel_rect(tile), case['panel'])):
        assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (tile, got, want)
    # Roll brackets (glass.hpp roll_arm / roll_scale / roll_distance / on_roll_bracket): the same arm and scale, and round
    # both top corners the same distance to the centre line and the same hit region (where the point is not within float
    # error of the region's edge or of the desktop's).
    roll = case['roll']
    assert abs(fp.roll_arm(tile) - roll['arm']) < 1e-3 and abs(fp.roll_scale(tile) - roll['scale']) < 1e-4, (tile, roll['arm'], roll['scale'])
    reach = (fp.ROLL_HEAD_HALF + fp.ROLL_PAD) * fp.roll_scale(tile)
    tx, ty, tw, th = tile
    for side, px, py, dist, on in roll['probes']:
        name, q = fp.ROLL_SIDES[side], (px, py)
        got = fp.roll_distance(tile, name, q)
        assert abs(got - dist) < 2e-3 * max(1.0, dist), (tile, name, q, got, dist)
        e = fp.ROLL_EDGE_CLEAR * fp.roll_scale(tile)  # the edge band's outer edge, as well as the desktop's own
        edge = min(abs(px - tx), abs(px - tx - tw), abs(py - ty), abs(py - ty - th),
                   abs(px - tx + e), abs(px - tx - tw - e), abs(py - ty + e), abs(py - ty - th - e))
        if abs(got - reach) > 1e-2 and edge > 1e-2:
            assert fp.on_roll(tile, name, q) == bool(on), (tile, name, q, got, reach, on)
        probes += 1; hits += bool(on)
assert probes > 1000 and 100 < hits < probes, (probes, hits)
print(f"glass contract: {len(mirror)} constants, {len(data['cases'])} layouts and {probes} roll bracket probes ({hits} hits) match")
