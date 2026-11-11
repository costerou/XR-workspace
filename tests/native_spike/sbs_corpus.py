"""The glasses' 3D mode corpus for the Rust rebuild (rust/crates/xr-workspace/VIEW_CONTROLS.md, `immersive`, level 1).

One golden file, rust/corpus/immersive/sbs.json, made only by this module from the shipped Python sbs.py:

* modes: `offered`, `best_wide`, `is_wide`, `narrow_rate` and `pro2_2d_rate` of each monitor, per glasses model;
* scenarios: an `sbs.ButtonSbs` on a recording stand-in client (what `query('monitors')` answers, in turn, the last for
  ever; None the output away; 'error' an OSError) and a stand-in clock (its sleep advances it), driven through a list
  of steps (`enter`, `leave`, `settle`, `left_by_hand`, `follow_by_hand`, `switching`, `parked`, `advance`). It keeps
  each step's result (or its error's text), every client call in order, the toasts, and the clock at the end.

The Rust replay (tests/immersive_corpus.rs) gives `view_controls::immersive::sbs::ButtonSbs` the same answers through
its `Display` and must make the same calls, give the same results and toast the same text. The client's own requests
(the mode rule and its readback, the park) are `hyprland.rs`'s, held to the Python by the hyprland and Lua corpora.

Regenerate (after a deliberate change to sbs.py) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/sbs_corpus.py --write

test_sbs_corpus.py fails when the file and the Python disagree.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import sbs  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'immersive'
FILE = CORPUS / 'sbs.json'
TWO_D = ['1920x1080@60.00Hz', '1920x1200@120.00Hz', '1920x1200@90.00Hz', '1920x1200@60.00Hz', '1920x1080@90.00Hz']
WIDE = ['3840x1200@90.00Hz', '3840x1200@60.00Hz', '3840x1080@60.00Hz']
PRO2_2D = ['1920x1080@120.00Hz', '1920x1080@90.00Hz', '1920x1080@60.00Hz']
PRO2_WIDE = ['3840x1080@60.00Hz', '1920x1080@60.00Hz']


def monitor(width=1920, height=1200, hz=120.0, modes=TWO_D, name='DP-2'):
    return {'name': name, 'id': 1 if name == 'DP-2' else 2, 'width': width, 'height': height, 'x': -3000, 'y': 9000,
            'scale': 1.25, 'refreshRate': hz, 'transform': 0, 'mirrorOf': 'none', 'disabled': False, 'dpmsStatus': True,
            'availableModes': modes}


WIDE_DRIVEN = monitor(width=3840, hz=90.0, modes=WIDE)
PRO2 = monitor(height=1080, hz=120.0, modes=PRO2_2D)
PRO2_WIDE_OFFERED = monitor(height=1080, hz=120.0, modes=PRO2_WIDE)
PRO2_WIDE_DRIVEN = monitor(width=3840, height=1080, hz=60.0, modes=PRO2_WIDE)


class Client:
    """The compositor as ButtonSbs uses it, recording every call. `script`: what query answers, in turn, the last for
    ever (None: the output is away; 'error': OSError). `fail`: method name -> the ValueError text it raises."""
    def __init__(self, script, fail):
        self.script, self.fail, self.calls = list(script), dict(fail), []

    def query(self, kind, timeout=None):
        self.calls.append(['query'])
        now = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if now == 'error': raise OSError('gone')
        return [m for m in [now, monitor(name='DP-1')] if m is not None]

    def _call(self, name, *args):
        self.calls.append([name, *args])
        if name in self.fail: raise ValueError(self.fail[name])

    def apply_wide_mode(self, output, rate, model='luma-ultra'): self._call('apply_wide_mode', output, rate, model)
    def apply_pro2_mode(self, output, rate): self._call('apply_pro2_mode', output, rate)
    def apply_session_mode(self, output): self._call('apply_session_mode', output)
    def park_presentation(self, output): self._call('park_presentation', output)


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


def modes():
    shown = [None, monitor(), monitor(modes=WIDE), WIDE_DRIVEN, monitor(modes=['3840x1200@60.00Hz']),
             monitor(modes=['3840x1200@120.00Hz', '3840x1200@30.00Hz']), monitor(modes=['3840x1080@60.00Hz']),
             monitor(modes=['garbage', 3, None, '3840x1200@90.00Hz', '1920x1200@120.00Hz extra', '3840x1200@90Hz']),
             monitor(hz=119.6), monitor(hz=90.0), monitor(width=1920, height=1080, hz=60.0), PRO2, PRO2_WIDE_OFFERED,
             PRO2_WIDE_DRIVEN, monitor(height=1080, hz=90.0, modes=['1920x1080@90.00Hz', '1920x1080@60.00Hz']),
             monitor(height=1080, hz=144.0, modes=['1920x1080@60.00Hz']), {**monitor(), 'availableModes': None}]
    out = []
    for m in shown:
        row = {'monitor': m, 'offered': [list(x) for x in sbs.offered(m)]}
        for model in ('luma-ultra', 'pro2', 'other'):
            row[model] = {'best_wide': sbs.best_wide(m, model), 'is_wide': sbs.is_wide(m, model),
                          'narrow_rate': sbs.narrow_rate(m, model)}
        row['pro2_2d_rate'] = {str(p): sbs.pro2_2d_rate(m, p) for p in (None, 60.0, 90.0, 144.0)}
        out.append(row)
    return out


def scenarios():
    """(name, model, enter_wait, script, fail, steps)."""
    e = lambda cancel_after=None: ['enter', cancel_after]
    luma = 'luma-ultra'
    return [
        # EnterTests
        ('enter.no_press', luma, 2, [monitor()], {}, [e(), ['switching']]),
        ('enter.follows_the_press', luma, 45, [monitor(), monitor(), monitor(modes=WIDE)], {}, [e(), ['switching']]),
        ('enter.output_away_a_while', luma, 45, [monitor(), None, None, None, monitor(width=3840, hz=60.0, modes=WIDE)], {}, [e()]),
        ('enter.broken_replies', luma, 1, ['error'], {}, [e()]),
        ('enter.already_offered', luma, 45, [monitor(modes=WIDE)], {}, [e()]),
        ('enter.already_driven', luma, 45, [WIDE_DRIVEN], {}, [e()]),
        ('enter.driven_wide_at_60', luma, 45, [monitor(width=3840, hz=60.0, modes=WIDE)], {}, [e()]),
        ('enter.cancelled', luma, 45, [monitor()], {}, [e(3)]),
        ('enter.only_the_wrong_mode', luma, 2, [monitor(), monitor(modes=['3840x1080@60.00Hz'])], {}, [e()]),
        ('enter.wrong_then_away', luma, 2, [monitor(), monitor(modes=['3840x1080@60.00Hz']), None], {}, [e()]),
        ('enter.mode_will_not_take', luma, 45, [monitor(modes=WIDE)], {'apply_wide_mode': 'not observed'}, [e()]),
        ('enter.mode_error_without_text', luma, 45, [monitor(modes=WIDE)], {'apply_wide_mode': ''}, [e()]),
        ('enter.park_refused', luma, 45, [monitor(modes=WIDE)], {'park_presentation': 'no place'}, [e()]),
        ('enter.expect_window', luma, 45, [monitor(), monitor(modes=WIDE)], {},
         [['switching'], e(), ['switching'], ['advance', sbs.EXPECT_S + 1], ['switching']]),
        ('enter.narrow_rate_kept', luma, 45, [monitor(hz=120.0), monitor(modes=WIDE)], {}, [e(), ['leave'], ['settle']]),
        # LeaveTests and ParkTests
        ('leave.asks_once', luma, 45, [WIDE_DRIVEN], {}, [['leave'], ['leave'], ['parked'], ['switching']]),
        ('leave.nothing_to_leave', luma, 45, [monitor()], {}, [['leave'], ['parked']]),
        ('leave.offered_not_driven', luma, 45, [monitor(modes=WIDE)], {}, [['leave'], ['parked']]),
        ('leave.output_away', luma, 45, [None], {}, [['leave'], ['parked']]),
        ('leave.120_comes_back', luma, 45, [WIDE_DRIVEN, WIDE_DRIVEN, monitor()], {},
         [['leave'], ['settle'], ['settle'], ['parked'], ['switching'], ['advance', sbs.ENTER_WAIT_S], ['switching']]),
        ('leave.park_order', luma, 45, [WIDE_DRIVEN, monitor()], {}, [['leave'], ['settle']]),
        ('leave.session_mode_refused', luma, 45, [WIDE_DRIVEN, monitor()], {'apply_session_mode': 'no'}, [['leave'], ['settle'], ['parked']]),
        ('leave.output_away_on_settle', luma, 45, [WIDE_DRIVEN, None], {}, [['leave'], ['settle'], ['parked']]),
        ('leave.enter_again_before_the_press', luma, 45, [WIDE_DRIVEN], {}, [['leave'], e(), ['parked']]),
        ('leave.parked_window_ends', luma, 45, [WIDE_DRIVEN], {}, [['leave'], ['advance', sbs.ENTER_WAIT_S + 1], ['switching'], ['parked']]),
        ('left_by_hand.2d', luma, 45, [monitor()], {}, [['left_by_hand'], ['parked'], ['switching']]),
        ('left_by_hand.wide', luma, 45, [WIDE_DRIVEN], {}, [['left_by_hand']]),
        ('left_by_hand.away', luma, 45, [None], {}, [['left_by_hand']]),
        ('left_by_hand.2d_at_90', luma, 45, [monitor(hz=90.0)], {}, [['left_by_hand'], ['parked']]),
        # follow_by_hand: the button held while 3D is off
        ('follow.hand_wide', luma, 45, [monitor(), WIDE_DRIVEN, WIDE_DRIVEN, monitor()], {},
         [['follow_by_hand'], ['switching'], ['follow_by_hand'], ['switching'], ['follow_by_hand'],
          ['advance', sbs.EXPECT_S + 1], ['follow_by_hand'], ['switching']]),
        ('follow.away', luma, 45, [None], {}, [['follow_by_hand'], ['switching']]),
        ('follow.neither', luma, 45, [monitor(hz=90.0)], {}, [['follow_by_hand'], ['switching']]),
        ('follow.while_switching', luma, 45, [WIDE_DRIVEN, WIDE_DRIVEN], {}, [['leave'], ['follow_by_hand']]),
        ('follow.then_enter', luma, 45, [WIDE_DRIVEN], {}, [['follow_by_hand'], e()]),
        # Pro 2: L1, 3840x1080, its 2D rate put back
        ('pro2.enter', 'pro2', 2, [PRO2, PRO2_WIDE_OFFERED], {}, [e()]),
        ('pro2.no_press', 'pro2', 1, [PRO2], {}, [e()]),
        ('pro2.leave_and_settle', 'pro2', 45, [PRO2, PRO2_WIDE_OFFERED, PRO2_WIDE_DRIVEN, PRO2_WIDE_DRIVEN,
                                               monitor(width=3840, height=1080, hz=60.0, modes=PRO2_2D)], {},
         [e(), ['leave'], ['settle'], ['settle'], ['parked']]),
        ('pro2.settle_already_driven', 'pro2', 45, [PRO2_WIDE_DRIVEN, monitor(height=1080, hz=90.0, modes=PRO2_2D)], {},
         [['leave'], ['settle'], ['parked']]),
        ('pro2.settle_rule_refused', 'pro2', 45, [PRO2_WIDE_DRIVEN, monitor(width=3840, height=1080, hz=60.0, modes=PRO2_2D)],
         {'apply_pro2_mode': 'not observed'}, [['leave'], ['settle'], ['parked']]),
        ('pro2.settle_park_refused', 'pro2', 45, [PRO2_WIDE_DRIVEN, monitor(height=1080, hz=120.0, modes=PRO2_2D)],
         {'park_presentation': 'no place'}, [['leave'], ['settle'], ['parked']]),
        ('pro2.left_by_hand', 'pro2', 45, [PRO2], {}, [['left_by_hand'], ['parked']]),
        ('pro2.left_by_hand_still_wide', 'pro2', 45, [PRO2_WIDE_OFFERED], {}, [['left_by_hand']]),
        ('pro2.follow', 'pro2', 45, [PRO2, PRO2_WIDE_DRIVEN], {}, [['follow_by_hand'], ['follow_by_hand'], ['switching']]),
    ]


def run(model, enter_wait, script, fail, steps):
    client, clock, told = Client(script, fail), Clock(), []
    button = sbs.ButtonSbs(client, 'DP-2', told.append, model=model, clock=clock, sleep=clock.sleep, enter_wait=enter_wait)
    results = []
    for step in steps:
        try:
            if step[0] == 'enter':
                calls = []
                limit = step[1]
                cancelled = (lambda: False) if limit is None else (lambda: calls.append(1) or len(calls) > limit)
                results.append(button.enter(cancelled))
            elif step[0] == 'advance':
                clock.t += step[1]
                results.append(None)
            else:
                results.append(getattr(button, step[0])())
        except Exception as exc:  # noqa: BLE001 - the text is the record
            results.append({'error': str(exc) or type(exc).__name__})
    return {'results': results, 'calls': client.calls, 'told': told, 'clock': clock.t}


def build():
    out = {'modes': modes(), 'scenarios': []}
    for name, model, wait, script, fail, steps in scenarios():
        out['scenarios'].append({'name': name, 'model': model, 'enter_wait': wait, 'script': script, 'fail': fail,
                                 'steps': steps, **run(model, wait, script, fail, steps)})
    return out


def render(data):
    return json.dumps(data, indent=1, sort_keys=True) + '\n'


if __name__ == '__main__':
    if sys.argv[1:] == ['--write']:
        CORPUS.mkdir(parents=True, exist_ok=True)
        FILE.write_text(render(build()))
    else:
        sys.stdout.write(render(build()))
