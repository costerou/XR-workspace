"""Phase 6 corpus for the Rust port of tools/native_spike/monitor_rules.py (docs/rust-rebuild-plan.md).

rust/corpus/supervisor/monitor_rules.json holds, for a set of monitors.lua texts: the rules find() sees, the text
remove() leaves for given desktops (or that it leaves the file alone), and what clear(), named() and refuse_start() say
about the file, with the reload failing or not. Each case runs in its own scratch folder, so no real config is read.
Backup names hold the time, so the notification's copy name is written as <backup>. Byte cases (text that is not
UTF-8) are in hex; the size limit is checked in the Rust tests, as a corpus of over 1 MiB is not worth its size.

Regenerate (after a deliberate change to monitor_rules.py) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/monitor_rules_corpus.py --write

test_monitor_rules_corpus.py fails when the file and the Python code disagree. The Rust tests in
rust/crates/xr-workspace/src/monitor_rules/tests.rs hold the Rust port to it.
"""
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import monitor_rules  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'supervisor'
MONITOR_RULES_FILE = CORPUS / 'monitor_rules.json'

OMARCHY_LINE = 'hl.monitor({ output = "XR-1", mode = "preferred", position = "auto", scale = 1.25 })'
INCIDENT = ('-- See https://wiki.hypr.land/Configuring/Basics/Monitors/\n'
            'hl.monitor({ output = "", mode = "preferred", position = "auto", scale = omarchy_monitor_scale })\n'
            + OMARCHY_LINE + '\n'
            '\n-- Ultrawide at its full 240Hz refresh rate.\n'
            'hl.monitor({ output = "DP-1", mode = "5120x1440@240", position = "0x0", scale = 1.25 })\n'
            '-- hl.monitor({ output = "DP-2", mode = "2560x1440@144", position = "0x0", scale = 1 })\n')

TEXTS = {
    'incident': INCIDENT,
    'empty': '',
    'no-rule': 'hl.monitor({ output = "DP-1", mode = "preferred" })\n',
    'no-final-newline': 'a = 1\n' + OMARCHY_LINE,
    'crlf': 'a = 1\r\n' + OMARCHY_LINE + '\r\nb = 2\r\n',
    'cr-only': 'a = 1\r' + OMARCHY_LINE + '\rb = 2',
    'unicode-breaks': 'a ' + OMARCHY_LINE + ' b\x0bc\x0c' + OMARCHY_LINE.replace('XR-1', 'XR-2') + '\x1cd\x85e\n',
    'unicode-spaces': '　hl.monitor(\t{ output\x1f= "XR-1" } )\x1d -- note\n',
    'trailing-comment': OMARCHY_LINE + '  -- added by the scale control\n',
    'commented-out': '-- ' + OMARCHY_LINE + '\n  --' + OMARCHY_LINE + '\n',
    'comment-mid-line': 'hl.monitor({ mode = "preferred" -- output = "XR-1" })\n',
    'string-dashes': 'hl.monitor({ output = "XR-1", description = "a--b" })\n',
    'single-quotes': "hl.monitor({output='XR-3'})\n",
    'mixed-quotes': 'hl.monitor({ output = "XR-4\' })\n',
    'no-options': 'hl.monitor({ output = "XR-12" })\n',
    'tight': 'hl.monitor({output="XR-1",})\n',
    'xr-zero': 'hl.monitor({ output = "XR-0" })\nhl.monitor({ output = "XR-01" })\n',
    'xr-long': 'hl.monitor({ output = "XR-123456789" })\n',
    'lowercase': 'hl.monitor({ output = "xr-1" })\n',
    'other-key-first': 'hl.monitor({ mode = "preferred", output = "XR-1" })\n',
    'nested-table': 'hl.monitor({ output = "XR-1", transform = { 1 } })\n',
    'space-before-paren': 'hl.monitor ({ output = "XR-1" })\n',
    'code-after': OMARCHY_LINE + ' x = 1\n',
    'two-on-a-line': 'hl.monitor({ output = "XR-1" }) hl.monitor({ output = "XR-2" })\n',
    'multi-line': 'hl.monitor({\n  output = "XR-1",\n  mode = "preferred",\n})\n',
    'several': (OMARCHY_LINE + '\n' + OMARCHY_LINE.replace('XR-1', 'XR-2') + '\nhl.monitor({\n output = "XR-2"\n})\n'
                + OMARCHY_LINE + '\n'),
    'output-in-text': 'local note = "output = \'XR-7\' is a desktop"\n',
    'spaces-around-equals': 'hl.monitor({ output\t =\t\t"XR-5" , scale = 2 })\t\n',
}
BYTES = {
    'bad-start-byte': b'a = 1\n\xff\n',
    'bad-continuation': ('a\n' + OMARCHY_LINE + '\n').encode() + b'\xe2\x28\xa1\n',
    'truncated-one': b'abc\xe2',
    'truncated-two': b'abc\xe2\x82',
}
NAME_SETS = [['XR-1'], ['XR-1', 'XR-2'], ['XR-2', 'XR-3', 'XR-4', 'XR-5', 'XR-12']]
DESKTOPS = ['XR-1', 'XR-10', 'XR-0', 'XR-', 'xr-1', 'XR-1 ', ' XR-1', 'XR-1\n', 'XR-١', 'DP-1', '']


class Client:
    def __init__(self, path, reload_error=None):
        self.monitors_config, self.reload_error, self.reloads = path, reload_error, 0

    def reload_config(self):
        self.reloads += 1
        if self.reload_error: raise ValueError(self.reload_error)


def scratch(raw):
    folder = Path(tempfile.mkdtemp(prefix='monitor-rules-corpus-'))
    path = folder / 'monitors.lua'
    path.write_bytes(raw)
    return folder, path


def clean(folder):
    for item in folder.iterdir(): item.unlink()
    folder.rmdir()


def backup_free(text):
    return re.sub(r'monitors\.lua\.bak\.xr-rule-[0-9]+', '<backup>', text)


def case(name, raw):
    out = {'name': name, **({'raw_hex': raw.hex()} if name in BYTES else {'raw': raw.decode('utf-8')})}
    try:
        text = raw.decode('utf-8')
        out['find'] = monitor_rules.find(text)
        out['find_xr1'] = monitor_rules.find(text, ['XR-1'])
    except ValueError:
        pass
    removed = []
    for names in NAME_SETS:
        folder, path = scratch(raw)
        try:
            backup = monitor_rules.remove(path, names)
            entry = {'names': names, 'result': None if backup is None else path.read_bytes().decode('utf-8')}
            if backup is not None and backup.read_bytes() != raw: raise AssertionError('backup differs')
        except (OSError, ValueError) as exc:
            entry = {'names': names, 'error': str(exc)}
        finally:
            clean(folder)
        removed.append(entry)
    out['remove'] = removed
    clears = []
    for names in NAME_SETS:
        for reload_error in (None, 'config reload rejected: nope'):
            folder, path = scratch(raw)
            notes = []
            client = Client(path, reload_error)
            try:
                reasons = monitor_rules.clear(client, names, notify=notes.append)
                clears.append({'names': names, 'reload_error': reload_error, 'reasons': reasons,
                               'notes': [backup_free(n) for n in notes], 'reloads': client.reloads,
                               'after': path.read_bytes().decode('utf-8', errors='replace'),
                               'files': sorted(backup_free(p.name) for p in folder.iterdir())})
            finally:
                clean(folder)
    out['clear'] = clears
    folder, path = scratch(raw)
    try:
        out['named'] = [{'names': names, 'reasons': monitor_rules.named(Client(path), names)} for names in NAME_SETS]
        try:
            monitor_rules.refuse_start(Client(path))
            out['refuse_start'] = None
        except ValueError as exc:
            out['refuse_start'] = str(exc)
        if path.read_bytes() != raw: raise AssertionError('named or refuse_start wrote the file')
    finally:
        clean(folder)
    return out


def monitor_rules_corpus():
    cases = [case(name, text.encode('utf-8')) for name, text in TEXTS.items()]
    cases += [case(name, raw) for name, raw in BYTES.items()]
    desktops = []
    for name in DESKTOPS:
        try: desktops.append({'name': name, 'ok': monitor_rules.desktop(name) == name})
        except ValueError as exc: desktops.append({'name': name, 'error': str(exc)})
    return {'about': 'monitor_rules.py results, made by tests/native_spike/monitor_rules_corpus.py.',
            'limit': monitor_rules.LIMIT, 'desktops': desktops, 'cases': cases}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: monitor_rules_corpus.py [--write]')
    text = dumps(monitor_rules_corpus())
    if argv == ['--write']:
        CORPUS.mkdir(parents=True, exist_ok=True)
        MONITOR_RULES_FILE.write_text(text)
        print('wrote', MONITOR_RULES_FILE.relative_to(ROOT))
        return 0
    stale = not MONITOR_RULES_FILE.exists() or MONITOR_RULES_FILE.read_text() != text
    if stale: print('stale:', MONITOR_RULES_FILE.relative_to(ROOT))
    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
