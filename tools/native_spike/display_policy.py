"""Pure display eligibility, not provider support or authorization to switch modes.

Pro 2: current EDID and https://www.viture.com/academy/xr-glasses/pro-2.
High refresh and SBS require later opt-in validation. Never synthesize a mode or
infer the glasses model from EDID. Callers must supply the verified model key.
"""
import math


def initial_picture_mode(model, width, height, refresh):
    """Whether an observed mode meets the model's initial picture target.

    A false result authorizes no correction: preserve the physical mode and report
    the mismatch. This helper does not establish tracking/provider compatibility.
    Legacy targets retain the existing Luma/Beast session contract.
    """
    target = {'pro2': (1920, 1080, 60), 'luma-ultra': (1920, 1200, 120),
              'beast': (1920, 1200, 120)}.get(model)
    return bool(target and type(width) is int and type(height) is int
                and (width, height) == target[:2]
                and type(refresh) in (int, float) and math.isfinite(refresh)
                and abs(refresh - target[2]) <= 0.5)
