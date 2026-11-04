"""What do the glasses do on DisplayPort when R1 is held? Nobody has looked yet, and 3D rests on the answer (docs/immersive-3d.md).

Run this with XR stopped and the glasses plugged in, hold R1 until they switch, then hold it again to come back:

    python3 -m tools.native_spike.sbs_probe

It only reads: the kernel's connector files in /sys/class/drm, and `hyprctl monitors all -j`. It changes no mode, sends nothing to the
glasses and opens no USB device, so it is safe beside anything. Each change is printed as it happens, each new EDID is saved in
~/.local/state/viture-workspace/sbs-probe/ for edid-decode, and Ctrl+C prints what it means for 3D.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .sbs import RATES, WIDE

DRM = Path('/sys/class/drm')
POLL_S = 0.1
MODE = re.compile(r'(\d+)x(\d+)')
FINDINGS = {
    'ready': 'Hyprland offered {mode}: XR can follow the glasses into 3D.',
    'wrong_rate': 'Hyprland offered 3840x1200, but only at {rates} Hz, which VITURE does not list: add the rate to sbs.RATES after checking the picture.',
    'hyprland_blind': 'The kernel saw 3840x1200 and Hyprland did not list it: the output never reconnected in Hyprland. XR cannot follow this yet.',
    'narrow': 'They offered {modes} and no 3840x1200. XR\'s 3D needs 3840x1200, so it needs a change for this.',
    'silent': 'Nothing changed on DisplayPort. Either R1 was not held long enough, or the Luma Ultra switches some other way.',
}


def connector(name, root=DRM):
    """The kernel's folder for an output name such as DP-2, whichever card it is on, or None."""
    for path in sorted(root.glob(f'card*-{name}')):
        return path
    return None


def kernel_state(path):
    """(status, modes in the kernel's order, the EDID's bytes). Reading these files changes nothing: the kernel keeps them."""
    try:
        return ((path / 'status').read_text().strip(), tuple((path / 'modes').read_text().split()), (path / 'edid').read_bytes())
    except OSError:
        return ('gone', (), b'')


def hyprland_modes(name, run=subprocess.run):
    """What Hyprland lists for the output, None if it does not list it, () if hyprctl cannot be asked."""
    try: done = run(['hyprctl', 'monitors', 'all', '-j'], capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError): return ()
    try: found = [m for m in json.loads(done.stdout) if m.get('name') == name]
    except ValueError: return ()
    return tuple(found[0].get('availableModes') or ()) if found else None


def wide_rates(modes):
    """The rates of 3840x1200 in a list of Hyprland's mode strings."""
    out = []
    for mode in modes or ():
        m = re.fullmatch(r'(\d+)x(\d+)@([0-9.]+)Hz', mode)
        if m and (int(m[1]), int(m[2])) == WIDE: out.append(float(m[3]))
    return out


def verdict(seen_kernel, seen_hyprland):
    """(finding, what fills it in), from every mode list seen while watching."""
    rates = sorted({r for modes in seen_hyprland for r in wide_rates(modes)}, reverse=True)
    usable = [r for r in rates if any(abs(r - want) < 0.5 for want in RATES)]
    if usable: return 'ready', {'mode': f'3840x1200@{usable[0]:g}'}
    if rates: return 'wrong_rate', {'rates': ', '.join(f'{r:g}' for r in rates)}
    kernel = {m for modes in seen_kernel for m in modes}
    if f'{WIDE[0]}x{WIDE[1]}' in kernel: return 'hyprland_blind', {}
    narrow = sorted(m for m in kernel if (s := MODE.fullmatch(m)) and int(s[1]) >= 2 * int(s[2]))
    if narrow: return 'narrow', {'modes': ', '.join(narrow)}
    return 'silent', {}


def main(argv=None, out=sys.stdout):
    argv = sys.argv[1:] if argv is None else argv
    name = argv[0] if argv else 'DP-2'
    path = connector(name)
    if path is None:
        print(f'no connector called {name}', file=out)
        return 1
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'viture-workspace' / 'sbs-probe'
    state.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last_kernel = last_hyprland = None
    seen_kernel, seen_hyprland, edids = [], [], set()
    print(f'Watching {name}. Hold R1 on the glasses until they switch, wait, hold it again. Ctrl+C when done.', file=out, flush=True)
    try:
        while True:
            kernel, hyprland = kernel_state(path), hyprland_modes(name)
            stamp = f'[{time.monotonic() - started:7.2f}]'
            if kernel != last_kernel:
                print(f'{stamp} kernel: {kernel[0]}, modes {" ".join(sorted(set(kernel[1]))) or "none"}', file=out, flush=True)
                seen_kernel.append(kernel[1])
                digest = hashlib.sha256(kernel[2]).hexdigest()[:12]
                if kernel[2] and digest not in edids:
                    edids.add(digest)
                    saved = state / f'edid-{datetime.now():%H%M%S}-{digest}.bin'
                    saved.write_bytes(kernel[2])
                    print(f'{stamp} new EDID saved: {saved}', file=out, flush=True)
                last_kernel = kernel
            if hyprland != last_hyprland:
                print(f'{stamp} Hyprland: {"not listed" if hyprland is None else " ".join(hyprland) or "no answer"}', file=out, flush=True)
                seen_hyprland.append(hyprland)
                last_hyprland = hyprland
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    finding, fill = verdict(seen_kernel, seen_hyprland)
    print(f'\n{FINDINGS[finding].format(**fill)}', file=out, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
