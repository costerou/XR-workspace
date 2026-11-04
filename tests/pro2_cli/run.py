import os,subprocess,sys,tempfile
from pathlib import Path
if __name__=='__main__':
 with tempfile.TemporaryDirectory() as tmp:
  env=dict(os.environ,PRO2_CLI_NS='1',XDG_RUNTIME_DIR=tmp,WAYLAND_DISPLAY='no-host',DISPLAY='')
  raise SystemExit(subprocess.run(['unshare','-Ur','-i','-n','--',sys.executable,'-m','unittest','discover','-s','tests/pro2_cli','-v'],cwd=Path(__file__).resolve().parents[2],env=env).returncode)
