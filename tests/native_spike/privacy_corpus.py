"""Corpus for the Rust privacy mode (rust/crates/xr-workspace/VIEW_CONTROLS.md, `privacy`, parity level 1).

rust/corpus/supervisor/privacy.json: the bytes `privacy.PrivacyDisplay` (and recovery's `privacy.wake`) sends through
the shipped HyprlandClient to a stand-in compositor on a temporary socket (never the user's Hyprland), the replies it
got, the presses' completions and the notifications, for each scenario below. The stand-in keeps each monitor's DPMS
state and applies the generated blank / wake Lua to it, so the read-backs see the change. tests/privacy_transcripts.rs
replays every scenario against the Rust PrivacyDisplay on hyprland::Client: the n-th connection gets the n-th reply.

Regenerate (after a deliberate change) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/privacy_corpus.py --write

Without --write it exits 1 when the file is stale (test_privacy_corpus.py).
"""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.native_spike import hyprland, privacy  # noqa: E402
import lua_corpus  # noqa: E402

FILE = ROOT / 'rust' / 'corpus' / 'supervisor' / 'privacy.json'
GLASSES, FALLBACK = 'DP-2', 'DP-1'
SLOW = 3600.0   # the watcher never ticks in a scenario that does not wait for it
WATCH = 0.5     # the watcher scenario's period: its one tick lands well after the press, close well before the next


def _monitor(i, name, description=None, dpms=True):
    return lua_corpus._monitor(i, name, description=description or f'Synthetic {name}', dpmsStatus=dpms)


DESK = [_monitor(0, 'DP-1'), _monitor(1, 'DP-2', 'VITURE Beast'), _monitor(2, 'XR-1')]


class Compositor(lua_corpus.Compositor):
    """lua_corpus's stand-in with the given monitors, applying privacy's generated DPMS Lua (unless `stuck`)."""
    def __init__(self, root, monitors, stuck=()):
        super().__init__(root)
        self.monitors, self.stuck = [dict(m) for m in monitors], set(stuck)

    def set(self, name, on):
        for m in self.monitors:
            if m['name'] == name and name not in self.stuck: m['dpmsStatus'] = on

    def _reply(self, request):
        text = request.decode('utf-8')
        names = {m['name'] for m in self.monitors}
        if blank := re.match(r'/eval local t,g="([^"]+)","([^"]+)" ', text):
            if blank[1] in names and blank[2] in names: self.set(blank[1], False); self.set(blank[2], True)
            return b'ok'
        if wake := re.match(r'/eval local t="([^"]+)" ', text):
            self.set(wake[1], True)
            return b'ok'
        return super()._reply(request)


def _wait(predicate, seconds=5.0):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > end: raise RuntimeError('the scenario did not reach its step')
        time.sleep(0.005)


def scenarios():
    """(name, monitors, stuck, fallback, watch, steps). A step is press | close | wake | wake:<name> | external_on:<name>
    | watch_reblank (waits until the watcher has blanked the display again)."""
    off = [_monitor(0, 'DP-1', dpms=False), *DESK[1:]]
    two = [_monitor(0, 'DP-1'), _monitor(1, 'DP-2', 'VITURE Beast'), _monitor(2, 'HDMI-A-1')]
    return [
        ('toggle', DESK, (), FALLBACK, SLOW, ['press', 'press', 'close']),
        ('first_press_blanks_a_sleeping_display', off, (), FALLBACK, SLOW, ['press', 'close']),
        ('no_fallback_the_only_candidate', DESK, (), None, SLOW, ['press', 'press']),
        ('two_candidates_none_the_fallback', two, (), 'DP-3', SLOW, ['press']),
        ('the_display_will_not_turn_off', DESK, ('DP-1',), FALLBACK, SLOW, ['press']),
        ('close_wakes_and_refuses_later_presses', DESK, (), FALLBACK, SLOW, ['press', 'close', 'press']),
        ('close_without_a_blank_touches_nothing', DESK, (), FALLBACK, SLOW, ['close', 'press']),
        ('the_watcher_blanks_a_display_something_else_woke', DESK, (), FALLBACK, WATCH,
         ['press', 'external_on:DP-1', 'watch_reblank', 'close']),
        ('recovery_wakes_a_sleeping_display', off, (), FALLBACK, SLOW, ['wake']),
        ('recovery_leaves_a_lit_display', DESK, (), FALLBACK, SLOW, ['wake']),
        ('recovery_refuses_a_virtual_output', DESK, (), FALLBACK, SLOW, ['wake:XR-1']),
    ]


def record(monitors, stuck, fallback, watch, steps):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        (root / 'hypr').mkdir(mode=0o700)
        compositor = Compositor(root, monitors, stuck)
        notified, out, expected = [], [], 0
        try:
            client = hyprland.HyprlandClient(lua_corpus.INSTANCE, root)
            display = privacy.PrivacyDisplay(client, GLASSES, fallback, notify=notified.append, watch=watch)
            for step in steps:
                if step == 'press':
                    done = threading.Event(); got = []
                    display.request(lambda ok, detail: (got.append([ok, detail]), done.set()))
                    if not done.wait(5): raise RuntimeError('the press did not complete')
                    told = got[0][1] != 'Privacy owner closed'  # every other completion is also a notification
                    expected += told
                    _wait(lambda: len(notified) >= expected)
                    out.append({'step': step, 'done': got[0]})
                elif step == 'close':
                    display.close(); out.append({'step': step})
                elif step.startswith('wake'):
                    name = step.partition(':')[2] or FALLBACK
                    out.append({'step': step, 'returned': privacy.wake(client, name)})
                elif step.startswith('external_on:'):
                    compositor.set(step.partition(':')[2], True); out.append({'step': step})
                elif step == 'watch_reblank':
                    seen = len(compositor.exchanges)
                    _wait(lambda: any(e['request'].startswith('/eval local t,g=') for e in compositor.exchanges[seen:]))
                    out.append({'step': step, 'requests': len(compositor.exchanges)})
                else: raise ValueError(step)
        finally: compositor.close()
        return {'exchanges': compositor.exchanges, 'steps': out, 'notifications': notified}


def corpus():
    out = {'schema': 1, 'source': 'tests/native_spike/privacy_corpus.py', 'instance': lua_corpus.INSTANCE,
           'glasses': GLASSES,
           'note': 'One request per connection. Requests and replies are the exact bytes, as UTF-8 text.',
           'scenarios': []}
    for name, monitors, stuck, fallback, watch, steps in scenarios():
        out['scenarios'].append({'name': name, 'fallback': fallback, 'watch': watch, 'stuck': list(stuck),
                                 'monitors': monitors, **record(monitors, stuck, fallback, watch, steps)})
    return out


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: privacy_corpus.py [--write]')
    text = lua_corpus.dumps(corpus())
    if argv == ['--write']:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(text)
        print('wrote', FILE.relative_to(ROOT))
        return 0
    if not FILE.exists() or FILE.read_text() != text:
        print('stale:', FILE.relative_to(ROOT)); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
