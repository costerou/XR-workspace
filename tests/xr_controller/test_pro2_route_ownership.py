"""Whose run is a Pro 2 session? A panel route session is the controller's own; a supervised bundle run stays external.

The real probe, controller and monitor (test_incident_supervised.Story), with the route's real consent.
"""
import hashlib
import json

from tests.xr_controller.test_incident_supervised import RUN, SUPERVISED, Story
from tools.native_spike.panel_consent import build_consent, write_consent
from tools.pro2_diagnostic.candidate import SHA256
from tools.xr_controller import pro2_route as route


class Ownership(Story):
    def setUp(self):
        super().setUp()
        self.config = self.root / 'config'; self.config.mkdir(mode=0o700)
        data = self.root / 'data'
        bundle = data / 'viture-workspace' / route.BUNDLE_DIR / 'bundle'; (bundle / 'bin').mkdir(parents=True)
        files = {}
        for name in ('pro2-sdk-bridge', 'xr-native-spike', 'libxr-ownership.so'):
            path = bundle / 'bin' / name; path.write_bytes(b'inert ' + name.encode()); files['bin/' + name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (bundle / 'manifest.json').write_text(json.dumps(dict(schema=1, sdk_sha256=SHA256, files=files)))
        self.route = route.write_pin(bundle, data)
        base = self.probe
        self.probe = lambda: dict(base(), model='pro2', pro2_route=self.route)
        self.start()
        self.controller.route_config = self.config

    def route_run(self):
        """What confirm_start leaves: the consent written where every controller run's goes, and a unit."""
        write_consent(self.fd, build_consent(dict(run_id=RUN, instance='sig', output='DP-2', recovery_output='DP-1', model='pro2', tracking='pro2-sdk',
            mode_switch=False, desktops=[dict(width=1920, height=1080, scale=1)], plugin=self.route['plugin'], renderer=self.route['renderer'],
            pro2_route=route.consent_block(self.route, 44.5))))
        self.units.up.add(RUN)
        return self.journal(RUN)

    def test_a_route_session_is_the_controllers_own(self):
        self.route_run()
        self.tick(5)
        snap = self.snap()
        self.assertEqual((snap['state'], snap['run_id']), ('running', RUN))
        self.assertEqual(self.probe()['external'], [])
        self.assertEqual([j['run_id'] for j in self.probe()['journals']], [RUN])
        self.assertEqual(self.kinds(), [])

    def test_stop_reaches_a_route_session(self):
        self.route_run(); self.tick(2)
        stopped = []
        self.units.stop = stopped.append
        self.assertTrue(self.controller.request(dict(op='stop'))['ok'])
        self.assertEqual(stopped, [RUN])

    def test_a_route_session_that_dies_offers_recover_not_external(self):
        path = self.route_run(); self.tick(2)
        self.units.up.discard(RUN)
        self.tick(8)
        self.assertEqual(self.snap()['state'], 'recovery-needed')
        reply = self.controller.request(dict(op='begin_recover'))
        self.assertTrue(reply['ok'], reply); self.assertEqual(reply['journal'], str(path))

    def test_a_supervised_bundle_run_stays_external_with_the_route_on(self):
        self.supervised()
        self.tick(3)
        snap = self.snap()
        self.assertEqual((snap['state'], snap['run_id']), ('external-run', None))
        self.assertEqual([j['run_id'] for j in self.probe()['external']], [SUPERVISED])
        self.assertEqual(self.kinds(), [])
        self.assertFalse(self.controller.request(dict(op='begin_start'))['ok'])       # its outputs and glasses are taken
        self.assertFalse(self.controller.request(dict(op='set_pro2_projection', projection=46.5))['ok'])

    def test_the_route_files_are_not_mistaken_for_consents(self):
        # The bridge's private folder sits at <state>/<run_id>/: a folder, with no .json of its own.
        (self.state / SUPERVISED).mkdir()
        self.supervised()
        self.tick(2)
        self.assertEqual(self.snap()['state'], 'external-run')
