"""Run independent QA in fresh user, IPC and network namespaces. No SDK installed.

Usage: python tests/pro2_runtime_qa/run.py [unittest arguments]
Default: discover -s tests/pro2_runtime_qa -v
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    env = dict(os.environ)
    for key in ('PRO2_PRIVATE_REPLAY', 'WAYLAND_DISPLAY', 'DISPLAY', 'LD_PRELOAD'):
        env.pop(key, None)
    for kind in ('user', 'ipc', 'net'):
        env['PRO2_QA_PARENT_' + kind.upper()] = os.readlink('/proc/self/ns/' + kind)
    with tempfile.TemporaryDirectory(prefix='pro2-qa-runtime-') as tmp:
        env['XDG_RUNTIME_DIR'] = tmp
        args = sys.argv[1:] or ['discover', '-s', 'tests/pro2_runtime_qa', '-v']
        return subprocess.run(['unshare', '-Ur', '-i', '-n', '--', sys.executable,
                               '-m', 'unittest', *args], cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
