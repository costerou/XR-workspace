"""Independent actual native bridge tests; synthetic SDK, private IPC only."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from tests.pro2_bridge import build_bridge, implementation

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


class NativeRuntimeQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for kind in ('user', 'ipc', 'net'):
            old = os.environ.get('PRO2_QA_PARENT_' + kind.upper())
            if old is None or os.readlink('/proc/self/ns/' + kind) == old:
                raise RuntimeError('Run via python tests/pro2_runtime_qa/run.py; fresh namespaces required')
        cls.tmp = tempfile.TemporaryDirectory(prefix='pro2-native-qa-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.build = Path(cls.tmp.name)
        cls.sdk = cls.build / 'fixture.so'
        cls.missing_sdk = cls.build / 'missing-symbol.so'
        cls.production = cls.build / 'production-bridge'
        cls.binary = cls.build / 'bridge'
        cls.probe = cls.build / 'probe'
        for cmd in (
            ['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', '-pthread',
             str(HERE / 'sdk_fixture.c'), '-o', str(cls.sdk)],
            ['cc', '-std=c11', '-shared', '-fPIC', '-pthread',
             '-Dxr_device_provider_close_imu=fixture_missing_close', str(HERE / 'sdk_fixture.c'), '-o', str(cls.missing_sdk)],
            ['c++', '-std=c++20', '-pthread', '-Ispike/include', str(HERE / 'probe.cpp'),
             'spike/src/pose.cpp', '-o', str(cls.probe)],
        ):
            subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True)
        build_bridge(cls.binary)
        build_bridge(cls.production, synthetic=False)
        cls.digest = hashlib.sha256(cls.sdk.read_bytes()).hexdigest()

    def run_bridge(self, case='normal', identity=None, extra=(), probe=False, interrupt=False, mutate=False, sdk_file=None, binary=None):
        with tempfile.TemporaryDirectory(prefix='pro2-case-', dir=self.build) as tmp:
            folder = Path(tmp); usb = folder / 'sysfs'; usb.mkdir(); device = usb / '1-1'; device.mkdir()
            fields = dict(idVendor='35ca', idProduct='1301', product='VITURE PRO 2 XR Glasses', busnum='1', devnum='13')
            fields.update(identity or {})
            for key, value in fields.items():
                if value is not None: (device / key).write_text(value + '\n')
            if case == 'multiple':
                other = usb / '1-2'; other.mkdir()
                for key, value in fields.items(): (other / key).write_text(value + '\n')
            env = dict(os.environ, PRO2_QA_CASE=case, PRO2_QA_TRACE=str(folder / 'trace'))
            prefix = str(folder / 'pose_')
            library = sdk_file or self.sdk
            digest = hashlib.sha256(library.read_bytes()).hexdigest()
            sdk_fd = os.open(library, os.O_RDONLY | os.O_CLOEXEC)
            status_fd = os.open(folder / 'status', os.O_WRONLY | os.O_CREAT, 0o600)
            args = [str(binary or self.binary), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd),
                    '--status-fd', str(status_fd), '--prefix', prefix, '--projection', '44.5',
                    '--seconds', '.18', '--fixture-root', str(usb), '--fixture-hash', digest, *extra]
            observer = subprocess.Popen([str(self.probe), prefix], stdout=subprocess.PIPE, stderr=subprocess.PIPE) if probe else None
            timer = None
            try:
                process = subprocess.Popen(args, pass_fds=(sdk_fd, status_fd), env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if interrupt or mutate:
                    def action():
                        if interrupt: process.send_signal(signal.SIGTERM)
                        else: (device / 'devnum').write_text('14\n')
                    timer = threading.Timer(.08, action); timer.start()
                stdout, stderr = process.communicate(timeout=5)
            finally:
                if timer: timer.cancel(); timer.join()
                os.close(sdk_fd); os.close(status_fd)
                if 'process' in locals() and process.poll() is None: process.kill(); process.communicate()
            observed = None
            if observer:
                try:
                    data, err = observer.communicate(timeout=5)
                    self.assertEqual(observer.returncode, 0, (data, err))
                    observed = json.loads(data)
                finally:
                    if observer.poll() is None: observer.kill(); observer.communicate()
            trace = (folder / 'trace').read_text().splitlines() if (folder / 'trace').exists() else []
            records = [json.loads(line) for line in (folder / 'status').read_text().splitlines()]
            self.assertFalse(any('WRONG_OWNER' in item for item in trace), trace)
            self.assertFalse(list(folder.glob('pose_*')), 'private publisher files leaked after teardown')
            self.assertEqual(stdout, b'', 'native status must use dedicated FD')
            return process.returncode, records, trace, observed

    def test_happy_path_real_native_reader_and_cleanup(self):
        code, records, trace, observed = self.run_bridge(probe=True)
        self.assertEqual(code, 0, records)
        self.assertEqual([r['event'] for r in records], ['ready', 'done'])
        self.assertGreater(records[-1]['samples'], 2)
        self.assertEqual(records[-1]['cleanup_errors'], 0)
        self.assertEqual(trace[-4:], ['close', 'stop', 'shutdown', 'destroy'])
        self.assertTrue(observed['disabled_after'])

    def test_identity_hash_and_multiple_fail_before_sdk(self):
        cases = [('normal', {'idProduct': '1104'}, ()), ('normal', {'product': 'VITURE Pro'}, ()),
                 ('normal', {'idVendor': 'ffff'}, ()), ('multiple', {}, ()),
                 ('normal', {'idProduct': None}, ()), ('normal', {}, ('--fixture-hash', '0' * 64))]
        for case, identity, extra in cases:
            with self.subTest(case=case, identity=identity, extra=extra):
                code, records, trace, _ = self.run_bridge(case, identity, extra)
                self.assertNotEqual(code, 0)
                self.assertFalse(trace, trace)
                self.assertFalse(any(r['event'] == 'ready' for r in records))

    def test_sdk_errors_and_cleanup_continue(self):
        for case in ('null_create', 'unsupported_pid', 'unsupported_rate', 'wrong_family', 'initialize',
                     'start', 'register', 'open', 'wrong_firmware', 'firmware_error', 'firmware_length', 'firmware_short_length',
                     'close', 'stop', 'shutdown'):
            with self.subTest(case=case):
                code, records, trace, _ = self.run_bridge(case)
                self.assertNotEqual(code, 0, (case, records))
                if 'create' in trace and case != 'null_create':
                    self.assertEqual(trace[-3:], ['stop', 'shutdown', 'destroy'])
                if case not in ('close', 'stop', 'shutdown'):
                    self.assertFalse(any(r['event'] == 'ready' for r in records))

    def test_terminal_stream_faults(self):
        for case in ('queue_overflow', 'null_callback', 'duplicate', 'clock_reverse', 'nonfinite', 'bad_norm', 'stale_after_ready', 'no_pose'):
            with self.subTest(case=case):
                code, records, trace, _ = self.run_bridge(case)
                self.assertNotEqual(code, 0, (case, records))
                self.assertTrue(records[-1]['error'], records)
                self.assertEqual(trace[-4:], ['close', 'stop', 'shutdown', 'destroy'])

    def test_callback_during_close_and_signal_shutdown(self):
        for case, interrupted in [('callback_during_close', False), ('normal', True)]:
            with self.subTest(case=case):
                code, records, trace, _ = self.run_bridge(case, interrupt=interrupted)
                self.assertEqual(code, 0, records)
                self.assertEqual(trace[-4:], ['close', 'stop', 'shutdown', 'destroy'])

    def test_topology_change_terminal(self):
        # The devnum changes after ready. The C++ bridge ends the run on it; the Rust bridge (D12)
        # treats it as an unplug and replug and reconnects, which test_rust_bridge covers in depth.
        code, records, _, _ = self.run_bridge(mutate=True)
        if implementation() == 'rust':
            self.assertEqual(code, 0, records)
            self.assertEqual([r['event'] for r in records], ['ready', 'lost', 'reconnected', 'done'])
            self.assertEqual(records[1]['reason'], 'unplugged')
            self.assertEqual(records[-1]['error'], '')
            return
        self.assertNotEqual(code, 0, records)
        self.assertEqual(records[-1]['error'], 'identity-changed')

    def test_projection_denied_without_bounded_explicit_value(self):
        for value in ('nan', 'inf', '0', '44.4', '50.1'):
            with self.subTest(value=value):
                code, records, trace, _ = self.run_bridge(extra=('--projection', value))
                self.assertNotEqual(code, 0)
                self.assertFalse(records); self.assertFalse(trace)


    def test_missing_cleanup_abi_rejected_before_create(self):
        code, records, trace, _ = self.run_bridge(sdk_file=self.missing_sdk)
        self.assertNotEqual(code, 0)
        self.assertEqual(trace, ['load'])
        self.assertEqual(records[-1]['error'], 'sdk-symbol')

    def test_two_samples_cannot_claim_readiness(self):
        code, records, trace, _ = self.run_bridge('two_pose')
        self.assertNotEqual(code, 0)
        self.assertEqual([r['event'] for r in records], ['done'])
        self.assertFalse(records[-1]['ready'])
        self.assertEqual(records[-1]['samples'], 0)

    def test_startup_timeout_is_bounded_and_cleans_up(self):
        started = time.monotonic()
        code, records, trace, _ = self.run_bridge('no_pose', extra=('--seconds', '3.2'))
        elapsed = time.monotonic() - started
        self.assertNotEqual(code, 0)
        self.assertEqual(records[-1]['error'], 'no-pose')
        self.assertLess(elapsed, 4.5)
        self.assertEqual(trace[-4:], ['close', 'stop', 'shutdown', 'destroy'])


    def test_production_binary_rejects_fixture_options_before_sdk(self):
        code, records, trace, _ = self.run_bridge(binary=self.production)
        self.assertEqual(code, 2)
        self.assertFalse(records)
        self.assertFalse(trace)
