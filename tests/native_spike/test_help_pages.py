"""Help card pages (Ctrl+Alt+H): sections, the page keys (Ctrl+Alt+PgUp / PgDn), the `help` frame's `sections` and `turn`,
the renderer's `help_geometry` status line and the overview free pointer's clicks on the card's arrows and indicator.
How many rows fit a page is the renderer's (spike/tests/help_card_test.cpp).

Offline only: fake clocks and the free-pointer test rig. No compositor, renderer binary or glasses.
"""
import json
from pathlib import Path
import unittest
from unittest import mock

from tools.native_spike import bind_check, free_pointer as fp, view, xr_control
from tools.native_spike.ipc import decode_control

from test_free_pointer import G, PointerBase, X2, frames, last

ROOT = Path(__file__).resolve().parents[2]
# The help card in the middle of the 1920x1200 canvas (over the middle desktop), with its paged footer controls.
CARD = {'card': (560, 300, 800, 600), 'prev': (820, 840, 40, 40), 'indicator': (880, 840, 160, 40), 'next': (1060, 840, 40, 40)}
LINE = {'help_geometry': {k: list(v) for k, v in CARD.items()}}


def centre(r):
    return (r[0] + r[2] / 2, r[1] + r[3] / 2)


class BindAndRowTests(unittest.TestCase):
    def test_page_keys_are_run_binds_with_one_help_row(self):
        chords = {op: chord for chord, op in view.BINDS}
        self.assertEqual(chords['help_prev'], 'CTRL + ALT + Page_Up')
        self.assertEqual(chords['help_next'], 'CTRL + ALT + Page_Down')
        self.assertEqual(view.HELP_PAGE_OPS, {'help_prev': -1, 'help_next': 1})
        self.assertIn(['Ctrl+Alt+PgUp / PgDn', 'Help: previous / next page'], view.help_rows())
        for op in view.HELP_PAGE_OPS:
            self.assertIn(op, xr_control.OPS)
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(decode_control(frame)['op'], op)
        self.assertNotIn('help_prev', view.USER_OPS)  # keys only, not panel buttons

    def test_the_renderers_page_hint_names_the_real_keys(self):
        hud = (ROOT / 'spike/include/xr/hud.hpp').read_text()
        self.assertIn('kHelpPageHint = "Ctrl+Alt+PgUp / PgDn ', hud)

    def test_rows_are_grouped_in_sections_that_cover_every_row_once(self):
        rows, sections = view.help_rows(), view.help_sections()
        self.assertEqual(sum(sections), len(rows))
        self.assertTrue(all(n >= 1 for n in sections))
        self.assertEqual(len(rows), len({k for k, _ in rows}))
        self.assertEqual(rows[0][0], 'Ctrl+Alt+Up')                        # moving around first
        self.assertEqual(rows[sum(sections[:-1])][0], 'Ctrl+Alt+H')         # this card's keys last
        self.assertEqual(rows[-1][0], 'Ctrl+Alt+PgUp / PgDn')

    def test_ops_outside_every_section_and_extra_rows_still_show(self):
        with mock.patch.object(view, 'HELP_SECTIONS', (('zoom_in',),)), mock.patch.object(view, 'EXTRA_HELP_ROWS', (('Mouse', 'Free pointer'),)):
            rows, sections = view.help_rows(), view.help_sections()
        self.assertEqual(len(rows), len(view.BINDS) - len(view.HELP_PAIRS) + 1)
        self.assertEqual(sections, [len(rows) - 1, 1])
        self.assertEqual(rows[-1], ['Mouse', 'Free pointer'])

    def test_many_rows_need_no_cap_below_the_renderers(self):
        extra = tuple((f'Key {i}', f'Action {i}') for i in range(40))
        with mock.patch.object(view, 'EXTRA_HELP_ROWS', extra):
            payload = json.loads(view.help_frame('run', 's', 3, True))['payload']
        self.assertEqual(len(payload['rows']), len(view.help_rows()) + 40)
        self.assertLessEqual(len(payload['rows']), 64)  # spike/include/xr/ipc.hpp kMaxHelpRows
        self.assertEqual(sum(payload['sections']), len(payload['rows']))

    def test_bind_check_treats_xkb_aliases_as_one_key(self):
        self.assertEqual(bind_check.normalize('CTRL + ALT + Page_Up'), bind_check.normalize('ctrl + alt + Prior'))
        self.assertEqual(bind_check.normalize('CTRL + ALT + Page_Down'), bind_check.normalize('CONTROL + ALT + next'))
        self.assertNotEqual(bind_check.normalize('CTRL + ALT + Page_Up'), bind_check.normalize('CTRL + ALT + Page_Down'))


class FrameTests(unittest.TestCase):
    def test_help_frame_carries_sections_and_an_optional_turn(self):
        shown = json.loads(view.help_frame('run', 's', 3, True))
        self.assertEqual(set(shown['payload']), {'visible', 'rows', 'sections'})
        turned = json.loads(view.help_frame('run', 's', 4, True, turn=-1))['payload']
        self.assertEqual(turned['turn'], -1)
        for bad in (2, -2, 0.5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError): view.help_frame('run', 's', 4, True, turn=bad)


class ControlTests(PointerBase):
    def test_page_keys_turn_only_an_open_card(self):
        self.assertIsNone(self.control.on_message({'op': 'help_next', 'payload': {}}))  # closed: nothing
        self.op('help')
        nxt = self.op('help_next', ops={'help'})
        self.assertEqual(len(nxt), 1)
        self.assertEqual((nxt[0]['payload']['visible'], nxt[0]['payload']['turn']), (True, 1))
        self.assertEqual(self.op('help_prev', ops={'help'})[0]['payload']['turn'], -1)
        self.op('help')  # closed
        reopened = self.op('help', ops={'help'})[0]['payload']
        self.assertTrue(reopened['visible'])
        self.assertNotIn('turn', reopened)  # the renderer starts a reopened card on page 1

    def test_turning_pages_keeps_the_card_up(self):
        self.op('help')
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
        self.op('help_next')
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
        self.assertIsNone(frames(self.control.poll(), {'help'}) or None)
        self.assertTrue(self.control.help_visible)

    def test_typing_mode_and_a_video_leave_nothing_to_turn(self):
        self.op('help')
        self.op(view.TYPING_OP)
        self.assertFalse(self.control.help_visible)
        self.assertEqual(self.op('help_next', ops={'help'}), [])


class GeometryTests(unittest.TestCase):
    def test_the_renderers_line_parses(self):
        self.assertEqual(fp.parse_help_geometry(LINE['help_geometry']), CARD)
        self.assertIsNone(fp.parse_help_geometry(None))
        self.assertEqual(fp.parse_help_geometry({'card': [1, 2, 3, 4]}), {'card': (1, 2, 3, 4)})  # a one-page card

    def test_malformed_lines_are_refused(self):
        good = LINE['help_geometry']
        for bad in ([], {}, {'prev': [0, 0, 1, 1]}, dict(good, card=[0, 0, 0, 5]), dict(good, card=[0, 0, 5]),
                    dict(good, card=[0, 0, 5, 1.5]), dict(good, card=[0, 0, 5, True]), dict(good, card=[0, 99999, 5, 5]),
                    {'card': good['card'], 'prev': good['prev']}, dict(good, extra=[0, 0, 1, 1])):
            with self.subTest(bad=bad), self.assertRaises(ValueError): fp.parse_help_geometry(bad)

    def test_supervisor_routes_the_line_and_its_null(self):
        from tools.native_spike.supervisor import Supervisor
        control = mock.Mock()
        sup = Supervisor(mock.Mock(), lambda: None, control=control)
        sup.status_bytes(json.dumps(LINE).encode() + b'\n')
        control.set_help_geometry.assert_called_once_with(CARD)
        sup.status_bytes(b'{"help_geometry":null}\n')
        control.set_help_geometry.assert_called_with(None)
        sup.status_bytes(b'{"help_geometry":{"card":[0,0,0,0]}}\n')  # refused: no stale card is kept either
        self.assertEqual(control.set_help_geometry.call_args_list[-1], mock.call(None))
        self.assertEqual(sup.status, {})

    def test_hit_prefers_the_controls_then_the_card(self):
        self.assertEqual(fp.help_hit(CARD, centre(CARD['prev'])), ('help', 'prev'))
        self.assertEqual(fp.help_hit(CARD, centre(CARD['next'])), ('help', 'next'))
        self.assertEqual(fp.help_hit(CARD, centre(CARD['indicator'])), ('help', 'indicator'))
        self.assertEqual(fp.help_hit(CARD, (600, 320)), ('help', 'card'))
        self.assertIsNone(fp.help_hit(CARD, (550, 320)))
        self.assertEqual(fp.help_hit(CARD, (550, 320), margin=fp.ENTER_PX * 3), ('help', 'card'))
        self.assertIsNone(fp.help_hit(None, (600, 320)))


class PointerTests(PointerBase):
    def open_card(self):
        self.op('help')
        self.control.set_help_geometry(CARD)

    def onto(self, r, ops=None):
        x, y = centre(r)
        return self.move_canvas(x - self.p.pointer[0], y - self.p.pointer[1], ops=ops)

    def test_a_cursor_onto_the_open_card_turns_into_the_free_dot(self):
        self.open_card()
        out = self.at(X2 + 960, 540)  # the middle desktop's centre: under the card
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1], 'free')  # left button bound to the run, cursor hidden
        self.assertEqual(self.p.hover, ('help', 'card'))
        self.assertTrue(last(out, 'overview_pointer')['visible'])

    def test_a_card_opening_over_a_still_cursor_takes_the_click_from_the_window(self):
        self.at(X2 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')
        self.open_card()  # no cursor motion after this
        self.control.poll()
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1], 'free')
        self.assertEqual(self.p.hover, ('help', 'card'))

    def test_with_the_card_closed_its_geometry_is_ignored(self):
        self.control.set_help_geometry(CARD)  # e.g. a line still in flight after the close
        self.at(X2 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')

    def test_clicks_on_the_arrows_and_indicator_turn_pages(self):
        self.open_card()
        self.at(X2 + 960, 540)
        for name, turn in (('next', 1), ('prev', -1), ('indicator', 1)):
            with self.subTest(name=name):
                self.onto(CARD[name])
                self.assertEqual(self.p.hover, ('help', name))
                out = self.op('resize_press')
                help_ = last(out, 'help')
                self.assertEqual((help_['visible'], help_.get('turn')), (True, turn))
                self.assertEqual(self.op('resize_release', ops={'help', 'placement', 'view'}), [])
        self.assertEqual(self.p.help_clicks, 3)
        self.assertEqual(self.p.mode, 'free')

    def test_a_click_elsewhere_on_the_card_does_nothing_and_leaves_a_panel_open(self):
        self.control.state.zoomed = False
        self.control.on_message({'op': 'menu_toggle', 'payload': {}})
        self.assertTrue(self.control.state.menu_open)
        self.open_card()
        self.at(X2 + 960, 540)
        self.onto((600, 320, 2, 2))
        out = self.op('resize_press') + self.op('resize_release')
        self.assertFalse(any(f['op'] in ('help', 'placement', 'placement_menu', 'view') for f in out))
        self.assertTrue(self.control.state.menu_open)

    def test_the_dot_leaves_the_card_into_a_desktop_only_clear_of_it(self):
        self.open_card()
        self.at(X2 + 960, 540)
        card = CARD['card']
        self.onto((card[0] + 10, 600, 0, 0))
        self.move_canvas(-10 - fp.ENTER_PX / 2, 0)  # just off the card's left edge, over the left desktop
        self.assertEqual(self.p.mode, 'free')
        self.move_canvas(-fp.ENTER_PX * 2, 0)
        self.assertEqual(self.p.mode, 'desktop')


if __name__ == '__main__':
    unittest.main()
