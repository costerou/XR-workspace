#!/usr/bin/env python3
"""CPU renderer boundary fixture, no Wayland/GPU API, real capability socket."""
import sys,socket,time,os,json,subprocess
from pathlib import Path
args=dict(zip(sys.argv[1::2],sys.argv[2::2]));trace=Path(os.environ['XDG_RUNTIME_DIR'])/'renderer.json'
assert args['--pose-feed']=='xrld' and float(args['--pro2-test-projection'])==44.5
fd=int(args['--supervised-fd']);conn=socket.socket(fileno=fd);data=b''
while not data.endswith(b'\n'):data+=conn.recv(1)
assert json.loads(data)['op']=='start'
trace.write_text(json.dumps(dict(stage='renderer-launched',private_prefix=args['--pro2-test-pose-prefix'],capture=args['--capture'],wayland_fd_present=int(args['--wayland-fd'])>=0)))
if os.environ.get('PRO2_CLI_CASE')=='renderer-fail':os._exit(9)
conn.setblocking(False)
probe=subprocess.Popen([os.environ['PRO2_CLI_PROBE'],args['--pro2-test-pose-prefix']],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
while probe.poll() is None:
 conn.send(b'H')
 try:conn.recv(100000)
 except BlockingIOError:pass
 time.sleep(.03)
out,err=probe.communicate();trace.write_text(json.dumps(dict(stage='renderer-native-probe',result=probe.returncode,summary=out.decode(),error=err.decode())))
os._exit(probe.returncode)
