"""The options menu designs. Each lays out a snapshot exactly as options_menu.py sends it: every tab, both scopes, every row, in its
order, with its real label, value and enabled state. Nothing here adds a control. A design changes where things are and how they look."""
from dataclasses import dataclass, field
from . import family as F
from .family import Panel, TAB_NAMES, shown

CATEGORIES = ('placement', 'output', '3d', 'stabilization', 'layout', 'environment', 'view', 'video', 'input', 'keybinds', 'windows', 'privacy', 'help', 'session')
HINT = '↑↓ row  ←→ adjust  Tab next  Esc close'


@dataclass
class Grid:
    """Where a body's rows go. Column widths in panel px; a `slot` is one row's place on a line."""
    x0: float; x1: float; top: float; rows: int
    row_h: float = 34; label_px: int = 24; label_w: float = 310; value_w: float = 88; gap: float = 10
    columns: int = 1; pair_actions: bool = True


@dataclass
class Page:
    panel: Panel
    index: int
    count: int
    drawn: list = field(default_factory=list)   # the ids of the rows on this page


def flow(rows, g, fonts):
    """Rows to lines of slots, in order: [(index, column, span, line_units)]. Wide text takes the whole line; a confirmation's
    label, longer than any button, takes two lines of text and two rows."""
    columns = 2 if g.columns == 2 or g.pair_actions else 1
    half = (g.x1 - g.x0 - g.gap) / 2
    full = g.x1 - g.x0
    lines, line = [], []
    def close():
        nonlocal line
        if line: lines.append(line); line = []
    for i, r in enumerate(rows):
        pairable = r['kind'] == 'action' and g.pair_actions or g.columns == 2
        text = fonts.width(r['label'], F.BUTTON_PX, True) + 24 + (22 if r['id'] in F.ROW_ICONS else 0)
        if r['kind'] == 'action' and (r['value'] or text > full):      # keeps its own line: a label too long for one, or a value
            close(); lines.append([(i, 0, 2, 2 if text > full else 1)]); continue
        if g.columns == 2 and r['kind'] != 'action' and fonts.width(r['label'], g.label_px) > g.label_w: pairable = False
        if not pairable or text > half:
            close(); lines.append([(i, 0, 2, 1)]); continue
        line.append((i, len(line), 1, 1))
        if len(line) == columns: close()
    close()
    return lines


def pages_of(lines, g):
    pages, page, used = [], [], 0
    for line in lines:
        units = max(s[3] for s in line)
        if used + units > g.rows and page:
            pages.append(page); page, used = [], 0
        page.append(line); used += units
    return pages + [page] if page or not pages else pages


def wrap(text, px, width, fonts):
    words, lines, now = text.split(), [], ''
    for w in words:
        if fonts.width((now + ' ' + w).strip(), px, True) > width: lines.append(now); now = w
        else: now = (now + ' ' + w).strip()
    return lines + [now]


def draw_row(p, g, row, x0, y0, x1, y1, focused, opened):
    a = 1.0 if row['enabled'] else F.DISABLED
    cy = (y0 + y1) / 2
    kind, label = row['kind'], row['label']
    p.hit('row:' + row['id'], x0, y0, x1, y1)
    if kind == 'action':
        style = 'off' if not row['enabled'] else 'armed' if row['id'] == 'confirmation.apply' else 'tonal'
        lines = wrap(label, F.BUTTON_PX, x1 - x0 - 24, p.fonts) if p.fonts.width(label, F.BUTTON_PX, True) > x1 - x0 - 24 else None
        if row['value']:     # a child's row carries its keys beside its name
            p.button(x0, y0 + 1, x1 - 96, y1 - 1, label, style)
        else:
            p.button(x0, y0 + 1, x1, y1 - 1, label, style, F.ROW_ICONS.get(row['id']), lines=lines)
        if focused: p.stroke(x0, y0 + 1, x1, y1 - 1, min((y1 - y0 - 2) / 2, 16), 2, 'primary')
        return
    if focused: p.band(x0 - 12 if x0 - 12 >= 0 else 0, y0, min(x1 + 12, p.w), y1)
    ink = 'on_secondary_container' if focused else 'on_surface'
    body = x0 + 2
    tail = x1 - 2
    lw = g.label_w if kind in ('range', 'choice', 'toggle') else p.fonts.width(label, g.label_px, focused)
    if kind == 'text':
        lw = p.fonts.width(label, F.BUTTON_PX, False)
        p.text(label, F.BUTTON_PX, False, body, cy, ink, a, (x0, x1))
        p.field(body + lw + g.gap, y0 + 4, tail, y1 - 4, row['value'], usable=row['enabled'], caret=False)
        return
    label_text = p.fit(label, g.label_px, focused, (lw if kind != 'readonly' else (x1 - x0) * .62))
    p.text(label_text, g.label_px, focused, body, cy, ink, a, (x0, body + (lw if kind != 'readonly' else (x1 - x0) * .62)))
    start = body + lw + g.gap
    if kind == 'range':
        lo, hi, value = row['minimum'], row['maximum'], float(row['value'])
        flat = 0 if lo <= 0 <= hi and row.get('unit') != '%' else (100 if lo <= 100 <= hi and row['id'] in ('placement_distance', 'background_brightness') else lo)
        p.slider(start, tail - g.value_w - g.gap, cy, (value - lo) / (hi - lo), (flat - lo) / (hi - lo), a, focused)
        p.right(shown(row), g.label_px, focused, tail, cy, 'on_secondary_container' if focused else 'on_surface_variant', a, (tail - g.value_w, tail))
    elif kind == 'toggle':
        p.switch(tail - 46, cy, row['value'] == 'true', a)
    elif kind == 'choice':
        p.field(start, y0 + 4, tail, y1 - 4, row['value'], is_open=opened, usable=row['enabled'])
    elif kind == 'readonly':
        room = tail - (body + (x1 - x0) * .62) - 8
        value = p.fit(row['value'] or '—', F.BUTTON_PX, False, room)
        p.right(value, F.BUTTON_PX, False, tail, cy, 'on_secondary_container' if focused else 'on_surface_variant', a, (tail - room, tail))


def draw_body(p, g, rows, page, focus, open_row):
    drawn, y, field_box = [], g.top, None
    half = (g.x1 - g.x0 - g.gap) / 2
    for line in page:
        units = max(s[3] for s in line)
        for i, column, span, _ in line:
            x0 = g.x0 if span == 2 or column == 0 else g.x0 + half + g.gap
            x1 = g.x1 if span == 2 or column == 1 else g.x0 + half
            row = rows[i]
            draw_row(p, g, row, x0, y, x1, y + units * g.row_h, i == focus, row['id'] == open_row)
            drawn.append(row['id'])
            if row['id'] == open_row: field_box = (x0 + 2 + g.label_w + g.gap, y + 4, x1 - 2, y + g.row_h - 4)
        y += units * g.row_h
    if open_row and field_box:
        row = next(r for r in rows if r['id'] == open_row)
        x0, y0, x1, y1 = field_box
        n = len(row['choices'])
        h = n * F.ITEM_H
        top = y0 - F.LIST_GAP - h if y0 - F.LIST_GAP - h >= 46 else y1 + F.LIST_GAP
        p.menu_list(x0, top, x1, top + h)
        for k, choice in enumerate(row['choices']):
            p.list_item(x0, top + k * F.ITEM_H, x1, top + (k + 1) * F.ITEM_H, p.fit(choice, F.BUTTON_PX, True, x1 - x0 - 16), choice == row['value'], f'item:{row["id"]}:{choice}')
    return drawn


def footer(p, snap, y, x0=22):
    x1 = p.w - 22
    status = snap['status']
    message = '' if status == 'idle' else (snap['message'] or status.capitalize())
    role = 'on_surface_variant'
    p.right(HINT, F.HINT_PX, False, x1, y, role, 1, (x0, x1))
    if message:
        room = x1 - p.fonts.width(HINT, F.HINT_PX) - 24 - x0
        p.text(p.fit(message, F.HINT_PX, False, room), F.HINT_PX, False, x0, y, 'primary' if status == 'applied' else 'on_surface', 1, (x0, x0 + room))


def scope_labels(snap):
    return [snap['monitor'], 'Workspace']


def close_button(p, x1, focus):
    x0 = x1 - 74
    p.button(x0, 9, x1, 35, 'Close', 'tonal')
    p.hit('close', x0, 9, x1, 35)
    if focus == -1: p.stroke(x0, 9, x1, 35, 13, 2, 'primary')
    return x0


def flow_chips(fonts, width, px=16, pad=10, gap=6):
    """Tabs in order, as many to a line as fit: [[(category, x0, x1)]], each line stretched to the width."""
    lines, line, used = [], [], 0
    for c in CATEGORIES:
        w = fonts.width(TAB_NAMES[c], px, True) + 2 * pad
        if line and used + gap + w > width: lines.append((line, used)); line, used = [], 0
        line.append((c, w)); used += w + (gap if len(line) > 1 else 0)
    lines.append((line, used))
    out = []
    for line, used in lines:
        extra = (width - used) / len(line)
        x, row = 0, []
        for c, w in line:
            row.append((c, x, x + w + extra)); x += w + extra + gap
        out.append(row)
    return out


class Design:
    number, slug, name, blurb, w, h = 0, '', '', '', 0, 0
    keeps_geometry = False
    grid: Grid

    def page_count(self, snap, fonts):
        return len(pages_of(flow(snap['rows'], self.grid, fonts), self.grid))

    def draw(self, snap, fonts, page=0, open_row=None, open_nav=False):
        p = Panel(f'{self.slug}:{snap["category"]}:{snap["scope"]}:{page}', self.w, self.h, fonts)
        p.shell(self.grabber)
        self.nav(p, snap, open_nav)
        pages = pages_of(flow(snap['rows'], self.grid, fonts), self.grid)
        drawn = []
        if snap['rows']:
            drawn = draw_body(p, self.grid, snap['rows'], pages[page], snap['focus'], open_row)
            if len(pages) > 1: self.scroll(p, page, len(pages))
        else:
            p.centre('Nothing here for this scope', F.BUTTON_PX, False, self.grid.x0, self.grid.x1, self.grid.top + 2 * self.grid.row_h, 'on_surface_variant')
        self.after(p, snap, open_nav)
        return Page(p, page, len(pages), drawn)

    grabber = True

    def after(self, p, snap, open_nav): pass

    def scroll(self, p, page, count):
        """Where the list is, when it has more rows than fit: a track in the slider's colours down the right edge."""
        g = self.grid
        x = g.x1 + 8
        y0, y1 = g.top + 4, g.top + g.rows * g.row_h - 4
        p.fill(x, y0, x + 4, y1, 2, 'primary', F.TRACK_ALPHA)
        step = (y1 - y0) / count
        p.fill(x, y0 + page * step, x + 4, y0 + (page + 1) * step, 2, 'primary')


class PlacementTwin(Design):
    """The placement panel's own shell and height, one column wider: sections become its dropdown, scope its level-bar capsule."""
    number, slug, name = 1, 'placement-twin', 'Placement Twin'
    blurb = 'Same height, header, 34 px rows and 24 px text as the placement menu. Tabs become a Section dropdown, scope a level-bar capsule.'
    w, h = 560, 394
    grid = Grid(22, 526, 122, 7, label_w=302, value_w=78)

    def nav(self, p, snap, open_nav):
        cy = p.header(snap['monitor'], None)
        p.right('Options', 24, False, close_button(p, p.w - 22, snap['focus']) - 10, cy, 'on_surface_variant')
        y = 46 + 17
        p.text('Section', 24, False, 22, y, 'on_surface')
        box = (184, 50, 538, 76)
        p.field(*box, TAB_NAMES[snap['category']], is_open=open_nav)
        p.hit('section', *box)
        y = 80 + 17
        p.text('Scope', 24, False, 22, y, 'on_surface')
        p.segmented(184, 84, 538, 110, scope_labels(snap), SCOPE.index(snap['scope']), names=['scope:monitor', 'scope:workspace'], focused=SCOPE.index(snap['scope']) if snap['focus'] == -4 else None)
        p.fill(22, 117, 538, 118, 0, 'outline_variant')
        footer(p, snap, 376)

    def after(self, p, snap, open_nav):
        if not open_nav: return
        x0, x1, y0 = 184, 538, 78
        items = 7
        p.menu_list(x0, y0, x1, y0 + items * 23)
        half = (x1 - x0) / 2
        for i, c in enumerate(CATEGORIES):
            ix = x0 + (i // items) * half
            y = y0 + (i % items) * 23
            p.list_item(ix, y, ix + half, y + 23, TAB_NAMES[c], c == snap['category'], f'tab:{c}')


SCOPE = ('monitor', 'workspace')


class SideRail(Design):
    """Every tab always in sight down a rail of list rows; the selected one is the placement menu's fully rounded band."""
    number, slug, name = 2, 'side-rail', 'Side Rail'
    blurb = 'All 14 tabs always visible on a rail using the selected-row band. Rows keep the placement menu\'s size to their right.'
    w, h = 780, 442
    grid = Grid(196, 746, 50, 10, label_w=296, value_w=78)
    RAIL = 180

    def nav(self, p, snap, open_nav):
        cy = p.header(snap['monitor'], None)
        close_x = close_button(p, p.w - 22, snap['focus'])
        p.segmented(close_x - 244, 9, close_x - 12, 35, scope_labels(snap), SCOPE.index(snap['scope']), names=['scope:monitor', 'scope:workspace'], focused=SCOPE.index(snap['scope']) if snap['focus'] == -4 else None)
        p.text('Options', 24, False, 110, cy, 'on_surface_variant')
        p.fill(self.RAIL, 42, self.RAIL + 1, self.h - 34, 0, 'outline_variant')
        h, top = 26, 48
        for i, c in enumerate(CATEGORIES):
            y = top + i * h
            on = c == snap['category']
            if on: p.fill(10, y + 1, self.RAIL - 8, y + h - 1, (h - 2) / 2, 'secondary_container')
            p.text(TAB_NAMES[c], F.BUTTON_PX, on, 24, y + h / 2, 'on_secondary_container' if on else 'on_surface', 1, (10, self.RAIL - 8))
            p.hit(f'tab:{c}', 10, y, self.RAIL - 8, y + h)
            if on and snap['focus'] == -3: p.stroke(10, y + 1, self.RAIL - 8, y + h - 1, (h - 2) / 2, 2, 'primary')
        footer(p, snap, self.h - 16, 196)


class ChipDeck(Design):
    """Every tab always in sight as a chip, the 3D button's tonal and lit capsules, above the same rows."""
    number, slug, name = 3, 'chip-deck', 'Chip Deck'
    blurb = 'Tabs as capsules in the 3D button\'s tonal and lit colours, three lines over placement-sized rows. Nothing hides behind a page.'
    w, h = 640, 428
    grid = Grid(22, 606, 150, 7, label_w=322, value_w=78)

    def nav(self, p, snap, open_nav):
        cy = p.header(snap['monitor'], None)
        close_x = close_button(p, p.w - 22, snap['focus'])
        p.segmented(close_x - 236, 9, close_x - 12, 35, scope_labels(snap), SCOPE.index(snap['scope']), names=['scope:monitor', 'scope:workspace'], focused=SCOPE.index(snap['scope']) if snap['focus'] == -4 else None)
        y = 50
        for line in flow_chips(p.fonts, self.w - 44):
            for c, x0, x1 in line:
                p.chip(22 + x0, y, 22 + x1, y + 26, TAB_NAMES[c], c == snap['category'], c == snap['category'] and snap['focus'] == -3)
                p.hit(f'tab:{c}', 22 + x0, y, 22 + x1, y + 26)
            y += 31
        p.fill(22, y + 2, self.w - 22, y + 3, 0, 'outline_variant')
        footer(p, snap, self.h - 16)


class DropInReskin(Design):
    """Today's 600 x 440 and every hit rect of options_geometry unchanged, redrawn with the family's parts."""
    number, slug, name = 4, 'drop-in-reskin', 'Drop-in Reskin'
    blurb = 'Today\'s exact 600 x 440 and hit zones, four tabs a page, redrawn in the family\'s parts. Smallest build, smallest text change.'
    w, h = 600, 440
    keeps_geometry = True
    grid = Grid(16, 584, 88, 8, row_h=38, label_px=18, label_w=274, value_w=122, gap=10, pair_actions=False)

    def nav(self, p, snap, open_nav):
        scope = snap['scope'] == 'monitor'
        cy = 22
        p.text(snap['monitor'] if scope else 'All monitors', F.BUTTON_PX + 2, True, 16, cy, 'on_surface')
        # free_pointer.py's header rects, unchanged: scope 350..440, Next 440..530, Close 530.. Fixed, so never sized to their text.
        p.button(350, 9, 438, 35, 'Workspace' if scope else 'Monitor', 'tonal', px=14)
        p.hit('scope:toggle', 350, 9, 440, 35)
        p.button(442, 9, 528, 35, 'Next', 'tonal', px=14)
        p.hit('next', 440, 9, 530, 35)
        if snap['focus'] == -4: p.stroke(350, 9, 438, 35, 13, 2, 'primary')
        if snap['focus'] == -2: p.stroke(442, 9, 528, 35, 13, 2, 'primary')
        p.button(532, 9, 594, 35, 'Close', 'tonal', px=14)
        p.hit('close', 530, 9, 594, 35)
        if snap['focus'] == -1: p.stroke(532, 9, 594, 35, 13, 2, 'primary')
        start = CATEGORIES.index(snap['category']) // 4 * 4
        for i, c in enumerate(CATEGORIES[start:start + 4]):
            x = 16 + i * 142
            p.chip(x, 46, x + 136, 78, TAB_NAMES[c], c == snap['category'], c == snap['category'] and snap['focus'] == -3)
            p.hit(f'tab:{c}', x, 44, x + 136, 80)
        p.fill(16, 83, 584, 84, 0, 'outline_variant')
        footer(p, snap, 418, 16)

    def scroll(self, p, page, count):
        g = self.grid
        x = g.x1 + 4
        y0, y1 = g.top + 4, g.top + g.rows * g.row_h - 4
        p.fill(x, y0, x + 4, y1, 2, 'primary', F.TRACK_ALPHA)
        step = (y1 - y0) / count
        p.fill(x, y0 + page * step, x + 4, y0 + (page + 1) * step, 2, 'primary')


class WideSheet(Design):
    """Two columns of rows, so even Video's 17 rows sit on one page with no scrolling."""
    number, slug, name = 5, 'wide-sheet', 'Wide Sheet'
    blurb = 'Two columns of rows at button size, so Video\'s 17 rows fit with no scrolling. The widest panel, the most on screen at once.'
    w, h = 860, 454
    grid = Grid(22, 830, 114, 9, label_px=18, label_w=232, value_w=56, gap=14, columns=2)

    def nav(self, p, snap, open_nav):
        cy = p.header(snap['monitor'], None)
        close_x = close_button(p, p.w - 22, snap['focus'])
        p.text('Options', 24, False, 110, cy, 'on_surface_variant')
        p.segmented(close_x - 244, 9, close_x - 12, 35, scope_labels(snap), SCOPE.index(snap['scope']), names=['scope:monitor', 'scope:workspace'], focused=SCOPE.index(snap['scope']) if snap['focus'] == -4 else None)
        y = 48
        for line in flow_chips(p.fonts, self.w - 44):
            for c, x0, x1 in line:
                p.chip(22 + x0, y, 22 + x1, y + 26, TAB_NAMES[c], c == snap['category'], c == snap['category'] and snap['focus'] == -3)
                p.hit(f'tab:{c}', 22 + x0, y, 22 + x1, y + 26)
            y += 30
        footer(p, snap, self.h - 16)


DESIGNS = [PlacementTwin(), SideRail(), ChipDeck(), DropInReskin(), WideSheet()]
