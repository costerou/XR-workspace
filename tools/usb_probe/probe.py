"""What XRLinuxDriver moves over USB, per endpoint, with and without the quiet driver (docs/usb-drops.md).

`python3 -m tools.usb_probe.probe --shim build/release/libxr-quiet-driver.so [--dof3] [--seconds 10]`

Runs the real driver against the real glasses, under the same isolation as a run, for calibration plus --seconds. It refuses
unless no XR run and no other driver is up. Prints one JSON line.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from tools.native_spike import survive, tracking

HERE = Path(__file__).resolve().parent


def busy():
    units = subprocess.run(['systemctl', '--user', 'list-units', 'viture-xr-*', '--no-legend', '--state=active,activating'],
                           capture_output=True, text=True).stdout.strip()
    return bool(units) or bool(tracking._other_drivers())


def rates(before, after, seconds):
    zero = [0, 0, 0, 0]
    return {ep: {'urb_per_s': round((now[1] - before.get(ep, zero)[1]) / seconds, 1),
                 'kB_per_s': round((now[2] - before.get(ep, zero)[2]) / seconds / 1000, 1), 'errors': now[3]}
            for ep, now in after.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--shim', type=Path)
    ap.add_argument('--dof3', action='store_true')
    ap.add_argument('--seconds', type=float, default=10)
    args = ap.parse_args(argv)
    if busy(): print('an XR run or a driver is up: not touching the glasses', file=sys.stderr); return 2
    try: tracking._check_usb_access()
    except ValueError as exc: print(exc, file=sys.stderr); return 2
    with tempfile.TemporaryDirectory() as tmp:
        count = Path(tmp) / 'usb_count.so'
        subprocess.run(['gcc', '-shared', '-fPIC', '-O2', '-o', count, HERE / 'usb_count.c', '-ldl', '-lpthread'], check=True)
        out = Path(tmp) / 'counts.txt'
        env = dict(os.environ, XR_USB_COUNT=str(out), LD_PRELOAD=' '.join(str(p) for p in ([args.shim.resolve()] if args.shim else []) + [count]))
        if args.dof3: env['XR_QUIET_DOF'] = '3'
        read = lambda: {l.split()[0]: list(map(int, l.split()[1:])) for l in out.read_text().splitlines()}
        offset = tracking.DRIVER_LOG.stat().st_size
        started = time.monotonic()
        child = subprocess.Popen(tracking._driver_argv(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True, env=env)
        result = {'dof3': args.dof3, 'shim': bool(args.shim)}
        try:
            while time.monotonic() - started < 45 and tracking.READY.encode() not in tracking.DRIVER_LOG.read_bytes()[offset:]:
                if child.poll() is not None: result['exited'] = child.returncode; print(json.dumps(result)); return 1
                time.sleep(0.2)
            time.sleep(2.2)
            before, at = read(), time.monotonic()
            time.sleep(args.seconds)
            after, seconds = read(), time.monotonic() - at
            result['endpoints'] = rates(before, after, seconds)
            result['six_dof_log'] = [line for line in tracking.DRIVER_LOG.read_bytes()[offset:].decode(errors='replace').splitlines() if 'quiet-driver' in line][:3]
        finally:
            try: os.kill(child.pid, signal.SIGTERM)
            except OSError: pass
            try: child.wait(5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
