"""Run genuine CLI fixtures entirely in private scratch and user/IPC/net namespaces."""
import os,subprocess,sys,tempfile
from pathlib import Path
if __name__=='__main__':
 root=Path(__file__).resolve().parents[2]
 scratch=Path(os.environ.get('TMPDIR',str(root/'build/pro2-cli-scratch'))).resolve()
 scratch.mkdir(mode=0o700,parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='pro2-cli-ns-',dir=scratch) as tmp:
  env=dict(os.environ,PRO2_CLI_NS='1',TMPDIR=tmp,XDG_RUNTIME_DIR=tmp,WAYLAND_DISPLAY='no-host',DISPLAY='')
  raise SystemExit(subprocess.run(['unshare','-Ur','-i','-n','--',sys.executable,'-m','unittest','discover','-s','tests/pro2_cli','-v'],cwd=root,env=env).returncode)
