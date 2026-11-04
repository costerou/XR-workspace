"""The 3D button mockups' checker and page parts. Offline: no compositor, no GPU, a fixed M3 scheme instead of the binary."""
import re
import sys
import unittest
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "button_3d_mockups"))

import button_options as b  # noqa: E402
import check_button_options as check  # noqa: E402
import make_button_mockups as make  # noqa: E402


def scheme(primary, tint):
    """A dark scheme in the shape button_preview prints: tones of `primary`, and `tint` for the darkest roles."""
    def tone(level):
        return [c * level for c in primary]
    roles = {"accent": primary, "primary": tone(0.8), "on_primary": tone(0.2), "primary_container": tone(0.55), "on_primary_container": tone(0.12),
             "primary_bright": tone(1.0), "secondary_container": tone(0.28), "on_secondary_container": tone(0.95), "surface": tone(0.07),
             "surface_container_low": tone(0.1), "surface_container": tone(0.12), "surface_container_high": tone(0.16),
             "surface_container_highest": tone(0.2), "on_surface": tone(0.95), "on_surface_variant": tone(0.8), "outline": tone(0.58),
             "outline_variant": tone(0.28)}
    return {k: [min(c, 0.749) for c in v] for k, v in roles.items()}


THEMES = {"grey": b.Theme(scheme([0.74, 0.74, 0.74], None)), "blue": b.Theme(scheme([0.478, 0.635, 0.969], None)),
          "red": b.Theme(scheme([0.969, 0.463, 0.557], None)), "green": b.Theme(scheme([0.62, 0.808, 0.416], None))}


class Options(unittest.TestCase):
    def test_eight_to_pick_from_with_distinct_numbers_and_names(self):
        self.assertEqual([o.number for o in b.OPTIONS], list(range(1, 9)))
        self.assertEqual(len({o.name for o in b.OPTIONS}), 8)

    def test_every_option_passes_on_the_real_zone_code(self):
        for option in b.OPTIONS:
            self.assertEqual(check.zone_problems(option, *check.TILE), [], option.name)

    def test_every_option_shrinks_with_the_bar(self):
        for option in b.OPTIONS:
            self.assertLess(check.narrowest(option), 300, option.name)

    def test_two_control_options_say_how_their_second_control_is_used(self):
        for option in b.OPTIONS:
            two = "all" in option.hits(b.Geo(*check.TILE))
            self.assertEqual(two, option.all_how.startswith("Click"), option.name)


class CheckerCatchesWhatItClaims(unittest.TestCase):
    """Each limit, broken on purpose: a check that cannot fail proves nothing."""

    def failures(self, draw, hits=None):
        hits = hits or (lambda g: {"3d": g.beside(44)})
        return check.measure(b.Option(99, "Bad", "", "", draw, hits), THEMES)[1]

    def test_a_good_design_is_clean(self):
        self.assertEqual(self.failures(b.twin_pill), [])

    def test_on_the_content(self):
        def draw(s, f):
            b.twin_pill(s, f)
            s.capsule(100, 100, 50, 30, s.t.primary)
        self.assertTrue(any("on the content" in w for w in self.failures(draw)))

    def test_past_a_side_edge(self):
        def draw(s, f):
            b.twin_pill(s, f)
            s.capsule(s.W - 10, -40, 40, 26, s.t.primary)
        self.assertTrue(any("side edge" in w for w in self.failures(draw)))

    def test_on_the_bar(self):
        self.assertTrue(any("overlaps the bar" in w for w in self.failures(b.twin_pill, lambda g: {"3d": (g.x + 10, g.y, 44, g.h)})))

    def test_in_a_corner_zone(self):
        problems = self.failures(b.twin_pill, lambda g: {"3d": (g.tile[2] - 15, -12, 12, 6)})
        self.assertTrue(any("resize zone" in w for w in problems))

    def test_over_the_cap(self):
        def draw(s, f):
            b.twin_pill(s, f)
            s.capsule(*b.tile_geo(s).beside(44), (1, 1, 1))
        self.assertTrue(any("over the cap" in w for w in self.failures(draw)))

    def test_on_that_looks_like_off(self):
        def draw(s, f):
            b.twin_pill(s, b.Frame(b.OFF))
        problems = self.failures(draw)
        self.assertTrue(any("on and off" in w for w in problems) and any("global/on" in w for w in problems))

    def test_a_colour_of_its_own(self):
        def draw(s, f):
            s.capsule(*b.tile_geo(s).beside(44), (0.7, 0.1, 0.1) if f.lit else s.t.secondary_container)
            if f.state == b.GLOBAL:
                s.disc(*b.tile_geo(s).beside(44)[:2], 6, s.t.primary)
        problems = self.failures(draw)
        self.assertTrue(any("off the theme" in w for w in problems) and any("tint of its own" in w for w in problems))


class HelpAndPanel(unittest.TestCase):
    def test_the_key_is_free_and_the_row_it_shares_exists(self):
        sections = make.run_view._help_sections()
        rows = [r[0] for s in sections for r in s]
        self.assertIn("Ctrl+Alt+E", rows)
        self.assertNotIn(make.KEY_3D, [make.run_view.chord_label(c) for c, _ in make.run_view.BINDS])

    def test_the_shared_row_fits_the_wire_and_the_card(self):
        limit = int(re.search(r"kMaxHelpText = (\d+)", (REPO / "spike/include/xr/ipc.hpp").read_text()).group(1))
        self.assertLessEqual(len(make.SHARED_ACTION), limit)
        self.assertLessEqual(len(make.SHARED_ACTION), 42)  # the card's budget at its roomiest text

    def test_panel_row_constants_are_the_background_rows(self):
        glass = (REPO / "spike/include/xr/glass.hpp").read_text()
        def value(pattern):
            return float(re.search(pattern, glass).group(1))
        self.assertEqual(make.PANEL_ROW_H, value(r"kPanelRowH = ([\d.]+)f"))
        self.assertEqual(make.PANEL_PAD, value(r"kPanelPad = ([\d.]+)f"))
        self.assertEqual(make.BOX_X, value(r"kPanelTrackX0 = ([\d.]+)f"))
        self.assertEqual(make.SWITCH_W, value(r"kBackgroundSwitchW = ([\d.]+)f"))
        self.assertEqual(make.SWITCH_H, value(r"kBackgroundSwitchH = ([\d.]+)f"))
        self.assertEqual(make.BOX_W, 480 - 22 - 46 - 12 - 184)  # kBackgroundBoxW, the comment's 216

    def test_panel_splice_adds_exactly_one_row_and_keeps_the_rest(self):
        frame = Image.new("RGB", (40, 120), (10, 20, 30))
        for y in range(120):
            frame.putpixel((0, y), (y, 0, 0))
        panel, box, scale = [0, 10, 40, 100], (0, 0, 40, 120), 1
        make_row = Image.new("RGBA", (40, make.PANEL_ROW_H), (0, 0, 0, 0))
        old = make.PANEL_BAND_TOP
        make.PANEL_BAND_TOP = 50
        try:
            out = make.panel_shot(frame, panel, box, scale, make_row)
        finally:
            make.PANEL_BAND_TOP = old
        self.assertEqual(out.size, (40, 120 + make.PANEL_ROW_H))
        cut = 10 + 50 + 3
        self.assertEqual(out.getpixel((0, cut - 1))[0], cut - 1)
        self.assertEqual(out.getpixel((0, cut + make.PANEL_ROW_H - 1))[0], cut)  # stretched from the cut's own row
        self.assertEqual(out.getpixel((0, 119 + make.PANEL_ROW_H))[0], 119)


if __name__ == "__main__":
    unittest.main()
