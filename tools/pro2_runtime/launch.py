"""Bounded parent-supervised Pro2 runtime launcher. No normal tracking entry point."""
from contextlib import ExitStack
import hashlib
import json
import math
import os
import re
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


# SDK report rates the bridge may ask for (viture_protocol_public.h). The pinned SDK's capability check allows codes 0..3 for the Pro 2
# in pose mode, and the bridge still asks it before opening. 60 unless a run says otherwise: docs/pro2/smoothness.md.
POSE_RATES=(60,90,120,240)
ROUTE_SECONDS=12*3600  # a panel session's deadline (bridge.cpp kRouteSeconds); Stop comes first
COMPETING_EVERY_S=1.0


def validate_mount_pitch(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not -30<=value<=30:raise ValueError('mount pitch must be -30..30 degrees')
    return float(value)


def validate_pose_rate(value):
    if type(value) is not int or value not in POSE_RATES:raise ValueError('pose rate must be exactly 60, 90, 120 or 240 Hz')
    return value


def pinned_fd(path,digest):
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        with os.fdopen(os.dup(fd),'rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('artifact hash mismatch')
        return fd
    except BaseException:os.close(fd);raise


def validate_bundle_location(root):
    """Dedicated supervised artifact path grammar, not normal installed-layout grammar.

    Hyprland's plugin request is tokenized text (no shell). Restrict every root
    component, require canonical/no-symlink location, then pin the exact suffix.
    """
    root=Path(root).absolute()
    if str(root)!=os.path.normpath(str(root)) or any(part in ('.','..') or not re.fullmatch(r'[A-Za-z0-9_.-]+',part) for part in root.parts[1:]):
        raise ValueError('unsupported supervised bundle location: canonical ASCII path without whitespace/control tokens required')
    if not root.is_dir() or root.resolve(strict=True)!=root:
        raise ValueError('unsupported supervised bundle location: directory and ancestors must not be symlinks')
    return root


def validate_bundle_plugin(root,manifest,path=None):
    root=validate_bundle_location(root)
    expected=root/'bin/libxr-ownership.so'
    if path is not None and (not isinstance(path,str) or path!=str(expected)):
        raise ValueError('supervised bundle plugin pin required')
    if expected.resolve(strict=True)!=expected:
        raise ValueError('supervised bundle plugin must not traverse symlinks')
    digest=manifest.get('files',{}).get('bin/libxr-ownership.so')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('supervised plugin manifest pin missing')
    fd=pinned_fd(expected,digest);os.close(fd)
    return str(expected)


def verify_manifest(root):
    root=validate_bundle_location(root)
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('sdk_sha256')!=SHA256:raise ValueError('runtime manifest scope')
    for name,digest in manifest['files'].items():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts:raise ValueError('manifest path')
        if (root/p).resolve(strict=True)!=root/p:raise ValueError('bundle artifacts must not traverse symlinks')
        fd=pinned_fd(root/p,digest);os.close(fd)
    for name in ('bin/pro2-sdk-bridge','bin/xr-native-spike','bin/libxr-ownership.so'):
        if name not in manifest['files']:raise ValueError('required artifact missing')
    validate_bundle_plugin(root,manifest)  # same exact check as command, before SDK owner construction
    return root,manifest


def competing(exclude=()):
    # Same-user explicit known providers. This is not a claim to inspect every inaccessible process.
    names={'xrDriver','xr-luma-driver','pro2-sdk-bridge'}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) in exclude:continue
        try:
            if proc.stat().st_uid==os.getuid():
                if (proc/'comm').read_text().strip() in names:return True
                if b'libglasses.so' in (proc/'maps').read_bytes():return True  # bytes: mapped file names need not be text
        except OSError:continue
    return False


class RuntimeSession:
    def __init__(self,root,run_dir,projection,seconds,approved,pose_rate_hz=60,panel_route=False,mount_pitch_deg=0.0):
        self.mount_pitch_deg=validate_mount_pitch(mount_pitch_deg)
        self.projection=validate_projection(projection,approved);self.pose_rate_hz=validate_pose_rate(pose_rate_hz);self.panel_route=panel_route is True
        if type(seconds) is not int or not 1<=seconds<=(ROUTE_SECONDS if self.panel_route else 300):raise ValueError('bounded seconds required')
        self.root,self.manifest=verify_manifest(root);self.seconds=seconds
        self.directory=Path(run_dir).absolute();self.directory.mkdir(mode=0o700,parents=False,exist_ok=False)
        self.prefix=str(self.directory/'pose_');self.child=None;self.events=[];self.ready=False;self.done=None;self.buffer=b''
        self.status=None;self.started=None;self.log=None;self.scanner=None;self.display=None;self.last_display=None;self.link=None;self.reconnects=0
        self.nonce=secrets.token_hex(16);self.health_path=self.directory/'health.json';self.thread=None;self.stop_monitor=threading.Event();self.lock=threading.RLock();self.failure=None
    def command(self,exe,sdk,write,display=None):
        return ['unshare','-Urn','--',f'/proc/self/fd/{exe}','--supervised-pro2-test','--sdk-fd',str(sdk),'--status-fd',str(write),*(['--display-fd',str(display)] if display is not None else []),'--prefix',self.prefix,'--projection',str(self.projection),'--seconds',str(self.seconds),
            *(['--pose-rate',str(self.pose_rate_hz)] if self.pose_rate_hz!=60 else []),*(['--mount-pitch',str(self.mount_pitch_deg)] if self.mount_pitch_deg else []),*(['--panel-route'] if self.panel_route else [])]
    def start(self):
        admit(inventory())
        if competing():raise ValueError('competing provider; parent must establish exclusive topology')
        try:
            with ExitStack() as resources:
                sdk=pinned_fd(LIBRARY,SHA256);resources.callback(os.close,sdk)
                exe=pinned_fd(self.root/'bin/pro2-sdk-bridge',self.manifest['files']['bin/pro2-sdk-bridge']);resources.callback(os.close,exe)
                read,write=os.pipe();self.status=read;os.set_blocking(read,False);resources.callback(os.close,write)
                display,self.display=os.pipe();resources.callback(os.close,display)
                self.log=open(self.directory/'sdk.private.log','xb');os.chmod(self.directory/'sdk.private.log',0o600)
                self.child=subprocess.Popen(self.command(exe,sdk,write,display),pass_fds=(sdk,exe,write,display),stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,start_new_session=True)
                self.started=time.monotonic()
                metadata=self.directory/'process.json'
                fd=os.open(metadata,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as stream:
                    json.dump(dict(pid=self.child.pid,process_group=self.child.pid,started_monotonic=self.started,exclusive_topology_not_atomic=True),stream)
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
                    self.scanner=threading.Thread(target=self.scan,daemon=True);self.scanner.start()
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
                        if event.get('pose_rate_hz',60)!=self.pose_rate_hz:raise ValueError('pose rate status mismatch')
                        if abs(event.get('mount_pitch_deg',0)-self.mount_pitch_deg)>.001:raise ValueError('mount pitch status mismatch')
                        self.ready=True
                    elif event.get('event')=='done':self.done=event
                    elif event.get('event')=='display':self.last_display=event;continue  # one per 3D switch: not counted as a flood
                    elif event.get('event') in ('lost','reconnected'):
                        # The Rust bridge waiting out a USB drop (D12): the feed is paused, readers hold the last pose, and the
                        # bridge itself ends the run (done, device-lost) if the glasses are not back in 30 s. Health stays ready.
                        self.link=event;self.reconnects+=event['event']=='reconnected';continue
                    else:raise ValueError('runtime status event')
                    self.events.append(event)
                    if len(self.events)>8:raise ValueError('runtime status flood')
            except BlockingIOError:pass
        if self.started and time.monotonic()-self.started>self.seconds+10:raise TimeoutError('runtime wall deadline')
    def write_health(self,ready,reason=None):
        temp=self.directory/'health.tmp'
        fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(dict(nonce=self.nonce,ready=ready,monotonic=time.monotonic(),**({'reason':reason} if reason else {})),stream)
        os.replace(temp,self.health_path)
    def monitor(self):
        while not self.stop_monitor.wait(.02):
            try:
                with self.lock:self.healthy();self.write_health(True)
            except Exception as exc:
                self.failure=self.failure or str(exc) or type(exc).__name__;self.write_health(False,self.failure);return
    def scan(self):
        # Its own thread: about 50 ms of /proc/*/maps, and each read holds that process's mmap lock, so a slow one must not delay the
        # heartbeat, and once a second is plenty.
        while not self.stop_monitor.wait(COMPETING_EVERY_S):
            try:
                if competing(exclude=(self.child.pid,)):self.failure=self.failure or 'competing provider during bounded test'
            except Exception as exc:self.failure=self.failure or f'provider scan: {exc}'
    def set_display(self,on):
        """The glasses to 3D (3840x1080@60) or back to 2D at 1920x1080@120, through the bridge's SDK (bridge.cpp --display-fd).
        Returns at once: the caller follows the mode Hyprland then reads (sbs.ButtonSbs). OSError once the bridge is gone."""
        with self.lock:
            if self.display is None or self.child is None or self.child.poll() is not None:raise OSError('Pro 2 bridge not running')
            os.write(self.display,b'\x32' if on is True else b'\x34')
    def healthy(self):
        if self.failure:raise ValueError(self.failure)
        self.poll()
        if not self.ready or self.done is not None or self.child.poll() is not None:raise ValueError('Pro2 test provider no longer ready')
    def close(self):
        self.stop_monitor.set()
        if self.thread:self.thread.join(timeout=2)
        if self.scanner:self.scanner.join(timeout=2)
        self.write_health(False,self.failure)
        with self.lock:
            if self.display is not None:os.close(self.display);self.display=None
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
