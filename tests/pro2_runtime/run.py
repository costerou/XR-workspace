"""Run runtime/controller fixtures in new user+IPC+network namespace; no real SDK."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
if __name__=='__main__':
    with tempfile.TemporaryDirectory() as tmp:
        env=dict(os.environ,PRO2_RUNTIME_NAMESPACE_TEST='1',XDG_RUNTIME_DIR=tmp,WAYLAND_DISPLAY='pro2-no-live',DISPLAY='')
        raise SystemExit(subprocess.run(['unshare','-Ur','-i','-n','--',sys.executable,'-m','unittest','discover','-s','tests/pro2_runtime','-v'],cwd=Path(__file__).resolve().parents[2],env=env).returncode)
