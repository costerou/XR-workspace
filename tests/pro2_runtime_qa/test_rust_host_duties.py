"""The launch.py wrapper duties done by the Rust host itself (rust/crates/xr-pro2-bridge/src/host.rs).

`--unshare` gives it its own user and network namespace as `unshare -Urn` does; `--scan-competitors`
refuses before the SDK, and stops mid-run, while a same-user provider runs, as `launch.competing`;
`--health <path> --nonce <n>` keeps the heartbeat that `scope.TestScope.healthy` reads, and leaves
`ready: false` with the reason when the run ends. Synthetic SDK and sysfs as test_rust_bridge.py.
Each host runs as pid 1 of its own PID namespace with its own /proc, so the scan sees only the
stand-in competitors the test puts there, never another suite's bridges running on the machine.
Needs PRO2_BRIDGE_RUST_DIR (see tests/pro2_bridge.py); run via run.py.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from tests.pro2_runtime_qa.test_rust_bridge import HERE, RUST, ROOT, sysfs_from_snapshot
from tools.pro2_runtime.scope import TestScope, read_health

NONCE = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'


@unittest.skipUnless(RUST, 'set PRO2_BRIDGE_RUST_DIR (python3 tests/pro2_bridge.py <dir>)')
class RustHostDuties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for kind in ('user', 'ipc', 'net'):
            old = os.environ.get('PRO2_QA_PARENT_' + kind.upper())
            if old is None or os.readlink('/proc/self/ns/' + kind) == old:
                raise RuntimeError('Run via python tests/pro2_runtime_qa/run.py; fresh namespaces required')
        cls.tmp = tempfile.TemporaryDirectory(prefix='pro2-rust-duties-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.build = Path(cls.tmp.name)
        cls.sdk = cls.build / 'fixture.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-pthread', str(HERE / 'sdk_fixture.c'), '-o', str(cls.sdk)],
                       cwd=ROOT, check=True, capture_output=True)
        cls.digest = hashlib.sha256(cls.sdk.read_bytes()).hexdigest()
        cls.rust = Path(RUST) / 'synthetic/xr-pro2-bridge'
        cls.rust_production = Path(RUST) / 'production/xr-pro2-bridge'

    def start(self, folder, case='normal', seconds='.25', extra=(), binary=None, prelude='', fixture=True):
        """The host as pid 1 of a fresh PID namespace and /proc; `prelude` is shell run there first."""
        usb = folder / 'sysfs'; sysfs_from_snapshot(usb)
        env = dict(os.environ, PRO2_QA_CASE=case, PRO2_QA_TRACE=str(folder / 'trace'))
        sdk_fd = os.open(self.sdk, os.O_RDONLY | os.O_CLOEXEC)
        status_fd = os.open(folder / 'status', os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            return subprocess.Popen(['unshare', '-pf', '--mount-proc', '--', 'sh', '-c', prelude + ' exec "$@"', 'sh',
                                     str(binary or self.rust), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd), '--status-fd', str(status_fd),
                                     '--prefix', str(folder / 'pose_'), '--projection', '44.5', '--seconds', seconds,
                                     *(('--fixture-root', str(usb), '--fixture-hash', self.digest) if fixture else ()), *extra],
                                    pass_fds=(sdk_fd, status_fd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        finally:
            os.close(sdk_fd); os.close(status_fd)

    def finish(self, process, folder):
        try:
            process.communicate(timeout=8)
        finally:
            if process.poll() is None: process.kill(); process.communicate()
        records = [json.loads(line) for line in (folder / 'status').read_text().splitlines()]
        trace = (folder / 'trace').read_text().splitlines() if (folder / 'trace').exists() else []
        return process.returncode, records, trace

    def wait_for(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate(): return True
            time.sleep(.01)
        return False

    def competitor(self, folder, name='xrDriver', preload=None, after=0):
        """Shell that starts a stand-in provider in the host's namespace: sleep under a provider's name, or with a library
        named libglasses.so mapped. At once (and waited for), or `after` seconds into the run. The namespace's end kills it."""
        exe = folder / name; shutil.copy('/bin/sleep', exe)
        env = f'env -u PRO2_QA_TRACE LD_PRELOAD={preload} ' if preload else ''  # its copy of the fixture must not trace
        if after: return f'(sleep {after}; exec {env}{exe} 30) &'
        seen = f'grep -qs libglasses.so /proc/$c/maps' if preload else f'grep -qsx {name[:15]} /proc/$c/comm'
        return f'{env}{exe} 30 & c=$!; until {seen}; do sleep .01; done;'

    def host_pid(self, process):
        """pid 1 of the namespace, as this namespace numbers it: unshare's one child."""
        self.assertTrue(self.wait_for(lambda: Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()))
        return int(Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()[0])

    def test_full_duties_run_is_isolated_healthy_then_reports_not_ready(self):
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            folder = Path(tmp); health = folder / 'health.json'
            scope = TestScope(prefix=str(folder / 'pose_'), projection=44.5, health_path=str(health), nonce=NONCE)
            process = self.start(folder, seconds='1.5', extra=('--unshare', '--scan-competitors', '--health', str(health), '--nonce', NONCE))
            try:
                self.assertTrue(self.wait_for(health.exists), 'no heartbeat')
                pid = self.host_pid(process)
                for kind in ('user', 'net'):
                    self.assertNotEqual(os.readlink(f'/proc/{pid}/ns/{kind}'), os.readlink(f'/proc/self/ns/{kind}'), kind)
                ages = []
                for _ in range(20):  # 0.4 s of reads against 20 ms replaces: every one whole, private and fresh
                    scope.healthy()
                    ages.append(time.monotonic() - read_health(str(health))['monotonic'])
                    time.sleep(.02)
                self.assertLess(max(ages), .5, ages)
                self.assertGreater(ages[0], -.001, 'the heartbeat clock is not the reader\'s CLOCK_MONOTONIC')
            finally:
                code, records, trace = self.finish(process, folder)
            self.assertEqual(code, 0, records)
            self.assertEqual([r['event'] for r in records], ['ready', 'done'])
            self.assertEqual(records[-1]['error'], '')
            last = read_health(str(health))
            self.assertEqual((last['nonce'], last['ready'], 'reason' in last), (NONCE, False, False))
            with self.assertRaises(ValueError): scope.healthy()
            self.assertEqual(health.stat().st_mode & 0o777, 0o600)
            self.assertFalse((folder / 'health.tmp').exists())

    def test_fault_after_ready_leaves_its_reason(self):
        # A bad sample after ready still ends the run (a stale feed after ready is a reconnect now, not an end).
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            folder = Path(tmp); health = folder / 'health.json'
            code, records, _ = self.finish(self.start(folder, 'nonfinite', '2', ('--health', str(health), '--nonce', NONCE)), folder)
            self.assertEqual((code, [r['event'] for r in records], records[-1]['error']), (1, ['ready', 'done'], 'nonfinite'), records)
            self.assertEqual(read_health(str(health)), dict(nonce=NONCE, ready=False, monotonic=read_health(str(health))['monotonic'], reason='nonfinite'))

    def test_each_duty_alone_is_a_normal_run(self):
        for extra in (('--unshare',), ('--scan-competitors',), ('--health', 'HEALTH', '--nonce', NONCE)):
            with self.subTest(extra=extra[0]), tempfile.TemporaryDirectory(dir=self.build) as tmp:
                folder = Path(tmp); extra = tuple(str(folder / 'health.json') if a == 'HEALTH' else a for a in extra)
                code, records, trace = self.finish(self.start(folder, extra=extra), folder)
                self.assertEqual((code, [r['event'] for r in records], records[-1]['error']), (0, ['ready', 'done'], ''), records)
                self.assertEqual(trace[-4:], ['close', 'stop', 'shutdown', 'destroy'])

    def test_production_build_takes_the_duty_flags(self):
        # No fixture there: the flags parse, then the real sysfs has no Pro 2 (or the run ends on the SDK hash); never exit 2.
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            folder = Path(tmp); health = folder / 'health.json'
            code, records, _ = self.finish(self.start(folder, seconds='.2', binary=self.rust_production, fixture=False, extra=(
                '--unshare', '--scan-competitors', '--health', str(health), '--nonce', NONCE)), folder)
            self.assertEqual((code, [r['event'] for r in records]), (1, ['done']))
            self.assertRegex(records[-1]['error'], r'^(identity-\w+|sdk-hash)$', records)
            self.assertEqual(read_health(str(health))['reason'], records[-1]['error'])

    def test_competitor_refused_before_the_sdk(self):
        for label, options in (('by-name', dict(name='xrDriver')), ('by-name-bridge', dict(name='xr-pro2-bridge')),
                               ('by-mapping', dict(name='quiet', preload='libglasses.so'))):
            with self.subTest(label=label), tempfile.TemporaryDirectory(dir=self.build) as tmp:
                folder = Path(tmp)
                if options.get('preload'):
                    lib = folder / 'libglasses.so'; shutil.copy(self.sdk, lib); options['preload'] = lib
                prelude = self.competitor(folder, **options)
                code, records, trace = self.finish(self.start(folder, extra=('--unshare', '--scan-competitors'), prelude=prelude), folder)
                self.assertEqual((code, [r['event'] for r in records], records[-1]['error']), (1, ['done'], 'competing-provider'))
                self.assertEqual(trace, [], 'the SDK was touched')

    def test_competitor_appearing_mid_run_ends_it(self):
        # By mapping too: the rescan runs from outside the host's new user namespace, where the kernel lets it read maps.
        for label, options in (('by-name', dict(name='xrDriver')), ('by-mapping', dict(name='quiet', preload='libglasses.so'))):
            with self.subTest(label=label), tempfile.TemporaryDirectory(dir=self.build) as tmp:
                folder = Path(tmp); health = folder / 'health.json'
                if options.get('preload'):
                    lib = folder / 'libglasses.so'; shutil.copy(self.sdk, lib); options['preload'] = lib
                prelude = self.competitor(folder, after=1, **options)
                process = self.start(folder, seconds='6', extra=('--unshare', '--scan-competitors', '--health', str(health), '--nonce', NONCE),
                                     prelude=prelude)
                try:
                    self.assertTrue(self.wait_for(health.exists), 'no heartbeat')
                    began = time.monotonic()
                finally:
                    code, records, trace = self.finish(process, folder)
                self.assertLess(time.monotonic() - began, 3, 'the scanner looks once a second')
                self.assertEqual(records[0]['event'], 'ready', 'refused at the start, not stopped mid-run')
                self.assertEqual((code, records[-1]['error']), (1, 'competing-provider'))
                self.assertEqual(trace[-4:], ['close', 'stop', 'shutdown', 'destroy'])
                self.assertEqual(read_health(str(health))['reason'], 'competing-provider')

    def test_bad_duty_arguments_exit_2_silently(self):
        bad = (('--health', '/x/health.json'), ('--nonce', NONCE), ('--health', 'relative.json', '--nonce', NONCE),
               ('--health', '/x/health.json', '--nonce', ''), ('--health', '/x/health.json', '--nonce', 'has space'),
               ('--health', '/x/health.json', '--nonce', 'a' * 65), ('--health', '/x/health.tmp', '--nonce', NONCE),
               ('--health', '/x/', '--nonce', NONCE), ('--health',), ('--unshare', 'yes'))
        for extra in bad:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory(dir=self.build) as tmp:
                folder = Path(tmp)
                code, records, trace = self.finish(self.start(folder, extra=extra), folder)
                self.assertEqual((code, records, trace), (2, [], []))


if __name__ == '__main__':
    unittest.main()
