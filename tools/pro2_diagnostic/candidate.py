"""Standalone raw SDK diagnostic candidate. Never used by XR tracking lifecycle."""
import argparse
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import threading
import time

LIBRARY = '/usr/lib/xr-driver/libglasses.so'
SHA256 = '8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e'
EXACT = ('35ca', '1301', 'VITURE PRO 2 XR Glasses')
POSE, RATE60 = 1, 0  # viture_protocol_public.h:151,156; NOT USB command bytes


def inventory(root=Path('/sys/bus/usb/devices')):
    rows=[]
    for device in root.iterdir():
        try: vendor=(device/'idVendor').read_text().strip().lower()
        except OSError: continue
        if vendor != '35ca': continue
        def read(name):
            try: return (device/name).read_text().strip()
            except OSError: return ''
        rows.append((vendor,read('idProduct').lower(),read('product')))
    return rows


def admit(rows):
    if rows != [EXACT]:
        raise ValueError('require exactly one VITURE USB device, exact 35ca:1301 and VITURE PRO 2 XR Glasses')


class PoseLog:
    def __init__(self): self.previous=None
    def sample(self,values,timestamp):
        values=list(values); issues=[]
        if len(values)!=7: issues.append('callback-length')
        elif not all(type(v) in (int,float) and math.isfinite(v) for v in values): issues.append('nonfinite')
        elif abs(math.sqrt(sum(v*v for v in values[3:7]))-1)>0.05: issues.append('quaternion-norm')
        if type(timestamp)!=int or not 0<=timestamp<2**64: issues.append('timestamp-range')
        else:
            if self.previous is not None and timestamp<=self.previous: issues.append('timestamp-not-increasing')
            self.previous=timestamp
        # Invalid floats remain inspectable JSON strings; never normalize or reorder.
        safe=[v if type(v) in (int,float) and math.isfinite(v) else repr(v) for v in values]
        return dict(kind='pose',host_monotonic_ns=time.monotonic_ns(),sdk_values=safe,
                    timestamp_raw=timestamp,timestamp_unit='unknown',frame_observed='unverified',issues=issues)


class SDK:
    """ctypes ABI from pinned public C headers; loading is itself an authorized step."""
    CALLBACK=C.CFUNCTYPE(None,C.POINTER(C.c_float),C.c_uint64)
    def __init__(self, library=LIBRARY, sha256=SHA256):
        fd=os.open(library,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        try:
            with os.fdopen(os.dup(fd),'rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
            if digest!=sha256: raise ValueError('installed SDK hash changed; re-review required')
            self.lib=C.CDLL(f'/proc/self/fd/{fd}',mode=os.RTLD_LOCAL|os.RTLD_NOW)
        finally: os.close(fd)
        self.h=None
        self.functions={}
        def bind(name,args,result=C.c_int):
            f=getattr(self.lib,'xr_device_provider_'+name); f.argtypes=args; f.restype=result
            self.functions[name]=f
        bind('create',[C.c_int],C.c_void_p)
        for name in ('start','stop','shutdown','get_device_type'): bind(name,[C.c_void_p])
        bind('destroy',[C.c_void_p],None)
        bind('initialize',[C.c_void_p,C.c_char_p,C.c_char_p])
        bind('register_imu_pose_callback',[C.c_void_p,self.CALLBACK])
        bind('open_imu',[C.c_void_p,C.c_uint8,C.c_uint8])
        bind('close_imu',[C.c_void_p,C.c_uint8])
        bind('is_product_id_valid',[C.c_int])
        bind('is_product_support_imu_frequency',[C.c_int,C.c_int,C.c_int])
        bind('get_glasses_version',[C.c_void_p,C.c_char_p,C.POINTER(C.c_int)])
    def capabilities(self):
        return (self.functions['is_product_id_valid'](0x1301)==1 and
                self.functions['is_product_support_imu_frequency'](0x1301,POSE,RATE60)==1)
    def create(self):
        self.h=self.functions['create'](0x1301)
        if not self.h: raise RuntimeError('SDK create returned NULL')
    def family(self): return self.functions['get_device_type'](self.h)
    def call(self,name):
        actual={'register':'register_imu_pose_callback','open':'open_imu','close':'close_imu'}.get(name,name)
        args={'initialize':(None,None),'open':(POSE,RATE60),'close':(POSE,)}.get(name,())
        rc=self.functions[actual](self.h,*args)
        if rc not in (None,0): raise RuntimeError(f'SDK {name} returned {rc}')
    def register(self,callback):
        def receive(ptr,stamp):
            callback([ptr[i] for i in range(7)] if ptr else [],int(stamp))
        self.callback=self.CALLBACK(receive)  # retain until destroy, including cleanup failures
        rc=self.functions['register_imu_pose_callback'](self.h,self.callback)
        if rc: raise RuntimeError(f'SDK register returned {rc}')
    def firmware(self):
        data=C.create_string_buffer(256); length=C.c_int(len(data))
        rc=self.functions['get_glasses_version'](self.h,data,C.byref(length))
        # Version only, never SDK serial/log buffers. Clamp/reject invalid response length.
        valid=rc==0 and 0<=length.value<=len(data)
        return dict(status=rc,version=data.value.decode('ascii','replace')[:128] if valid else None)


def capture(rows,loader,seconds,emit,stop=None,recheck=None):
    admit(rows)  # before loader/dlopen/create or any SDK operation
    sdk=loader()
    if not sdk.capabilities(): raise ValueError('SDK does not support exact PID pose60 capability')
    pending=queue.Queue(maxsize=8192); dropped=0; log=PoseLog(); errors=[]
    def receive(values,stamp):
        nonlocal dropped
        try: pending.put_nowait((list(values),stamp,time.monotonic_ns()))
        except queue.Full: dropped+=1
    created=False; opened=False; samples=0; invalid=0
    try:
        if recheck: admit(recheck())
        sdk.create(); created=True
        if sdk.family()!=1: raise ValueError('SDK did not return Gen2')
        sdk.call('initialize'); sdk.call('start'); sdk.register(receive)
        emit(dict(kind='firmware_query',**sdk.firmware()))
        opened=True  # close attempt even if open returns failure after partial activation
        sdk.call('open')
        end=time.monotonic()+seconds
        while time.monotonic()<end and not (stop and stop.is_set()):
            try: values,stamp,host=pending.get(timeout=min(.1,max(0,end-time.monotonic())))
            except queue.Empty: continue
            result=log.sample(values,stamp); result['host_monotonic_ns']=host; emit(result)
            samples+=1; invalid+=bool(result['issues'])
    finally:
        if created:
            for action in (['close'] if opened else [])+['stop','shutdown','destroy']:
                try: sdk.call(action)
                except Exception as exc: errors.append(str(exc))
        emit(dict(kind='cleanup',errors=errors,queue_dropped=dropped))
        if errors: raise RuntimeError('SDK cleanup failed: '+'; '.join(errors))
    emit(dict(kind='summary',samples=samples,invalid=invalid,queue_dropped=dropped))
    return samples,invalid,dropped


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-authorized-sdk-session',action='store_true')
    parser.add_argument('--seconds',type=int,default=15,choices=range(1,31))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.parent_authorized_sdk_session: parser.error('parent-authorized active SDK session required')
    rows=inventory(); admit(rows)
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        def emit(record): f.write(json.dumps(record,allow_nan=False)+'\n'); f.flush()
        emit(dict(kind='contract',model='pro2',library_sha256=SHA256,layout=['roll','pitch','yaw','qw','qx','qy','qz'],
                  timestamp_unit='unknown',documented_frame='NWU',observed_frame='unverified',conversion='none',pitch_correction='none'))
        samples,invalid,dropped=capture(rows,SDK,args.seconds,emit,stop,recheck=inventory)
        if not samples or invalid or dropped: raise RuntimeError('diagnostic incomplete/invalid; review log, do not advance gate')

if __name__=='__main__': main()
