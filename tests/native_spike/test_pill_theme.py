"""The glass pills' theme colour, supervisor side. The pills and an open placement panel's highlights take the colour the
highlight border and glow use: Hyprland general:col.active_border, sent to the renderer as the `style` op at the start
of a run and after every config reload (an Omarchy theme switch). There is no pill-specific frame: the renderer
(Presentation::set_accent) recolours the pills' glass and re-renders an open panel itself. The renderer side is the
glass_theme ctest (spike/tests/glass_theme_test.cpp)."""
import json
from pathlib import Path
import selectors
import socket
import tempfile
import unittest
from unittest import mock

from tools.native_spike import video, view
from tools.native_spike.supervisor import Supervisor
from tools.native_spike.profiles import LayoutStore
from control_wire import in_order

NAMES = ['XR-1', 'XR-2', 'XR-3']
# Hyprland's getoption general:col.active_border replies for borders Omarchy themes set: a plain colour (vantablack's
# accent), a two-colour gradient (hackerman), a theme's own border (lumon) and Omarchy's default looknfeel gradient.
THEMES = [({'option': 'general:col.active_border', 'gradient': 'ff8d8d8d 0deg', 'set': True}, (0x8d, 0x8d, 0x8d)),
          ({'gradient': 'ee26a269 ee2ec27e 45deg', 'set': True}, (0x26, 0xa2, 0x69)),
          ({'gradient': 'fff2fcff 0deg', 'set': True}, (0xf2, 0xfc, 0xff)),
          ({'gradient': 'ee33ccff ee00ff99 45deg', 'set': True}, (0x33, 0xcc, 0xff))]


def frames(data):
    in_order(data)
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class PillThemeTests(unittest.TestCase):
    def control(self, border):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(Path(tmp.name) / 'l.json'),
                             None, border=border)
        self.addCleanup(c.join_focus, 2)
        return c

    def test_the_run_starts_with_the_theme_colour_for_the_pills(self):
        c = self.control(lambda: THEMES[1][0])
        out = frames(c.initial_frame())
        self.assertEqual([f['op'] for f in out][-1], 'style')
        self.assertEqual(out[-1]['payload'], {'border': [0x26, 0xa2, 0x69]})

    def test_a_theme_switch_with_a_pill_open_recolours_it_through_the_style_frame_only(self):
        replies = [THEMES[0][0]]
        c = self.control(lambda: replies[-1])
        c.initial_frame()
        opened = frames(c.on_message({'op': 'menu_toggle', 'payload': {}}))
        self.assertEqual([f['op'] for f in opened], ['placement_menu'])
        menu = c.state.menu()
        self.assertTrue(menu['open'])
        last_id = opened[-1]['request_id']
        for reply, rgb in THEMES[1:] + THEMES[:1]:  # through every border shape and back to the first theme
            with self.subTest(rgb=rgb):
                replies.append(reply)
                out = frames(c.on_events(b'configreloaded>>\n'))
                self.assertEqual([f['op'] for f in out], ['style'])  # nothing pill-specific: the renderer recolours them
                self.assertEqual(out[0]['payload'], {'border': list(rgb)})
                self.assertEqual(set(out[0]), {'schema', 'request_id', 'run_id', 'session', 'op', 'payload'})
                self.assertGreater(out[0]['request_id'], last_id); last_id = out[0]['request_id']
                self.assertEqual(c.state.menu(), menu)  # the open panel is left as it was
        self.assertIsNone(c.on_events(b'configreloaded>>\n'))  # a reload without a theme change sends nothing

    def test_a_reload_before_any_theme_colour_is_known_still_sends_the_first_one(self):
        replies = [None]  # the compositor could not answer at the start
        c = self.control(lambda: replies[-1])
        self.assertNotIn('style', [f['op'] for f in frames(c.initial_frame())])
        c.on_message({'op': 'menu_toggle', 'payload': {}})
        replies.append(THEMES[2][0])
        out = frames(c.on_events(b'configreloaded>>\n'))
        self.assertEqual([(f['op'], f['payload']) for f in out], [('style', {'border': [0xf2, 0xfc, 0xff]})])


class InstalledPathThemeSwitchTests(unittest.TestCase):
    """The installed run's wiring end to end, without a compositor: managed.py hands the supervisor a VideoControl wrapping
    the ViewControl, Hyprland's socket2 bytes arrive on the event socket, and the `style` frame goes out on the renderer
    socket, where Presentation::set_accent recolours the pills, border and glow together. Each theme switch, the same
    as omarchy-theme-set's `hyprctl reload`, recolours a running session: no restart."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.replies = [THEMES[0][0]]
        self.view = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(Path(tmp.name) / 'l.json'),
                                     None, border=lambda: self.replies[-1])
        self.addCleanup(self.view.join_focus, 2)
        self.control = video.VideoControl(self.view, threads=False)
        self.sup = Supervisor(mock.Mock(), lambda: None, control=self.control)
        self.hypr, self.view.events = socket.socketpair()
        self.renderer, self.renderer_peer = socket.socketpair()
        for s in (self.hypr, self.view.events, self.renderer, self.renderer_peer): self.addCleanup(s.close)
        self.view.events.setblocking(False); self.renderer_peer.settimeout(1)
        self.selector = selectors.DefaultSelector(); self.addCleanup(self.selector.close)
        self.selector.register(self.control.events, selectors.EVENT_READ)  # what Supervisor.run registers
        self.sup._send(self.control.initial_frame(), self.renderer)
        self.assertEqual(self.read()[-1], ('style', {'border': [0x8d, 0x8d, 0x8d]}))

    def read(self):
        data = b''
        while not data.endswith(b'\n'): data += self.renderer_peer.recv(65536)
        return [(f['op'], f['payload']) for f in frames(data)]

    def switch(self, events):
        self.hypr.sendall(events)
        for key, _ in self.selector.select(1): self.sup._events_ready(self.renderer, self.selector)

    def test_every_theme_switch_reaches_the_renderer_as_a_style_frame(self):
        for reply, rgb in THEMES[1:] + THEMES[:1]:
            with self.subTest(rgb=rgb):
                self.replies.append(reply)
                self.switch(b'activewindow>>kitty,x\nconfigreloaded>>\n')  # among other events, as socket2 sends them
                self.assertEqual(self.read(), [('style', {'border': list(rgb)})])
        self.assertEqual(self.view.reloads, len(THEMES))

    def test_a_reload_event_split_across_reads_still_recolours(self):
        self.replies.append(THEMES[3][0])
        self.switch(b'configrel')
        self.switch(b'oaded>>\n')
        self.assertEqual(self.read(), [('style', {'border': [0x33, 0xcc, 0xff]})])


if __name__ == '__main__':
    unittest.main()
