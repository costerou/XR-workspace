"""The edge pan is one smooth path whatever the layout: every layout in a grid of sizes (0.5x to 10x), positions, curvature, roll and
pushes, through the real renderer's animation (edge_pan_trace) with the real edge_pan.Plan, the hand 1:1 and a landing.

A desktop's seen size goes one way from where it starts to where it ends, with nothing past either end. The view only ever gets
nearer the target, its speed has no second start, and nothing jumps from one frame to the next. Pulling back undoes it.
Offline: no GL, no display, nothing live.

usage: edge_pan_motion_test.py EDGE_PAN_TRACE REPO_ROOT
"""
import itertools
import math
import sys
from pathlib import Path

trace_binary, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from tools.edge_pan_clips import motion as m  # noqa: E402
from tools.native_spike import edge_pan, orbit  # noqa: E402

SIZE_FLOOR = 0.002       # of a desktop's seen size: less than this back is rounding, not a move
SIZE_STEP = 0.09         # the most a desktop's seen size may change in one 120 Hz frame: the last of a 0.5x desktop coming up to 6 times its size
SIZE_KINK = 0.02         # the most that step may itself change from one frame to the next: a jump, however small the step around it
TURN_BACK = math.radians(0.02)  # the most the view may go back toward home
DIP = 0.20               # the share of its speed the view may lose between two faster stretches
JOLT = 0.34              # the largest frame-to-frame change of the view's speed, as a share of its peak speed
ROLL_FLOOR = math.radians(0.02)
LANDED_S = 0.80          # edge_pan's landing fits the switch cooldown (unified_cursor.SWITCH_COOLDOWN_S)
edge_pan_ease = 0.030   # view.hpp kPanEaseMs
FOV = (46.0, 52.0)
DISTANCES = (200, 100, 71, 38, 20, 10)     # wire: 0.5x to 10x
SOURCES = (((1920, 1080),) * 3, ((1920, 1200),) * 3, ((2560, 1080), (1920, 1200), (1920, 1080)))
SIDES = ('right', 'left', 'up', 'down')


def half_angle(half, tiles, index, distance):
    """Half the seen width and height of a desktop in degrees, as drawn at the overview."""
    t = tiles[index]
    hw, hh = half
    k = 100 / distance
    return math.degrees(math.atan(t[2] / 1920 * hw * k)), math.degrees(math.atan(t[3] / 1200 * hh * k))


def build(sources, spacing, diagonal, side, d_home, d_target, extra, off, curve, roll_home, roll_target):
    """Home straight ahead, the target past `side` with `extra` degrees between them, `off` degrees off the push, the third out of the way."""
    half, tiles = m.half_fov(diagonal), m.overview(list(sources), 1920, 1200, spacing)
    (hw, hh), (tw, th) = half_angle(half, tiles, 0, d_home), half_angle(half, tiles, 1, d_target)
    sign = 1 if side in ('right', 'up') else -1
    across = side in ('right', 'left')
    az, el = (sign * (hw + tw + extra), off) if across else (off, sign * (hh + th + extra))
    if abs(el) > 80 or abs(az) > 178: return None
    third = (-sign * 130, 0, 100, 0) if across else (150, -sign * 30, 100, 0)
    spots = [(0, 0, d_home, curve), (az, el, d_target, curve), third]
    return m.scene(f'{d_home}/{d_target} {side} +{extra} off {off} curve {curve} roll {roll_home}', spots, sources=sources, spacing=spacing,
                   rolls=[roll_home, roll_target, 0], diagonal=diagonal)


def cases():
    grid = itertools.product(DISTANCES, DISTANCES, (2, 14, 45), (0, 12), (0, 60), ((0, 0), (1500, -2200)))
    for i, (d_home, d_target, extra, off, curve, rolls) in enumerate(grid):
        side = SIDES[i % 4]
        sources = SOURCES[i % 3]
        spacing = (40, 0, 120)[i % 3]
        layout = build(sources, spacing, FOV[i % 2], side, d_home, d_target, extra, off, curve, *rolls)
        if layout is None: continue
        along = (0.5, 0.3, 0.7)[i % 3]
        if layout.target(0, side, along) != 1: continue
        plan = layout.plan(0, 1, side, along)
        if plan is not None: yield layout, plan, side


def check(motion, layout, plan, label):
    problems = []
    if motion.commit is None: return [f'{label}: never landed']
    hs, ts = motion.span(motion.home_h), motion.span(motion.target_h)
    for who, s in (('home', hs), ('target', ts)):
        turns, back = m.reversals([math.log(v) for v in s], SIZE_FLOOR)
        if turns: problems.append(f'{label}: {who} size turned {turns} times, by {back * 100:.1f}%')
        if m.outside(s) > SIZE_FLOOR: problems.append(f'{label}: {who} size went {m.outside(s) * 100:.1f}% past its ends')
        rate = [math.log(s[i + 1] / s[i]) for i in range(len(s) - 1)]
        step = max(abs(v) for v in rate)
        kink = max(abs(rate[i + 1] - rate[i]) for i in range(len(rate) - 1))
        if step > SIZE_STEP: problems.append(f'{label}: {who} size jumped {step * 100:.1f}% in a frame')
        if kink > SIZE_KINK: problems.append(f'{label}: {who} size changed speed by {kink * 100:.1f}% of itself in a frame')
    left = motion.span(motion.left())
    back = motion.backtrack()
    if back > TURN_BACK: problems.append(f'{label}: the view went back {math.degrees(back):.3f} degrees')
    if plan.way <= math.pi:  # (past half a turn the angle still to go first rises, straight on)
        closer = max(left[i + 1] - left[i] for i in range(len(left) - 1))
        if closer > TURN_BACK: problems.append(f'{label}: the view went {math.degrees(closer):.3f} degrees further from the target')
    if left[-1] > math.radians(0.01): problems.append(f'{label}: the view stopped {math.degrees(left[-1]):.3f} degrees short')
    if motion.dip() > DIP: problems.append(f'{label}: the view stopped and started again, losing {motion.dip() * 100:.0f}% of its speed')
    peak = max(motion.speed[motion.first:motion.last + 1])
    if peak > math.radians(4) and math.radians(motion.jolt()) > JOLT * peak: problems.append(f'{label}: the view speed jumped {motion.jolt():.0f} deg/s, peak {math.degrees(peak):.0f}')
    for who, roll in (('home', motion.home_roll), ('target', motion.target_roll)):
        turns, by = m.reversals(motion.span(roll), ROLL_FLOOR)
        if turns: problems.append(f'{label}: {who} roll turned by {math.degrees(by):.2f} degrees')
    if motion.t[motion.last] - motion.t[motion.commit] > LANDED_S: problems.append(f'{label}: the landing took {motion.t[motion.last] - motion.t[motion.commit]:.2f} s')
    if apart(layout, plan.home, plan.target):  # bumping keeps desktops apart as placed; with no gap to do it in, no order could hide it
        for share, frame in motion.order_pops():
            if share > 0.002: problems.append(f'{label}: the two changed places on screen while {share * 100:.1f}% overlapped')
    end = motion.frames[motion.last]['desktops'][motion.target]
    if abs(end['at'][0]) > 1e-3 or abs(end['at'][1]) > 1e-3: problems.append(f'{label}: the target ended off centre')
    # The hand's part is 1:1: the view has turned rate x travel by the end of it, less the ease's lag.
    frame = motion.commit
    turned = motion.turned(frame)
    want = min(plan.total_px * plan.rate, plan.way / plan.spin)
    lag = motion.speed[frame] * edge_pan_ease + math.radians(0.15)
    if abs(turned - want) > lag: problems.append(f'{label}: the hand turned the view {math.degrees(turned):.2f} degrees, not {math.degrees(want):.2f}')
    # The border goes over with no flash: each frame's strength is near the last, and the target's is no brighter than the pan's own.
    frames = motion.frames[motion.first:motion.last + 1]
    for name in ('alpha', 'left_alpha'):
        worst = max(abs(frames[i + 1][name] - frames[i][name]) for i in range(len(frames) - 1) if frames[i]['landing'] == frames[i + 1]['landing'])
        if worst > 0.12: problems.append(f'{label}: the border jumped {worst:.2f} in a frame')
    hand_end = max((f['alpha'] for f in frames if not f['landing']), default=0.0)
    landed = max((f['alpha'] for f in frames if f['landing']), default=0.0)
    if landed > hand_end + 0.01: problems.append(f'{label}: the target\'s border flashed to {landed:.2f} from {hand_end:.2f}')
    return problems


def apart(layout, a, b):
    g = layout.g
    return edge_pan._apart(orbit.footprint(g, g['tiles'][a], {**layout.placements[a], 'roll': layout.rolls[a]}),
                           orbit.footprint(g, g['tiles'][b], {**layout.placements[b], 'roll': layout.rolls[b]}))


def pulled_back(layout, plan, label):
    home = plan.home
    frames = m.trace(m.script(layout, plan, back_at=0.6), trace_binary)
    motion = m.Motion(frames, home, plan.target)
    problems = []
    start, end = frames[0]['desktops'][home], frames[-1]['desktops'][home]
    if abs(start['seen'][1] - end['seen'][1]) > 0.5 or frames[-1]['switching']: problems.append(f'{label}: pulled back, home did not come back')
    if m._angle(frames[0]['aim'], frames[-1]['aim']) > math.radians(0.01): problems.append(f'{label}: pulled back, the view did not come back')
    if frames[-1]['focus'] != home: problems.append(f'{label}: pulled back, it switched')
    h = [f['desktops'][home]['seen'][1] for f in frames]
    turns, by = m.reversals([math.log(v) for v in h], SIZE_FLOOR)
    if turns > 1: problems.append(f'{label}: pulled back, home size turned {turns} times, by {by * 100:.1f}%')
    if m.reversals([m._angle(f['aim'], frames[0]['aim']) for f in frames], math.radians(0.05))[0] > 1: problems.append(f'{label}: pulled back, the view wandered')
    return problems


def interrupted(layout, plan, label):
    """Landings cut short: a key is a clean cut, and un-maximizing carries on from what is drawn, with neither desktop jumping."""
    problems = []
    lines = m.script(layout, plan, tail=0.05)
    done = float(next(line for line in lines if line.startswith('target')).split()[-1])
    home, target = plan.home, plan.target
    base = [line for line in lines if not line.startswith('frame') or float(line.split()[1]) <= done + 1]
    ms = [done + 80 + i * 1000 / 120 for i in range(90)]
    third = next(i for i in range(len(layout.sources)) if i not in (home, target))
    cut = base + [f'frame {done + 80:.3f} 0 0 0', f'target 1 {third} 0 1 0 {done + 80:.3f}', f'frame {done + 82:.3f} 0 0 0']
    frames = m.trace(cut, trace_binary)
    if frames[-1]['switching'] or frames[-1]['focus'] != third or frames[-1]['zoom'] != 1 or frames[-1]['left_zoom'] != 0: problems.append(f'{label}: a key during a landing was no cut')
    away = base + [f'frame {done + 80:.3f} 0 0 0', f'target 0 {target} 0 0 0 {done + 80:.3f}'] + [f'frame {t:.3f} 0 0 0' for t in ms]
    frames = m.trace(away, trace_binary)
    for who in (home, target):
        h = [f['desktops'][who]['seen'][1] for f in frames[-len(ms) - 1:]]
        worst = max(abs(h[i + 1] / h[i] - 1) for i in range(len(h) - 1))
        if worst > SIZE_STEP: problems.append(f'{label}: un-maximized during a landing, desktop {who} jumped {worst * 100:.1f}% in a frame')
    if frames[-1]['switching'] or frames[-1]['zoom'] != 0 or frames[-1]['left_zoom'] != 0: problems.append(f'{label}: un-maximized during a landing, it did not reach the overview')
    return problems


def main():
    count, failures, worst = 0, [], {}
    for layout, plan, side in cases():
        count += 1
        label = layout.name
        frames = m.trace(m.script(layout, plan), trace_binary)
        motion = m.Motion(frames, plan.home, plan.target)
        failures += check(motion, layout, plan, label)
        if count % 7 == 0: failures += pulled_back(layout, plan, label)
        if count % 11 == 0: failures += interrupted(layout, plan, label)
    for name, (layout, home, side, along) in m.named_layouts().items():
        target = layout.target(home, side, along)
        assert target is not None, name
        plan = layout.plan(home, target, side, along)
        assert plan is not None, name
        for mode in (m.FOLLOW, m.LOCKED, m.UNTRACKED):
            frames = m.trace(m.script(layout, plan, mode=mode), trace_binary)
            motion = m.Motion(frames, home, target)
            count += 1
            if mode == m.UNTRACKED:  # nothing turns the view: only the sizes and the roll are the pan's
                hs, ts = motion.span(motion.home_h), motion.span(motion.target_h)
                if m.reversals([math.log(v) for v in hs], SIZE_FLOOR)[0] or m.reversals([math.log(v) for v in ts], SIZE_FLOOR)[0]: failures.append(f'{name} untracked: a size turned')
            else: failures += check(motion, layout, plan, f'{name} mode {mode}')
        # No zoom out to the overview in between: the two are never both back at their placed size at once, and the zoom never
        # drops to the overview's 0 with nothing coming up.
        motion = m.Motion(m.trace(m.script(layout, plan), trace_binary), home, target)
        rest_home, rest_target = motion.home_h[motion.last], motion.target_h[0]
        sag = motion.overview_sag(rest_home, rest_target)
        if sag > 0: failures.append(f'{name}: both at their placed size for {sag:.2f} s on the way')
    print(f'{count} pans, {len(failures)} problems')
    for f in failures[:40]: print(' ', f)
    if len(failures) > 40: print(f'  ... and {len(failures) - 40} more')
    assert count > 200, count
    assert not failures


main()
