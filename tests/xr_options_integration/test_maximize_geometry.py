"""V2 geometry against supervisor's real hit surface (native parity remains a gate)."""
import pytest
from tools.native_spike import free_pointer as fp


@pytest.mark.parametrize('tile', [(0,0,1920,1080), (800,-200,640,480),
    (-900,600,5120,1440), (15,30,300,1200), (0,0,3840,1080)])
def test_maximize_drawn_and_padded_gap_matches_level_bar(tile):
    # Native and supervisor owners should expose this geometry alongside level_bar_rect.
    rect = fp.maximize_rect(tile)
    x, y, w, h = fp.level_bar_rect(tile)
    s = fp.level_bar_scale(tile)
    assert rect == pytest.approx((x-38*s, y, 26*s, 26*s))
    # Drawn gap=12s; both controls expand by 4s, leaving an inert 4s gap.
    assert x-4*s - (rect[0]+rect[2]+4*s) == pytest.approx(4*s)
    center = (rect[0]+rect[2]/2, rect[1]+rect[3]/2)
    assert fp.level_bar_segment(tile, center) is None
