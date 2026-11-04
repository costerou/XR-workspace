"""Offline production-filter measurements. Requires numpy and Pillow; never opens devices.

python measure.py --trace build/stabilization_trace --out DIRECTORY axes.csv replay-poses.bin ...
Binary logs are xr-luma-driver --replay --pose-log (five doubles: ns,w,x,y,z).
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LEVELS = ['Off', 'Light', 'Medium', 'Strong']


def mul(a, b):
    av, bv = a[..., :3], b[..., :3]
    return np.concatenate((a[..., 3:4]*bv+b[..., 3:4]*av+np.cross(av, bv),
                           a[..., 3:4]*b[..., 3:4]-np.sum(av*bv, axis=-1, keepdims=True)), axis=-1)


def conj(q):
    return q*np.array([-1, -1, -1, 1])


def rotvec(q):
    q = q*np.where(q[..., 3:4]<0, -1, 1)
    n = np.linalg.norm(q[..., :3], axis=-1, keepdims=True)
    return q[..., :3]*np.divide(2*np.arctan2(n, q[..., 3:4]), n, out=np.full_like(n, 2.), where=n>1e-12)*180/np.pi


def distance(a, b):
    return np.linalg.norm(rotvec(mul(conj(a), b)), axis=-1)


def run(trace, t, q):
    data = io.StringIO(); np.savetxt(data, np.column_stack((t*1000, q)), fmt='%.12g')
    result = subprocess.run([str(trace)], input=data.getvalue(), text=True, capture_output=True, check=True)
    rows = np.loadtxt(io.StringIO(result.stdout))
    return rows[:, 1:].reshape(-1, 4, 5)


def load(path):
    if path.suffix == '.csv':
        a = np.loadtxt(path, delimiter=',', skiprows=1); t, q = a[:, 1]/1000, a[:, 2:6]
    else:
        a = np.fromfile(path, dtype='<f8').reshape(-1, 5); t, q = a[:, 0]/1e9, a[:, [2, 3, 4, 1]]
    q /= np.linalg.norm(q, axis=1)[:, None]
    # Select the latest available pose at each 120 Hz display read. Do not invent
    # interpolated samples across a drop or join separate recording sessions.
    indices = np.unique(np.searchsorted(t, np.arange(t[0], t[-1], 1/120), side='right')-1)
    return t[indices]-t[0], q[indices], len(t)


def measure(trace, path):
    t, q, original_count = load(path); out = run(trace, t, q)
    filtered = out[:, :, :4]
    speed = np.r_[0, distance(q[1:], q[:-1])/np.maximum(np.diff(t), 1e-9)]
    # Only whole quiet one-second windows, selected from RAW data for every level:
    # 95th percentile speed below 3 deg/s, max below 8, with no timestamp gap.
    # Remove each window's linear trend in a common local rotation-vector frame.
    # This measures local jitter, not unknown true head orientation or yaw bias.
    sums = np.zeros(4); count = 0; windows = 0; example = None
    for start in range(1, int(t[-1])-1):
        idx = np.flatnonzero((t>=start)&(t<start+1))
        if len(idx)<110 or np.max(np.diff(t[idx]))>.025 or np.percentile(speed[idx],95)>=3 or max(speed[idx])>=8: continue
        local = rotvec(mul(conj(q[idx[0]]), filtered[idx]))
        design = np.column_stack((np.ones(len(idx)), t[idx]-t[idx[0]]))
        residual = local.reshape(len(idx), -1)-design@np.linalg.lstsq(design, local.reshape(len(idx), -1), rcond=None)[0]
        residual = residual.reshape(-1, 4, 3)
        sums += np.sum(residual**2, axis=(0, 2)); count += len(idx); windows += 1
        if example is None: example = (t[idx]-t[idx[0]], residual)
    rms = np.sqrt(sums/count) if count else np.full(4, np.nan)
    error = distance(filtered, q[:, None, :])
    bands = {}
    for target in [30,90,300]:
        take = (speed>=target*.8)&(speed<=target*1.2)
        values = error[take]/speed[take,None]*1000
        bands[str(target)] = {'samples':int(take.sum()), 'max_added_ms':np.max(values, axis=0).tolist() if len(values) else None}
    return {'file':str(path), 'seconds':float(t[-1]), 'source_samples':original_count, 'renderer_samples':len(t),
            'quiet_windows':windows, 'quiet_samples':count, 'jitter_deg_rms':rms.tolist() if count else None,
            'reduction_pct':((1-rms/rms[0])*100).tolist() if count else None, 'lag_bands':bands}, example


def synthetic(trace):
    results=[]
    for hz in [60,120,1000]:
        # Offset onset from sample boundaries; measure max, not mean or rounded xcorr.
        t=np.arange(0,1,1/hz)
        for speed in [30,90,300]:
            for onset in [.2001,.203,.207,.211]:
                a=speed*np.clip(t-onset,0,.3)*np.pi/180
                q=np.column_stack((np.sin(a/2)*.6, np.zeros_like(a), np.sin(a/2)*.8, np.cos(a/2)))
                out=run(trace,t,q)[:,:,:4]
                err=distance(out,q[:,None,:])/speed*1000
                results.append({'hz':hz,'speed':speed,'onset_s':onset,'max_added_ms':err.max(axis=0).tolist()})
    return results


def draw(path, results, synth):
    im=Image.new('RGB',(1400,990),'#101923'); d=ImageDraw.Draw(im)
    font='/usr/share/fonts/TTF/DejaVuSans.ttf'
    if not Path(font).exists(): font='/usr/share/fonts/dejavu/DejaVuSans.ttf'
    def f(size): return ImageFont.truetype(font,size)
    def text(x,y,s,size=22,color='#e8edf4'): d.text((x,y),s,font=f(size),fill=color)
    text(42,30,'Steadier when still. Fast when you turn.',32)
    text(42,80,'Measured offline with the production filter at 120 display updates per second.',21,'#abb9c9')
    colors=['#8b9aab','#52c7ea','#7ee0a8','#d0a6ff']
    for col,r in enumerate(results[:2]):
        x=42+col*690; text(x,140,'20-minute recording (quiet sections)' if 'usbmon' in r['file'] else 'Still-head recording',25)
        text(x,180,f"{r['quiet_windows']} quiet seconds • jitter in degrees RMS",19,'#abb9c9')
        rms=r['jitter_deg_rms']; maximum=max(rms)*1.12
        for i,(label,v) in enumerate(zip(LEVELS,rms)):
            y=230+i*72; text(x,y,label,22)
            w=390*v/maximum if maximum else 0
            d.rectangle((x+100,y,x+100+w,y+28),fill=colors[i])
            text(x+105+w,y,f'{v:.5f}°',18)
        text(x,535,'Less small movement: '+ ' / '.join(f'{v:.0f}%' for v in r['reduction_pct'][1:]),20)
    text(42,610,'Added turn delay — worst synthetic onset, including between display frames',25)
    for i,speed in enumerate([30,90,300]):
        x=42+i*450
        worst=np.max([r['max_added_ms'] for r in synth if r['speed']==speed],axis=0)
        text(x,660,f'{speed} degrees/second',23)
        for j,label in enumerate(LEVELS[1:],1): text(x,700+34*(j-1),f'{label}: {worst[j]:.3f} ms',21,colors[j])
    text(42,835,'Off: 0 ms. Target: under 2 ms. Clear turns pass through without smoothing.',22)
    text(42,887,'Jitter: quiet 1-second windows, linear trend removed. Real head ground truth is unavailable.',19,'#abb9c9')
    text(42,920,'These are filter-only measurements, not motion-to-photon latency. No live XR changes or install.',19,'#abb9c9')
    im.save(path)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--trace',type=Path,required=True); parser.add_argument('--out',type=Path,required=True); parser.add_argument('files',nargs='+',type=Path); args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    results=[]
    for path in args.files:
        r,_=measure(args.trace.resolve(),path);results.append(r);print(json.dumps(r),flush=True)
    synth=synthetic(args.trace.resolve())
    report={'levels':LEVELS,'strengths':[0,25,60,100],'real':results,'synthetic_turns':synth,
            'method':'120 Hz latest available input; quaternion distance; quiet windows: raw p95 speed <3 deg/s, max <8; per-window linear trend removed; no physical ground truth. Added error/speed is an equivalent delay, not a measured time shift.'}
    (args.out/'measurements.json').write_text(json.dumps(report,indent=2)+'\n')
    chosen=sorted([r for r in results if r['quiet_windows']],key=lambda r:('usbmon' not in r['file'],'stage' not in r['file']))
    draw(args.out/'before-after.png',chosen,synth)
    html='<html><body style="margin:0"><img style="width:100%;height:auto" src="'+str((args.out/'before-after.png').resolve())+'" alt="Stabilization before and after measurements"></body></html>'
    (args.out/'index.html').write_text(html)

if __name__=='__main__': main()
