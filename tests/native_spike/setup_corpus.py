"""Corpus for the Rust rebuild: a saved monitor setup the broker refused goes back to the default.

One golden file, rust/corpus/setup/discard.json, made only by this module from the shipped Python code: for a
profiles.json as it is on disk (or none), the run's desktops and the create failure, what managed._setup_failed (with
profiles.discard_unusable and PlacementStore.reset behind it) does: whether it is asked at all (None for the default
three desktops), what it logs, and the profiles.json it leaves, byte for byte. The cases are the CreateFailureTests of
test_remember_setup.py plus the shapes a reset must clean or keep: other models' profiles, legacy placements, a
previous_setup, invalid view settings, a Pro 2 profile, corrupt and foreign files.
rust/crates/xr-workspace/tests/setup_corpus.rs replays every case against view_controls::setup.

Regenerate (after a deliberate change to one of those modules) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/setup_corpus.py --write

test_setup_corpus.py fails when the file and the Python code disagree.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import geometry as g, managed, profiles  # noqa: E402
from tools.native_spike.hyprland import OwnershipBlocked  # noqa: E402

SETUP_FILE = ROOT / 'rust' / 'corpus' / 'setup' / 'discard.json'

MODEL = 'luma-ultra'
D169 = dict(g.DEFAULT_DESKTOP)
WIDE = {'width': 2560, 'height': 1080, 'scale': 1.0}
SHARP = {'width': 2560, 'height': 1440, 'scale': 1.25}
BIG = {'width': 3840, 'height': 2160, 'scale': 2.0}
FOUR = [WIDE, SHARP, D169, BIG]
PLACED = [{'x': -450, 'y': 30, 'distance': 80, 'curvature': 40}, {'x': 0, 'y': -120, 'distance': 140, 'curvature': 0},
          {'x': 1800, 'y': 0, 'distance': 100, 'curvature': 120}, {'x': 600, 'y': 900, 'distance': 10, 'curvature': 7}]
ROLLS = [250, -1200, 0, 9000]
LEGACY = [{**p, 'yaw': 5, 'pitch': -3, 'roll': 1} for p in PLACED]


def saved_four(path, model=MODEL, **profile):
    """test_remember_setup's CreateFailureTests set-up: FOUR saved through the store, on a profile with `profile`."""
    profiles.save_profile(path, model, {**profiles.DEFAULT_PROFILE, 'tracking_driver': 'luma' if model == MODEL else 'xrdriver',
                                        **profile})
    profiles.PlacementStore(path, model).save_desktops(FOUR, PLACED, ROLLS)


def raw(path, data):
    path.write_text(data if isinstance(data, str) else json.dumps(data))


ENTRY = {'desktops': FOUR, 'placements': LEGACY, 'spacing': 999, 'mode': 'follow', 'prediction': 'yes', 'stabilization': 60,
         'video_quality': 'junk', 'environment': {'background': 'nowhere'}, 'immersive': 7, 'tracking': False,
         'mode_switch': 1, 'tracking_driver': '3dof', 'fallback': 'HDMI-A-1', 'cursor_switch_animate': True, 'extra': [1, 2],
         'previous_setup': {'desktops': [WIDE], 'placements': [PLACED[0]], 'rolls': [12], 'spacing': 120}}
OTHERS = {'beast': {'odd': 1e5, 'name': 'café \U0001F600', 'n': [1, 2.5, None, True]}, 'zzz': {'dropped': True}}

# (name, prepare(path), model, desktops, the broker's refusal)
CASES = [
    ('refused outputs reset the saved setup', saved_four, MODEL, FOUR, 'creation incomplete'),
    ('missing readback resets it', saved_four, MODEL, FOUR, 'missing readback'),
    ('a geometry mismatch resets it', saved_four, MODEL, FOUR, 'actual geometry/workspace mismatch'),
    ('an unusable output resets it', saved_four, MODEL, FOUR, 'unusable output'),
    ('a lost epoch keeps it', saved_four, MODEL, FOUR, 'broker epoch lost; preserve journal'),
    ('a changed session keeps it', saved_four, MODEL, FOUR, 'compositor session changed'),
    ('other desktops by now keep it', saved_four, MODEL, [D169, WIDE], 'creation incomplete'),
    ('the default three ask nothing', saved_four, MODEL, [D169] * 3, 'creation incomplete'),
    ('nothing saved resets nothing', lambda p: None, MODEL, FOUR, 'creation incomplete'),
    ('a corrupt file resets nothing', lambda p: raw(p, '{"schema": 2, '), MODEL, FOUR, 'creation incomplete'),
    ('another schema resets nothing', lambda p: raw(p, {'schema': 1, 'profiles': {MODEL: {'desktops': FOUR}}}), MODEL, FOUR,
     'creation incomplete'),
    ('a float schema is the schema', lambda p: raw(p, {'schema': 2.0, 'profiles': {MODEL: {'desktops': FOUR}}}), MODEL, FOUR,
     'creation incomplete'),
    ('an invalid saved setup resets nothing', lambda p: raw(p, {'schema': 2, 'profiles': {MODEL: {'desktops': FOUR, 'rolls': [1]}}}),
     MODEL, FOUR, 'creation incomplete'),
    ('a hand-written profile is cleaned, other models kept',
     lambda p: raw(p, {'schema': 2, 'profiles': {**OTHERS, MODEL: ENTRY}}), MODEL, FOUR, 'creation incomplete'),
    ('an integer scale matches', lambda p: raw(p, {'schema': 2, 'profiles': {MODEL: {'desktops': [{**WIDE, 'scale': 1}, BIG]}}}),
     MODEL, [WIDE, BIG], 'creation incomplete'),
    ('a repeated key keeps its first place',
     lambda p: raw(p, '{"schema": 2, "profiles": {"beast": {"a": 1}, "luma-ultra": {"desktops": [{"width": 2560, "height": 1080, '
                      '"scale": 1.0}]}, "beast": {"a": 2}}}'), MODEL, [WIDE], 'creation incomplete'),
    ('an invalid previous setup is dropped',
     lambda p: raw(p, {'schema': 2, 'profiles': {MODEL: {'desktops': FOUR, 'previous_setup': {'desktops': [WIDE], 'spacing': -1},
                                                        'fallback': 7, 'rolls': None, 'placements': None}}}),
     MODEL, FOUR, 'creation incomplete'),
    ('a pro 2 profile', lambda p: saved_four(p, 'pro2', tracking=True, mode_switch=True), 'pro2', FOUR, 'creation incomplete'),
    ('a beast profile with our driver', lambda p: raw(p, {'schema': 2, 'profiles': {'beast': {**ENTRY, 'tracking_driver': 'luma'}}}),
     'beast', FOUR, 'unusable output'),
    ('a folder it cannot write logs the reset failing', saved_four, MODEL, FOUR, 'creation incomplete'),
]
READ_ONLY = {'a folder it cannot write logs the reset failing'}  # the folder is mode 0500 when the reset runs


def run(name, prepare, model, desktops, error):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'profiles.json'
        prepare(path)
        before = path.read_text() if path.exists() else None
        failed = managed._setup_failed(profiles.ProfileStore(path, model), desktops)
        log = io.StringIO()
        if name in READ_ONLY: Path(tmp).chmod(0o500)
        try:
            if failed is not None:
                with contextlib.redirect_stderr(log): failed(OwnershipBlocked(error))
        finally: Path(tmp).chmod(0o700)
        # The folder and save_profile's mkstemp name differ by run: DIR and TMP.
        text = re.sub(r'\.profiles\.[^./\']+\.tmp', '.profiles.TMP.tmp', log.getvalue().replace(tmp, 'DIR'))
        return {'name': name, 'model': model, 'desktops': desktops, 'error': error, 'before': before,
                'read_only': name in READ_ONLY, 'asked': failed is not None, 'log': text,
                'after': path.read_text() if path.exists() else None}


def build():
    return {'cases': [run(*case) for case in CASES]}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=True) + '\n'


def main(argv):
    text = dumps(build())
    if '--write' in argv:
        SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETUP_FILE.write_text(text, encoding='utf-8')
        return 0
    if not SETUP_FILE.exists() or SETUP_FILE.read_text(encoding='utf-8') != text:
        print(f'stale: {SETUP_FILE.relative_to(ROOT)}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
