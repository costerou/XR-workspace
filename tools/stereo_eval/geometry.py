"""Angles on the glasses: what a pixel of disparity means to the eyes, and the published comfort limits.

Sign used everywhere in this package: disparity d = x_right - x_left, in arcminutes or pixels. d > 0 is uncrossed (behind
the screen plane), d < 0 is crossed (in front of it), d = 0 is on the plane and looks exactly like 2D.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Optics:
    """Defaults are the Luma Ultra's retail numbers. The focal distance and the zero-disparity distance are not published
    in those terms: 3 m is inferred from "152 inch at 3 m". The renderer reports the real diagonal FOV on its status line
    (its fallback without a driver value is 46 degrees)."""
    diagonal_fov_deg: float = 52.0
    eye_w: int = 1920
    eye_h: int = 1200
    focal_distance_m: float = 3.0
    ipd_mm: float = 63.0

    @property
    def half_tan_x(self) -> float:
        """tan(half horizontal FOV); same formula as half_fov_tan in spike/src/video.cpp."""
        aspect = self.eye_w / self.eye_h
        return math.tan(math.radians(self.diagonal_fov_deg / 2)) * aspect / math.sqrt(1 + aspect * aspect)

    @property
    def horizontal_fov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.half_tan_x))

    @property
    def px_per_degree(self) -> float:
        """Eye pixels per degree at the centre of the view."""
        return self.eye_w / 2 / self.half_tan_x * math.pi / 180

    @property
    def px_per_arcmin(self) -> float:
        return self.px_per_degree / 60

    def image_px_per_arcmin(self, image_w: int) -> float:
        """Image pixels per arcminute for an image that fills the eye's width (a maximized desktop)."""
        return self.px_per_arcmin * image_w / self.eye_w

    def arcmin_to_percent_width(self, arcmin: float) -> float:
        return 100 * arcmin / 60 / self.horizontal_fov_deg


@dataclass(frozen=True)
class ComfortLimits:
    near_arcmin: float   # crossed, in front of the plane
    far_arcmin: float    # uncrossed, behind the plane
    near_vergence_m: float
    far_vergence_m: float   # math.inf: the limit is the eyes going parallel


def shibata_limits(optics: Optics = Optics()) -> ComfortLimits:
    """Zone of comfort (Shibata, Kim, Hoffman, Banks, J. Vision 2011): D_f = m D_v + T, in dioptres. The far side is also
    capped at parallel eyes: the eyes must never diverge. The authors call the fit an estimate, from six sampled
    vergence-focal points and questionnaires, so treat this as an outer fence and not a target."""
    focal = 1 / optics.focal_distance_m
    near = (focal + 0.626) / 1.035
    far = max((focal - 0.442) / 1.129, 0.0)
    to_arcmin = optics.ipd_mm / 1000 * 180 / math.pi * 60
    return ComfortLimits((near - focal) * to_arcmin, (focal - far) * to_arcmin, 1 / near, 1 / far if far else math.inf)
