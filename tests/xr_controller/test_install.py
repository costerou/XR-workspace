"""Pinned install: temp dirs and fake binaries only; nothing is executed."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from tools.xr_controller.install import install, load_installed, verify_pinned


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.build, self.data = root / 'build', root / 'data'
        self.build.mkdir(); self.data.mkdir()
        (self.build / 'xr-native-spike').write_bytes(b'renderer-bytes')
        (self.build / 'libxr-ownership.so').write_bytes(b'plugin-bytes')

    def test_install_plugin_symlink_refuses_real_dir(self):
        from tools.xr_controller.install import install_plugin
        repo = Path(__file__).resolve().parents[2]
        config = Path(self.tmp.name) / 'config'
        link = install_plugin(repo, config)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), repo / 'omarchy-plugin/cody.viture-workspace')
        self.assertEqual(install_plugin(repo, config), link)
        link.unlink(); link.mkdir()
        marker = link / 'keep'; marker.write_text('untouched')
        with self.assertRaisesRegex(ValueError, 'non-symlink'):
            install_plugin(repo, config)
        self.assertEqual(marker.read_text(), 'untouched')
        marker.unlink()
        link.rmdir(); link.write_text('keep file')
        with self.assertRaises(ValueError): install_plugin(repo, config)
        self.assertEqual(link.read_text(), 'keep file')

    def test_install_copies_and_hashes(self):
        entry = install(self.build, self.data)
        self.assertEqual(set(entry), {'plugin', 'renderer'})
        self.assertEqual(entry['renderer']['sha256'], hashlib.sha256(b'renderer-bytes').hexdigest())
        self.assertEqual(entry['plugin']['sha256'], hashlib.sha256(b'plugin-bytes').hexdigest())
        combined = hashlib.sha256((entry['renderer']['sha256'] + entry['plugin']['sha256']).encode()).hexdigest()[:12]
        for key, data in (('renderer', b'renderer-bytes'), ('plugin', b'plugin-bytes')):
            path = Path(entry[key]['path'])
            self.assertEqual(path.parent, self.data / 'viture-workspace/bin' / combined)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        info = self.data / 'viture-workspace/installed.json'
        self.assertEqual(stat.S_IMODE(info.stat().st_mode), 0o600)
        self.assertEqual(json.loads(info.read_text()), entry)
        self.assertEqual(load_installed(self.data), entry)
        for key in entry: verify_pinned(entry[key])

    def test_luma_driver_is_pinned_when_built(self):
        for where in (self.build, self.build / 'driver'):  # beside the renderer, or in its own CMake folder
            with self.subTest(where):
                where.mkdir(exist_ok=True)
                (where / 'xr-luma-driver').write_bytes(b'luma-' + where.name.encode())
                entry = install(self.build, self.data)
                self.assertEqual(set(entry), {'plugin', 'renderer', 'luma_driver'})
                path = Path(entry['luma_driver']['path'])
                self.assertEqual(path.name, 'xr-luma-driver')
                self.assertEqual(path.read_bytes(), b'luma-' + where.name.encode())
                self.assertEqual(entry['luma_driver']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o500)
                self.assertEqual(path.parent, Path(entry['renderer']['path']).parent)
                self.assertEqual(load_installed(self.data), entry)
                verify_pinned(entry['luma_driver'])
                (where / 'xr-luma-driver').unlink()

    def test_a_new_luma_driver_alone_gets_its_own_folder(self):
        (self.build / 'xr-luma-driver').write_bytes(b'one')
        first = install(self.build, self.data)
        (self.build / 'xr-luma-driver').write_bytes(b'two')
        second = install(self.build, self.data)
        self.assertNotEqual(first['luma_driver']['path'], second['luma_driver']['path'])
        verify_pinned(first['luma_driver'])  # a session on the first keeps its pin

    def test_changed_luma_driver_is_detected(self):
        (self.build / 'xr-luma-driver').write_bytes(b'one')
        entry = install(self.build, self.data)['luma_driver']
        path = Path(entry['path']); path.chmod(0o700); path.write_bytes(b'tampered')
        with self.assertRaises(ValueError): verify_pinned(entry)

    def test_build_without_luma_keeps_the_old_install_shape(self):
        entry = install(self.build, self.data)
        combined = hashlib.sha256((entry['renderer']['sha256'] + entry['plugin']['sha256']).encode()).hexdigest()[:12]
        self.assertEqual(Path(entry['plugin']['path']).parent.name, combined)

    def test_installed_json_rejects_a_luma_pin_outside_the_install(self):
        entry = install(self.build, self.data)
        info = self.data / 'viture-workspace/installed.json'
        info.write_text(json.dumps({**entry, 'luma_driver': {'path': '/usr/bin/evil', 'sha256': 'a' * 64}}))
        with self.assertRaises(ValueError): load_installed(self.data)
        info.write_text(json.dumps({**entry, 'something_else': entry['plugin']}))
        with self.assertRaises(ValueError): load_installed(self.data)

    def test_verify_detects_modification(self):
        entry = install(self.build, self.data)
        path = Path(entry['renderer']['path'])
        path.chmod(0o700); path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'): verify_pinned(entry['renderer'])
        verify_pinned(entry['plugin'])
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'): verify_pinned(entry['renderer'])

    def test_verify_rejects_symlink(self):
        entry = install(self.build, self.data)
        path = Path(entry['plugin']['path']); path.unlink()
        path.symlink_to(self.build / 'libxr-ownership.so')
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'): verify_pinned(entry['plugin'])

    def test_install_never_points_into_build_dir(self):
        entry = install(self.build, self.data)
        for key in entry:
            path = Path(entry[key]['path'])
            self.assertTrue(path.is_absolute())
            self.assertFalse(path.is_relative_to(self.build))
            self.assertTrue(path.is_relative_to(self.data / 'viture-workspace/bin'))

    def test_reinstall_same_bytes_same_dir(self):
        first = install(self.build, self.data)
        stamp = Path(first['renderer']['path']).stat().st_mtime_ns
        inode = Path(first['renderer']['path']).stat().st_ino
        second = install(self.build, self.data)
        self.assertEqual(first, second)
        self.assertEqual(Path(second['renderer']['path']).stat().st_ino, inode)
        self.assertEqual(Path(second['renderer']['path']).stat().st_mtime_ns, stamp)

    def test_changed_bytes_use_new_dir(self):
        first = install(self.build, self.data)
        (self.build / 'xr-native-spike').write_bytes(b'renderer-v2')
        second = install(self.build, self.data)
        self.assertNotEqual(first['renderer']['path'], second['renderer']['path'])
        verify_pinned(first['renderer']); verify_pinned(second['renderer'])

    def test_load_installed_is_strict(self):
        entry = install(self.build, self.data)
        info = self.data / 'viture-workspace/installed.json'
        def put(value): info.write_text(json.dumps(value))
        good = json.loads(info.read_text())
        bad = []
        bad.append(dict(good, extra=1))
        bad.append({'plugin': good['plugin']})
        bad.append(dict(good, renderer=dict(good['renderer'], sha256=good['renderer']['sha256'].upper())))
        bad.append(dict(good, renderer=dict(good['renderer'], sha256='ab')))
        bad.append(dict(good, renderer=dict(good['renderer'], path=str(self.build / 'xr-native-spike'))))
        bad.append(dict(good, renderer=dict(good['renderer'], path='relative/x')))
        bad.append(dict(good, renderer=dict(good['renderer'], extra=1)))
        bad.append(dict(good, plugin=[]))
        for value in bad:
            with self.subTest(value=value):
                put(value)
                with self.assertRaises(ValueError): load_installed(self.data)
        info.write_text('not json')
        with self.assertRaises(ValueError): load_installed(self.data)
        info.unlink()
        with self.assertRaises(ValueError): load_installed(self.data)

    def test_missing_build_output_fails(self):
        (self.build / 'libxr-ownership.so').unlink()
        with self.assertRaises(ValueError): install(self.build, self.data)
        self.assertFalse((self.data / 'viture-workspace/installed.json').exists())


if __name__ == '__main__':
    unittest.main()
