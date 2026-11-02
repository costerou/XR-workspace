"""The renderer's menu_geometry status lines parse in the supervisor (view.parse_menu_geometry) and describe the card.

usage: menu_geometry_contract_test.py MENU_GEOMETRY_DUMP REPO_ROOT
"""
import json
import subprocess
import sys

dump, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from tools.native_spike import view  # noqa: E402

lines = subprocess.run([dump], check=True, capture_output=True, text=True).stdout.splitlines()
assert len(lines) >= 3, lines
for line in lines:
    status, width, height = line.split('\t')
    value = json.loads(status)
    assert set(value) == {'menu_geometry'}, value
    g = view.parse_menu_geometry(value['menu_geometry'])
    assert (g['w'], g['h']) == (int(width), int(height)), (g['w'], g['h'], width, height)
    assert all(0 <= x0 and x1 <= g['w'] and 0 <= y0 for x0, y0, x1, y1 in g['tabs'])
    assert all(0 < lo < hi < g['w'] and 0 <= top < bottom <= g['h'] for _, top, bottom, lo, hi in g['sliders'])
    # The supervisor's hit test finds the selected row's slider at the middle of its band and both ends.
    state = view.ViewState(['XR-1', 'XR-2', 'XR-3'])
    state.menu_open, state.menu_field = True, 'pitch'
    state.set_geometry(g)
    state.apply_menu('menu_mouse')
    sl = next(s for s in g['sliders'] if s[0] == 4)
    for x, want in ((sl[3], -45), (sl[4], 45)):
        state.mouse.update(x=float(x), y=(sl[1] + sl[2]) / 2)
        state.apply_menu('menu_mouse_press'); state.apply_menu('menu_mouse_release')
        assert state.placements[0]['pitch'] == want, (state.placements[0]['pitch'], want)
print(f'menu geometry contract: {len(lines)} layouts parse and hit-test')
