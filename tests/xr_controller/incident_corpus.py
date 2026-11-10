"""Incident corpus for the Rust rebuild (docs/rust-rebuild-plan.md, RR-55: incident monitor and on-incident hook).

One golden file, rust/corpus/incidents/scenarios.json, made only by this module from the shipped Python IncidentMonitor.
Each scenario is a flat list of events (an observation, a pass of the monitor at a time, a change to the fake host, a
controller restart) and what the monitor left behind: the report files byte for byte, its .monitor-state, the toasts,
the reports handed to the on-incident hook and its journal lines. The Rust monitor replays the same events against
the same fake host (crates/xr-workspace/src/incident/corpus_tests.rs) and must leave the same bytes.

The fake host is the one in test_incident_monitor.py, as data: every field is JSON, so both sides read it alike.

Regenerate (after a deliberate change to the monitor or the reports) with:

    TMPDIR=/tmp /usr/bin/python3 -m tests.xr_controller.incident_corpus --write

test_incident_corpus.py fails when the file and the Python code disagree.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.xr_controller.incident_monitor import CORE_WAIT_S, IncidentMonitor  # noqa: E402
from tools.xr_controller.incident_report import Reports, Scrubber  # noqa: E402

FILE = ROOT / 'rust' / 'corpus' / 'incidents' / 'scenarios.json'
REPORTS = '<reports>'  # the temporary incidents folder, in anything that names it

RUN = '2298c7a18df736b9b6c4bde5b5877031'
OTHER = '549bcebd15b354c2c168fabd58cc1e0c'
PIN = '/home/testuser/.local/share/viture-workspace/bin/f0de154899b9/'
CONSENT = dict(run_id=RUN, instance='sig', output='DP-2', recovery_output='DP-1', tracking='xrlinuxdriver', model='luma-ultra',
               renderer=dict(path=PIN + 'xr-native-spike', sha256='a' * 64), plugin=dict(path=PIN + 'libxr-ownership.so', sha256='b' * 64))
DP2 = dict(name='DP-2', width=1920, height=1200, refresh=120.0, dpms=True)
DP1 = dict(name='DP-1', width=5120, height=1440, refresh=240.0, dpms=True)
GOOD = dict(presented_hz=120.0, missed=0, capture_path='dmabuf', tracking=True, capture_failure_run=0)
CLEAN = dict(run_id=RUN, phase='done', complete=True, renderer_returncode=0, remaining=[], error=None)
T0 = 1793750000.0
UNIT = f'viture-xr-{RUN}.service'
TZ = 'America/New_York'  # the kernel and journal stamps; baked into the events, so the replay never reads a zone

TIMING = json.dumps({'kind': 'generated_pattern_presentation_timing', 'presented': 510042, 'discarded': 1, 'abandoned': 1,
                     'submitted': 510044, 'interval_count': 510041, 'interval_mean_ns': 8421000.5, 'interval_max_ns': 91000000,
                     'observation_duration_ns': 4295000000000, 'interval_histogram': [0, 1], 'clock_id': 1})
CAPTURE = json.dumps({'kind': 'managed_output_capture', 'outputs': [{'name': 'XR-1', 'frames': 209893, 'failures': 0, 'reallocations': 1,
                      'path': 'dmabuf', 'width': 1920}], 'tracking': {'feed': True, 'tracking': True, 'samples': 900, 'lost_events': 0}})


def stamp(at):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(at)) + time.strftime('%z', time.localtime(at))[:3] + ':00'


def initial_host():
    return dict(boot='boot-1', consents={RUN: CONSENT, OTHER: dict(CONSENT, run_id=OTHER)}, results={}, recoveries={}, kernel=[],
                driver='', drm_state='connected', ports=['5-2.1', '5-2.3'], hypr_pid=1690, hypr_up=True, driver_up=True, cores=[],
                journals={}, broken=[])


class Host:
    """The fake host of test_incident_monitor.py, reading a JSON dict."""
    def __init__(self, fields): self.f = fields

    def _maybe(self, name):
        if name in self.f['broken']: raise OSError(f'{name} broke')

    def boot_id(self): return self.f['boot']
    def consent(self, run_id): self._maybe('consent'); return self.f['consents'].get(run_id)
    def result(self, run_id): self._maybe('result'); return self.f['results'].get(run_id)
    def recovery(self, run_id): self._maybe('recovery'); return self.f['recoveries'].get(run_id)
    def kernel_start(self): self._maybe('kernel'); return len(self.f['kernel'])
    def kernel_read(self, cursor): self._maybe('kernel'); return self.f['kernel'][cursor:], len(self.f['kernel'])
    def kernel_around(self, first, last): self._maybe('kernel'); return list(self.f['kernel'])
    def journal(self, unit, lines): self._maybe('journal'); return self.f['journals'].get(unit, [])[-lines:]
    def driver_start(self): self._maybe('driver'); return len(self.f['driver'])
    def driver_read(self, offset): self._maybe('driver'); return self.f['driver'][offset:], len(self.f['driver'])
    def driver_tail(self, lines): self._maybe('driver'); return self.f['driver'].splitlines()[-lines:]
    def drm(self, output): self._maybe('drm'); return self.f['drm_state']
    def usb_ports(self): self._maybe('usb'); return set(self.f['ports'])
    def hyprland_pid(self, instance): return self.f['hypr_pid']
    def alive(self, pid): return self.f['hypr_up'] if pid == self.f['hypr_pid'] else None
    def driver_alive(self): return self.f['driver_up']
    def tracking_driver(self): return 'luma'
    def source(self): return dict(sha='13c8d5e' + '0' * 33, dirty=False)

    def core_dumps(self, since, exes, pids):
        self._maybe('core')
        return [c for c in self.f['cores'] if c['exe_path'] in exes or c['pid'] in pids]


class Script:
    """Builds a scenario's events the way test_incident_monitor.Case drives the monitor."""
    def __init__(self):
        self.now, self.events, self.host = T0, [], initial_host()

    def set(self, **fields):
        for name, value in fields.items():
            self.host[name] = copy.deepcopy(value)
            self.events.append(dict(op='set', field=name, value=copy.deepcopy(value)))

    def result(self, **result): self.set(results=dict(self.host['results'], **{RUN: dict(CLEAN, **result)}))
    def journal(self, lines): self.set(journals=dict(self.host['journals'], **{UNIT: lines}))

    def drop(self, n=42):
        at = stamp(self.now)
        self.set(kernel=self.host['kernel'] + [f'{at} h kernel: usb 5-2: USB disconnect, device number {n}',
                                               f'{at} h kernel: usb 5-2.1: USB disconnect, device number {n + 1}'])

    def fresh(self): self.events.append(dict(op='fresh'))

    def tick(self, state='running', run_id=RUN, seconds=1, status=GOOD, **over):
        for _ in range(seconds):
            o = dict(dict(state=state, run_id=run_id, stop_requested=None, monitors=[DP2, DP1], plugins=1, status=status, instance='sig'), **over)
            o['at'] = self.now
            self.events.append(dict(op='observe', observation=o))
            self.events.append(dict(op='step', now=self.now))
            self.now += 1.0

    def idle(self, seconds=1, **over): self.tick('idle', None, seconds, status=None, **over)

    def session(self, seconds=20, run_id=RUN):
        self.tick('starting', run_id, 3, status=None)
        self.tick('running', run_id, seconds)


def core(pid=4242, exe=PIN + 'xr-native-spike'):
    return dict(exe_path=exe, exe=Path(exe).name, pid=pid, signal='SIGSEGV', frames=['#0  0x00007fa8 _ZN2xr12Presentation4drawEv (xr-native-spike + 0x10)'])


# --- the scenarios ----------------------------------------------------------------------------------------------------

def renderer_crash(s):
    """The gate: an injected renderer SIGSEGV. The report waits for the core dump, then names the frame and the exit report."""
    s.session()
    s.journal([f'{stamp(s.now)} h python3[1]: xr-workspace: run: renderer started',
               f'{stamp(s.now)} h python3[1]: {TIMING}', f'{stamp(s.now)} h python3[1]: {CAPTURE}',
               f'{stamp(s.now)} h systemd[1]: {UNIT}: Main process exited, code=dumped, status=11/SEGV',
               f"{stamp(s.now)} h systemd[1]: {UNIT}: Failed with result 'core-dump'."])
    s.result(renderer_returncode=-11, error='renderer exited with signal 11')
    s.idle(10)
    s.set(cores=[core()])
    s.idle(30)


def crash_without_core(s):
    """A crash whose core dump never comes is not held for ever."""
    s.session()
    s.result(renderer_returncode=-11)
    s.idle(int(CORE_WAIT_S) + 8)


def usb_drop_ends_session(s):
    """2026-11-03 19:18: the hub dropped twice, the run exited 1."""
    s.session()
    s.set(driver=s.host['driver'] + time.strftime('%Y-%m-%d %H:%M:%S.100 Retrying driver connection in 1 second\n', time.localtime(s.now)))
    s.drop()
    s.tick(seconds=7)
    s.drop(46)
    s.set(drm_state='disconnected')
    s.tick(seconds=1, status=None)
    s.result(renderer_returncode=1)
    s.journal(['2026-11-03T19:18:40-05:00 h python3[1]: dispatch: closed, removed or disconnected',
               f'2026-11-03T19:18:41-05:00 h systemd[1]: {UNIT}: Main process exited, code=exited, status=2/INVALIDARGUMENT',
               f"2026-11-03T19:18:41-05:00 h systemd[1]: {UNIT}: Failed with result 'exit-code'."])
    s.idle(60)


def drop_survived_then_stop(s):
    """A drop the session survives is reported live; the Stop after it amends that report."""
    s.session()
    s.drop()
    s.tick(seconds=40)
    s.tick('stopping', stop_requested=RUN, seconds=2, status=None)
    s.result()
    s.idle(30)


def killed_then_failed_recover(s):
    """A killed unit, then a Recover that comes back to recovery-needed with its reasons."""
    s.session()
    s.tick('recovery-needed', status=None, seconds=30)
    s.tick('confirming', status=None, seconds=2)
    s.tick('starting', status=None, seconds=3)
    step = '790c77a9-bb02-4d53-9c96-3c70f62bb571'
    s.set(recoveries={RUN: dict(remaining=[step], reasons=['XR-1: the ownership plugin refused to remove it (state blocked)'])})
    s.result(phase='run', complete=False, remaining=[step], error='ownership plugin retained for explicit recovery')
    s.tick('recovery-needed', status=None, seconds=30)


def hyprland_crash(s):
    """The compositor goes: the run's Hyprland is gone and the session ends with it."""
    s.session()
    s.set(hypr_up=False, drm_state='disconnected')
    s.tick(seconds=2, status=None)
    s.result(renderer_returncode=-15, error='compositor exited')
    s.idle(int(CORE_WAIT_S) + 8)


def mode_switch_and_lost_video(s):
    """The glasses' output changes mode, then goes dark."""
    s.session()
    s.tick(seconds=3, monitors=[dict(DP2, refresh=60.0), DP1])
    s.tick(seconds=40, monitors=[dict(DP2, refresh=60.0), DP1])
    s.set(drm_state='disconnected')
    s.tick(seconds=40, monitors=[DP1], status=dict(GOOD, presented_hz=0.0))
    s.tick('stopping', stop_requested=RUN, seconds=2, status=None)
    s.result()
    s.idle(10)


def rate_limited(s):
    """Three runs that fail alike: the later ones are counted, not toasted, and the count reaches the next report."""
    for run_id in (RUN, OTHER, RUN):
        s.session(run_id=run_id)
        s.set(results=dict(s.host['results'], **{run_id: dict(CLEAN, run_id=run_id, renderer_returncode=3)}))
        s.idle(30)
        s.set(results={})
        s.now += 200.0


def controller_restart_live(s):
    """A controller restart in a live session is no incident; the saved watch carries on."""
    s.session()
    s.fresh()
    s.tick(seconds=10)
    s.tick('stopping', stop_requested=RUN, seconds=2, status=None)
    s.result()
    s.idle(10)


def controller_restart_after_crash(s):
    """The controller comes back to a run that crashed while it was away."""
    s.session()
    s.fresh()
    s.result(renderer_returncode=-11)
    s.set(cores=[core()])
    s.idle(30)


def clean_session(s):
    """Start, run, Stop, unplug: nothing to report."""
    s.session()
    s.tick('stopping', stop_requested=RUN, seconds=2, status=None)
    s.result()
    s.drop()
    s.idle(60)


def broken_sources(s):
    """Sources that fail cost their evidence, never the report."""
    s.set(broken=['journal', 'core'])
    s.session()
    s.result(renderer_returncode=3)
    s.idle(30)


SCENARIOS = (renderer_crash, crash_without_core, usb_drop_ends_session, drop_survived_then_stop, killed_then_failed_recover,
             hyprland_crash, mode_switch_and_lost_video, rate_limited, controller_restart_live, controller_restart_after_crash,
             clean_session, broken_sources)


# --- the replay -------------------------------------------------------------------------------------------------------

def replay(events):
    """What the Python monitor leaves after `events`: the same function as the Rust corpus test."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        directory = root / 'incidents'
        host = Host(initial_host())
        toasts, handed, said = [], [], []
        scrub = Scrubber(home='/home/testuser', user='testuser', host='testhost')
        clock = [T0]

        def fresh():
            return IncidentMonitor(host, Reports(directory), notify=toasts.append, hand_on=lambda p: handed.append(p.name), scrub=scrub,
                                   clock=lambda: clock[0])

        with mock.patch('tools.xr_controller.incident_monitor.log', side_effect=said.append):
            monitor = fresh()
            for event in events:
                if event['op'] == 'set': host.f[event['field']] = copy.deepcopy(event['value'])
                elif event['op'] == 'fresh': monitor = fresh()
                elif event['op'] == 'observe': monitor.observe(event['observation'])
                else:
                    clock[0] = event['now']
                    monitor.step(event['now'])
        files = {}
        if directory.exists():
            for path in sorted(directory.iterdir()):
                files[path.name] = path.read_text().replace(str(directory), REPORTS)
        return dict(files=files, toasts=toasts, handed=handed,
                    log=[line for line in said if line.startswith(('incident:', 'mode-switch:'))])


def corpus():
    scenarios = []
    for build in SCENARIOS:
        s = Script()
        build(s)
        scenarios.append(dict(name=build.__name__, about=build.__doc__, events=s.events, expect=replay(s.events)))
    return dict(about=__doc__.split('\n\n')[0], t0=T0, scenarios=scenarios)


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: incident_corpus.py [--write]')
    text = dumps(corpus())
    if argv:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(text)
        print(f'wrote {FILE.relative_to(ROOT)}')
    else:
        sys.stdout.write(text)


os.environ['TZ'] = TZ
time.tzset()

if __name__ == '__main__':
    main(sys.argv[1:])
