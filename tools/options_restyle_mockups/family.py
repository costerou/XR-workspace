"""The placement menu's visual language as drawing parts. Every number comes from glass.hpp / glass.cpp (restyle_preview info gives
the header's constants; the rest are noted), every colour is an M3 role of glass_theme_scheme, every shape one of the renderer's
md_* calls. A Panel only collects operations: restyle_preview rasterises them with the renderer's code."""
from . import harness

MARGIN = 32                 # kGlassShadowMargin: the shell's shadow needs this round the shape
BAND_INSET = 10             # glass.cpp kBandInset
TRACK_H, TRACK_ALPHA = 4, .24   # kTrackH, kTrackAlpha
HALO_R, FOCUS_ALPHA = 14, .10   # kHaloR (2 * knob), kMdStateFocus
BUTTON_PX, HINT_PX = 18, 18     # kButtonLabelPx, kPanelHintPx
FIELD_PAD, CARET_PX, CARET_INSET = 12, 24, 16   # kFieldPadX, kCaretPx, kCaretInset
EXTRA_SMALL = 4             # kMdShapeExtraSmall
DISABLED = .38              # kMdDisabledContent
DISABLED_FILL = .12         # kMdDisabledContainer
FIELD_DISABLED = .04        # kFieldDisabled
ITEM_H, LIST_GAP = 20, 2    # kScaleItemH, kScaleListGap
SWITCH_ON, SWITCH_OFF = 9, 6    # kSwitchHandleOn / Off

TAB_NAMES = dict(placement='Placement', output='Output', stabilization='Stabilization', layout='Layout', environment='Environment',
                 view='View', video='Video', input='Input', keybinds='Key binds', windows='Windows', privacy='Privacy', help='Help', session='Session')
TAB_NAMES['3d'] = '3D'
ROW_ICONS = dict(add_monitor='add', remove_monitor='remove', ultrawide='width', roll_ccw='rotate_left', roll_cw='rotate_right', roll_level='level')
ASKS_FIRST = {'remove_monitor', 'layout_reset', 'stop'}   # options_menu.py: these go through the confirmation child


class Fonts:
    """HudFont widths. JetBrains Mono is monospaced, so one measurement per size and weight stands for every string; check_mockups
    measures each string drawn with HudFont::measure again."""
    def __init__(self):
        self.cache = {}

    def advance(self, px, bold):
        if (px, bold) not in self.cache:
            self.cache[px, bold] = harness.measure([(px, bold, 'M' * 100)])[0] / 100
        return self.cache[px, bold]

    def width(self, text, px, bold=False):
        return round(len(text) * self.advance(px, bold))


class Panel:
    """Operations in panel px. `hits` and `texts` are what check_mockups verifies."""
    def __init__(self, name, w, h, fonts):
        self.name, self.w, self.h, self.fonts, self.ops, self.hits, self.texts = name, w, h, fonts, [], [], []

    def shell(self, grabber=True, radius=18):
        """The open panel's glass: glass_look at t = 1 through glass_shade, and its grabber."""
        m = MARGIN
        self.ops.append(['shell', m, m, self.w, self.h, radius, 1.0, 1.0])
        if grabber:
            self.ops.append(['grabber', m + (self.w - 44) / 2, m + 8, 44, 5, 2.5, 1.0, 1.0])

    def fill(self, x0, y0, x1, y1, r, role, a=1.0):
        self.ops.append(['fill', x0 + MARGIN, y0 + MARGIN, x1 + MARGIN, y1 + MARGIN, r, role, a])

    def stroke(self, x0, y0, x1, y1, r, w, role, a=1.0):
        self.ops.append(['stroke', x0 + MARGIN, y0 + MARGIN, x1 + MARGIN, y1 + MARGIN, r, w, role, a])

    def disc(self, x, y, r, role, a=1.0):
        self.ops.append(['disc', x + MARGIN, y + MARGIN, r, role, a])

    def icon(self, name, x, y, size, role, a=1.0):
        self.ops.append(['icon', name, x + MARGIN, y + MARGIN, size, role, a])

    def text(self, s, px, bold, x, cy, role, a=1.0, cell=None):
        """`cell`: (x0, x1) the text must stay inside."""
        self.ops.append(['text', s, px, bold, x + MARGIN, cy + MARGIN, role, a])
        w = self.fonts.width(s, px, bold)
        self.texts.append(dict(text=s, px=px, bold=bold, x=x, cy=cy, w=w, cell=cell or (0, self.w)))

    def right(self, s, px, bold, x1, cy, role, a=1.0, cell=None):
        self.text(s, px, bold, x1 - self.fonts.width(s, px, bold), cy, role, a, cell)

    def centre(self, s, px, bold, x0, x1, cy, role, a=1.0):
        self.text(s, px, bold, (x0 + x1 - self.fonts.width(s, px, bold)) / 2, cy, role, a, (x0, x1))

    def fit(self, s, px, bold, width):
        """`s` cut to `width` with an ellipsis: a value may be longer than any cell."""
        if self.fonts.width(s, px, bold) <= width: return s
        n = max(int(width / self.fonts.advance(px, bold)) - 1, 1)
        return s[:n].rstrip() + '…'

    def hit(self, name, x0, y0, x1, y1):
        self.hits.append(dict(name=name, rect=(x0, y0, x1 - x0, y1 - y0)))

    def overlay(self):
        m = MARGIN
        return dict(w=self.w + 2 * m, h=self.h + 2 * m, ops=self.ops, name=self.name)

    # --- The placement menu's parts ------------------------------------------------------------------------------------
    def header(self, title, right=None):
        """Header: the desktop bold left, what this is right, the divider under them (glass.cpp render_glass_panel)."""
        cy = (8 + 5 + 42) / 2
        self.text(title, 24, True, 22, cy, 'on_surface')
        if right: self.right(right, 24, False, self.w - 22, cy, 'on_surface_variant')
        self.fill(22, 41, self.w - 22, 42, 0, 'outline_variant')
        return cy

    def band(self, x0, y0, x1, y1):
        self.fill(x0, y0 + 2, x1, y1 - 2, (y1 - y0 - 4) / 2, 'secondary_container')

    def slider(self, x0, x1, cy, t, start, a=1.0, focused=False):
        """Inactive track, active track from `start` (the flat value) to `t`, handle, and the focused handle's halo."""
        lo, hi = x0 + 7, x1 - 7
        at = lambda v: lo + (hi - lo) * v
        self.fill(x0, cy - 2, x1, cy + 2, 2, 'primary', TRACK_ALPHA * a)
        p, q = at(min(t, start)), at(max(t, start))
        if q - p >= 1: self.fill(p, cy - 2, q, cy + 2, 2, 'primary', a)
        if focused: self.disc(at(t), cy, HALO_R, 'primary', FOCUS_ALPHA * a)
        self.disc(at(t), cy, 7, 'primary', a)

    def switch(self, x, cy, on, a=1.0):
        """The Background switch, 46 x 26."""
        x1, y0, y1 = x + 46, cy - 13, cy + 13
        self.fill(x, y0, x1, y1, 13, 'primary' if on else 'surface_container_highest', a)
        if not on: self.stroke(x, y0, x1, y1, 13, 2, 'outline', a)
        self.disc(x1 - 13 if on else x + 13, cy, SWITCH_ON if on else SWITCH_OFF, 'on_primary' if on else 'outline', a)

    def field(self, x0, y0, x1, y1, value, is_open=False, usable=True, caret=True):
        """glass.cpp dropdown_row's box: a filled text field with an indicator and, for a dropdown, an arrow."""
        a = 1.0 if usable else DISABLED
        cy = (y0 + y1) / 2
        self.fill(x0, y0, x1, y1, EXTRA_SMALL, 'surface_container_highest' if usable else 'on_surface', 1.0 if usable else FIELD_DISABLED)
        h = 2 if is_open else 1
        self.fill(x0, y1 - h, x1, y1, 0, 'primary' if is_open else 'on_surface_variant' if usable else 'on_surface', a)
        room = x1 - x0 - FIELD_PAD - (CARET_INSET + 10 if caret else FIELD_PAD)
        self.text(self.fit(value, BUTTON_PX, True, room), BUTTON_PX, True, x0 + FIELD_PAD, cy, 'on_surface', a, (x0 + 4, x1 - (CARET_INSET if caret else 4)))
        if caret: self.icon('arrow_drop_up' if is_open else 'arrow_drop_down', x1 - CARET_INSET, cy, CARET_PX, 'primary' if is_open else 'on_surface_variant', a)

    def button(self, x0, y0, x1, y1, label, style='tonal', icon=None, px=BUTTON_PX, lines=None):
        """glass_panel_button_style: filled tonal, primary while on, error container while it asks, M3 disabled."""
        fill, ink, fa, ia = dict(tonal=('secondary_container', 'on_secondary_container', 1, 1), on=('primary', 'on_primary', 1, 1),
                                 armed=('error_container', 'on_error_container', 1, 1), off=('on_surface', 'on_surface', DISABLED_FILL, DISABLED))[style]
        self.fill(x0, y0, x1, y1, min((y1 - y0) / 2, 16), fill, fa)
        lines = lines or [label]
        cy0 = (y0 + y1) / 2 - (len(lines) - 1) * 11
        for n, line in enumerate(lines):
            w = self.fonts.width(line, px, True)
            room = 16 + 6 if icon and n == 0 else 0
            left = (x0 + x1 - w - room) / 2
            if icon and n == 0: self.icon(icon, left + 8, cy0, 16, ink, ia)
            self.text(line, px, True, left + room, cy0 + n * 22, ink, ia, (x0 + 8, x1 - 8))

    def segmented(self, x0, y0, x1, y1, labels, active, focused=None, px=BUTTON_PX, names=None):
        """The level bar as a control: a capsule in the primary container with a hot segment in the primary, on hairline dividers."""
        n = len(labels)
        widths = [self.fonts.width(t, px, True) + 20 for t in labels]
        spare = (x1 - x0 - sum(widths)) / n
        self.fill(x0, y0, x1, y1, (y1 - y0) / 2, 'primary_container')
        x = x0
        for i, label in enumerate(labels):
            w = widths[i] + spare
            if i == active: self.fill(x + 2, y0 + 2, x + w - 2, y1 - 2, (y1 - y0 - 4) / 2, 'primary')
            elif i < n - 1 and active != i + 1: self.fill(x + w - .5, y0 + 7, x + w + .5, y1 - 7, 0, 'on_primary_container', .5)
            self.centre(label, px, True, x, x + w, (y0 + y1) / 2, 'on_primary' if i == active else 'on_primary_container')
            if focused == i: self.stroke(x, y0, x + w, y1, (y1 - y0) / 2, 2, 'primary')
            self.hit((names or labels)[i], x, y0, x + w, y1)
            x += w

    def chip(self, x0, y0, x1, y1, label, active, focused=False, px=16):
        """A tab as the 3D button's tonal off / primary on capsule."""
        self.fill(x0, y0, x1, y1, (y1 - y0) / 2, 'primary' if active else 'secondary_container')
        self.centre(label, px, active, x0, x1, (y0 + y1) / 2, 'on_primary' if active else 'on_secondary_container')
        if focused: self.stroke(x0 - 2, y0 - 2, x1 + 2, y1 + 2, (y1 - y0) / 2 + 2, 2, 'primary')

    def menu_list(self, x0, y0, x1, y1):
        """list_frame: an M3 menu over what is under it."""
        self.fill(x0, y0 - 1, x1, y1 + 1, EXTRA_SMALL, 'surface_container')
        self.stroke(x0, y0 - 1, x1, y1 + 1, EXTRA_SMALL, 1, 'outline_variant')

    def list_item(self, x0, y0, x1, y1, label, current, name):
        if current: self.fill(x0 + 2, y0 + 1, x1 - 2, y1 - 1, EXTRA_SMALL, 'secondary_container')
        self.text(label, BUTTON_PX, True, x0 + FIELD_PAD, (y0 + y1) / 2, 'on_secondary_container' if current else 'on_surface', 1, (x0 + 4, x1 - 4))
        self.hit(name, x0, y0, x1, y1)


def shown(row):
    """A row's value as the placement panel prints it: hud.cpp placement_menu_rows's units, not the wire's."""
    value, unit = row['value'], row.get('unit', '')
    if row['kind'] == 'range':
        v = float(value)
        if unit == '0.1 deg':
            whole = int(abs(v) / 10 + .5)
            return ('+' if v > 0 else '-' if v < 0 and whole else '') + f'{whole}\N{DEGREE SIGN}'
        return f'{int(v)}\N{DEGREE SIGN}' if unit == 'deg' else f'{int(v)}%' if unit == '%' else f'{value} {unit}'.strip()
    return value + (f' {unit}' if unit else '')
