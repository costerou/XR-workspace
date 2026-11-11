"""The view's 3D corpus for the Rust rebuild (rust/crates/xr-workspace/VIEW_CONTROLS.md, `immersive`, level 1).

One golden file, rust/corpus/immersive/view.json, made only by this module from the shipped Python view.ViewControl:
its 3D methods (`immersive_toggle`, `set_immersive`, `immersive_click`, `immersive_tail_click`, `immersive_pointer`,
`_immersive_sync`, `_immersive_tick`, `_immersive_status`, `renderer_restarted`) on a stand-in `ImmersiveRun` that
records each `request` and hands out the scenario's events. Each scenario starts from a saved setting and names, and
its steps may also change what the view knows (a VR video, typing mode, maximized, the desktops). Per step it keeps:

* frames: the renderer frames, as text, one per line;
* requests: what the worker was asked for (`[desktops, setting]`);
* toasts, in order; saved: how many times the view settings were saved;
* touched: whether the step counted as input (`last_input`);
* the result of `status` steps, and an error's text for a refused setting.

The Rust replay (tests/immersive_corpus.rs) drives `view_controls::immersive::Immersive` with a stand-in `Worker` and
the view's part as a `Shown`, and must give the same frames, requests, toasts, saves and status.

Regenerate (after a deliberate change to view.py's 3D or immersive.py) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/immersive_view_corpus.py --write

test_immersive_view_corpus.py fails when the file and the Python disagree.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import immersive, view  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'immersive'
FILE = CORPUS / 'view.json'
NAMES = ['XR-1', 'XR-2', 'XR-3']
FIRST_ID = 7


class Run:
    """ImmersiveRun as the view sees it: `request` recorded, `take_events` the scenario's next batch."""
    def __init__(self):
        self.requests, self.pending, self.phase, self.switch = [], [], 'off', False
        self.sbs = type('Sbs', (), {'button': 'R1'})()
        self.engine = type('Engine', (), {'binary': None})()

    def request(self, desktops, done=None, setting=None):
        self.requests.append([list(desktops), setting])

    def take_events(self):
        events, self.pending = self.pending, []
        return events

    def switching(self): return self.switch
    def observe_renderer(self, report): pass


class Store:
    def __init__(self): self.saved = 0
    def save(self, settings): self.saved += 1


def scenarios():
    """(name, saved setting, names, steps)."""
    one = {**immersive.DEFAULTS, 'desktops': {'XR-2': True}}
    every = {**immersive.DEFAULTS, 'global': True}
    strong = {**immersive.DEFAULTS, 'global': True, 'strength': 150, 'preset': 'video', 'convergence': 70,
              'strengths': {'XR-1': 50}}
    tick = lambda *events: ['tick', [list(e) for e in events]]
    return [
        ('nothing_on', None, NAMES, [['sync'], ['pointer', -1, False], ['status']]),
        ('saved_on_at_start', every, NAMES, [['sync'], ['sync'], ['status']]),
        ('toggle_on_and_off', None, NAMES, [['toggle'], tick(('active', NAMES)), ['status'], ['toggle'], ['status']]),
        ('toggle_with_one_on_turns_all_off', one, NAMES, [['sync'], ['toggle'], ['toggle']]),
        ('failed_engine_then_retry', None, NAMES,
         [['toggle'], tick(('failed', 'engine', 'it is not installed')), ['status'], ['tick', []], ['toggle'],
          tick(('active', NAMES))]),
        ('failed_sbs_and_died', every, NAMES,
         [['sync'], tick(('active', NAMES)), tick(('failed', 'died', 'it quit')), ['click', 1], ['tail', 0]]),
        ('leave_toast', every, NAMES, [['sync'], tick(('leave', 'nope')), tick(('off',))]),
        ('click_and_tail', None, NAMES,
         [['click', 0], ['click', 2], ['tail', 1], ['tail', 1], ['click', 0], tick(('active', ['XR-2', 'XR-3'])), ['status']]),
        ('stale_clicks', None, NAMES,
         [['click', 3], ['click', -1], ['tail', 9], ['zoomed', True], ['click', 0], ['zoomed', False], ['typing', True],
          ['click', 0], ['typing', False], ['video', True], ['tail', 0], ['video', False], ['click', 0]]),
        ('hover', None, NAMES, [['pointer', 1, False], ['pointer', 1, False], ['pointer', 1, True], ['pointer', -1, False]]),
        ('video_stands_down', every, NAMES,
         [['sync'], tick(('active', NAMES)), ['video', True], ['tick', []], ['toggle'], ['toggle'], ['video', False],
          ['tick', []], tick(('active', NAMES))]),
        ('panel_sets_it', None, NAMES,
         [['set', every], ['set', every], ['set', {'global': 0}], ['set', strong], ['set', {**strong, 'strength': 999}],
          ['set', one], ['status']]),
        ('panel_retries_after_a_failure', every, NAMES,
         [['sync'], tick(('failed', 'sbs', 'no button')), ['set', every], ['status']]),
        ('desktop_added_follows_global', every, ['XR-1'], [['sync'], ['names', ['XR-1', 'XR-2']], ['tick', []],
                                                           ['names', ['XR-1']], ['tick', []]]),
        ('one_desktop_removed', one, NAMES, [['sync'], ['names', ['XR-1', 'XR-3']], ['tick', []]]),
        ('renderer_restarted', every, NAMES, [['sync'], ['restarted'], ['sync'], ['restarted'], ['pointer', 0, True]]),
        ('look_only_while_on', strong, NAMES, [['sync'], ['toggle'], ['toggle']]),
        ('status_switching', every, NAMES, [['switch', True], ['status'], ['phase', 'glasses'], ['status']]),
    ]


def run(setting, names, steps):
    run3d, store, toasts = Run(), Store(), []
    t = [0.0]
    c = view.ViewControl(view.ViewState(names, immersive=setting), 'r1', 'sess', None, store, None,
                         first_request_id=FIRST_ID, clock=lambda: t[0])
    c.immersive_run = run3d
    c.notify = toasts.append
    out = []
    for i, step in enumerate(steps):
        t[0] = float(i + 1)
        before_req, before_saved, before_toasts = len(run3d.requests), store.saved, len(toasts)
        result, frames = None, b''
        kind = step[0]
        if kind == 'sync': frames = c._immersive_sync()
        elif kind == 'tick':
            run3d.pending = [tuple(e) for e in step[1]]
            frames = c._immersive_tick()
        elif kind == 'toggle': frames = c.immersive_toggle()
        elif kind == 'set':
            try: frames = c.set_immersive(step[1])
            except ValueError as exc: result = {'error': str(exc)}
        elif kind == 'click': frames = c.immersive_click(step[1])
        elif kind == 'tail': frames = c.immersive_tail_click(step[1])
        elif kind == 'pointer': frames = c.immersive_pointer(step[1], step[2])
        elif kind == 'status': result = c._immersive_status()
        elif kind == 'restarted': c.renderer_restarted()
        elif kind == 'video': c.video_active = step[1]
        elif kind == 'typing': c.state.typing = step[1]
        elif kind == 'zoomed': c.state.zoomed = step[1]
        elif kind == 'names': c.state.names = list(step[1])
        elif kind == 'switch': run3d.switch = step[1]
        elif kind == 'phase': run3d.phase = step[1]
        else: raise ValueError(kind)
        for thread in c._threads: thread.join(3)
        out.append({'frames': [line for line in frames.decode().split('\n') if line],
                    'requests': run3d.requests[before_req:], 'toasts': toasts[before_toasts:],
                    'saved': store.saved - before_saved, 'touched': c.last_input == t[0], 'result': result})
    return out


def build():
    return {'first_request_id': FIRST_ID, 'run_id': 'r1', 'session': 'sess',
            'scenarios': [{'name': name, 'setting': setting, 'names': names, 'steps': steps, 'results': run(setting, names, steps)}
                          for name, setting, names, steps in scenarios()]}


def render(data):
    return json.dumps(data, indent=1, sort_keys=True) + '\n'


if __name__ == '__main__':
    if sys.argv[1:] == ['--write']:
        CORPUS.mkdir(parents=True, exist_ok=True)
        FILE.write_text(render(build()))
    else:
        sys.stdout.write(render(build()))
