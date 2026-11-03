"""The supervisor's orbit placement (tools/native_spike/orbit.py place_point: the pill drag, its hit testing and the
no-overlap guard) is the renderer's (view.hpp place_point on glass.hpp placement_from_wire, the wire units included).

usage: placement_contract_test.py PLACEMENT_GEOMETRY_DUMP REPO_ROOT
"""
import json
import subprocess
import sys

dump, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from tools.native_spike import orbit  # noqa: E402

ABS, REL = 2e-4, 1e-4  # the renderer computes in float
KEYS = ('x', 'y', 'distance', 'curvature')  # every desktop always faces you: no yaw / pitch / roll

data = json.loads(subprocess.run([dump], check=True, capture_output=True, text=True).stdout)
cases = data['cases']
assert cases, 'no cases'
points = worst = behind = turned = rolled = 0
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
# The dump covers the orbit's whole range (both ends of x, behind you, and both poles) and finds desktops behind you.
xs, ys = {c['placement']['x'] for c in cases}, {c['placement']['y'] for c in cases}
assert {0, orbit.X_LIMIT, -orbit.X_LIMIT} <= xs and {0, orbit.Y_LIMIT} <= ys and min(ys) < 0, (sorted(xs), sorted(ys))
assert behind > 0 and turned > 0 and rolled > 0
print(f"placement contract: {points} points on {len(cases)} placed desktops match (worst error {worst:.1e}, {behind} behind you, "
      f"{turned} turned to face you at their slot, {rolled} rolled level)")
