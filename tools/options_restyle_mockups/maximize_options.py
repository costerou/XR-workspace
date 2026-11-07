"""The places Restore can live without sitting on the desktop (R1..R4): mockups, because Cody picks where it goes.

An R option is a place and a way to get there. Its `reach` says how the pointer arrives, in terms check_mockups can run against the
real free_pointer.py; `new_handoff` is True where today's code cannot get a pointer there at all."""
from dataclasses import dataclass
from .family import Panel, MARGIN

GLYPH_W, GLYPH_H = 14, 10    # a desktop's 16:10, not today's square
DENSITY = 3                  # kHandleAtlasScale


def glyph(p, cx, cy, restore, role, scale=1.0, stroke=2.0):
    """One screen for Maximize. For Restore a smaller screen behind it. The whole ink is centred on (cx, cy)."""
    w, h = GLYPH_W * scale, GLYPH_H * scale
    if not restore:
        p.stroke(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 2 * scale, stroke * scale, role)
        return
    w, h = w * .84, h * .84
    d = 3 * scale
    x0, y0 = cx - (w + d) / 2, cy - (h + d) / 2
    p.stroke(x0 + d, y0, x0 + d + w, y0 + h, 1.6 * scale, stroke * scale * .85, role)
    p.fill(x0, y0 + d, x0 + w, y0 + d + h, 1.6 * scale, BACK[p.name], 1)
    p.stroke(x0, y0 + d, x0 + w, y0 + d + h, 1.6 * scale, stroke * scale, role)


BACK = {}    # what Restore's front screen is filled with: the button's own fill, so the back screen goes behind it


def button_panel(name, w, h, fonts):
    return Panel(name, w, h, fonts)


@dataclass
class Look:
    number: int; slug: str; name: str; blurb: str
    w: float; h: float; gap: float      # design px, scaled with the bar; gap between it and the bar
    idle: tuple; hot: tuple             # (fill, ink)
    inner: float = 13                   # the corner beside the bar; 13 = a whole capsule
    hit_y: float = 4                    # padding above and below, design px
    glyph_scale: float = 1.0
    keeps_rect: bool = False

    def rect(self, bar, s):
        return (bar[0] - (self.gap + self.w) * s, bar[1] + (bar[3] - self.h * s) / 2, self.w * s, self.h * s)

    def hit(self, bar, s):
        """The drawn shape padded like the bar, but a gap under 8 px is split down its middle, as immersive_part splits its own."""
        x, y, w, h = self.rect(bar, s)
        pad = 4 * s
        right = x + w + min(pad, self.gap * s / 2)
        return (x - pad, y - self.hit_y * s, right - (x - pad), h + 2 * self.hit_y * s)

    def draw(self, restore, hot, fonts, label=None):
        fill, ink = self.hot if hot else self.idle
        p = Panel(f'{self.slug}:{"restore" if restore else "maximize"}:{hot}', self.w, self.h, fonts)
        BACK[p.name] = fill
        w, h, r = self.w, self.h, self.h / 2
        if self.inner < r:     # a connected end: the capsule's round outer end, the 3D button's 4 px inner corners
            p.fill(0, 0, w / 2 + r, h, r, fill)
            p.fill(r, 0, w, h, self.inner, fill)
        else:
            p.fill(0, 0, w, h, r, fill)
        glyph(p, w / 2, h / 2, restore, ink, self.glyph_scale)
        return p


# The look built into render_handle_atlas: a capsule in the bar's fill, the primary under the dot.
LOOKS = [Look(1, 'capsule', 'Capsule', 'Built.', 38, 26, 12, ('primary_container', 'on_primary_container'), ('primary', 'on_primary'))]


@dataclass
class Place:
    number: int; slug: str; name: str; blurb: str
    reach: str          # exactly how the pointer gets there
    covers: str         # what it covers, at rest and in use
    new_handoff: bool   # needs new pointer-handoff code
    build: str          # what building it takes
    test: str           # the stated, testable way to reach it


PILL_W, PILL_H, CORNER_INSET = 122, 26, 6   # the Restore pill: icon and label, the bar's height
ZONE_LOGICAL = 16                           # hot corner, output logical px; deeper than NUDGE_PX (12), which throws a touch back in
DWELL_MS, FADE_MS = 350, 200
NEAR_FAR, NEAR_FULL = 160, 48               # R2: canvas px from the chip: invisible past FAR, whole inside FULL
STEP_ZOOM = .84                             # R3: how far the picture steps back for its band
BAND_PILL_GAP = 7
BAND_PX = 46                                 # R3: the band a pill needs, its height and a margin each side

PLACES = [
    Place(1, 'hot-corner', 'Hot Corner', 'Nothing is drawn. Rest the pointer in the bottom-right corner for 350 ms and a Restore pill grows out of it, under the pointer.',
          f'Throw the mouse into the bottom-right corner: the cursor stops there by itself, because the output ends. Rest {DWELL_MS} ms inside the {ZONE_LOGICAL} x {ZONE_LOGICAL} logical px zone. The pill appears under the pointer and the existing Restore mode takes over. Click it. Leave it and it fades in {FADE_MS} ms.',
          f'At rest nothing, and the app keeps the pointer in the corner: the zone only reads the cursor until the {DWELL_MS} ms are up. While the pill is up, a 122 x 26 pill, and there the app gets no pointer.', False,
          f'No new handoff: the existing _poll_restore route reaches it. New: a {DWELL_MS} ms dwell timer before the mesh is armed, one wire flag so the renderer shows the pill, and options_restore_rect moved to the corner.',
          f'Move the cursor to the corner, hold {DWELL_MS} ms: restore_mesh_hit is True on the real ray. check_mockups runs it for every corner pixel of the zone on all three desktops.'),
    Place(2, 'fade-chip', 'Fade Chip', 'A 26 px round key in the corner, invisible until the pointer comes near, whole when it is close. No wait: click it as soon as you reach it.',
          f'Move toward the bottom-right corner: the chip fades in from {NEAR_FAR} px away and is whole by {NEAR_FULL} px. The cursor reaches it by itself because the output ends there. Reaching it enters the existing Restore mode; click.',
          f'At rest nothing, when the pointer is more than {NEAR_FAR} px away. Near the corner a 26 px key, part see-through until {NEAR_FULL} px. Its cost: the hit zone is live even while the key is invisible, so the app never gets the pointer in the last 36 x 36 px of that corner, and a click there is Restore.', False,
          'No new handoff: the same route. New: the supervisor sends the cursor\'s distance (already sampled, read-only) as an alpha on one wire field; the renderer fades the key.',
          f'Move the cursor to the corner: restore_mesh_hit is True on the real ray with no timer. Alpha is 0 past {NEAR_FAR} px and 1 inside {NEAR_FULL} px.'),
    Place(3, 'step-back-band', 'Step-Back Band', 'The only one fully outside the picture. Rest at the top edge and the whole picture steps back to 84%, opening a band above it with Restore in it. On a 16:9 desktop the band is already there.',
          f'Rest {DWELL_MS} ms at the top edge. The picture eases back to 84% and the cursor hands over to the free dot, which leaves the output and travels up into the band. Click Restore there. Move back down and the picture comes forward again.',
          'Never any content, and the app keeps the pointer everywhere it can reach: the band is outside the picture. On 16:10 the picture itself shrinks to 84% while the band is open, so text softens for that moment. The Pro 2 session\'s desktops already leave a band (330 px on the ultrawide, 60 px on the 16:9), so they need no step-back.', True,
          'NEEDS NEW POINTER-HANDOFF CODE: today no output pixel maps past the picture (restore_cursor_ray refuses it), so the cursor must hand off to the dot before it is on a button, which is the "excursion pointer" the approved interface v3 rules out, so Cody must lift that rule. Also a held zoom state in the renderer, which must hold the picture lower on a desktop placed high: XR-1 rides up by 29 px at 84%.',
          'Today, sweeping every output pixel never hits a mesh in the band: check_mockups proves that, and that is why it needs the handoff. A built version passes if a resting cursor reaches the band and a click lands with no app click.'),
    Place(4, 'keys-and-hint', 'Keys and Hint', 'No Restore on the picture at all. Ctrl+Alt+Down, which already exists, is the way back, and the overview\'s Maximize tooltip teaches it.',
          'The pointer does not go anywhere. Press Ctrl+Alt+Down (bound today, zoom_out). Hover Maximize in the overview and its tooltip says so.',
          'Nothing, ever, and the app has the pointer everywhere. The hint is shown in the overview, beside the Maximize button.', False,
          'Least code: remove Restore\'s draw and hit, and lengthen the tooltip. No wire change. No mouse way back: Cody loses the click.',
          'Ctrl+Alt+Down returns to the overview: the zoom_out row in tests/xr_options_acceptance/inventory.json, already tested by the view suites.'),
]


def restore_pill(fonts, look, hot=True):
    """R1's pill: the look's colours, its icon and the word the placement menu would use."""
    fill, ink = look.hot if hot else look.idle
    p = Panel('pill:restore', PILL_W, PILL_H, fonts)
    BACK[p.name] = fill
    p.fill(0, 0, PILL_W, PILL_H, PILL_H / 2, fill)
    glyph(p, 20, PILL_H / 2, True, ink)
    p.text('Restore', 18, True, 36, PILL_H / 2, ink, 1, (32, PILL_W - 8))
    return p


def round_key(fonts, look, hot):
    fill, ink = look.hot if hot else look.idle
    p = Panel('chip:restore', 26, 26, fonts)
    BACK[p.name] = fill
    p.disc(13, 13, 13, fill)
    glyph(p, 13, 13, True, ink, .9)
    return p


def tooltip(fonts, text):
    w = fonts.width(text, 16, True) + 24
    p = Panel('tooltip', w, 26, fonts)
    p.fill(0, 0, w, 26, 13, 'surface_container_highest')
    p.stroke(0, 0, w, 26, 13, 1, 'outline_variant')
    p.text(text, 16, True, 12, 13, 'on_surface', 1, (4, w - 4))
    return p


def placed(panel, x, y, s=1.0, density=DENSITY, name=None, alpha=1.0, space='tile', desktop=None):
    """An overlay of `panel` with its box at tile (x, y), scaled by `s`."""
    m = MARGIN
    out = dict(panel.overlay(), rect=[x - m * s, y - m * s, (panel.w + 2 * m) * s, (panel.h + 2 * m) * s], density=density, alpha=alpha, space=space, name=name or panel.name)
    if desktop is not None: out['desktop'] = desktop
    return out


def bounds(rect, space, desktop, name):
    """Nothing drawn: asks the renderer where `rect` lands on the canvas, without the shadow's margin."""
    return dict(w=rect[2], h=rect[3], x=rect[0], y=rect[1], ops=[], space=space, desktop=desktop, name=name)


def arrow_overlay(x, y):
    """The app's own cursor in the capture, for frames where it is still the real one."""
    return dict(w=40, h=40, x=x - 4, y=y - 4, density=2, ops=[['arrow', 4, 4, 22, 'on_surface', 'surface']], name='cursor')
