"""One consented panel run per transient unit: verify pins, load plugin, optional session mode, run, unload.

Invoked as `python -I -m tools.xr_controller.runner --consent <path> --state-dir <dir>`.
Writes `<state-dir>/<run_id>.result.json` (0600, atomic) on every exit path once arguments parse. Both it and the
`.progress.json` beside it carry the start-up timeline (`startup`: tools/native_spike/startup.py).
"""
import time
STARTED_NS = time.monotonic_ns()  # before the imports below: when this process reached Python (startup.py `runner`)

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from tools.native_spike import faults as run_faults, monitor_rules, startup
from tools.native_spike.hyprland import HyprlandClient
from tools.native_spike.ipc import TOKEN, private_dir, read_private_json
from tools.native_spike.managed import run_managed
from tools.native_spike.panel_consent import LUMA_PIN, load_panel_consent
from tools.native_spike.profiles import ProfileStore, log, profiles_path
from tools.xr_controller import renderer_switch
from tools.xr_controller.install import load_installed, tracking_driver_path, verify_pinned

from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME as PLUGIN_NAME, ownership_plugin_count

NOTIFY_SEND = '/usr/bin/notify-send'


def _run_id(raw):
    run_id = raw.get('run_id') if isinstance(raw, dict) else None
    return run_id if isinstance(run_id, str) and TOKEN.fullmatch(run_id) and len(run_id) <= 80 else None


def _write_result(state_dir, name, result):
    fd = private_dir(state_dir, create=True)
    try:
        tmp = f'.{name}.{secrets.token_hex(8)}.tmp'
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(result, sort_keys=True).encode()); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try: os.unlink(tmp, dir_fd=fd)
            except OSError: pass
            raise
        os.fsync(fd)
    finally:
        os.close(fd)


def _toast(body):
    """A desktop notification, best-effort, and the same line in the run's journal. Never affects the run."""
    print(f'xr-workspace: {body}', file=sys.stderr, flush=True)
    try: subprocess.run([NOTIFY_SEND, '--app-name=XR Workspace', 'XR Workspace', body], stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.SubprocessError): pass


def execute_pro2(consent_path, raw, progress=None):
    """A Pro 2 panel session: its own route (pro2_route.py), a pinned bundle with no installed.json or tracking.py driver."""
    from .pro2_route import execute as execute_route
    result = {'run_id': _run_id(raw), 'phase': 'run', 'complete': False, 'renderer_returncode': None, 'remaining': None, 'error': None}
    try:
        startup.mark('verify')  # its bundle and ABI (pro2_route.execute); pro2_test.run_session marks the rest
        if progress and result['run_id']: progress(result['run_id'], 'tracking')
        result.update(execute_route(consent_path, Path(consent_path).parent))
    except BaseException as exc:  # recorded, never re-raised: the result file is written on every exit
        result['error'] = str(exc) or type(exc).__name__
    if not result['error']: result['phase'] = 'done'
    return result


def execute(consent_path, data_home, progress=None):
    try:
        first = read_private_json(consent_path)
        if isinstance(first, dict) and first.get('model') == 'pro2': return execute_pro2(consent_path, first, progress)
    except (OSError, ValueError): pass  # the path below reports it, in its own words
    result = {'run_id': None, 'phase': 'verify', 'complete': False,
              'renderer_returncode': None, 'remaining': None, 'error': None}
    errors, client, plugin, attempted = [], None, None, False
    faults = run_faults.Faults()
    managed_started, run = False, None
    runtime, consent, watch = None, None, None
    startup.mark('verify')
    try:
        # The consent names its own scope; the second read re-checks it against the first.
        raw = read_private_json(consent_path)
        result['run_id'] = _run_id(raw)
        if not isinstance(raw, dict): raise ValueError('invalid consent fields')
        consent = load_panel_consent(consent_path, raw.get('instance'), raw.get('output'))
        if consent['run_id'] != result['run_id']: raise ValueError('consent changed while reading')
        installed = load_installed(data_home)
        for key in ('plugin', LUMA_PIN):
            if key == LUMA_PIN and key not in consent: continue
            if consent[key] != installed.get(key): raise ValueError(f'consent {key} is not the pinned install')
            verify_pinned(installed[key])
        # The C++ renderer, or the Rust one when the renderer setting says so (renderer_switch.py); never the other instead.
        renderer = renderer_switch.start_renderer(consent['renderer'], installed, data_home)
        plugin = installed['plugin']['path']
        if progress: progress(result['run_id'], 'verify')
        result['phase'] = 'plugin'
        if not os.environ.get('XDG_RUNTIME_DIR'): raise ValueError('XDG_RUNTIME_DIR not set')
        runtime = Path(os.environ['XDG_RUNTIME_DIR'])
        client = HyprlandClient(consent['instance'], runtime)
        monitor_rules.refuse_start(client)  # before the wait for tracking: up to 120 seconds to hear nothing can start
        if consent['tracking'] is not False:
            from tools.native_spike import tracking
            result['phase'] = 'tracking'
            startup.mark('tracking')
            if progress: progress(result['run_id'], 'tracking')
            if consent['tracking'] == tracking.LUMA:
                try: tracking.ensure_ready_luma(runtime, installed[LUMA_PIN]['path'])
                except ValueError as exc:  # nothing has moved yet: no plugin, no desktops, so the run just ends here
                    _toast(str(exc))
                    raise
                watch = tracking.LumaWatch(runtime, installed[LUMA_PIN]['path'], _toast)
                watch.start()
            else:
                binary = tracking_driver_path(consent.get('tracking_driver', tracking.XRDRIVER), data_home)
                log(f'head tracking driver: {binary}')
                tracking.ensure_ready(runtime, binary=binary)
        result['phase'] = 'plugin'
        startup.mark('plugin')
        if progress: progress(result['run_id'], 'plugin')
        # Other plugins (e.g. Omarchy's hyprbars) may stay loaded; only a broker of ours already present blocks.
        if ownership_plugin_count(client.plugins_loaded()): raise ValueError('ownership plugin already loaded')
        verify_pinned(installed['plugin'])
        attempted = True
        client.plugin_load(plugin, data_home=data_home)
        if ownership_plugin_count(client.plugins_loaded()) != 1: raise ValueError('plugin load not confirmed by readback')
        startup.mark('desktops')
        if progress: progress(result['run_id'], 'desktops')  # the mode, the layout and the run: the desktops and the picture
        if consent['mode_switch'] is not False:
            result['phase'] = 'mode'
            client.apply_session_mode(consent['output'])  # raises unless 1920x1200@~120 is read back
        result['phase'] = 'layout'
        client.park_presentation(consent['output'])
        result['phase'] = 'run'
        verify_pinned(renderer)
        managed_started = True
        run = run_managed(client, consent, runtime, desktops=consent['desktops'],
                          store=ProfileStore(profiles_path(), consent['model']), renderer=renderer['path'], faults=faults)
        result.update(complete=run['complete'], renderer_returncode=run['renderer_returncode'], remaining=run['remaining'])
        if run.get('stop_reason'): result['stop_reason'] = run['stop_reason']
        if run.get('holds'): result['usb_holds'] = run['holds']  # survive.py: drops this run outlived, or waited out
        if run.get('reasons'): result['recovery'] = run['reasons']  # `remaining` lists the same recovery's steps
    except BaseException as exc:  # recorded, never re-raised: the result file is written on every exit
        errors.append(str(exc) or type(exc).__name__)
        if isinstance(exc, Exception): faults.record(run_faults.RUN, exc, fatal=True)  # (the rest is Stop: nothing went wrong)
    finally:
        if watch is not None: watch.finish()
        # A saved broker epoch is required for explicit recovery. A return/exception
        # from run_managed does not by itself prove that recovery completed.
        preserve = managed_started and (
            (run is not None and run.get('complete') is not True) or
            (run is None and (runtime / 'xr-workspace-spike' / consent['run_id'] / 'journal.json').exists()))
        if preserve:
            why = (run or {}).get('reasons')
            errors.append('ownership plugin retained for explicit recovery' + (': ' + '; '.join(why) if why else ''))
        if attempted and not preserve:
            if not errors: result['phase'] = 'teardown'
            try:
                # Only ever the pinned path this runner asked to load (also after an unconfirmed load). Its own pin, not
                # installed.json: an install during the session must not leave this plugin behind with a clean journal,
                # where no Recover is offered and every start refuses with "already loaded".
                client.plugin_unload(plugin, data_home=data_home, recovery_pin=installed['plugin'])
                if PLUGIN_NAME in client.plugins_loaded(): raise ValueError('plugin still loaded after unload')
            except Exception as exc:
                errors.append('unload: ' + (str(exc) or type(exc).__name__))
                faults.record(run_faults.RUN, exc, fatal=True)
    if not errors: result['phase'] = 'done'
    result['error'] = '; '.join(errors) or None
    if faults.report(): result['faults'] = faults.report()
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog='tools.xr_controller.runner')
    ap.add_argument('--consent', required=True, type=Path)
    ap.add_argument('--state-dir', required=True, type=Path)
    args = ap.parse_args(argv)
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    startup.reset()
    confirmed = startup.written_ns(args.consent)  # the controller writes the consent as Start is confirmed
    if confirmed is not None and confirmed <= STARTED_NS: startup.mark('confirmed', confirmed)
    startup.mark('runner', STARTED_NS)
    def progress(run_id, phase):
        _write_result(args.state_dir, run_id+'.progress.json', dict(run_id=run_id, phase=phase, startup=startup.summary(startup.marks())))
    result = execute(args.consent, data_home, progress=progress)
    result['startup'] = startup.summary(startup.marks())
    try:
        _write_result(args.state_dir, f"{result['run_id'] or 'invalid'}.result.json", result)
        if result['run_id']:
            (args.state_dir/(result['run_id']+'.progress.json')).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        print(f'result not written: {exc}', file=sys.stderr); return 2
    if result['error']: print(f"blocked: {result['error']}", file=sys.stderr)
    ok = result['phase'] == 'done' and result['complete'] is True and result['renderer_returncode'] == 0
    return 0 if ok else 2


if __name__ == '__main__':
    sys.exit(main())
