"""The supervisor's orbit placement (tools/native_spike/orbit.py place_point: the pill drag, its hit testing and the
no-overlap guard) is the renderer's (view.hpp place_point on glass.hpp placement_from_wire, the wire units included).

usage: placement_contract_test.py PLACEMENT_GEOMETRY_DUMP REPO_ROOT
"""
import json
import subprocess
import sys

dump, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from tools.native_spike import edge_pan, free_pointer as fp, orbit  # noqa: E402

ABS, REL = 2e-4, 1e-4  # the renderer computes in float
KEYS = ('x', 'y', 'distance', 'curvature')  # every desktop always faces you: no yaw / pitch / roll

data = json.loads(subprocess.run([dump], check=True, capture_output=True, text=True).stdout)
cases = data['cases']
assert cases, 'no cases'
points = worst = behind = turned = rolled = glass = 0
for case in cases:
    w, h, hw, hh = case['canvas']
    g = {'w': w, 'h': h, 'half': (hw, hh)}
    tile, p = tuple(case['tile']), case['placement']
    assert tuple(sorted(set(p) - {'roll'})) == tuple(sorted(KEYS)), p  # 'roll': the levelling roll (roll-level icons)
    rolled += 'roll' in p
    for px, py, *want in case['points']:
        got = orbit.place_point(g, tile, p, px, py)
        for a, b in zip(got, want):
            err = abs(a - b)
            assert err <= ABS + REL * abs(b), (case['canvas'], tile, p, (px, py), got, want)
            worst = max(worst, err)
        points += 1
        behind += want[2] > 0
        # At its slot (no orbit, distance 1, flat) an off-centre desktop still faces you, so it leaves the canvas plane
        # z = -1 everywhere but its centre: the billboard is exercised, not only the orbit.
        if p == {'x': 0, 'y': 0, 'distance': 100, 'curvature': 0} and abs(want[2] + 1) > 1e-3: turned += 1
    # The glass (pill and panel) under it: free_pointer.glass_to_desktop is view.hpp's (fixed size whatever the distance).
    for gx, gy, *want in case['glass']:
        got = orbit.place_point(g, tile, p, *fp.glass_to_desktop(tile, p, gx, gy))
        for a, b in zip(got, want):
            assert abs(a - b) <= ABS + REL * abs(b), ('glass', case['canvas'], tile, p, (gx, gy), got, want)
        glass += 1
# The dump covers the orbit's whole range (both ends of x, behind you, and both poles) and finds desktops behind you.
xs, ys = {c['placement']['x'] for c in cases}, {c['placement']['y'] for c in cases}
assert {0, orbit.X_LIMIT, -orbit.X_LIMIT} <= xs and {0, orbit.Y_LIMIT} <= ys and min(ys) < 0, (sorted(xs), sorted(ys))
assert behind > 0 and turned > 0 and rolled > 0
assert glass > 0 and 10 in {c['placement']['distance'] for c in cases}
# The edge pan's two ends: edge_pan.py measures the hand's way on what the renderer draws (view.hpp edge_pan_path).
pans = data['pans']
assert len(pans) >= 100, len(pans)
for case in pans:
    w, h, hw, hh = case['canvas']
    g = {'w': w, 'h': h, 'half': (hw, hh), 'tiles': [tuple(t) for t in case['tiles']]}
    tile, q = edge_pan.drawn_home(g, g['tiles'][0], case['placements'][0], case['zoom'])
    e = edge_pan.point(g, tile, q, case['side'], case['exit'])
    p = edge_pan.point(g, g['tiles'][2], case['placements'][2], edge_pan.OPPOSITE[case['side']], case['entry'])
    for got, want in ((e, case['e']), (p, case['p'])):
        assert all(abs(a - b) <= 1e-3 for a, b in zip(got, want)), (case, got, want)
print(f"edge pan contract: {len(pans)} pans match")
print(f"placement contract: {points} points and {glass} glass points on {len(cases)} placed desktops match (worst error {worst:.1e}, "
      f"{behind} behind you, {turned} turned to face you at their slot, {rolled} rolled level)")
