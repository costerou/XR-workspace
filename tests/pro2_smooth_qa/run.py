"""Run this directory's tests in private user, IPC and network namespaces, with a private HOME and no compositor in the environment.

    /usr/bin/python3 tests/pro2_smooth_qa/run.py [-v]

Nothing here loads the vendor SDK, opens USB, or contacts a compositor. Each test named for a defect was red before its fix.
it is fixed. Everything else is a claim that held.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if __name__ == '__main__':
    base = os.environ.get('PRO2_QA_TMP')  # a private directory; default: the system's
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        t = Path(tmp)
        for name in ('home', 'config', 'state', 'data', 'run'): (t / name).mkdir(mode=0o700)
        env = {k: v for k, v in os.environ.items() if k not in ('HYPRLAND_INSTANCE_SIGNATURE', 'WAYLAND_DISPLAY', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS')}
        env.update(PRO2_QA_NAMESPACE='1', HOME=str(t / 'home'), XDG_CONFIG_HOME=str(t / 'config'), XDG_STATE_HOME=str(t / 'state'), XDG_DATA_HOME=str(t / 'data'),
                   XDG_RUNTIME_DIR=str(t / 'run'), PYTHONDONTWRITEBYTECODE='1')
        raise SystemExit(subprocess.run(['unshare', '-Ur', '-i', '-n', '--', '/usr/bin/python3', '-m', 'unittest', 'discover', '-s', 'tests/pro2_smooth_qa', *sys.argv[1:]], cwd=ROOT, env=env).returncode)
