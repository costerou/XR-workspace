"""The options pill and menu are the placement pill and menu: same constants, same drawing code, hit zones that match what is drawn.
A guard against the two drifting apart again."""
import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.native_spike import free_pointer as fp, options_geometry as geo, options_menu as om, view

ROOT = Path(__file__).resolve().parents[2]
GLASS = (ROOT / 'spike/src/glass.cpp').read_text()
HEADER = (ROOT / 'spike/include/xr/options_geometry.hpp').read_text()


def body_of(name):
    start = GLASS.index(name)
    depth, i = 0, GLASS.index('{', start)
    while True:
        depth += {'{': 1, '}': -1}.get(GLASS[i], 0)
        i += 1
        if depth == 0: return GLASS[start:i]


class SharedDrawing(unittest.TestCase):
    def test_both_panels_draw_with_the_same_parts(self):
        menu, placement = body_of('HudImage render_options_panel('), body_of('HudImage render_glass_panel(')
        row = body_of('void options_row(')
        for part in ('panel_header(', 'dropdown_row('):
            self.assertIn(part, menu, part); self.assertIn(part, placement, part)
        for part in ('selected_band(', 'slider_parts(', 'panel_switch(', 'panel_button(', 'dropdown_box('):
            self.assertIn(part, row, part)
        self.assertIn('slider_row', placement)

    def test_the_menu_paints_no_panel_of_its_own(self):
        self.assertNotRegex(body_of('HudImage render_options_panel('), r'md_fill_rounded\(im, ?0, ?0')

    def test_the_menu_takes_the_theme_and_the_font(self):
        wayland = (ROOT / 'spike/src/wayland.cpp').read_text()
        self.assertIn('render_options_panel(options, options_font.get(), options_accent)', wayland)
        self.assertNotRegex(GLASS, r'glass_theme_scheme\(\{\.45')

    def test_sizes_come_from_the_placement_panel(self):
        for line in ('kOptionsW=kPanelW+kOptionsExtraW', 'kOptionsH=kPanelH', 'kOptionsPad=kPanelPad', 'kOptionsHeaderH=kPanelHeaderH', 'kOptionsRowH=kPanelRowH',
                     'kOptionsSectionTop=kPanelRowsTop', 'kOptionsFooterTop=kPanelFooterTop', 'kOptionsBoxH=kScaleBoxH', 'kOptionsBoxX0=kPanelTrackX0', 'kMaximizeH=kLevelBarH'):
            self.assertIn(line, HEADER)


class Geometry(unittest.TestCase):
    def test_the_options_pill_is_the_drag_pills_twin(self):
        for width in (300, 640, 1920):
            pill = fp.pill_rect((100, 50, width, 400))
            gear = geo.gear_rect(pill)
            self.assertEqual(gear[2:], pill[2:])
            self.assertEqual(gear[1], pill[1])
            self.assertEqual(gear[0], pill[0] + pill[2] + geo.GEAR_GAP)

    def test_no_control_overlaps_another(self):
        rects = [geo.close_box(), geo.section_box(), geo.scope_box(), (geo.PAD, geo.BODY_TOP, geo.W - 2 * geo.PAD, geo.BODY_H)]
        for i, a in enumerate(rects):
            self.assertTrue(a[0] >= 0 and a[1] >= 0 and a[0] + a[2] <= geo.W and a[1] + a[3] <= geo.H, a)
            for b in rects[i + 1:]:
                self.assertFalse(a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3], (a, b))

    def test_the_section_list_stays_on_the_panel_and_every_section_has_one_item(self):
        for i in range(geo.SECTIONS):
            x, y, w, h = geo.section_item(i)
            self.assertLessEqual(y + h, geo.H); self.assertLessEqual(x + w, geo.W)
            self.assertEqual(geo.section_at(x + w / 2, y + h / 2), i)
        self.assertEqual(geo.SECTIONS, len(om.CATEGORIES))

    def test_native_section_order_is_the_supervisors(self):
        self.assertEqual(tuple(re.findall(r'\{"([a-z0-9]+)","[^"]+"\}', HEADER)), om.CATEGORIES)

    def test_a_knob_stays_under_the_hand(self):
        row = dict(minimum=0, maximum=100, step=1)
        self.assertEqual(geo.range_value(row, geo.KNOB_LO), 0)
        self.assertEqual(geo.range_value(row, geo.KNOB_HI), 100)


class Focus(unittest.TestCase):
    def menu(self):
        c = view.ViewControl(view.ViewState(['XR-1', 'XR-2']), 'run', 'session', Mock(), Mock(), Mock())
        c.options.open('XR-1')
        return c

    def test_the_list_is_not_a_stop_on_the_way_past(self):
        c = self.menu()
        c.options.intent('options_focus', focus=geo.FOCUS_SECTION)
        c.options_key('Down')
        self.assertEqual(c.options.snapshot()['focus'], geo.FOCUS_CLOSE)
        c.options_key('Up')
        self.assertEqual(c.options.snapshot()['focus'], geo.FOCUS_SECTION)
        c.options_key('Tab')
        self.assertEqual(c.options.snapshot()['focus'], geo.FOCUS_CLOSE)

    def test_picking_a_section_shuts_the_list(self):
        c = self.menu()
        c.options.intent('options_focus', focus=geo.FOCUS_LIST)
        c.options.intent('options_category', category='view')
        d = c.options.snapshot()
        self.assertEqual((d['category'], d['focus']), ('view', geo.FOCUS_SECTION))

    def test_escape_shuts_the_list_then_the_menu(self):
        c = self.menu()
        c.options.intent('options_focus', focus=geo.FOCUS_LIST)
        c.options_key('Escape')
        self.assertTrue(c.options.snapshot()['open'])
        c.options_key('Escape')
        self.assertFalse(c.options.snapshot()['open'])

    def test_scroll_is_whole_rows_of_the_shared_height(self):
        rows = [dict(id=str(i), label='x', scope='monitor', kind='action', value='', enabled=True) for i in range(20)]
        self.assertEqual(om.OptionsMenu.scroll_limit(rows), 20 * geo.ROW_H - geo.BODY_H)


class ChoiceLists(unittest.TestCase):
    """A click or a key opens a choice's list; only a pick applies. Nothing else may change Resolution."""
    def setUp(self):
        self.applied = []
        def rows(control, scope, monitor, category):
            return [dict(id='resolution', label='Resolution', scope=scope, kind='choice', value='1920x1200', enabled=True, choices=['1920x1080', '1920x1200', '2560x1600']),
                    dict(id='other', label='Other', scope=scope, kind='action', value='', enabled=True)]
        self.c = view.ViewControl(view.ViewState(['XR-1']), 'run', 'session', Mock(), Mock(), Mock())
        self.c.options._rows = rows
        self.c.options._apply = lambda control, row, value, monitor, scope: self.applied.append((row, value))
        self.c.options.open('XR-1')

    def snap(self): return self.c.options.snapshot()

    def test_activating_opens_the_list_on_the_current_value_and_applies_nothing(self):
        self.c.options_activate(0)
        self.assertEqual((self.snap()['focus'], self.snap()['list']), (0, 1))
        self.assertEqual(self.applied, [])

    def test_left_and_right_no_longer_change_a_choice(self):
        self.c.options.intent('options_focus', focus=0)
        self.c.options_key('Right'); self.c.options_key('Left')
        self.assertEqual(self.applied, [])
        self.assertEqual(self.snap()['list'], -1)

    def test_a_pick_applies_exactly_that_value_and_shuts_the_list(self):
        self.c.options_activate(0)
        self.c.options_pick(2)
        self.assertEqual(self.applied, [('resolution', '2560x1600')])
        self.assertEqual(self.snap()['list'], -1)

    def test_keys_walk_the_list_and_return_picks(self):
        self.c.options.intent('options_focus', focus=0)
        self.c.options_key('Return')
        self.c.options_key('Down'); self.c.options_key('Down')
        self.assertEqual(self.applied, [])                    # walking applies nothing
        self.assertEqual(self.snap()['list'], 0)              # 1, 2, wrapped to 0
        self.c.options_key('Return')
        self.assertEqual(self.applied, [('resolution', '1920x1080')])

    def test_escape_and_tab_leave_it_unpicked(self):
        self.c.options_activate(0)
        self.c.options_key('Escape')
        self.assertTrue(self.snap()['open'])
        self.assertEqual(self.snap()['list'], -1)
        self.c.options_activate(0)
        self.c.options_key('Tab')
        self.assertEqual((self.snap()['list'], self.snap()['focus']), (-1, 1))
        self.assertEqual(self.applied, [])

    def test_a_disabled_choice_does_not_open(self):
        self.c.options._rows = lambda *a: [dict(id='scale', label='Scale', scope='monitor', kind='choice', value='1', enabled=False, choices=['1', '2'])]
        self.c.options.refresh()
        self.c.options_activate(0)
        self.assertEqual(self.snap()['list'], -1)

    def test_a_readback_that_takes_the_row_shuts_the_list(self):
        self.c.options_activate(0)
        self.c.options._rows = lambda *a: [dict(id='other', label='Other', scope='monitor', kind='action', value='', enabled=True)]
        self.c.options.refresh()
        self.assertEqual(self.snap()['list'], -1)

    def test_lists_fit_and_match_their_items(self):
        for count in (2, 12, 17, 32):
            for row in (geo.BODY_TOP, geo.BODY_TOP + 6 * geo.ROW_H):
                for i in range(count):
                    x, y, w, h = geo.choice_item(row, count, i)
                    self.assertTrue(x >= 0 and x + w <= geo.W and y >= geo.HEADER_H - 1 and y + h <= geo.H, (count, row, i))
                    self.assertEqual(geo.choice_at(x + w / 2, y + h / 2, row, count), i)


if __name__ == '__main__':
    unittest.main()
