"""OFFLINE H1 replay candidate: no SDK, device, IPC writer or production import.

Provisional scope: pinned SDK / firmware30.0.00.002_20260804, parent G2/G3 + labeled roll.
H1 has sign-consistent yaw/pitch/roll evidence; native private replay compatibility
is tested. This is not live renderer/optical or production activation approval.
"""
import math
import struct


def basis(wxyz):
    w,x,y,z=wxyz
    return [w,-z,-x,y]  # exact upstream imu.c quaternion_eus_to_nwu basis change


class ReplayAdapter:
    def __init__(self,*,replay_only=False):
        if replay_only is not True: raise ValueError('offline replay candidate only')
        self.closed=False;self.begin_session()
    def begin_session(self):
        if self.closed: raise ValueError('closed replay session')
        self.active=False;self.last_ts=None;self.last_host=None;self.origin=None;self.history=[]
    def pause(self):
        self.active=False;self.history=[]
    def close(self):
        self.pause();self.closed=True
    def push(self,values,timestamp_raw,host_ns):
        if self.closed: raise ValueError('closed replay session')
        try:
            if len(values)!=7 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values): raise ValueError('invalid pose')
            if type(timestamp_raw)!=int or not 0<=timestamp_raw<2**64:raise ValueError('invalid uint64 time')
            if type(host_ns)!=int or host_ns<0:raise ValueError('invalid receipt time')
            if self.last_ts is not None and timestamp_raw<=self.last_ts:raise ValueError('reset/wrap/duplicate: explicit new session required')
            if self.last_host is not None and (host_ns<=self.last_host or host_ns-self.last_host>250000000):raise ValueError('stale/reversed receipt: explicit new session required')
            q=basis(values[3:7]);norm=math.sqrt(sum(v*v for v in q))
            if abs(norm-1)>.05:raise ValueError('invalid quaternion norm')
            q=[v/norm for v in q]
            if self.history and sum(a*b for a,b in zip(q,self.history[-1][0]))<0:q=[-v for v in q]
            if self.origin is None:self.origin=timestamp_raw
            ms=(timestamp_raw-self.origin)/1000000  # explicit G2/G3 ns-scale replay hypothesis
            if ms>=2**24-1000:
                self.origin=timestamp_raw;ms=0;self.history=[]  # publisher float-time epoch policy
            self.last_ts=timestamp_raw;self.last_host=host_ns
            self.history.append((q,ms));self.history=self.history[-3:]
            history=list(reversed(self.history))
            while len(history)<3:history.append(history[-1])
            packet=[]
            for quat,_ in history:packet.extend([quat[1],quat[2],quat[3],quat[0]])
            packet.extend([t for _,t in history]);packet.append(0)
            self.active=True
            return list(struct.unpack('<16f',struct.pack('<16f',*packet)))
        except (ValueError,TypeError,OverflowError):
            self.pause()
            # No implicit reconnect after a fault; begin_session is required.
            self.closed=True
            raise


def replay(records):
    """Validate private capture offline; return aggregates only, never adapted poses."""
    from .candidate import SHA256
    contract=next((r for r in records if r.get('kind')=='contract'),{})
    firmware=next((r for r in records if r.get('kind')=='firmware_query'),{})
    if contract.get('model')!='pro2' or contract.get('library_sha256')!=SHA256 or firmware.get('status')!=0 or firmware.get('version')!='30.0.00.002_20260804':
        raise ValueError('replay candidate scope mismatch')
    cleanup=[r for r in records if r.get('kind')=='cleanup']
    if len(cleanup)!=1 or cleanup[0].get('errors')!=[] or cleanup[0].get('queue_dropped')!=0:raise ValueError('unclean capture')
    adapter=ReplayAdapter(replay_only=True);count=0;last_ms=None;maximum_norm_error=0
    try:
        for row in records:
            if row.get('kind')!='pose':continue
            if row.get('issues'):raise ValueError('capture validation issue')
            packet=adapter.push(row['sdk_values'],row['timestamp_raw'],row['host_monotonic_ns'])
            if len(packet)!=16 or not all(math.isfinite(v) for v in packet):raise ValueError('packet shape')
            if last_ms is not None and packet[12]<=last_ms:raise ValueError('unexpected epoch/reset in short replay')
            last_ms=packet[12];maximum_norm_error=max(maximum_norm_error,abs(sum(v*v for v in packet[:4])-1));count+=1
    finally:adapter.close()
    if not count:raise ValueError('empty replay')
    return dict(samples=count,last_relative_ms=last_ms,maximum_output_norm_squared_error=maximum_norm_error,
                teardown_closed=adapter.closed,teardown_active=adapter.active,published_to_ipc=False,
                mapping='H1 offline candidate only',timestamp_scale='assumed 1e9 ticks/s for replay',optical_correction_applied=False)
