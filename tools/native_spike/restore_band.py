"""The Step-Back Band's rest (R3, spike/include/xr/options_geometry.hpp has the picture and the pill).

The pointer asks for the band by resting pressed against the output's top edge. Near the edge is not enough: tabs and title bars live
there, and only the edge stops a hand. Hyprland clamps the cursor to the output, so the outermost row is where a hand pressing up ends."""
import math

DWELL_S = 0.35      # a rest this long opens the band
TOUCH_PX = 0.5      # logical px: this close to the top row is touching the edge, as free_pointer.EDGE_PX
ZONE_PX = 14        # logical px: the rest survives this far down, past EdgePush's NUDGE_PX (12), which puts a touch back in below a desktop placed up there
STILL_PX = 2        # logical px: a hand this steady is resting; more restarts the rest, so sliding along the edge never opens the band
LAND_PX = 24        # logical px: where the app gets the cursor back, under the zone, so coming back does not open it again
RETURN_PX = 8       # canvas px: this far into the picture is back on the picture
IDLE_S = 10         # the band shuts by itself after this long with no hand motion
HELD_MAX_S = 30     # a press with no release after this long was lost
# Mirrors spike/include/xr/options_geometry.hpp; test_restore_band compares them.
STEP_SCALE, STEP_MS, BAND_MIN, BAND_GAP = .84, 220, 46, 12


class Dwell:
    """Pressed against the top edge and resting. step() takes each cursor sample and says when the rest is up."""
    def __init__(self):
        self.reset()
        self.armed = True

    def reset(self):
        """Nothing has been touched."""
        self.touched, self.anchor, self.since = False, None, None

    def restart(self):
        """The rest starts over, as after a click: the touch stands."""
        self.anchor, self.since = None, None

    def disarm(self):
        """Not again until the pointer has left the zone."""
        self.reset()
        self.armed = False

    def step(self, cursor, output, blocked, now):
        if cursor is None or output is None or not output['x'] <= cursor[0] < output['x'] + output['width'] or not output['y'] <= cursor[1] < output['y'] + output['height']:
            self.reset()
            return False
        down = cursor[1] - output['y']
        if down > ZONE_PX:
            self.reset()
            self.armed = True
            return False
        if not self.armed: return False
        if down <= TOUCH_PX: self.touched = True
        if not self.touched: return False
        if blocked:
            self.restart()
            return False
        if self.anchor is None or math.hypot(cursor[0] - self.anchor[0], cursor[1] - self.anchor[1]) > STILL_PX:
            self.anchor, self.since = cursor, now
            return False
        return now - self.since >= DWELL_S - 1e-9
