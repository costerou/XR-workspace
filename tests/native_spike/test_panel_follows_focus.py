"""An open placement panel follows the focus (view.ViewControl.follow_focus): when the selection moves to another desktop in the
overview, that desktop's panel opens and the old one closes. Only while a panel is open: otherwise nothing opens."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, profiles, view

NAMES = ['XR-1', 'XR-2', 'XR-3']
MOVED = {'x': 100, 'y': 0, 'distance': 100, 'curvature': 0}


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 100.0
        self.saved = []
        self.binds = []
        store = mock.Mock()
        store.save.side_effect = lambda placements: self.saved.append(placements)
        self.c = view.ViewControl(view.ViewState(NAMES), 'run', 's', lambda name: None, profiles.LayoutStore(Path(self.tmp.name) / 'layout.json'),
                                  None, placements=store, menu_binds=self.binds.append, clock=lambda: self.now)

    def send(self, op):
        return [json.loads(line) for line in (self.c.on_message({'op': op, 'payload': {}}) or b'').splitlines()]

    def menu(self, frames):
        return next((f['payload'] for f in frames if f['op'] == 'placement_menu'), None)

    def test_the_panel_goes_where_the_selection_goes(self):
        self.send('menu_toggle')
        self.assertEqual(self.c.state.menu_desktop, 0)
        out = self.send('next')
        self.assertEqual([f['op'] for f in out], ['view', 'placement_menu'])  # the highlight, then the panel
        self.assertEqual(out[0]['payload']['selected'], 1)
        menu = self.menu(out)
        self.assertEqual((menu['open'], menu['desktop']), (True, 1))
        self.assertEqual(self.c.state.menu_desktop, 1)
        self.assertEqual(self.menu(self.send('prev'))['desktop'], 0)

    def test_only_one_panel_is_ever_open(self):
        self.send('menu_toggle')
        self.send('next')
        self.assertTrue(self.c.state.menu_open)
        self.assertEqual(self.c.state.menu_desktop, self.c.state.selected)

    def test_with_every_pill_collapsed_nothing_opens(self):
        out = self.send('next')
        self.assertEqual([f['op'] for f in out], ['view'])
        self.assertFalse(self.c.state.menu_open)

    def test_a_key_that_moves_nothing_moves_no_panel(self):
        # The panel is on XR-3, opened by its pill, with XR-1 selected: Ctrl+Alt+Left at the row's end changes no selection.
        self.c.pill_click(2)
        self.assertEqual(self.send('prev'), [])
        self.assertEqual(self.c.state.menu_desktop, 2)

    def test_the_panel_catches_up_with_a_selection_it_was_ahead_of(self):
        self.c.pill_click(2)
        self.send('next')  # XR-2 selected: the panel moves from XR-3 to it
        self.assertEqual(self.c.state.menu_desktop, 1)

    def test_a_selection_onto_the_panels_own_desktop_changes_nothing_more(self):
        self.c.pill_click(1)
        self.assertEqual([f['op'] for f in self.send('next')], ['view'])  # XR-2: where the panel already is
        self.assertEqual(self.c.state.menu_desktop, 1)

    def test_the_panel_being_left_is_saved_at_once(self):
        self.send('menu_toggle')
        self.send('menu_inc')  # a change, waiting for its debounce
        self.assertIsNotNone(self.c.save_due)
        self.assertEqual(self.saved, [])
        self.send('next')
        self.assertIsNone(self.c.save_due)
        self.assertEqual(len(self.saved), 1)
        self.assertNotEqual(self.saved[0][0], view.ViewState(NAMES).placements[0])

    def test_the_panels_keys_stay_installed(self):
        self.send('menu_toggle')
        self.c.join_focus()
        self.assertEqual(self.binds, [True])
        self.send('next')
        self.c.join_focus()
        self.assertEqual(self.binds, [True])  # no remove and reinstall: the panel never closed

    def test_the_setting_stays_highlighted_and_lists_close(self):
        self.send('menu_toggle')
        self.send('menu_field_next')
        self.c.state.background_picker = True
        menu = self.menu(self.send('next'))
        self.assertEqual(menu['field'], 'y')
        self.assertFalse(menu['background_picker'])

    def test_remove_disarms(self):
        self.send('menu_toggle')
        self.c.state.remove_armed = True
        self.assertFalse(self.menu(self.send('next'))['armed'])

    def test_a_held_slider_lets_go(self):
        self.send('menu_toggle')
        self.c.state.hold_surface(dict(MOVED))
        self.send('next')
        self.assertIsNone(self.c.state.menu_surface)

    def test_not_while_maximized(self):
        # A maximize collapses the panel, so there is none to follow.
        self.send('menu_toggle')
        self.send('zoom_in')
        self.assertFalse(self.c.state.menu_open)
        self.send('next')
        self.assertFalse(self.c.state.menu_open)

    def test_the_helper_refuses_in_the_states_with_no_panel_to_move(self):
        self.send('menu_toggle')
        self.c.state.selected = 1
        for state in ('typing', 'zoomed'):
            setattr(self.c.state, state, True)
            self.assertEqual(self.c.follow_focus(), b'')
            setattr(self.c.state, state, False)
        self.c.video_active = True
        self.assertEqual(self.c.follow_focus(), b'')
        self.c.video_active = False
        self.assertNotEqual(self.c.follow_focus(), b'')
        self.assertEqual(self.c.follow_focus(), b'')  # it is there now

    def test_a_click_selecting_a_desktop_moves_it_too(self):
        self.send('menu_toggle')
        frames = [json.loads(line) for line in self.c.select_desktop(2).splitlines()]
        self.assertEqual([f['op'] for f in frames], ['view', 'placement_menu'])
        self.assertEqual((self.c.state.selected, self.c.state.menu_desktop, self.c.state.menu_open), (2, 2, True))
        self.assertEqual(self.c.select_desktop(2), b'')  # it is there

    def test_a_click_on_the_selected_desktop_brings_a_panel_tab_took_elsewhere(self):
        self.send('menu_toggle')
        self.send('menu_desktop_next')
        self.assertEqual((self.c.state.selected, self.c.state.menu_desktop), (0, 1))
        self.assertEqual(self.menu([json.loads(line) for line in self.c.select_desktop(0).splitlines()])['desktop'], 0)

    def test_a_click_selecting_with_every_pill_collapsed_opens_nothing(self):
        frames = [json.loads(line) for line in self.c.select_desktop(1).splitlines()]
        self.assertEqual([f['op'] for f in frames], ['view'])
        self.assertFalse(self.c.state.menu_open)

    def join(self):
        for t in self.c._menu_threads: t.join(2)
        return self.binds

    def test_a_press_on_a_desktop_moves_the_panel_and_gives_the_keyboard_back(self):
        self.send('menu_toggle')
        self.send('menu_desktop_next')  # the panel on XR-2, XR-1 selected: the press is on XR-1
        self.assertEqual(self.menu([json.loads(line) for line in self.c.desktop_press().splitlines()])['desktop'], 0)
        self.assertTrue(self.c.state.menu_open)
        self.assertEqual(self.join(), [True, False])  # R, Shift+Delete and the rest are the window's again

    def test_the_panel_keys_stay_off_through_more_presses_and_moves(self):
        self.send('menu_toggle')
        self.c.desktop_press()
        self.c.desktop_press()
        self.send('next')
        self.assertEqual(self.c.state.menu_desktop, 1)
        self.assertEqual(self.join(), [True, False])

    def test_using_the_panel_brings_its_keys_back(self):
        self.send('menu_toggle')
        self.c.desktop_press()
        self.c.panel_keys_on()
        self.c.panel_keys_on()  # once
        self.assertEqual(self.join(), [True, False, True])

    def test_keys_come_back_with_the_next_panel(self):
        self.send('menu_toggle')
        self.c.desktop_press()
        self.send('menu_toggle')
        self.assertFalse(self.c.menu_keys_off)
        self.send('menu_toggle')
        self.assertEqual(self.join(), [True, False, True])

    def test_a_reload_leaves_the_keys_off(self):
        self.send('menu_toggle')
        self.c.desktop_press()
        self.join()
        self.c._menu_binds_installed = False  # a config reload dropped them
        self.c._sync_menu_binds(force=True)
        self.assertEqual(self.join(), [True, False])

    def test_a_press_with_no_panel_does_nothing(self):
        self.assertEqual(self.c.desktop_press(), b'')
        self.assertFalse(self.c.menu_keys_off)
        self.assertEqual(self.join(), [])

    def test_the_pointer_selecting_a_desktop_moves_it_too(self):
        self.send('menu_toggle')
        pointer = fp.OverviewPointer(self.c)
        frames = [json.loads(line) for line in pointer._select(2).splitlines()]
        self.assertEqual([f['op'] for f in frames], ['view', 'placement_menu'])
        self.assertEqual(self.menu(frames)['desktop'], 2)
        self.assertEqual(pointer._select(2), b'')  # already selected


if __name__ == '__main__':
    unittest.main()
