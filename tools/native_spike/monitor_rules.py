"""A monitor rule naming an XR desktop in the user's Hyprland config: finding it, and the one narrow edit that removes it.

Omarchy's per-monitor scale (omarchy-hyprland-monitor-scaling: the Monitor panel's scale, Super+/ and Super+Alt+/) acts on
the focused monitor, whichever that is. On an XR desktop it replaces the broker's monitor rule in Hyprland and writes a
rule for that XR-n into monitors.lua, which Hyprland reloads at once. Run 4468c283 met this on 2026-11-03: the desktop took
the rule (preferred mode, 60 Hz, placed automatically), the broker's own rule was gone, and from then on:

  * a plugin older than 2026-11-04 refused to remove the output, because the rule of that name was not its own, so
    recovery could never finish (spike/ownership/plugin.cpp cleanup now leaves the user's rule and goes on);
  * every later start would fail, because the broker refuses to create an output whose name already has a rule.

So a run cannot start with such a rule, and one naming an output of a run, found at that run's recovery, was written during
the run. The panel's Recover takes it out (clear), before it asks the broker to remove anything: only a reload that no
longer finds the rule lets a plugin already loaded finish. Only the one-line form Omarchy writes goes. Anything else (a
multi-line table, say) was written by hand and is only reported, with its line. A copy of the file stays beside it, and the
swap is atomic, through any symlink, as Omarchy's own edit is.

This is the one exception to "saved monitor configuration is never edited" (docs/technical.md, Safety boundaries), and it
needs a person's click: Recover's confirm box lists the line (state.py begin_recover), and only the desktops it listed
reach recover-run (--clear-rule), so a controller from before this, which shows no such box, cannot cause an edit. Stop and
the CLI's recover only say that the line is there (named), and a start refuses with it (refuse_start). All of it is reached
only through a client that says where the file is (HyprlandClient.monitors_config), so test doubles never touch a real one.
"""
import os
from pathlib import Path
import re
import secrets
import stat
import time

NAME = r'XR-[1-9][0-9]*'
NAMING = re.compile(rf'''output\s*=\s*["']({NAME})["']''')
# A whole line holding one rule for an XR desktop, and nothing but a comment after it: what Omarchy writes.
ONE_LINE = re.compile(rf'''\s*hl\.monitor\(\s*\{{\s*output\s*=\s*["']{NAME}["']\s*(?:,[^{{}}]*)?\}}\s*\)\s*(?:--.*)?''')
LIMIT = 1048576


def config_path(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get('XDG_CONFIG_HOME') or str(Path(environ.get('HOME') or Path.home()) / '.config')
    return Path(base) / 'hypr' / 'monitors.lua'


def desktop(name):
    """`name` if it is an XR desktop's name, else ValueError: the only thing the Recover command line may ask to clear."""
    if not re.fullmatch(NAME, name): raise ValueError('not an XR desktop')
    return name


def config_of(client):
    """The file this client's compositor reads, or None for anything that does not say (every test double)."""
    path = getattr(client, 'monitors_config', None)
    return path if isinstance(path, Path) else None


def find(text, names=None):
    """Rules naming an XR desktop in a config's text, commented-out ones aside: [{'name', 'line', 'removable'}], lines
    counted from 1. `names`: only these desktops."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        match = NAMING.search(line.split('--', 1)[0])
        if match and (names is None or match[1] in names):
            found.append({'name': match[1], 'line': number, 'removable': ONE_LINE.fullmatch(line) is not None})
    return found


def _read(path):
    with open(path, 'rb') as stream: raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT: raise ValueError('monitor config too large')
    return raw.decode('utf-8')


def leftovers(names=None, path=None):
    """find() on the file at `path` (else the user's); [] with no file."""
    try: return find(_read(path if path is not None else config_path()), names)
    except FileNotFoundError: return []


def describe(rule, path):
    return (f"{Path(path).name} line {rule['line']} names {rule['name']}, so that desktop cannot be used: "
            f"remove that line, then run hyprctl reload")


def _write(path, text, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(text); stream.flush(); os.fsync(stream.fileno())


def remove(path, names):
    """The removable rules for `names` out of the file at `path`, and nothing else. The copy left beside it, or None if
    there was nothing to remove."""
    target = Path(path).resolve()
    text = _read(target)
    gone = {rule['line'] for rule in find(text, names) if rule['removable']}
    if not gone: return None
    mode = stat.S_IMODE(os.stat(target).st_mode)
    backup = target.with_name(f'{target.name}.bak.xr-rule-{time.time_ns()}')
    _write(backup, text, mode)
    kept = ''.join(line for number, line in enumerate(text.splitlines(keepends=True), 1) if number not in gone)
    temporary = target.with_name(f'.{target.name}.{secrets.token_hex(8)}.tmp')
    try:
        _write(temporary, kept, mode)
        os.replace(temporary, target)
    except BaseException:
        try: os.unlink(temporary)
        except OSError: pass
        raise
    return backup


def named(client, names):
    """What stands in the file for `names`, a run's outputs, as reasons. Changes nothing. Never raises."""
    path = config_of(client)
    if path is None or not names: return []
    try: return [describe(rule, path) for rule in leftovers(names, path)]
    except (OSError, ValueError): return []


def clear(client, names, notify=None):
    """The panel's Recover, before the broker is asked to remove `names`, a run's outputs: their one-line rules go and
    Hyprland reloads (module docstring). What this could not do comes back as reasons. Never raises: recovery runs regardless."""
    path = config_of(client)
    if path is None or not names: return []
    found, backup = [], None
    try:
        found = leftovers(names, path)
        if any(rule['removable'] for rule in found): backup = remove(path, names)
        if backup is not None: client.reload_config()
    except (OSError, ValueError) as exc:
        named = ', '.join(sorted({rule['name'] for rule in found})) or 'an XR desktop'
        return [f"could not take the rule for {named} out of {path.name}: {str(exc) or type(exc).__name__}"]
    if backup is not None and notify is not None:
        removed = sorted({rule['name'] for rule in found if rule['removable']})
        notify(f"Removed the {' and '.join(removed)} line Omarchy's scale control added to {path.name}. "
               f"A copy is in {backup.name}.")
    return [describe(rule, path) for rule in found if not rule['removable']]


def refuse_start(client):
    """A start, before anything is loaded: the broker would refuse the desktop with no reason a person could use. A config
    this cannot read proves nothing, so it stops nothing."""
    path = config_of(client)
    if path is None: return
    try: found = leftovers(None, path)
    except (OSError, ValueError): return
    if found: raise ValueError(describe(found[0], path))
