"""Phase 0 baseline for the Rust rebuild: supervisor CPU with the overview open, key press to renderer frame, and the
resident memory of the driver, supervisor and renderer after a long run (docs/rust-rebuild-plan.md, section 4).

usage: python3 -m tools.native_spike.bench_baseline --build build/release [--seconds 600] [--press-every 2]
                                                   [--json PATH] [--live]

Offline: the three workloads below run at the same time, as in a run, and none touches the live compositor, renderer,
glasses or controller service.
  supervisor  bench_control's overview (real Supervisor loop, ViewControl, unified cursor sampler and HyprlandClient
              against the fake compositor, fake 120 Hz renderer) for --seconds, pressing the recenter key through the
              real bind helper every --press-every seconds. Key press -> the renderer reading the recenter frame.
  driver      luma_rt_bench (the driver's real ring pipeline, real fusion and seqlock, fed by a fake 1 kHz transport).
  renderer    the real xr-native-spike in capture_e2e_bench's loopback compositor (gl-dmabuf, 3 outputs, 120 Hz).
Each process is sampled once a second from /proc/PID/stat and /proc/PID/status, the fields pidstat -u -r reads (sysstat
is not installed here): %usr, %system, %CPU per interval, and VmRSS (with RssAnon / RssFile / RssShmem), VmHWM and VmSwap
when the window ends. The load average, memory pressure and swap in use are recorded beside them.

--live also samples, read only, any live xr-native-spike, runner (the supervisor), xr-luma-driver or Pro 2 bridge
processes found at the start. A live run can end at any time; those that do are reported as exited.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from .bench_control import summary

REPO = Path(__file__).resolve().parents[2]
TICK = os.sysconf('SC_CLK_TCK')
MEMORY = ('VmRSS', 'RssAnon', 'RssFile', 'RssShmem', 'VmHWM', 'VmSwap')
LIVE = {'renderer': lambda comm, cmd: comm == 'xr-native-spike',
        'supervisor': lambda comm, cmd: 'tools.xr_controller.runner' in cmd,
        'driver': lambda comm, cmd: comm == 'xr-luma-driver' or '--supervised-pro2-test' in cmd}


def read_stat(pid):
    """(utime, stime) in clock ticks, as pidstat reads them: all threads of the process, not its children."""
    with open(f'/proc/{pid}/stat') as f: fields = f.read().rsplit(')', 1)[1].split()
    return int(fields[11]), int(fields[12])


def read_memory(pid):
    """VmRSS, its parts, VmHWM and VmSwap in KiB. Under memory pressure the kernel swaps idle pages out and VmRSS falls
    below the working set; VmRSS + VmSwap is then the size the process would hold with memory to spare."""
    out = {}
    with open(f'/proc/{pid}/status') as f:
        for line in f:
            key, _, value = line.partition(':')
            if key in MEMORY: out[key] = int(value.split()[0])
    return out


def descendants(pid):
    found, todo = [], [pid]
    while todo:
        p = todo.pop()
        for task in os.listdir(f'/proc/{p}/task'):
            try:
                with open(f'/proc/{p}/task/{task}/children') as f: kids = [int(k) for k in f.read().split()]
            except OSError: continue
            found += kids; todo += kids
    return found


def comm(pid):
    with open(f'/proc/{pid}/comm') as f: return f.read().strip()


def cmdline(pid):
    with open(f'/proc/{pid}/cmdline', 'rb') as f: return f.read().replace(b'\0', b' ').decode(errors='replace')


class Sampler:
    """pidstat -u -r 1 for one process."""
    def __init__(self, pid):
        self.pid, self.usr, self.sys, self.cpu, self.memory, self.exited = pid, [], [], [], {}, False
        self.t, (self.u, self.s) = time.monotonic(), read_stat(pid)
        self.t0, self.u0, self.s0 = self.t, self.u, self.s

    def sample(self):
        if self.exited: return
        try:
            t, (u, s) = time.monotonic(), read_stat(self.pid)
        except (OSError, IndexError):
            self.exited = True; return
        dt = (t - self.t) * TICK
        self.usr.append((u - self.u) / dt * 100); self.sys.append((s - self.s) / dt * 100)
        self.cpu.append(self.usr[-1] + self.sys[-1])
        self.t, self.u, self.s = t, u, s

    def finish(self):
        if not self.exited:
            try: self.memory = read_memory(self.pid)
            except OSError: self.exited = True
        window = self.t - self.t0
        return {'pid': self.pid, 'exited': self.exited, 'window_s': round(window, 1),
                'cpu_pct': round((self.u - self.u0 + self.s - self.s0) / TICK / window * 100, 2) if window else None,
                'usr_pct': round(statistics.fmean(self.usr), 2) if self.usr else None,
                'system_pct': round(statistics.fmean(self.sys), 2) if self.sys else None,
                'cpu_pct_per_second': summary(self.cpu, 1, 2),
                **{key.lower() + '_kib': self.memory.get(key) for key in MEMORY},
                'vmrss_plus_swap_kib': self.memory['VmRSS'] + self.memory['VmSwap'] if self.memory else None}


def swap_used_mib():
    info = {}
    with open('/proc/meminfo') as f:
        for line in f:
            key, _, value = line.partition(':'); info[key] = int(value.split()[0])
    return (info['SwapTotal'] - info['SwapFree']) // 1024


def wait_for(find, proc, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None: raise RuntimeError(f'{proc.args[0]} exited with {proc.returncode} before starting')
        pid = find()
        if pid: return pid
        time.sleep(0.05)
    raise RuntimeError(f'{proc.args[0]}: process not found')


def renderer_child(bench):
    def find():
        for pid in descendants(bench.pid):
            try:
                if comm(pid) == 'xr-native-spike': return pid
            except OSError: pass
        return None
    return find


def live_processes(exclude):
    found = {}
    for entry in os.listdir('/proc'):
        if not entry.isdigit() or int(entry) in exclude: continue
        pid = int(entry)
        try: c, cmd = comm(pid), cmdline(pid)
        except OSError: continue
        for role, match in LIVE.items():
            if match(c, cmd): found.setdefault(role, []).append(pid)
    return found


def run(build, seconds, press_every, live):
    build = Path(build).resolve()
    driver_bin, capture_bin = build / 'driver' / 'bench' / 'luma_rt_bench', build / 'capture_e2e_bench'
    renderer_bin = build / 'xr-native-spike'
    for path in (driver_bin, capture_bin, renderer_bin):
        if not path.exists(): raise SystemExit(f'{path} is missing: cmake --build {build} --target '
                                               'luma_rt_bench capture_e2e_bench xr-native-spike')
    tmp = Path(tempfile.mkdtemp(prefix='bench-baseline-'))
    supervisor_json, renderer_log, driver_log = tmp / 'overview.json', tmp / 'renderer.txt', tmp / 'driver.txt'
    env = dict(os.environ, TMPDIR=os.environ.get('TMPDIR', '/tmp'))
    # A little longer than the window, so every process is still up when the window ends and its memory is read.
    tail = 5
    procs = {
        'supervisor': subprocess.Popen([sys.executable, '-m', 'tools.native_spike.bench_control', '--only', 'overview',
                                        '--overview-seconds', str(seconds + tail), '--press-every', str(press_every),
                                        '--json', str(supervisor_json)], cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL),
        'driver': subprocess.Popen([str(driver_bin), '--seconds', str(seconds + tail + 1)], env=env,
                                   stdin=subprocess.DEVNULL, stdout=driver_log.open('w'), stderr=subprocess.STDOUT),
        'renderer': subprocess.Popen([str(capture_bin), '--mode', 'gl-dmabuf', '--outputs', '3', '--seconds',
                                      str(seconds + tail + 1), '--warmup', '1', '--renderer', str(renderer_bin)], env=env,
                                     stdin=subprocess.DEVNULL, stdout=renderer_log.open('w'), stderr=subprocess.STDOUT)}
    try:
        pids = {'supervisor': procs['supervisor'].pid, 'driver': procs['driver'].pid,
                'renderer': wait_for(renderer_child(procs['renderer']), procs['renderer'])}
        samplers = {role: Sampler(pid) for role, pid in pids.items()}
        ours = {os.getpid(), *descendants(os.getpid())}  # the benches' own renderer is not a live one
        live_samplers = {f'{role}:{pid}': Sampler(pid) for role, found in (live_processes(ours) if live else {}).items()
                         for pid in found}
        load0, start = os.getloadavg(), time.monotonic()
        while (now := time.monotonic()) < start + seconds:
            time.sleep(min(1.0, start + seconds - now))
            for s in (*samplers.values(), *live_samplers.values()): s.sample()
        results = {role: s.finish() for role, s in samplers.items()}
        live_results = {name: s.finish() for name, s in live_samplers.items()}
        load1 = os.getloadavg()
        for role, proc in procs.items():
            try: proc.wait(timeout=60)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        overview = json.loads(supervisor_json.read_text())['overview'] if supervisor_json.exists() else {}
        driver_json = next((json.loads(line[5:]) for line in driver_log.read_text().splitlines()
                            if line.startswith('json ')), {})
    finally:
        for proc in procs.values():
            if proc.poll() is None: proc.kill(); proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)
    # The bench process spawns the bind helper itself (in a real run Hyprland does): that cost is not the supervisor's.
    spawn = overview.get('press_spawn_cpu_pct', 0.0)
    results['supervisor']['press_spawn_cpu_pct'] = spawn
    results['supervisor']['cpu_pct_without_press_spawns'] = round(results['supervisor']['cpu_pct'] - spawn, 2)
    return {'seconds': seconds, 'press_every_s': press_every, 'cpus': os.cpu_count(),
            'loadavg_start': [round(x, 2) for x in load0], 'loadavg_end': [round(x, 2) for x in load1],
            'memory_pressure_end': Path('/proc/pressure/memory').read_text().split('\n')[0],
            'swap_used_mib_end': swap_used_mib(),
            'processes': results,
            'key_press_to_frame_ms': overview.get('key_press_to_frame_ms'),
            'key_presses_unanswered': overview.get('key_presses_unanswered'),
            'overview': {k: overview.get(k) for k in ('supervisor_cpu_pct', 'loop_thread_cpu_pct', 'sampler_thread_cpu_pct',
                                                      'samples_per_s', 'move_to_frame_ms', 'sampler_errors')},
            'driver_bench': driver_json,
            'renderer_bench_exit': procs['renderer'].returncode, 'driver_bench_exit': procs['driver'].returncode,
            'live': live_results}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m tools.native_spike.bench_baseline', description=__doc__.split('\n')[0])
    ap.add_argument('--build', required=True, help='CMake build directory with luma_rt_bench, capture_e2e_bench, xr-native-spike')
    ap.add_argument('--seconds', type=float, default=600, help='window; memory is read when it ends (default 600)')
    ap.add_argument('--press-every', type=float, default=2.0, help='recenter key press period in seconds (default 2)')
    ap.add_argument('--json', type=Path, help='also write the results here')
    ap.add_argument('--live', action='store_true', help='also sample live XR processes, read only')
    args = ap.parse_args(argv)
    result = run(args.build, args.seconds, args.press_every, args.live)
    text = json.dumps(result, indent=2) + '\n'
    print(text, end='')
    if args.json: args.json.write_text(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
