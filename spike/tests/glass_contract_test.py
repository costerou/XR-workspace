"""The supervisor's glass pill hit testing (tools/native_spike/free_pointer.py) uses the renderer's geometry (glass.hpp).

usage: glass_contract_test.py GLASS_GEOMETRY_DUMP REPO_ROOT
"""
import json
import subprocess
import sys

dump, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from tools.native_spike import free_pointer as fp  # noqa: E402

data = json.loads(subprocess.run([dump], check=True, capture_output=True, text=True).stdout)
c = data['constants']
mirror = {'pill_share': fp.PILL_SHARE, 'pill_min_w': fp.PILL_MIN, 'pill_max_w': fp.PILL_MAX, 'pill_h': fp.PILL_THICKNESS,
          'pill_gap': fp.PILL_GAP, 'panel_w': fp.PANEL_W, 'panel_h': fp.PANEL_H, 'header_h': fp.PANEL_HEADER_H,
          'rows_top': fp.PANEL_ROWS_TOP, 'row_h': fp.PANEL_ROW_H, 'knob_lo': fp.PANEL_KNOB_LO, 'knob_hi': fp.PANEL_KNOB_HI,
          'actions_top': fp.PANEL_ACTIONS_TOP, 'action_h': fp.PANEL_ACTION_H, 'action_w': fp.PANEL_ACTION_W,
          'action_gap': fp.PANEL_ACTION_GAP, 'actions': len(fp.PANEL_ACTIONS), 'max_desktops': fp.MAX_DESKTOPS}
assert set(mirror) == set(c), (sorted(mirror), sorted(c))
for name, value in mirror.items():
    assert abs(value - c[name]) < 1e-3, (name, value, c[name])
for i, want in enumerate(data['actions']):  # the desktop row's buttons (panel px)
    got = fp.action_rect(i)
    assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (i, got, want)
for case in data['cases']:
    tile = tuple(case['tile'])
    for got, want in ((fp.pill_rect(tile), case['pill']), (fp.panel_rect(tile), case['panel'])):
        assert all(abs(a - b) < 1e-3 for a, b in zip(got, want)), (tile, got, want)
print(f"glass contract: {len(mirror)} constants and {len(data['cases'])} layouts match")
