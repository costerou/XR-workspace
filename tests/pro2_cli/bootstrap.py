"""Interpreter shim prelude; fixture SDK only. Execute original packaged -c verbatim."""
import sys,os,json,hashlib,socket
from pathlib import Path

def install(root):
 sys.path.insert(0,str(root));from tools.pro2_runtime import launch
 sdk=Path(os.environ['PRO2_CLI_FIXTURE_SDK']);digest=hashlib.sha256(sdk.read_bytes()).hexdigest()
 launch.LIBRARY=str(sdk);launch.SHA256=digest
 launch.inventory=lambda:[('35ca','1301','VITURE PRO 2 XR Glasses')]
 launch.competing=lambda **kwargs:False
 command=launch.RuntimeSession.command
 launch.RuntimeSession.command=lambda self,*args:command(self,*args)+['--fixture-root',os.environ['PRO2_CLI_FIXTURE_USB'],'--fixture-hash',digest]
 # No actual driver/device/GPU may be opened in this Python process.
 def audit(event,args):
  if event=='ctypes.dlopen':raise AssertionError('Python CDLL forbidden')
  if event=='open' and isinstance(args[0],str) and args[0].startswith(('/dev/dri','/dev/bus/usb','/dev/hidraw')):raise AssertionError('host device forbidden')
 sys.addaudithook(audit)
 connect=socket.socket.connect
 def private_connect(s,address):
  if s.family!=socket.AF_UNIX:raise AssertionError('only private Unix sockets permitted')
  if not str(address).startswith(('/proc/self/fd/',os.environ['XDG_RUNTIME_DIR'])):raise AssertionError('host socket forbidden')
  return connect(s,address)
 socket.socket.connect=private_connect

if __name__=='__main__':
 args=sys.argv[1:];index=args.index('-c');code=args[index+1];tail=args[index+2:]
 root=Path(tail[0]);install(root);sys.argv=['-c']+tail
 exec(compile(code,'<original-packaged-cli>','exec'),globals())
