"""Bounded parent-supervised Pro2 runtime launcher. No normal tracking entry point."""
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import threading
import selectors
import signal
import subprocess
import tempfile
import time
from tools.pro2_diagnostic.candidate import LIBRARY, SHA256, inventory, admit


def validate_projection(value,approved):
    if approved is not True or type(value) not in (int,float) or not math.isfinite(value) or not 44.5<=value<=50:
        raise ValueError('explicit approximate supervised diagonal44.5..50 required')
    return float(value)


def pinned_fd(path,digest):
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        with os.fdopen(os.dup(fd),'rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('artifact hash mismatch')
        return fd
    except BaseException:os.close(fd);raise


def verify_manifest(root):
    root=Path(root).absolute()
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('sdk_sha256')!=SHA256:raise ValueError('runtime manifest scope')
    for name,digest in manifest['files'].items():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts:raise ValueError('manifest path')
        fd=pinned_fd(root/p,digest);os.close(fd)
    for name in ('bin/pro2-sdk-bridge','bin/xr-native-spike','bin/libxr-ownership.so'):
        if name not in manifest['files']:raise ValueError('required artifact missing')
    return root,manifest


def competing(exclude=()):
    # Same-user explicit known providers. This is not a claim to inspect every inaccessible process.
    names={'xrDriver','xr-luma-driver','pro2-sdk-bridge'}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) in exclude:continue
        try:
            if proc.stat().st_uid==os.getuid():
                if (proc/'comm').read_text().strip() in names:return True
                if 'libglasses.so' in (proc/'maps').read_text():return True
        except OSError:continue
    return False


class RuntimeSession:
    def __init__(self,root,run_dir,projection,seconds,approved):
        self.projection=validate_projection(projection,approved)
        if type(seconds) is not int or not 1<=seconds<=300:raise ValueError('bounded1..300seconds required')
        self.root,self.manifest=verify_manifest(root);self.seconds=seconds
        self.directory=Path(run_dir).absolute();self.directory.mkdir(mode=0o700,parents=False,exist_ok=False)
        self.prefix=str(self.directory/'pose_');self.child=None;self.events=[];self.ready=False;self.done=None;self.buffer=b''
        self.status=None;self.started=None;self.log=None
        self.nonce=secrets.token_hex(16);self.health_path=self.directory/'health.json';self.thread=None;self.stop_monitor=threading.Event();self.lock=threading.RLock();self.failure=None
    def command(self,exe,sdk,write):
        return ['unshare','-Urn','--',f'/proc/self/fd/{exe}','--supervised-pro2-test','--sdk-fd',str(sdk),'--status-fd',str(write),'--prefix',self.prefix,'--projection',str(self.projection),'--seconds',str(self.seconds)]
    def start(self):
        admit(inventory())
        if competing():raise ValueError('competing provider; parent must establish exclusive topology')
        try:
            with ExitStack() as resources:
                sdk=pinned_fd(LIBRARY,SHA256);resources.callback(os.close,sdk)
                exe=pinned_fd(self.root/'bin/pro2-sdk-bridge',self.manifest['files']['bin/pro2-sdk-bridge']);resources.callback(os.close,exe)
                read,write=os.pipe();self.status=read;os.set_blocking(read,False);resources.callback(os.close,write)
                self.log=open(self.directory/'sdk.private.log','xb');os.chmod(self.directory/'sdk.private.log',0o600)
                self.child=subprocess.Popen(self.command(exe,sdk,write),pass_fds=(sdk,exe,write),stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,start_new_session=True)
                self.started=time.monotonic()
        except BaseException:
            self.close();raise
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                self.poll()
                if self.ready:
                    self.healthy()
                    self.write_health(True)
                    self.thread=threading.Thread(target=self.monitor,daemon=True);self.thread.start()
                    return self
                if self.child.poll() is not None:raise ValueError('runtime exited before readiness')
                time.sleep(.01)
            raise TimeoutError('runtime readiness timeout')
        except BaseException:self.close();raise
    def poll(self):
        if self.status is not None:
            try:
                data=os.read(self.status,8192)
                self.buffer+=data
                if len(self.buffer)>65536:raise ValueError('runtime status overflow')
                while b'\n' in self.buffer:
                    line,self.buffer=self.buffer.split(b'\n',1);event=json.loads(line)
                    if event.get('event')=='ready':
                        if event.get('projection_diagonal_approx')!=self.projection:raise ValueError('projection status mismatch')
                        self.ready=True
                    elif event.get('event')=='done':self.done=event
                    else:raise ValueError('runtime status event')
                    self.events.append(event)
                    if len(self.events)>8:raise ValueError('runtime status flood')
            except BlockingIOError:pass
        if self.started and time.monotonic()-self.started>self.seconds+10:raise TimeoutError('runtime wall deadline')
    def write_health(self,ready):
        temp=self.directory/'health.tmp'
        fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(dict(nonce=self.nonce,ready=ready,monotonic=time.monotonic()),stream)
        os.replace(temp,self.health_path)
    def monitor(self):
        while not self.stop_monitor.wait(.02):
            try:
                with self.lock:self.healthy();self.write_health(True)
            except Exception as exc:
                self.failure=str(exc);self.write_health(False);return
    def healthy(self):
        if self.failure:raise ValueError('runtime health failed')
        self.poll()
        if not self.ready or self.done is not None or self.child.poll() is not None:raise ValueError('Pro2 test provider no longer ready')
        if competing(exclude=(self.child.pid,)):raise ValueError('competing provider during bounded test')
    def close(self):
        self.stop_monitor.set()
        if self.thread:self.thread.join(timeout=2)
        self.write_health(False)
        if self.child is not None:
            if self.child.poll() is None:
                os.killpg(self.child.pid,signal.SIGTERM)
                try:self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(self.child.pid,signal.SIGKILL);self.child.wait(timeout=2)
            try:self.poll()
            except (ValueError,TimeoutError):pass
        if self.status is not None:os.close(self.status);self.status=None
        if self.log:self.log.close();self.log=None
        if self.child is not None:
            if self.child.returncode!=0 or not self.done or self.done.get('cleanup_errors') or self.done.get('error'):
                raise RuntimeError('runtime failed or cleanup unconfirmed; preserve private logs, no automatic restart')
