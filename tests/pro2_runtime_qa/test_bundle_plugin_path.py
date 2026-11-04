"""Independent real BundleClient path dispatch; no SDK/compositor construction."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tools.xr_controller.pro2_test import BundleClient, execute
from tools.native_spike.hyprland import PLUGIN_PATH
from tools.pro2_diagnostic.candidate import SHA256

ACTUAL_ROOT = Path('/home/cody/.hermes/profiles/coderbotxr/cache/scratch/pro2-release-review-z3nsa4vu/pro2-runtime-release/runtime')
NORMAL_PATTERN = r'(?:/[A-Za-z0-9_.-]+)+/viture-workspace/bin/[0-9a-f]{12}/libxr-ownership\.so'


class BundlePluginPathQA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pro2-path-qa-'); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'pro2-runtime-release/runtime'
        self.make_bundle(self.root)

    def make_bundle(self, root):
        (root / 'bin').mkdir(parents=True)
        files = {}
        for name in ('pro2-sdk-bridge', 'xr-native-spike', 'libxr-ownership.so'):
            p = root / 'bin' / name; p.write_bytes(b'independent inert QA fixture')
            files['bin/' + name] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.manifest = dict(schema=1, sdk_sha256=SHA256, files=files)
        (root / 'manifest.json').write_text(json.dumps(self.manifest))

    def client(self, root=None):
        result = BundleClient.__new__(BundleClient)
        result.bundle_root = root or self.root
        result.bundle_manifest = self.manifest
        result._request = Mock(return_value=b'ok')
        return result

    def test_exact_parent_release_location_is_valid_with_pin_and_request_spy(self):
        client = self.client(ACTUAL_ROOT); path = str(ACTUAL_ROOT / 'bin/libxr-ownership.so')
        self.assertIsNone(PLUGIN_PATH.fullmatch(path), 'normal install regex is deliberately separate')
        # No file under the parent release is opened or changed. Supply inert FD.
        with tempfile.TemporaryFile() as fixture:
            with patch('tools.pro2_runtime.launch.pinned_fd', side_effect=lambda *_: os.dup(fixture.fileno())) as pin:
                client.plugin_load(path); client.plugin_unload(path)
        self.assertEqual(client._request.call_args_list[0].args, ('/plugin load ' + path,))
        self.assertEqual(client._request.call_args_list[1].args, ('/plugin unload ' + path,))
        self.assertEqual(pin.call_count, 2)
        for call in pin.call_args_list:
            self.assertEqual(str(call.args[0]), path)
            self.assertEqual(call.args[1], self.manifest['files']['bin/libxr-ownership.so'])

    def test_real_fixture_hash_gate_load_unload(self):
        client = self.client(); path = str(self.root / 'bin/libxr-ownership.so')
        client.plugin_load(path); client.plugin_unload(path)
        self.assertEqual(client._request.call_count, 2)

    def test_bad_path_verb_and_changed_hash_never_request(self):
        client = self.client()
        for verb, path in [('load', str(self.root / 'bin/../bin/libxr-ownership.so')),
                           ('load', str(self.root / 'bin/other.so')),
                           ('load', str(self.root / 'bin/libxr-ownership.so') + '\n'),
                           ('reload', str(self.root / 'bin/libxr-ownership.so'))]:
            with self.subTest(verb=verb, path=path), self.assertRaises(ValueError):
                client._plugin_command(verb, path)
        (self.root / 'bin/libxr-ownership.so').write_bytes(b'changed')
        with self.assertRaises(ValueError): client.plugin_load(str(self.root / 'bin/libxr-ownership.so'))
        client._request.assert_not_called()

    def test_symlink_plugin_never_request(self):
        client = self.client(); path = self.root / 'bin/libxr-ownership.so'
        inert = self.base / 'inert.so'; inert.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(inert)
        with self.assertRaises((ValueError, OSError)): client.plugin_load(str(path))
        client._request.assert_not_called()

    def test_normal_install_regex_unchanged(self):
        self.assertEqual(PLUGIN_PATH.pattern, NORMAL_PATTERN)
        self.assertIsNotNone(PLUGIN_PATH.fullmatch('/home/user/.local/share/viture-workspace/bin/0123456789ab/libxr-ownership.so'))
        self.assertIsNone(PLUGIN_PATH.fullmatch(str(self.root / 'bin/libxr-ownership.so')))

    def test_unsafe_bundle_preflight_before_client_or_sdk(self):
        data = dict(schema=1, gate='pro2-supervised-v1', model='pro2', run_id='qa-path', instance='offline',
                    output='DP-2', recovery_output='DP-1', seconds=1, projection=44.5,
                    parent_authorized=True, exclusive_topology=True, approximate_optics=True,
                    desktops=[dict(width=1920, height=1080, scale=1)])
        bad = self.base / 'unsafe bundle'; self.make_bundle(bad)
        client = Mock(side_effect=AssertionError('client reached before bundle path preflight'))
        sdk = Mock(side_effect=AssertionError('SDK reached before bundle path preflight'))
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.base)):
            with self.assertRaises(ValueError): execute(data, bad, self.base, client_factory=client, session_factory=sdk)
        client.assert_not_called(); sdk.assert_not_called()


    def test_symlink_root_or_bin_never_request(self):
        link = self.base / 'linked-runtime'; link.symlink_to(self.root, target_is_directory=True)
        client = self.client(link)
        with self.assertRaises((ValueError, OSError)): client.plugin_load(str(link / 'bin/libxr-ownership.so'))
        client._request.assert_not_called()
        real_bin = self.root / 'real-bin'; (self.root / 'bin').rename(real_bin)
        (self.root / 'bin').symlink_to(real_bin, target_is_directory=True)
        client = self.client()
        with self.assertRaises((ValueError, OSError)): client.plugin_load(str(self.root / 'bin/libxr-ownership.so'))
        client._request.assert_not_called()

    def test_packager_rejects_bad_location_before_compile(self):
        from tools.pro2_runtime import package
        with patch.object(package.subprocess, 'run') as compiler:
            with self.assertRaises(ValueError): package.build(self.base / 'not-built', self.base / 'unsafe package')
            compiler.assert_not_called()

    def test_cli_preflight_without_consent_state_sdk_or_client(self):
        import contextlib
        import io
        from tools.xr_controller import pro2_test
        output = io.StringIO()
        with patch('sys.argv', ['pro2-test', '--bundle', str(self.root), '--preflight']), \
             patch.object(pro2_test, 'BundleClient') as client, \
             patch.object(pro2_test, 'RuntimeSession') as sdk, \
             patch.object(pro2_test, 'read_private_json') as consent, \
             contextlib.redirect_stdout(output):
            self.assertEqual(pro2_test.main(), 0)
            client.assert_not_called(); sdk.assert_not_called(); consent.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertIs(result['sdk_started'], False)
        self.assertIs(result['compositor_contacted'], False)
        self.assertEqual(result['plugin_command_path'], 'validated')
        self.assertFalse((self.root / 'state').exists())
