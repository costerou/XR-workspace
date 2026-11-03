"""Do this run's keybinds clash with the desktop's own? Checked offline with Hyprland's own config loader.

    python -m tools.native_spike.bind_check [--config FILE] [--hyprland BINARY]

The user's Hyprland config (by default ~/.config/hypr/hyprland.lua: Omarchy's defaults plus the user's files) is
loaded by `Hyprland --verify-config`, which runs the Lua config with the real `hl` API but starts no compositor. It
runs in a throwaway HOME holding a copy of ~/.config/hypr and the Omarchy state the config reads, so nothing outside
that directory is written. A small harness wraps hl.bind / hl.unbind to record every chord the config binds, then runs
this run's own generated bind Lua (view binds, send-window binds, the placement panel's modal binds, the overview free
pointer's left button, the overview input lock's other buttons and wheel, the unified cursor's non-consuming
desktop-mode click binds) the same way, so Hyprland also checks that
ours load. The run's chords (view.BINDS, send.SEND_BINDS, MENU_BINDS, the free pointer's resize_press / resize_release,
input_lock.OTHER_BUTTONS and WHEEL) are then compared with the desktop's and with each other. A digit and its keycode (1 and code:10, as Omarchy binds workspace
digits) count as the same key. The running compositor is never contacted.

Exit status: 0 no clash, 1 clash found, 2 could not check (no Hyprland binary, config or harness error).
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import input_lock, resize, send, unified_cursor, view

# Hyprland modifier spellings -> one name each.
MODS = {'SUPER': 'SUPER', 'WIN': 'SUPER', 'LOGO': 'SUPER', 'MOD4': 'SUPER', 'META': 'SUPER', 'CTRL': 'CTRL',
        'CONTROL': 'CTRL', 'ALT': 'ALT', 'MOD1': 'ALT', 'SHIFT': 'SHIFT'}
# Digit keys by keysym and by keycode are one key: '1' is code:10 ... '0' is code:19.
DIGIT_CODES = {str((i + 1) % 10): f'code:{10 + i}' for i in range(10)}
# XKB keysym aliases that name one key (the help card's Ctrl+Alt+Page_Up / Page_Down is Prior / Next to XKB).
KEY_ALIASES = {'prior': 'page_up', 'next': 'page_down'}
# Omarchy state its Hyprland config reads at load (themes, toggles, layouts); copied, never linked.
STATE_DIRS = ('current', 'toggles', 'settings', 'workspace-layouts', 'monitor-workspaces', 'monitor-workspaces-merged',
              'indicators')
# Bind Lua is generated with placeholder paths; only its shape matters here.
DUMMY_ARGS = ('/usr/bin/python3', '/nonexistent/xr_control.py', '/nonexistent/control.sock', 'bindcheck', 'bindcheck')


def normalize(chord):
    """'SUPER + SHIFT + R' and 'shift + win + r' -> (frozenset({'SUPER', 'SHIFT'}), 'r')."""
    parts = [p.strip() for p in str(chord).split('+')]
    if not parts or not parts[-1]: raise ValueError(f'unreadable chord: {chord!r}')
    key = parts[-1].lower()
    key = KEY_ALIASES.get(key, key)
    return frozenset(MODS.get(p.upper(), p.upper()) for p in parts[:-1] if p), DIGIT_CODES.get(key, key)


def run_chords():
    """(set name, chord, op) for every bind this project installs during a run."""
    out = [('run', c, op) for c, op in view.BINDS] + [('send', c, op) for c, op in send.SEND_BINDS]
    out += [('menu', c, op) for c, op in view.MENU_BINDS]
    # The overview free pointer's left button (free mode only: pill and corner drags, pill clicks, panel sliders).
    out += [('pointer', resize.RESIZE_BUTTON, op) for op in resize.RESIZE_OPS]
    # The overview input lock, while the cursor is captured: the other buttons and the wheel (bound to nothing).
    return out + [('lock', b, input_lock.OTHER_BUTTON_OP) for b in input_lock.OTHER_BUTTONS] + \
        [('lock', w, 'nothing') for w in input_lock.WHEEL]


def _lua_string(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def harness_lua(config, out):
    chunks = [view.install_binds_lua(*DUMMY_ARGS, typing=True), view.remove_binds_lua(),  # typing mode's toggle-only set
              view.install_binds_lua(*DUMMY_ARGS), view.install_send_binds_lua(*DUMMY_ARGS),
              view.install_menu_binds_lua(*DUMMY_ARGS), resize.install_resize_binds_lua(*DUMMY_ARGS),
              input_lock.lock_binds_lua(*DUMMY_ARGS),  # into the free pointer's table, removed with it
              resize.remove_resize_binds_lua(),
              # The unified cursor's desktop-mode left button (non-consuming; pcall'd, so check both were taken).
              unified_cursor.click_binds_lua(*DUMMY_ARGS) +
              f' assert(#{resize.BIND_TABLE} == 2, "the unified cursor\'s click binds were refused")',
              resize.remove_resize_binds_lua(), view.remove_binds_lua()]
    ours = '\n'.join(f'do {c} end' for c in chunks)
    return f'''local out = assert(io.open({_lua_string(out)}, "w"))
local phase = "desktop"
local function clean(s) return (tostring(s):gsub("[\\t\\n]", " ")) end
local bind, unbind = hl.bind, hl.unbind
-- Hyprland 0.56.2 --verify-config segfaults on exit freeing the timers a config made (CConfigManager::cleanTimers);
-- verify mode never fires timers, so inert stand-ins keep the verdict.
local inert = setmetatable({{}}, {{__index = function() return function() end end}})
hl.timer = function() return inert end
hl.bind = function(keys, ...) out:write(phase, "\\tbind\\t", clean(keys), "\\n") return bind(keys, ...) end
hl.unbind = function(keys, ...) out:write(phase, "\\tunbind\\t", clean(keys), "\\n") return unbind(keys, ...) end
local ok, err = pcall(dofile, {_lua_string(config)})
out:write("desktop\\tloaded\\t", ok and "ok" or clean(err), "\\n")
phase = "run"
local ran, why = pcall(function()
{ours}
end)
out:write("run\\tloaded\\t", ran and "ok" or clean(why), "\\n")
out:close()
'''


def desktop_chords(records):
    """Chords the desktop config leaves bound: binds minus later unbinds of the same chord."""
    bound = {}
    for phase, kind, value in records:
        if phase != 'desktop' or kind not in ('bind', 'unbind'): continue
        try: key = normalize(value)
        except ValueError: continue
        if kind == 'bind': bound.setdefault(key, []).append(value)
        else: bound.pop(key, None)
    return bound


def clashes(records):
    """Human-readable clash lines (empty: none). A press/release pair on one button is one bind, not a clash."""
    desktop = desktop_chords(records)
    problems, seen = [], {}
    for where, chord, op in run_chords():
        key = normalize(chord)
        if key in desktop: problems.append(f'{chord} ({where}: {op}) is already bound by the desktop config as {desktop[key][0]}')
        for other_where, other_op in seen.get(key, []):
            pair = {op, other_op} == set(resize.RESIZE_OPS)  # one left-button bind: press, and release
            # The run, send, panel and free-pointer sets can all be live at once (a panel open, the pointer free).
            if not pair: problems.append(f'{chord} is bound twice in this project ({other_where}: {other_op}, {where}: {op})')
        seen.setdefault(key, []).append((where, op))
    return problems


def _isolated_home(home, config_dir):
    """A temporary HOME with copies of the Hyprland config and the Omarchy state it reads."""
    tmp = Path(tempfile.mkdtemp(prefix='xr-bind-check-'))
    shutil.copytree(config_dir, tmp / '.config' / 'hypr', symlinks=True)
    state = Path(home) / '.local' / 'state' / 'omarchy'
    for name in STATE_DIRS:
        src = state / name
        dst = tmp / '.local' / 'state' / 'omarchy' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir() and not src.is_symlink(): shutil.copytree(src, dst, symlinks=True)
        elif src.exists() or src.is_symlink(): shutil.copy2(src, dst, follow_symlinks=False)
    return tmp


def check(config=None, hyprland=None, home=None, timeout=60):
    """(exit status, report lines)."""
    home = Path(home or os.environ.get('HOME') or Path.home())
    config = Path(config) if config else home / '.config' / 'hypr' / 'hyprland.lua'
    binary = hyprland or shutil.which('Hyprland')
    if not binary: return 2, ['Hyprland not found; cannot run --verify-config']
    if not config.is_file(): return 2, [f'no Hyprland config at {config}']
    tmp = _isolated_home(home, config.parent)
    try:
        copied = tmp / '.config' / 'hypr' / config.name
        out, harness = tmp / 'binds.tsv', tmp / 'harness.lua'
        harness.write_text(harness_lua(str(copied), str(out)))
        env = {'HOME': str(tmp), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'XDG_CONFIG_HOME': str(tmp / '.config'),
               'XDG_STATE_HOME': str(tmp / '.local' / 'state'), 'XDG_RUNTIME_DIR': str(tmp), 'LANG': 'C.UTF-8'}
        # Omarchy's config reads these; a made-up instance matches no compositor socket (verify mode opens none anyway).
        env['OMARCHY_PATH'] = os.environ.get('OMARCHY_PATH') or '/usr/share/omarchy'
        env['HYPRLAND_INSTANCE_SIGNATURE'] = 'xr-bind-check'
        r = subprocess.run([binary, '--verify-config', '-c', str(harness)], env=env, cwd=tmp, timeout=timeout,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        verdict = r.stdout.split('Config parsing result:', 1)[-1].strip()
        if not out.exists(): return 2, [f'harness did not run: {verdict or r.stderr.strip()}']
        records = [tuple(line.split('\t', 2)) for line in out.read_text().splitlines() if line.count('\t') >= 2]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    loaded = {phase: value for phase, kind, value in records if kind == 'loaded'}
    if loaded.get('desktop') != 'ok': return 2, [f'desktop config failed to load: {loaded.get("desktop", "no result")}']
    if loaded.get('run') != 'ok': return 2, [f"this project's bind Lua failed to load: {loaded.get('run', 'no result')}"]
    report = [f'Hyprland --verify-config: {verdict.splitlines()[0] if verdict else "no verdict"} ({r.returncode})',
              f'desktop config: {len(desktop_chords(records))} chords bound']
    problems = clashes(records)
    if r.returncode != 0: return 2, report + ['Hyprland reported config errors (above); results may be incomplete']
    if problems: return 1, report + problems
    labels = ', '.join(sorted({view.chord_label(c) for w, c, _ in run_chords() if w == 'menu'}))
    sends = ', '.join(view.chord_label(c) for c, _ in send.SEND_BINDS)
    return 0, report + [f'no clash: {len(view.BINDS)} run binds (typing toggle {view.chord_label(view.TYPING_BIND[0])}), send-window binds ({sends}), '
                        f'placement panel binds ({labels}), the free pointer\'s left button (and the unified cursor\'s '
                        'non-consuming click binds) and the input lock\'s other buttons and wheel']


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tools.native_spike.bind_check', description=__doc__.split('\n\n')[0])
    parser.add_argument('--config', help='Hyprland Lua config (default ~/.config/hypr/hyprland.lua)')
    parser.add_argument('--hyprland', help='Hyprland binary (default: Hyprland on PATH)')
    args = parser.parse_args(argv)
    status, lines = check(args.config, args.hyprland)
    for line in lines: print(line)
    return status


if __name__ == '__main__':
    sys.exit(main())
