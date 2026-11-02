"""Offscreen panel regression: idle "Save placement" must persist what the sliders show, even when a controller
state push reloads settings between editing and saving (Cody's glasses test, 2026-11-01: all placements saved flat).

Drives the real Panel/Launcher/DesktopEditor/PlacementEditor in quickshell (offscreen, scratch HOME) against the canned
fake controller. Skipped when quickshell or the Omarchy shell components are not installed. Never touches a live session.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from tests.xr_controller.fake_controller import FakeServer
from tools.native_spike.geometry import DEFAULT_PLACEMENT

REPO = Path(__file__).resolve().parents[2]
SHELL = Path('/usr/share/omarchy/shell')
QML = r'''import QtQuick
import Quickshell
import "plugin" as Plugin
ShellRoot {
 Plugin.Panel { id:panel; socketPath:"SOCK"; Component.onCompleted: open({desktop:0}) }
 property var steps: [STEPS]
 property int i: 0
 function find(o, name) { if (!o) return null; if (o.objectName === name) return o;
   const kids = [].concat(o.children || [], o.contentItem ? [o.contentItem] : []);
   for (const k of kids) { const r = find(k, name); if (r) return r } return null }
 function editorOf(s) { let o = s; while (o && typeof o.setField !== "function") o = o.parent; return o }
 Timer { interval:1200; repeat:true; running:true; onTriggered: {
   if (i >= steps.length) { console.log("DONE"); Qt.quit(); return }
   const st = steps[i]; panel.selectedDesktop = st[0];
   Qt.callLater(function() {
     const ed = editorOf(find(panel.renderSurface, "placement_" + st[1]));
     if (!ed) { console.log("NO EDITOR"); Qt.quit(); return }
     ed.setField(st[1], st[2]);
     panel.loadSettings();  // what every controller state push does (about 1 Hz while a session runs)
     saveLater.ed = ed; saveLater.restart() })
 } }
 Timer { id:saveLater; property var ed; interval:400; onTriggered: {
     console.log("step", i, "draft at Save", JSON.stringify(ed.draft));
     find(ed, "placementSave").clicked(); i++ }
 }
 Timer { interval:10000; running:true; onTriggered: Qt.quit() }
}
'''


@unittest.skipUnless(shutil.which('quickshell') and (SHELL / 'Commons').exists(), 'quickshell / Omarchy shell not installed')
class PanelPlacementSaveTests(unittest.TestCase):
    EXPECT = [dict(DEFAULT_PLACEMENT, yaw=20), dict(DEFAULT_PLACEMENT, curvature=60), dict(DEFAULT_PLACEMENT, yaw=-20)]

    def run_panel(self, root, sock):
        """Cody's sequence: XR-1 yaw +20, XR-3 yaw -20, XR-2 curvature 60, each followed by Save placement."""
        shutil.copytree(REPO / 'omarchy-plugin/cody.viture-workspace', root / 'plugin')
        for name in ('Commons', 'Ui'): (root / name).symlink_to(SHELL / name)
        theme = root / '.local/state/omarchy/current/theme'; theme.mkdir(parents=True)
        (theme / 'colors.toml').write_text('foreground = "#e7eeea"\nbackground = "#1b1f1f"\naccent = "#b9e78a"\n')
        steps = ','.join(f'[{d},"{k}",{v}]' for d, k, v in ((0, 'yaw', 20), (2, 'yaw', -20), (1, 'curvature', 60)))
        (root / 'shell.qml').write_text(QML.replace('SOCK', str(sock)).replace('STEPS', steps))
        env = dict(os.environ, HOME=str(root), XDG_RUNTIME_DIR=str(root), XDG_CONFIG_HOME=str(root), XDG_STATE_HOME=str(root),
                   XDG_CACHE_HOME=str(root), QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='', QSG_RHI_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QT_QUICK_BACKEND='software')
        env.pop('WAYLAND_DISPLAY', None); env.pop('DISPLAY', None)
        result = subprocess.run(['quickshell', '-p', str(root / 'shell.qml')], env=env, capture_output=True, text=True, timeout=25)
        log = result.stdout + result.stderr
        self.assertIn('DONE', log, log[-3000:])
        self.assertNotIn('NO EDITOR', log)

    def test_idle_saves_survive_settings_reloads(self):
        with tempfile.TemporaryDirectory(prefix='xr-panel-placement-') as tmp:
            root = Path(tmp); sock = root / 'c.sock'
            server = FakeServer(sock, 'idle')
            server.profile['placements'] = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            try: self.run_panel(root, sock)
            finally: server.shutdown(); server.server_close(); worker.join()
            sent = [m['settings'] for m in server.requests if m.get('op') == 'set_settings']
            self.assertEqual(len(sent), 3, json.dumps(sent))
            self.assertEqual(server.profile['placements'], self.EXPECT)
            unexpected = [m for m in server.requests if m.get('op') not in ('status', 'get_settings', 'set_settings')]
            self.assertEqual(unexpected, [])

    def test_idle_saves_reach_profiles_json_through_real_controller(self):
        """Same panel sequence through the real Controller, server and profile writer to profiles.json on disk."""
        from unittest import mock
        from tools.native_spike import profiles
        from tools.xr_controller.__main__ import Store
        from tools.xr_controller.server import Server
        from tools.xr_controller.state import Controller
        with tempfile.TemporaryDirectory(prefix='xr-panel-placement-real-') as tmp:
            root = Path(tmp); config = root / 'config'; state = root / 'state'
            state.mkdir(mode=0o700); fd = os.open(state, os.O_DIRECTORY)
            observed = dict(model='luma-ultra', output='DP-2', reason=None, instance='offline', monitors=[dict(name='DP-2'), dict(name='DP-1')],
                            installed={}, plugins=[], journals=[])
            class Units:  # nothing is running; any run control would be a test failure
                def alive(self, run_id): return False
                def control(self, *args): raise AssertionError('no run control expected')
                def stop(self, run_id): raise AssertionError('no stop expected')
            def no_spawn(*args): raise AssertionError('no session may start')
            with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config)}):
                path = profiles.profiles_path()
                controller = Controller(lambda: 0.0, lambda: dict(observed), no_spawn, Units(), Store(), fd, state)
                (root / 'rt').mkdir(mode=0o700)
                sock = root / 'rt' / 'controller.sock'
                server = Server(controller, sock)
                stop = threading.Event()
                def loop():
                    while not stop.is_set(): server.step(0.05)
                worker = threading.Thread(target=loop, daemon=True); worker.start()
                try: self.run_panel(root, sock)
                finally:
                    stop.set(); worker.join(5); server.close(); os.close(fd)
                self.assertTrue(path.exists())
                self.assertEqual(json.loads(path.read_text())['profiles']['luma-ultra']['placements'], self.EXPECT)
                self.assertEqual(profiles.load_profile(path, 'luma-ultra')['placements'], self.EXPECT)


if __name__ == '__main__':
    unittest.main()
