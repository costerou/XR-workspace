"""Runs the Python bind helper (tools/native_spike/xr_control.py) for control_parity.rs, with its fixed tool paths
(/usr/bin/zenity, /usr/bin/wl-paste, /usr/bin/notify-send) pointed at the test's fake tools. Nothing else changes.

usage: python3 -I control_parity_harness.py XR_CONTROL_PY --lists
       python3 -I control_parity_harness.py XR_CONTROL_PY TOOLS_DIR ARG...
--lists prints the helper's op lists and fixed Lua as JSON, so the Rust port is held to the same ones.
"""
import importlib.util
import json
import os
import subprocess
import sys
import types

src = sys.argv[1]
spec = importlib.util.spec_from_file_location('xr_control', src)
xc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xc)

if sys.argv[2] == '--lists':
    names = ('OPS', 'LEGACY_OPS', 'VIDEO_OPS', 'HELPER_OPS', 'KEY_OP', 'LEAVE_KEY_PANEL', 'REMOVE_VIDEO_BINDS',
             'WAKE_ALL_DISPLAYS', 'REMOVE_MENU_BINDS', 'REMOVE_RESIZE_BINDS', 'REMOVE_DRAG_BINDS', 'SHOW_OVERVIEW_CURSOR',
             'NO_SESSION', 'MAX_CLIPBOARD', 'VIDEO_FILTER')
    print(json.dumps({n: getattr(xc, n) for n in names}))
    sys.exit(0)

tools = sys.argv[2]
REMAP = {'/usr/bin/' + n: os.path.join(tools, n) for n in ('zenity', 'wl-paste', 'notify-send')}
real_access, real_run = os.access, subprocess.run
proxy = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith('__')})
proxy.access = lambda path, mode: real_access(REMAP.get(path, path), mode)
xc.os = proxy
subprocess.run = lambda args, *a, **k: real_run([REMAP.get(args[0], args[0]), *args[1:]], *a, **k)
sys.exit(xc.main([src] + sys.argv[3:]))
