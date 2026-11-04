"""Independent wrapper/controller QA. Never creates real client or loads real SDK."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import test_native_runtime as native


class ControllerRouteQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.NativeRuntimeQA.setUpClass.__func__(cls)

    def make_bundle(self, root):
        bundle = root / 'bundle'; (bundle / 'bin').mkdir(parents=True)
        (bundle / 'bin/pro2-sdk-bridge').write_bytes(self.binary.read_bytes())
        (bundle / 'bin/pro2-sdk-bridge').chmod(0o700)
        # These are hash-checked inert fixture bytes, never loaded/executed.
        for name in ('xr-native-spike', 'libxr-ownership.so'):
            (bundle / 'bin' / name).write_bytes(b'QA inert artifact; no GPU/compositor')
        files = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (bundle / 'bin').iterdir()}
        (bundle / 'manifest.json').write_text(json.dumps(dict(schema=1, sdk_sha256=self.digest, files=files)))
        return bundle

    def fixture_context(self, root):
        from contextlib import ExitStack
        from tools.pro2_runtime import launch
        usb = root / 'sysfs'; usb.mkdir(); device = usb / '1-1'; device.mkdir()
        for key, value in dict(idVendor='35ca', idProduct='1301', product='VITURE PRO 2 XR Glasses', busnum='1', devnum='13').items():
            (device / key).write_text(value + '\n')
        real_popen = subprocess.Popen
        def synthetic_popen(command, **kwargs):
            # Inject only fixture root/hash; retain real wrapper process/FD/status lifetime.
            assert command[:3] == ['unshare', '-Urn', '--'], command
            return real_popen([*command, '--fixture-root', str(usb), '--fixture-hash', self.digest], **kwargs)
        stack = ExitStack()
        stack.enter_context(patch.object(launch, 'LIBRARY', str(self.sdk)))
        stack.enter_context(patch.object(launch, 'SHA256', self.digest))
        stack.enter_context(patch.object(launch, 'inventory', return_value=[('35ca', '1301', 'VITURE PRO 2 XR Glasses')]))
        stack.enter_context(patch.object(launch, 'competing', return_value=False))
        stack.enter_context(patch.object(launch.subprocess, 'Popen', side_effect=synthetic_popen))
        return stack

    def consent(self):
        return dict(schema=1, gate='pro2-supervised-v1', model='pro2', run_id='qa-runtime',
                    instance='offline', output='DP-2', recovery_output='DP-1', seconds=1,
                    projection=44.5, parent_authorized=True, exclusive_topology=True,
                    approximate_optics=True, desktops=[dict(width=1920, height=1080, scale=1)])

    def test_actual_wrapper_native_sdk_health_and_teardown(self):
        from tools.pro2_runtime import launch
        from tools.xr_controller.pro2_test import TestScope
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            root = Path(tmp)
            with self.fixture_context(root):
                bundle = self.make_bundle(root)
                session = launch.RuntimeSession(bundle, root / 'run', 44.5, 1, True)
                try:
                    session.start()
                    scope = TestScope(session.prefix, session.projection, str(session.health_path), session.nonce)
                    scope.healthy(); time.sleep(.08); scope.healthy()
                    self.assertTrue(session.ready)
                    self.assertEqual(os.stat(session.directory / 'sdk.private.log').st_mode & 0o777, 0o600)
                finally:
                    session.close()
                with self.assertRaises(ValueError): scope.healthy()
                self.assertFalse(list(session.directory.glob('pose_*')))
                self.assertEqual(session.done['cleanup_errors'], 0)

    def test_consent_and_projection_default_deny_before_client(self):
        from tools.xr_controller import pro2_test
        from tools.pro2_runtime.launch import validate_projection
        for key, value in [('parent_authorized', False), ('exclusive_topology', False), ('approximate_optics', False),
                           ('model', 'luma-ultra'), ('model', 'beast'), ('projection', None), ('projection', float('nan')),
                           ('seconds', True), ('seconds', 301), ('recovery_output', 'DP-2')]:
            data = self.consent(); data[key] = value
            with self.subTest(key=key, value=value):
                client = Mock(side_effect=AssertionError('client must not be reached'))
                session = Mock(side_effect=AssertionError('session must not be reached'))
                with self.assertRaises(ValueError): pro2_test.execute(data, '/nonexistent', '/nonexistent', client_factory=client, session_factory=session)
                client.assert_not_called(); session.assert_not_called()
        for value, approved in [(44.5, False), (None, True), (True, True), (44.4, True), (50.1, True), (float('inf'), True)]:
            with self.assertRaises(ValueError): validate_projection(value, approved)
        self.assertEqual(validate_projection(44.5, True), 44.5)
        self.assertEqual(validate_projection(50, True), 50)

    def test_actual_controller_route_uses_real_wrapper_and_only_fake_client(self):
        from tools.xr_controller import pro2_test
        from tools.pro2_runtime.launch import RuntimeSession
        from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            root = Path(tmp)
            client = Mock(); client.plugins_loaded.side_effect = [[], [OWNERSHIP_PLUGIN_NAME]]
            def run(fake_client, consent, runtime, **kwargs):
                self.assertIs(fake_client, client)
                self.assertEqual(consent['model'], 'pro2')
                self.assertIs(consent['mode_switch'], False)
                self.assertIsNone(consent['sbs_mode'])
                self.assertEqual(kwargs['store'].model, 'pro2')
                self.assertIn('pose_', kwargs['test_scope'].prefix)
                kwargs['test_scope'].healthy()
                return dict(complete=True)
            with self.fixture_context(root), patch.object(pro2_test.monitor_rules, 'refuse_start'):
                bundle = self.make_bundle(root)
                result = pro2_test.execute(self.consent(), bundle, root, client_factory=lambda *_: client,
                                          session_factory=RuntimeSession, run=run)
            self.assertTrue(result['complete'], result)
            client.park_presentation.assert_called_once_with('DP-2')
            client.plugin_unload.assert_called_once()
            client.set_dpms.assert_not_called()

    def test_scope_stale_wrong_nonce_and_future_receipt_fail_closed(self):
        from tools.xr_controller.pro2_test import TestScope
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            path = Path(tmp) / 'health'
            for data in [dict(nonce='wrong', ready=True, monotonic=time.monotonic()),
                         dict(nonce='ok', ready=False, monotonic=time.monotonic()),
                         dict(nonce='ok', ready=True, monotonic=time.monotonic()-1),
                         dict(nonce='ok', ready=True, monotonic=time.monotonic()+1)]:
                path.write_text(json.dumps(data)); path.chmod(0o600)
                scope = TestScope('/offline/pose_', 44.5, str(path), 'ok')
                with self.assertRaises(ValueError): scope.healthy()


    def test_terminal_event_in_same_batch_cannot_return_ready_session(self):
        from tools.pro2_runtime import launch
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            root = Path(tmp)
            with self.fixture_context(root), patch.dict(os.environ, PRO2_QA_CASE='ready_then_bad'):
                session = launch.RuntimeSession(self.make_bundle(root), root / 'run', 44.5, 1, True)
                returned = False
                try:
                    session.start()
                    returned = True
                except (ValueError, RuntimeError):
                    pass
                finally:
                    try: session.close()
                    except RuntimeError: pass
                self.assertFalse(returned, 'start returned ready after terminal done event in same batch')

    def test_artifact_rejection_closes_already_pinned_sdk_fd(self):
        from tools.pro2_runtime import launch
        with tempfile.TemporaryDirectory(dir=self.build) as tmp:
            root = Path(tmp)
            with self.fixture_context(root):
                bundle = self.make_bundle(root)
                session = launch.RuntimeSession(bundle, root / 'run', 44.5, 1, True)
                (bundle / 'bin/pro2-sdk-bridge').write_bytes(b'changed after manifest check')
                before = {n for n in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/' + n)}
                try:
                    with self.assertRaisesRegex(ValueError, 'hash mismatch'): session.start()
                    after = {n for n in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/' + n)}
                    self.assertEqual(after, before, 'SDK fd leaked after executable hash rejection')
                finally:
                    session.close()
                    # The red test owns any leaked descriptors; reclaim them here.
                    for number in {n for n in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/' + n)} - before:
                        try: os.close(int(number))
                        except OSError: pass
