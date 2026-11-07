"""One short supervised Pro 2 comparison, parent side: the same preflight, consent and launch as the first live test.

Run by the orchestrator, with Cody wearing the glasses and agreeing to this run. Stops by itself. Needs no sudo. Everything it
writes goes to a new private directory under ~/.local/state/viture-workspace/pro2-hil/. See docs/pro2/smooth-live-test.md.

The run checks two things: the picture stays level through left and right turns, and the run lasts its full time.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

HIL = Path.home() / '.local/state/viture-workspace/pro2-hil'
OUTPUT, FALLBACK = ('DP-2', 'VITURE'), ('DP-1', 'Odyssey G93SD')
PROVIDERS = ('xrDriver', 'xr-luma-driver', 'pro2-sdk-bridge')


def consent_for(run_id, instance, *, projection=44.5, pose_rate_hz=60, seconds=120):
    """Exactly one 1920x1080 desktop: the run keeps it (tools/pro2_runtime/scope.py fixed_desktops)."""
    consent = dict(schema=1, gate='pro2-supervised-v1', model='pro2', run_id=run_id, instance=instance, output=OUTPUT[0], recovery_output=FALLBACK[0],
                   seconds=seconds, projection=projection, parent_authorized=True, exclusive_topology=True, approximate_optics=True,
                   desktops=[dict(width=1920, height=1080, scale=1)])
    return {**consent, 'pose_rate_hz': pose_rate_hz} if pose_rate_hz != 60 else consent


def summarize(log_text, result):
    """What the run measured, for the report: aggregates from its status lines and the bridge's closing counts. No pose, no log text."""
    timing, status, guard = {}, {}, None
    for line in log_text.splitlines():
        if line.startswith('xr-workspace: run guard: '): guard = line.removeprefix('xr-workspace: run guard: ')[:300]
        if not line.startswith('{'): continue
        try: data = json.loads(line)
        except ValueError: continue
        if data.get('kind') == 'generated_pattern_presentation_timing': timing = data
        if data.get('kind') == 'managed_output_capture': status = data
    latency = status.get('latency') or {}
    prediction = latency.get('prediction') or {}
    tracking = status.get('tracking') or {}
    presented = timing.get('presented') or 0
    predicted = prediction.get('predicted_frames') or 0
    return dict(presented=presented, interval_histogram=timing.get('interval_histogram'), abandoned=timing.get('abandoned'),
                prediction_enabled=prediction.get('enabled'), predicted_frames=predicted, unpredicted_frames=prediction.get('unpredicted_frames'),
                predicted_share=round(predicted / presented, 3) if presented else 0.0, look_ahead_cfg=prediction.get('look_ahead_cfg'),
                smoothing=prediction.get('smoothing'), last_horizon_ms=prediction.get('last_horizon_ms'),
                read_to_present_p50_ms=(latency.get('read_to_present') or {}).get('p50_ms'), pose_age_p50_ms=(latency.get('pose_age_relative') or {}).get('p50_ms'),
                tracking_samples=tracking.get('samples'), lost_events=tracking.get('lost_events'), levelled_deg=tracking.get('levelled_deg'), fov_source=tracking.get('fov_source'), diagonal_fov_deg=tracking.get('diagonal_fov_deg'),
                desktops=[o.get('name') for o in status.get('outputs') or []], provider=(result or {}).get('provider'), complete=(result or {}).get('complete'),
                stop_reason=((result or {}).get('run') or {}).get('stop_reason'), guard_reason=guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--projection', type=float, default=44.5)
    parser.add_argument('--pose-rate', type=int, default=60, choices=(60, 90, 120, 240))
    parser.add_argument('--stop-after', type=int, default=90, help='seconds until this script stops the run')
    args = parser.parse_args()
    os.umask(0o077)
    bundle = args.bundle.absolute()
    subprocess.run([str(bundle / 'bin/pro2-supervised-test'), '--preflight'], check=True)
    instances = json.loads(subprocess.check_output(['hyprctl', 'instances', '-j'], text=True))
    assert len(instances) == 1, 'exactly one Hyprland instance'
    i = instances[0]
    env = dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE=i['instance'], WAYLAND_DISPLAY=i['wl_socket'], XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}')
    read = lambda command: subprocess.check_output(command, env=env, text=True)
    cue = lambda text, ms=9000: subprocess.run(['notify-send', '-u', 'critical', '-t', str(ms), 'Pro 2 XR test', text], env=env)
    monitors = json.loads(read(['hyprctl', 'monitors', '-j']))
    assert not any(m['name'].startswith('XR-') for m in monitors), 'an XR desktop is still there'
    assert not read(['systemctl', '--user', 'list-units', '--no-legend', 'viture-xr-*']).strip(), 'an XR run is active'
    assert 'no plugins loaded' in read(['hyprctl', 'plugin', 'list']), 'a compositor plugin is loaded'
    manifest = json.loads((bundle / 'manifest.json').read_text())
    version = json.loads(read(['hyprctl', 'version', '-j']))
    assert [version['version'], version['buildAquamarine']] == manifest['ownership_abi'], 'plugin built for another Hyprland'
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest, name
    assert hashlib.sha256(Path('/usr/lib/xr-driver/libglasses.so').read_bytes()).hexdigest() == manifest['sdk_sha256'], 'SDK changed'
    for process in Path('/proc').glob('[0-9]*'):
        try: assert (process / 'comm').read_text().strip() not in PROVIDERS, 'competing provider'
        except OSError: pass
    devices = []
    for device in Path('/sys/bus/usb/devices').glob('*'):
        try:
            if (device / 'idVendor').read_text().strip() == '35ca': devices.append(device)
        except OSError: pass
    assert len(devices) == 1, 'exactly one VITURE device'
    device = devices[0]
    assert (device / 'idProduct').read_text().strip() == '1301' and (device / 'product').read_text().strip() == 'VITURE PRO 2 XR Glasses'
    node = '/dev/bus/usb/' + (device / 'busnum').read_text().strip().zfill(3) + '/' + (device / 'devnum').read_text().strip().zfill(3)
    assert os.access(node, os.R_OK | os.W_OK), 'no access to the USB device'
    for name, model in (OUTPUT, FALLBACK):
        assert any(m['name'] == name and m['model'] == model for m in monitors), f'{name} is not {model}'
    run_id = 'pro2-supervised-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    state = HIL / run_id
    state.mkdir(mode=0o700)
    (state / 'display-before.json').write_text(json.dumps(monitors, indent=2))
    path = state / 'consent.json'
    path.write_text(json.dumps(consent_for(run_id, i['instance'], projection=args.projection, pose_rate_hz=args.pose_rate), indent=2))
    print('STATE', state, flush=True)
    cue('Starting in 30 seconds. Put the Pro 2 glasses on and sit facing forward. It stops by itself.', 28000)
    time.sleep(30)
    cue('Starting now. Look straight ahead for a moment, then turn slowly left and right and watch the desktop edges. It stops by itself. Take them off if anything feels wrong.')
    log_path = state / 'parent-launch.private.log'
    with log_path.open('x') as log:
        process = subprocess.Popen([str(bundle / 'bin/pro2-supervised-test'), '--consent', str(path), '--state-dir', str(state)], env=env, stdout=log, stderr=subprocess.STDOUT)
        print('CLI_PID', process.pid, flush=True)
        try: process.wait(timeout=args.stop_after)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):  # the run's time, or Ctrl+C: the same clean stop
            print('PARENT_NORMAL_STOP', flush=True); process.send_signal(signal.SIGTERM)
            try: process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                print('CLEANUP_NOT_FINISHED: no blind kill or reset; inspect the exact process', flush=True); raise
    print('CLI_EXIT', process.returncode, flush=True)
    (state / 'display-after.json').write_text(read(['hyprctl', 'monitors', '-j']))
    (state / 'plugins-after.txt').write_text(read(['hyprctl', 'plugin', 'list']))
    result_path = state / 'pro2-test-result.json'
    result = json.loads(result_path.read_text()) if result_path.exists() else None
    summary = summarize(log_path.read_text(), result)
    (state / 'measured.json').write_text(json.dumps(summary, indent=2))
    print('MEASURED', json.dumps(summary), flush=True)
    print('FINAL_STATE', state, flush=True)
    cue('Test finished. You can take the glasses off. Tell Coder Bot XR what you saw.', 15000)


if __name__ == '__main__': main()
