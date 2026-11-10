"""The Rust bridge (rust/crates/xr-pro2-bridge) against the C++ one it replaces, side by side.

Both run against the same synthetic SDK and a sysfs tree rebuilt from the real Pro 2 snapshot in
docs/pro2/fixtures/hardware-2026-11-04/usb.json: same exit code, same status events, same done
fields (all but the timing-dependent sample count and seconds), the same SDK call trace, and the
same feed as an independent C++ reader sees it. The production Rust binary must not link
libcrypto. The one intended difference is reconnect (D12): after ready, the Rust bridge waits out
a USB drop instead of ending the run, so those cases are checked on their own. Needs PRO2_BRIDGE_RUST_DIR (see tests/pro2_bridge.py); run via run.py.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import unittest
from tests.pro2_bridge import cpp_command

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SNAPSHOT = ROOT / 'docs/pro2/fixtures/hardware-2026-11-04/usb.json'
RUST = os.environ.get('PRO2_BRIDGE_RUST_DIR')
CASES = ('normal', 'null_create', 'unsupported_pid', 'unsupported_rate', 'wrong_family', 'initialize', 'start', 'register',
         'open', 'wrong_firmware', 'firmware_error', 'firmware_length', 'firmware_short_length', 'close', 'stop', 'shutdown',
         'queue_overflow', 'null_callback', 'duplicate', 'clock_reverse', 'nonfinite', 'bad_norm',
         'two_pose', 'ready_then_bad', 'callback_during_close', 'stdout_spoof')


def sysfs_from_snapshot(root):
    """The device directory and its interface as they were on the bench, plus a root hub to skip."""
    snapshot = json.loads(SNAPSHOT.read_text())
    device = root / Path(snapshot['sysfs']).name
    device.mkdir(parents=True)
    for name, value in snapshot['attributes'].items():
        if value is not None: (device / name).write_text(value + '\n')
    for interface in snapshot['interfaces']:
        (root / interface['name']).mkdir()
        for name, value in interface['attributes'].items():
            if value is not None: (root / interface['name'] / name).write_text(value + '\n')
    hub = root / 'usb1'; hub.mkdir()
    for name, value in dict(idVendor='1d6b', idProduct='0002', product='xHCI Host Controller', busnum='1', devnum='1').items():
        (hub / name).write_text(value + '\n')
    return device


@unittest.skipUnless(RUST, 'set PRO2_BRIDGE_RUST_DIR (python3 tests/pro2_bridge.py <dir>)')
class RustBridgeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for kind in ('user', 'ipc', 'net'):
            old = os.environ.get('PRO2_QA_PARENT_' + kind.upper())
            if old is None or os.readlink('/proc/self/ns/' + kind) == old:
                raise RuntimeError('Run via python tests/pro2_runtime_qa/run.py; fresh namespaces required')
        cls.tmp = tempfile.TemporaryDirectory(prefix='pro2-rust-parity-')
        cls.addClassCleanup(cls.tmp.cleanup)
        build = cls.build = Path(cls.tmp.name)
        cls.sdk = build / 'fixture.so'; cls.probe = build / 'probe'; cls.cpp = build / 'cpp-bridge'
        cls.rust = Path(RUST) / 'synthetic/xr-pro2-bridge'; cls.rust_production = Path(RUST) / 'production/xr-pro2-bridge'
        for cmd in (['cc', '-std=c11', '-shared', '-fPIC', '-pthread', str(HERE / 'sdk_fixture.c'), '-o', str(cls.sdk)],
                    ['c++', '-std=c++20', '-pthread', '-Ispike/include', str(HERE / 'probe.cpp'), 'spike/src/pose.cpp', '-o', str(cls.probe)],
                    cpp_command(cls.cpp)):
            subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True)
        cls.digest = hashlib.sha256(cls.sdk.read_bytes()).hexdigest()

    def run_bridge(self, binary, case='normal', extra=(), display=None, interrupt=False, action=None, sysfs=None, probe=False,
                   seconds='.25', env_extra=None):
        """action(device, usb, process): run on a timer 0.1 s in, for unplug and replug."""
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            folder = Path(tmp); usb = folder / 'sysfs'
            device = sysfs(usb) if sysfs else sysfs_from_snapshot(usb)
            prefix = str(folder / 'pose_')
            env = dict(os.environ, PRO2_QA_CASE=case, PRO2_QA_TRACE=str(folder / 'trace'), **(env_extra or {}))
            sdk_fd = os.open(self.sdk, os.O_RDONLY | os.O_CLOEXEC)
            status_fd = os.open(folder / 'status', os.O_WRONLY | os.O_CREAT, 0o600)
            fds = [sdk_fd, status_fd]; args = []
            if display is not None:
                read_end, write_end = os.pipe(); os.write(write_end, display); os.close(write_end)
                fds.append(read_end); args = ['--display-fd', str(read_end)]
            observer = subprocess.Popen([str(self.probe), prefix], stdout=subprocess.PIPE, stderr=subprocess.PIPE) if probe else None
            timer = None
            try:
                process = subprocess.Popen([str(binary), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd), '--status-fd', str(status_fd),
                                            '--prefix', prefix, '--projection', '44.5', '--seconds', seconds, '--fixture-root', str(usb),
                                            '--fixture-hash', self.digest, *args, *extra], pass_fds=fds, env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if interrupt or action:
                    def fire():
                        if interrupt: process.send_signal(signal.SIGTERM)
                        else: action(device, usb, process)
                    timer = threading.Timer(.1, fire); timer.start()
                stdout, stderr = process.communicate(timeout=8 + float(seconds))
            finally:
                if timer: timer.cancel(); timer.join()
                for fd in fds: os.close(fd)
                if process.poll() is None: process.kill(); process.communicate()
            observed = None
            if observer:
                data, _ = observer.communicate(timeout=8); observed = json.loads(data)
            trace = (folder / 'trace').read_text().splitlines() if (folder / 'trace').exists() else []
            records = [json.loads(line) for line in (folder / 'status').read_text().splitlines()]
            if case != 'stdout_spoof': self.assertEqual(stdout, b'', 'status goes to its own descriptor')
            self.assertFalse(list(folder.glob('pose_*')), 'private feed files left behind')
            return dict(code=process.returncode, records=records, trace=trace, observed=observed, stdout=stdout, stderr=stderr)

    def assertSame(self, cpp, rust, label):
        """Everything but the timing: samples and seconds vary run to run; gap buckets are compared as which ones are used."""
        def shape(result):
            records = []
            for r in result['records']:
                r = dict(r)
                if r['event'] == 'done':
                    r['samples'] = r['samples'] > 2 if r['ready'] else r['samples']
                    r['seconds'] = None
                    r['gap_ms_counts'] = [n > 0 for n in r['gap_ms_counts']]
                records.append(r)
            return dict(result, records=records, stderr=None)  # the Rust synthetic build reports wake_us there
        self.assertEqual(shape(cpp), shape(rust), label)

    def test_fixture_replay_matches_cpp_case_by_case(self):
        for case in CASES:
            with self.subTest(case=case):
                probe = case == 'normal'  # a reader of a run that fails early races its teardown
                cpp, rust = self.run_bridge(self.cpp, case, probe=probe), self.run_bridge(self.rust, case, probe=probe)
                self.assertSame(cpp, rust, case)
                self.assertTrue(rust['records'] and rust['records'][-1]['event'] == 'done', rust)

    def test_fixture_replay_normal_run_publishes_a_real_feed(self):
        rust = self.run_bridge(self.rust, probe=True)
        self.assertEqual(rust['code'], 0, rust)
        self.assertEqual([r['event'] for r in rust['records']], ['ready', 'done'])
        done = rust['records'][-1]
        self.assertEqual((done['error'], done['cleanup_errors'], done['pose_rate_hz']), ('', 0, 60))
        self.assertEqual(rust['trace'][:2], ['load', 'cap_pid'])
        self.assertEqual(rust['trace'][-4:], ['close', 'stop', 'shutdown', 'destroy'])
        self.assertEqual(rust['observed'], dict(seen=True, disabled_after=True, explicit_fov=True))
        # 16 ms fixture samples: every gap in the 13..18 ms bucket.
        self.assertEqual([n > 0 for n in done['gap_ms_counts']], [False, False, False, True, False, False])

    def test_callback_to_publish_p99_under_200_us(self):
        """D12: the callback wakes the owner through an eventfd, no 2 ms sleep. 240 Hz for 5 s, every sample timed
        from the callback to its publish (the synthetic build's wake_us stderr line)."""
        rust = self.run_bridge(self.rust, extra=('--pose-rate', '240'), seconds='5',
                               env_extra=dict(PRO2_QA_RATE='3', PRO2_QA_PERIOD_NS='4166667'))
        self.assertEqual(rust['code'], 0, rust)
        line = next(l for l in rust['stderr'].decode().splitlines() if l.startswith('wake_us '))
        print('\n' + line)
        wake = {k: float(v) for k, v in (field.split('=') for field in line.split()[1:])}
        self.assertGreater(wake['n'], 1000, line)
        self.assertLess(wake['p99'], 200, line)

    def test_fail_before_sdk_matches(self):
        def other_product(usb):
            device = sysfs_from_snapshot(usb); (device / 'idProduct').write_text('1104\n'); return device
        def two_devices(usb):
            device = sysfs_from_snapshot(usb); twin = usb / '1-6'; twin.mkdir()
            for f in device.iterdir():
                if f.is_file(): (twin / f.name).write_text(f.read_text())
            return device
        def empty_vendor(usb):
            device = sysfs_from_snapshot(usb); (device / 'idProduct').write_text(''); return device
        for label, sysfs, extra in (('mismatch', other_product, ()), ('two', two_devices, ()), ('unreadable', empty_vendor, ()),
                                    ('hash', None, ('--fixture-hash', '0' * 64))):
            with self.subTest(label=label):
                cpp, rust = self.run_bridge(self.cpp, sysfs=sysfs, extra=extra), self.run_bridge(self.rust, sysfs=sysfs, extra=extra)
                self.assertSame(cpp, rust, label)
                self.assertEqual(rust['trace'], [])
                self.assertNotEqual(rust['code'], 0)
        with self.subTest(label='missing-root'):
            self.assertEqual(self.run_missing_root(self.cpp), self.run_missing_root(self.rust))

    def run_missing_root(self, binary):
        """No sysfs tree at all: identity-filesystem, before the SDK. Its done line, seconds aside."""
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            folder = Path(tmp)
            sdk_fd = os.open(self.sdk, os.O_RDONLY); status_fd = os.open(folder / 'status', os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                code = subprocess.run([str(binary), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd), '--status-fd', str(status_fd),
                                       '--prefix', str(folder / 'pose_'), '--projection', '44.5', '--seconds', '.2',
                                       '--fixture-root', str(folder / 'absent'), '--fixture-hash', self.digest],
                                      pass_fds=(sdk_fd, status_fd), env=dict(os.environ, PRO2_QA_TRACE=str(folder / 'trace')), capture_output=True, timeout=8).returncode
            finally:
                os.close(sdk_fd); os.close(status_fd)
            records = [json.loads(line) for line in (folder / 'status').read_text().splitlines()]
            self.assertEqual(records[-1]['error'], 'identity-filesystem')
            records[-1]['seconds'] = None
            return code, records, (folder / 'trace').exists()

    def test_signal_topology_and_display_match(self):
        for label, options in (('sigterm', dict(interrupt=True)),
                               ('display', dict(display=bytes([0x32, 0x34]))), ('display-refused', dict(display=b'\x01')),
                               ('rate-240', dict(extra=('--pose-rate', '240'))), ('pitch', dict(extra=('--mount-pitch', '-8.5')))):
            with self.subTest(label=label):
                env_rate = {'rate-240': '3'}.get(label)
                if env_rate: os.environ['PRO2_QA_RATE'] = env_rate
                try:
                    cpp, rust = self.run_bridge(self.cpp, **options), self.run_bridge(self.rust, **options)
                finally:
                    os.environ.pop('PRO2_QA_RATE', None)
                self.assertSame(cpp, rust, label)

    def test_argument_faults_exit_2_silently_like_cpp(self):
        bad = (('--seconds', '301'), ('--seconds', '0'), ('--projection', '50.1'), ('--projection', 'nan'), ('--pose-rate', '120x'),
               ('--pose-rate', '100'), ('--mount-pitch', '31'), ('--prefix', '/tmp/shader_runtime_'), ('--prefix', 'relative_'),
               ('--bogus', '1'), ('--sdk-fd',))
        for extra in bad:
            with self.subTest(extra=extra):
                for binary in (self.cpp, self.rust):
                    with tempfile.TemporaryDirectory(dir=self.build) as tmp:
                        folder = Path(tmp); sysfs_from_snapshot(folder / 'sysfs')
                        sdk_fd = os.open(self.sdk, os.O_RDONLY); status_fd = os.open(folder / 'status', os.O_WRONLY | os.O_CREAT, 0o600)
                        try:
                            code = subprocess.run([str(binary), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd), '--status-fd', str(status_fd),
                                                   '--prefix', str(folder / 'pose_'), '--projection', '44.5', '--seconds', '.2',
                                                   '--fixture-root', str(folder / 'sysfs'), '--fixture-hash', self.digest, *extra],
                                                  pass_fds=(sdk_fd, status_fd), env=dict(os.environ, PRO2_QA_TRACE=str(folder / 'trace')),
                                                  capture_output=True, timeout=8).returncode
                        finally:
                            os.close(sdk_fd); os.close(status_fd)
                        self.assertEqual(code, 2, (binary.name, extra))
                        self.assertEqual((folder / 'status').read_text(), '')
                        self.assertFalse((folder / 'trace').exists())

    def test_production_build_refuses_fixture_flags_and_links_no_libcrypto(self):
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            folder = Path(tmp); sysfs_from_snapshot(folder / 'sysfs')
            sdk_fd = os.open(self.sdk, os.O_RDONLY); status_fd = os.open(folder / 'status', os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                code = subprocess.run([str(self.rust_production), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd), '--status-fd', str(status_fd),
                                       '--prefix', str(folder / 'pose_'), '--projection', '44.5', '--seconds', '.2',
                                       '--fixture-root', str(folder / 'sysfs'), '--fixture-hash', self.digest],
                                      pass_fds=(sdk_fd, status_fd), capture_output=True, timeout=8).returncode
            finally:
                os.close(sdk_fd); os.close(status_fd)
            self.assertEqual(code, 2)
            self.assertEqual((folder / 'status').read_text(), '')
        for binary in (self.rust, self.rust_production):
            ldd = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, check=True).stdout
            self.assertNotIn('libcrypto', ldd, ldd)
            self.assertNotIn('libssl', ldd, ldd)
        self.assertIn('libcrypto', subprocess.run(['ldd', str(self.cpp)], capture_output=True, text=True, check=True).stdout, 'the check would see it')

    # Reconnect (D12): Rust only. The C++ bridge ends the run on each of these.

    def assertReconnected(self, rust, reason, creates=2):
        events = [r['event'] for r in rust['records']]
        self.assertEqual(events, ['ready', 'lost', 'reconnected', 'done'], rust['records'])
        lost, back, done = rust['records'][1], rust['records'][2], rust['records'][3]
        self.assertEqual(lost['reason'], reason)
        self.assertGreaterEqual(back['attempts'], 1)
        self.assertGreater(back['down_ms'], 0)
        self.assertEqual((rust['code'], done['error'], done['cleanup_errors'], done['ready']), (0, '', 0, True), rust)
        self.assertEqual(rust['trace'].count('create'), creates, rust['trace'])
        # The first session is ended in the SDK's order before the next one starts.
        first_end = rust['trace'].index('destroy')
        self.assertEqual(rust['trace'][first_end - 3:first_end + 1], ['close', 'stop', 'shutdown', 'destroy'])
        self.assertEqual(rust['trace'][first_end + 1], 'create')
        self.assertEqual(rust['trace'][-4:], ['close', 'stop', 'shutdown', 'destroy'])
        self.assertNotIn(True, [':WRONG_OWNER' in t for t in rust['trace']])
        return back

    @staticmethod
    def unplug(away_s=.4, devnum='14', restore=True, twin=False):
        """The device directory leaves sysfs for away_s, then comes back under a new devnum, as a replug."""
        def action(device, usb, process):
            hidden = usb.parent / 'unplugged'
            device.rename(hidden)
            threading.Event().wait(away_s)
            if twin:
                copy = usb / '1-6'; copy.mkdir()
                for f in hidden.iterdir():
                    if f.is_file(): (copy / f.name).write_text(f.read_text())
            if restore:
                (hidden / 'devnum').write_text(devnum + '\n'); hidden.rename(device)
        return action

    def test_reconnect_after_unplug_and_replug(self):
        rust = self.run_bridge(self.rust, action=self.unplug(), seconds='1.5', probe=True)
        back = self.assertReconnected(rust, 'unplugged')
        self.assertGreaterEqual(back['down_ms'], 350)
        # A reader saw poses, then the pause on the unplug (it stops there; the fov segment it may
        # have opened before is not this test's concern).
        self.assertEqual({k: rust['observed'][k] for k in ('seen', 'disabled_after')}, dict(seen=True, disabled_after=True))
        # Samples flowed after the replug: 1.5 s minus the outage at 16 ms is well over 40.
        self.assertGreater(rust['records'][-1]['samples'], 40, rust['records'][-1])

    def test_reconnect_after_renumber(self):
        """The same port, a new bus address with no visible gap (the old unplug parity case)."""
        rust = self.run_bridge(self.rust, action=lambda device, usb, process: (device / 'devnum').write_text('14\n'), seconds='.8')
        self.assertReconnected(rust, 'unplugged')

    def test_reconnect_after_silence(self):
        """Glasses that stop sending but stay in sysfs: stale after 100 ms, then a fresh session."""
        for case in ('stall_once', 'stall_restart_clock'):
            with self.subTest(case=case):
                rust = self.run_bridge(self.rust, case, seconds='1')
                self.assertReconnected(rust, 'stale')

    def test_reconnect_asks_for_the_display_mode_again(self):
        rust = self.run_bridge(self.rust, 'stall_once', display=bytes([0x32]), seconds='1')
        events = [r['event'] for r in rust['records']]
        self.assertEqual(events.count('display'), 2, rust['records'])
        self.assertEqual(rust['trace'].count('display:50'), 2, rust['trace'])
        second = rust['trace'].index('display:50', rust['trace'].index('display:50') + 1)
        self.assertLess(rust['trace'].index('create', rust['trace'].index('destroy')), second)

    def test_device_never_back_is_device_lost(self):
        rust = self.run_bridge(self.rust, action=self.unplug(restore=False), seconds='1')
        self.assertEqual([r['event'] for r in rust['records']], ['ready', 'lost', 'done'])
        self.assertEqual((rust['code'], rust['records'][-1]['error'], rust['records'][-1]['cleanup_errors']), (1, 'device-lost', 0))
        self.assertEqual(rust['trace'].count('create'), 1)

    def test_failed_bring_up_retries_until_device_lost(self):
        rust = self.run_bridge(self.rust, 'reconnect_fails', action=self.unplug(away_s=.1), seconds='1.2')
        self.assertEqual([r['event'] for r in rust['records']], ['ready', 'lost', 'done'])
        self.assertEqual(rust['records'][-1]['error'], 'device-lost')
        self.assertGreaterEqual(rust['trace'].count('create'), 3, rust['trace'])  # 250 ms between attempts
        self.assertNotIn('open', rust['trace'][rust['trace'].index('destroy'):])

    def test_two_devices_after_unplug_fail_closed(self):
        rust = self.run_bridge(self.rust, action=self.unplug(twin=True), seconds='1')
        self.assertEqual(rust['records'][-1]['error'], 'exclusive-identity-required', rust['records'])
        self.assertEqual(rust['trace'].count('create'), 1)

    def test_sigterm_while_down_is_a_clean_stop(self):
        def unplug_then_stop(device, usb, process):
            device.rename(usb.parent / 'unplugged'); threading.Event().wait(.2)
            process.send_signal(signal.SIGTERM)
        rust = self.run_bridge(self.rust, action=unplug_then_stop, seconds='2')
        self.assertEqual([r['event'] for r in rust['records']], ['ready', 'lost', 'done'])
        self.assertEqual((rust['code'], rust['records'][-1]['error']), (0, ''))

    def test_lost_before_ready_still_fails_closed(self):
        """A drop before the first sample is published is a fault, as in the C++ bridge."""
        for binary in (self.cpp, self.rust):
            rust = self.run_bridge(binary, 'no_pose', action=lambda device, usb, process: (device / 'devnum').write_text('14\n'), seconds='.5')
            self.assertEqual(rust['records'][-1]['error'], 'identity-changed', binary)
            self.assertEqual(rust['trace'].count('create'), 1)


if __name__ == '__main__':
    unittest.main()
