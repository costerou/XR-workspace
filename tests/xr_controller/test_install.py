"""Pinned install: temp dirs, inert native binaries, and a local Python launcher fixture."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from tools.xr_controller import install as install_module
from tools.xr_controller.install import (DRIVER_3DOF_SHA256, DRIVER_3DOF_VERSION, STANDARD_DRIVER, install, load_installed, tracking_driver_path,
                                         tracking_drivers, verify_pinned)


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

    def test_the_depth_engine_launcher_needs_its_environment(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'bin'; target.mkdir()
            self.assertIsNone(install_module._write_engine(target, home=Path(d) / 'nobody'))
            self.assertFalse((target / install_module.ENGINE).exists())  # no environment: 3D says it is not installed

    def test_the_depth_engine_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            home, target = Path(d) / 'home', Path(d) / 'bin'
            target.mkdir(); python = home.joinpath(*install_module.ENGINE_PYTHON); python.parent.mkdir(parents=True); python.touch()
            dest = install_module._write_engine(target, home=home, repo='/some/checkout')
            self.assertEqual(dest, target / 'xr-depth-engine')
            self.assertEqual(dest.stat().st_mode & 0o777, 0o500)
            text = dest.read_text()
            self.assertIn(f'exec nice -n 5 {python} -I -c', text)  # below the renderer, and nothing from the working directory
            self.assertIn("sys.path.insert(0,'/some/checkout')", text)
            self.assertTrue(text.rstrip().endswith('serve --supervised "$@"'))
            from tools.native_spike import immersive
            self.assertEqual(install_module.ENGINE, immersive.ENGINE_NAME)
            self.assertEqual(immersive.find_engine(target / 'xr-native-spike'), dest)  # where a run looks

    def test_the_depth_engine_launcher_refuses_a_path_it_cannot_quote(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); python = home.joinpath(*install_module.ENGINE_PYTHON); python.parent.mkdir(parents=True); python.touch()
            self.assertRaises(ValueError, install_module._write_engine, home, home=home, repo="/a'; rm -rf x")

    def test_a_setup_gone_since_takes_the_launcher_away(self):
        with tempfile.TemporaryDirectory() as d:
            home, target = Path(d) / 'home', Path(d) / 'bin'
            target.mkdir(); python = home.joinpath(*install_module.ENGINE_PYTHON); python.parent.mkdir(parents=True); python.touch()
            install_module._write_engine(target, home=home, repo='/x')
            python.unlink()
            install_module._write_engine(target, home=home, repo='/x')
            self.assertFalse((target / install_module.ENGINE).exists())

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

    def test_the_quiet_driver_is_pinned_beside_the_renderer(self):
        build, data = Path(self.tmp.name) / 'qb', Path(self.tmp.name) / 'qd'
        build.mkdir()
        for name, body in ((install_module.RENDERER, b'r'), (install_module.PLUGIN, b'p'), (install_module.SHIM, b's')): (build / name).write_bytes(body)
        entry = install(build, data)
        self.assertEqual(set(entry), {'plugin', 'renderer'})  # installed.json stays what load_installed accepts
        load_installed(data)
        shim = install_module.load_shim(data)
        self.assertEqual(Path(shim).read_bytes(), b's'); self.assertNotIn('qb', shim)
        os.chmod(shim, 0o700); Path(shim).write_bytes(b'changed')
        with self.assertRaises(ValueError): install_module.load_shim(data)

    def test_an_install_without_the_quiet_driver_runs_the_plain_one(self):
        build, data = Path(self.tmp.name) / 'nb', Path(self.tmp.name) / 'nd'
        build.mkdir()
        for name, body in ((install_module.RENDERER, b'r'), (install_module.PLUGIN, b'p'), (install_module.SHIM, b's')): (build / name).write_bytes(body)
        install(build, data)
        (build / install_module.SHIM).unlink(); install(build, data)
        self.assertIsNone(install_module.load_shim(data))

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


class Pro2EngineInstallTests(unittest.TestCase):
    """Inert native bundle; the launcher runs only a local Python fixture."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home, self.repo, self.data = (self.root / name for name in ('home', 'repo', 'data'))
        self.home.mkdir(); self.repo.mkdir()
        patcher = unittest.mock.patch.object(install_module.Path, 'home', return_value=self.home)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = unittest.mock.patch.object(install_module, 'REPO', self.repo)
        patcher.start(); self.addCleanup(patcher.stop)

    def build_bundle(self, build_dir, destination):
        from tools.pro2_diagnostic.candidate import SHA256
        (destination / 'bin').mkdir(parents=True)
        files = {}
        for name in ('pro2-sdk-bridge', 'xr-native-spike', 'libxr-ownership.so'):
            path = destination / 'bin' / name
            path.write_bytes(b'inert ' + name.encode())
            files['bin/' + name] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = destination / 'manifest.json'
        manifest.write_text(json.dumps(dict(schema=1, sdk_sha256=SHA256, files=files)))
        manifest.chmod(0o400)

    def install_bundle(self):
        from tools.xr_controller import pro2_route
        install_module.install_pro2_bundle(self.root, self.data, builder=self.build_bundle)
        return Path(pro2_route.load_route(self.data)['bundle'])

    def test_missing_depth_environment_keeps_the_bundle_optional(self):
        from tools.pro2_runtime.launch import verify_manifest
        bundle = self.install_bundle()
        self.assertFalse((bundle / 'bin/xr-depth-engine').exists())
        self.assertNotIn('bin/xr-depth-engine', verify_manifest(bundle)[1]['files'])

    def test_depth_launcher_is_pinned_before_rename_and_runs_from_stable_checkout(self):
        from tools.native_spike.immersive import find_engine
        from tools.pro2_runtime.launch import verify_manifest
        previous = self.install_bundle()
        previous_manifest = (previous / 'manifest.json').read_bytes()
        python = self.home.joinpath(*install_module.ENGINE_PYTHON)
        python.parent.mkdir(parents=True); python.symlink_to(sys.executable)
        fixture = self.repo / 'tools/depth_engine'
        fixture.mkdir(parents=True)
        (fixture / '__main__.py').write_text('import json,sys\nprint(json.dumps([__file__,sys.argv[1:]]))\n')

        bundle = self.install_bundle()
        self.assertNotEqual(bundle, previous)
        self.assertEqual((previous / 'manifest.json').read_bytes(), previous_manifest)
        self.assertFalse((previous / 'bin/xr-depth-engine').exists())
        engine = find_engine(bundle / 'bin/xr-native-spike')
        self.assertIsNotNone(engine)
        manifest = verify_manifest(bundle)[1]
        self.assertEqual(manifest['files']['bin/xr-depth-engine'], hashlib.sha256(engine.read_bytes()).hexdigest())
        self.assertEqual(engine.stat().st_mode & 0o777, 0o500)
        self.assertEqual((bundle / 'manifest.json').stat().st_mode & 0o777, 0o400)
        output = json.loads(subprocess.check_output([str(engine), '--fixture'], text=True))
        self.assertEqual(output, [str(fixture / '__main__.py'), ['serve', '--supervised', '--fixture']])
        self.assertEqual(self.install_bundle(), bundle)


class TrackingDriverPinTests(unittest.TestCase):
    """The 3DoF test build. Temp dirs and fake bytes only; nothing here is executed."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.binary = self.data / 'viture-workspace/xrdriver-3dof' / DRIVER_3DOF_VERSION / 'xrDriver'

    def build(self, data=b'driver-bytes'):
        self.binary.parent.mkdir(parents=True, exist_ok=True); self.binary.write_bytes(data)
        patcher = unittest.mock.patch.object(install_module, 'DRIVER_3DOF_SHA256', hashlib.sha256(b'driver-bytes').hexdigest())
        patcher.start(); self.addCleanup(patcher.stop)

    def test_the_pin_is_a_full_hash_of_the_recorded_version(self):
        self.assertRegex(DRIVER_3DOF_SHA256, r'^[0-9a-f]{64}$')
        script = (Path(__file__).resolve().parents[2] / 'tools/xrdriver_3dof/build.sh').read_text()
        self.assertIn(f'VERSION={DRIVER_3DOF_VERSION}\n', script)  # the folder the build writes is the one pinned here

    def test_standard_is_always_offered_and_the_build_only_when_present(self):
        standard, test_build, _ = tracking_drivers(self.data)
        self.assertEqual((standard['key'], standard['path'], standard['available']), ('xrdriver', STANDARD_DRIVER, True))
        self.assertEqual((test_build['key'], test_build['path'], test_build['available']), ('3dof', str(self.binary), False))
        self.build()
        self.assertTrue(tracking_drivers(self.data)[1]['available'])

    def test_the_new_driver_is_offered_once_it_is_pinned(self):
        self.assertFalse(tracking_drivers(self.data)[2]['available'])
        pin = dict(path='/pinned/xr-luma-driver', sha256='b' * 64)
        luma = tracking_drivers(self.data, dict(luma_driver=pin))[2]
        self.assertEqual((luma['key'], luma['label'], luma['path'], luma['available']), ('luma', 'XR Luma driver', pin['path'], True))

    def test_the_new_driver_has_no_path_of_its_own_here(self):
        with self.assertRaisesRegex(ValueError, 'unknown tracking driver'): tracking_driver_path('luma', self.data)

    def test_standard_path_needs_no_pin(self):
        self.assertEqual(tracking_driver_path('xrdriver', self.data), STANDARD_DRIVER)

    def test_matching_build_gives_its_path(self):
        self.build()
        self.assertEqual(tracking_driver_path('3dof', self.data), str(self.binary))

    def test_changed_or_missing_build_is_refused_and_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'): tracking_driver_path('3dof', self.data)
        self.build(b'something else')
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'): tracking_driver_path('3dof', self.data)

    def test_a_symlink_is_not_the_pinned_file(self):
        self.build()
        real = self.data / 'real'; self.binary.rename(real); self.binary.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'): tracking_driver_path('3dof', self.data)

    def test_unknown_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'unknown tracking driver'): tracking_driver_path('/usr/bin/sh', self.data)

    def test_pin_leaves_installed_json_alone(self):
        build = self.data / 'build'; build.mkdir()
        (build / 'xr-native-spike').write_bytes(b'r'); (build / 'libxr-ownership.so').write_bytes(b'p')
        entry = install(build, self.data)
        self.assertEqual(set(load_installed(self.data)), {'plugin', 'renderer'})
        self.assertEqual(load_installed(self.data), entry)



if __name__ == '__main__':
    unittest.main()
