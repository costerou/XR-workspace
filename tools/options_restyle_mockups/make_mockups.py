"""Everything: python3 -m tools.options_restyle_mockups.make_mockups. Writes menu/, maximize/, index.html and checks.json to OUT."""
import argparse
import html
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import checks as C, family as F, harness as h, maximize_options as X, menu_options as M
from .harness import REAL, Stage, with_placement

NEUTRAL = [dict(d, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0)) for d in REAL]
SCENES = dict(row=dict(setup=NEUTRAL, spacing=40), rolled=dict(setup=with_placement(NEUTRAL, 1, roll=800), spacing=40),
              curved=dict(setup=with_placement(NEUTRAL, 1, curvature=60), spacing=40))
BEAST = [dict(w=1920, h=1080, scale=1.0, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0))]
CORNER = 'bottom-right'
LOOK_FOR_PLACES = X.LOOKS[0]
BG, INK, DIM, LINE = (10, 10, 12), (224, 224, 228), (150, 150, 156), (54, 54, 60)
FONT = '/usr/share/fonts/TTF/JetBrainsMonoNerdFont-{}.ttf'


def font(size, bold=False):
    return ImageFont.truetype(FONT.format('Bold' if bold else 'Regular'), size)


def wrap(text, f, width):
    d = ImageDraw.Draw(Image.new('L', (1, 1)))
    lines, now = [], ''
    for word in text.split():
        trial = (now + ' ' + word).strip()
        if d.textlength(trial, font=f) > width: lines.append(now); now = word
        else: now = trial
    return lines + [now]


def page(width, height):
    im = Image.new('RGB', (width, height), BG)
    return im, ImageDraw.Draw(im)


def caption(d, x, y, text, width, size=20, fill=DIM, bold=False):
    f = font(size, bold)
    for line in wrap(text, f, width):
        d.text((x, y), line, font=f, fill=fill); y += size + 8
    return y


def box_crop(im, box, scale, pad, aspect=None):
    """The canvas `box` (x0, y0, x1, y1) of a frame rendered at `scale`, padded, inside the frame."""
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    if aspect:
        w, hgt = x1 - x0, y1 - y0
        if w / hgt < aspect:
            grow = (hgt * aspect - w) / 2; x0 -= grow; x1 += grow
        else:
            grow = (w / aspect - hgt) / 2; y0 -= grow; y1 += grow
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, im.width / scale), min(y1, im.height / scale)
    return im.crop((int(x0 * scale), int(y0 * scale), int(x1 * scale), int(y1 * scale)))


def fitted(im, w, hgt):
    k = min(w / im.width, hgt / im.height)
    return im.resize((max(int(im.width * k), 1), max(int(im.height * k), 1)), Image.LANCZOS)


def cell(sheet, im, x, y, w, hgt, label=None):
    small = fitted(im, w, hgt)
    sheet.paste(small, (x, y))
    d = ImageDraw.Draw(sheet)
    d.rectangle((x - 1, y - 1, x + small.width, y + small.height), outline=LINE)
    if label: d.text((x, y + small.height + 6), label, font=font(16), fill=DIM)


class Run:
    def __init__(self, accent, out, work):
        self.accent, self.out, self.work = accent, Path(out), Path(work)
        self.stage = Stage(accent, self.work / 'desktops')
        self.fonts = F.Fonts()
        self.snaps = h.snapshots()
        self.children = h.child_snapshots()
        self.results = {}

    def snap(self, category, scope, **extra):
        return dict(next(s for s in self.snaps if s['category'] == category and s['scope'] == scope), **extra)

    def frames(self, frames, scale=2):
        reports, renderer = h.render(frames, self.accent, scale)
        self.results['renderer'] = json.loads(json.loads(renderer)['report'])['renderer'] if renderer else ''
        return reports

    def path(self, name):
        return self.work / 'frames' / name

    # --- Menu ---------------------------------------------------------------------------------------------------------
    def menu_overlay(self, d, tile, pill, snap, space='glass', centred=False):
        panel = d.draw(snap, self.fonts).panel
        x = tile[0] + (tile[2] - d.w) / 2
        y = tile[1] + (tile[3] - d.h) / 2 if centred else pill[1]
        return dict(panel.overlay(), x=x - F.MARGIN, y=y - F.MARGIN, space=space, density=1, name=d.slug), (x, y, d.w, d.h)

    def menu_scene_frames(self, d, snap):
        out = []
        for key, scene, look in (('real', dict(setup=REAL, spacing=h.REAL_SPACING), True), ('rolled', SCENES['rolled'], False), ('curved', SCENES['curved'], False)):
            base = dict(selected=1, look=look, **scene)
            frame = self.stage.frame(self.path(f'menu-{d.slug}-{key}.png'), pills=True, real_handles=True, **base)
            geo = h.layout([frame])[0]
            overlay, rect = self.menu_overlay(d, geo['tiles'][1], geo['pills'][1], snap)
            out.append(dict(frame, overlays=[overlay, X.bounds(rect, 'glass', 1, 'bounds')], pitch_for=list(rect), pitch_desktop=1))
        frame = self.stage.frame(self.path(f'menu-{d.slug}-maximized.png'), pills=False, zoom=1.0, selected=1)
        geo = h.layout([frame])[0]
        overlay, rect = self.menu_overlay(d, geo['tiles'][1], geo['pills'][1], snap, space='tile', centred=True)
        out.append(dict(frame, overlays=[overlay, X.bounds(rect, 'tile', 1, 'bounds')]))
        return out

    def all_tabs(self, d):
        """Every page of every tab in both scopes, the children, and the open lists: one sheet at panel size."""
        panels = []
        for snap in self.snaps:
            for n in range(d.page_count(snap, self.fonts)):
                suffix = f' page {n + 1}' if d.page_count(snap, self.fonts) > 1 else ''
                panels.append((f'{F.TAB_NAMES[snap["category"]]} / {snap["scope"]}{suffix}', d.draw(snap, self.fonts, n).panel))
        for name, snap in self.children.items():
            for n in range(d.page_count(snap, self.fonts)):
                panels.append((f'{name.replace("_", " ")} page {n + 1}', d.draw(snap, self.fonts, n).panel))
        for title, snap, kw in (('Dropdown open', self.snap('video', 'workspace', focus=-1), dict(open_row='quality_filter')),
                                ('Pending', self.snap('stabilization', 'workspace', status='pending', message='Waiting for owner readback', focus=0), {}),
                                ('Error', self.snap('output', 'monitor', status='error', message='Owner rejected change', focus=2), {}),
                                ('Close focused', self.snap('view', 'monitor', focus=-1), {}),
                                ('Scope focused', self.snap('view', 'workspace', focus=-4), {}),
                                ('Tab focused', self.snap('layout', 'workspace', focus=-3), {})):
            page_no = 0
            if kw.get('open_row'):
                page_no = next(n for n in range(d.page_count(snap, self.fonts)) if kw['open_row'] in d.draw(snap, self.fonts, n).drawn)
            panels.append((title, d.draw(snap, self.fonts, page_no, **kw).panel))
            self.results.setdefault('open_lists', {}).setdefault(d.slug, []).extend(C.panel_problems(panels[-1][1], covered=True))
        if d.slug == 'placement-twin':
            panels.append(('Section list open', d.draw(self.snap('video', 'workspace'), self.fonts, open_nav=True).panel))
        images = []
        for i, (_, p) in enumerate(panels):
            images.append(dict(p.overlay(), out=str(self.work / f'panel-{d.slug}-{i}.rgba')))
        h.job_call('raster', dict(accent=self.accent, images=images))
        cap = 0
        cols = max(2, 2400 // (d.w + 40))
        w, hgt = d.w + 40, d.h + 72
        rows = (len(panels) + cols - 1) // cols
        sheet, draw = page(cols * w + 40, rows * hgt + 130)
        draw.text((30, 24), f'{d.number:02d} {d.name}: every tab, scope, page, child and state ({len(panels)} panels)', font=font(30, True), fill=INK)
        draw.text((30, 70), 'Drawn at panel size by the renderer\'s own parts, in the real order, with the real labels', font=font(18), fill=DIM)
        for i, (title, p) in enumerate(panels):
            iw, ih = int(images[i]['w']), int(images[i]['h'])
            a = np.frombuffer(open(images[i]['out'], 'rb').read(), np.uint8).reshape(ih, iw, 4)
            cap = max(cap, int(a[..., :3].max()))
            x, y = 30 + (i % cols) * w, 120 + (i // cols) * hgt
            draw.text((x, y), title, font=font(16), fill=DIM)
            sheet.paste(Image.fromarray(a[..., :3]).crop((F.MARGIN, F.MARGIN, F.MARGIN + p.w, F.MARGIN + p.h)), (x, y + 24))
        name = self.out / 'menu' / f'{d.number:02d}-{d.slug}-all-tabs.png'
        sheet.save(name)
        return dict(panels=len(panels), brightest=cap)

    def menu(self):
        stage = self.stage
        sample = self.snap('placement', 'monitor', focus=6)
        base = dict(selected=1, look=True, setup=REAL, spacing=h.REAL_SPACING)
        real = self.snap('output', 'monitor')
        baseline = [stage.frame(self.path('real-placement.png'), pills=True, real_handles=True, real_placement=dict(desktop=1, field=0, res_w=1920, res_h=1200), **base),
                    stage.frame(self.path('real-options.png'), pills=True, real_handles=True, real_options=dict(real, desktop=1), **base),
                    stage.frame(self.path('real-options-placement.png'), pills=True, real_handles=True, real_options=dict(self.snap('placement', 'monitor'), desktop=1), **base)]
        self.frames(baseline)
        frames = [f for d in M.DESIGNS for f in self.menu_scene_frames(d, sample)]
        reports = self.frames(frames)
        by_name = {Path(f['out']).name: r for f, r in zip(frames, reports)}
        placement = Image.open(self.path('real-placement.png'))
        geo = h.layout([baseline[0]])[0]
        pill = geo['pills'][1]
        tile = geo['tiles'][1]
        (self.out / 'menu').mkdir(parents=True, exist_ok=True)
        self.results['menu'] = {}
        for d in M.DESIGNS:
            problems, pages = C.check_design(d, self.snaps, self.children, self.fonts)
            sheet = self.all_tabs(d)
            boxes = {k: by_name[f'menu-{d.slug}-{k}.png']['overlays'][1]['box'] for k in ('real', 'rolled', 'curved', 'maximized')}
            inside = {k: bool(b[0] >= 0 and b[1] >= 0 and b[2] <= 1920 and b[3] <= 1200) for k, b in boxes.items()}
            self.results['menu'][d.slug] = dict(number=d.number, name=d.name, size=[d.w, d.h], pages=pages, problems=problems, open_list_problems=self.results['open_lists'][d.slug],
                                                tabs_unreachable=sorted(C.tabs_reachable(d, self.snaps[0], self.fonts)), brightest=sheet['brightest'], panels=sheet['panels'],
                                                pitch_real=by_name[f'menu-{d.slug}-real.png']['pitch'], in_view=inside, boxes=boxes, geometry_changes=C.real_geometry_kept(d, self.fonts) if d.keeps_geometry else None)
            self.menu_page(d, boxes)
        self.menu_contact()

    def menu_page(self, d, boxes):
        im, draw = page(2400, 1560)
        draw.text((40, 28), f'{d.number:02d}  {d.name}', font=font(42, True), fill=INK)
        caption(draw, 40, 88, d.blurb, 2300, 22)
        placement = Image.open(self.path('real-placement.png'))
        report = self.placement_box
        mine = Image.open(self.path(f'menu-{d.slug}-real.png'))
        # Same scale for both: 1 canvas px = 1 page px, so the two panels can be compared directly.
        a = box_crop(placement, report, 2, 26).resize((int((report[2] - report[0] + 52)), int(report[3] - report[1] + 52)), Image.LANCZOS)
        b0 = boxes['real']
        b = box_crop(mine, b0, 2, 26)
        b = b.resize((b.width // 2, b.height // 2), Image.LANCZOS)
        draw.text((40, 142), 'The real placement menu, real compositor', font=font(20, True), fill=INK)
        im.paste(a, (40, 176))
        draw.text((a.width + 100, 142), f'This option, same scale, on Cody\'s real XR-2 (1920x1200 at 125%, 45% distance)', font=font(20, True), fill=INK)
        im.paste(b, (a.width + 100, 176))
        y = max(a.height, b.height) + 206
        x = 40
        titles = dict(real='Overview, Cody\'s saved layout', rolled='Overview, monitor rolled 8 degrees', curved='Overview, monitor curved 60 degrees',
                      maximized='Maximized: HYPOTHETICAL, the renderer draws the menu in the overview only')
        for key in ('real', 'rolled', 'curved', 'maximized'):
            shot = Image.open(self.path(f'menu-{d.slug}-{key}.png'))
            if key in ('rolled', 'curved'): shot = box_crop(shot, boxes[key], 2, 90, 1.6)
            draw.text((x, y), titles[key], font=font(15), fill=INK)
            cell(im, shot, x, y + 26, 570, 356)
            x += 586
        im = im.crop((0, 0, 2400, y + 400))
        im.save(self.out / 'menu' / f'{d.number:02d}-{d.slug}.png')

    def menu_contact(self):
        cols, w, hgt = 3, 960, 610
        sheet, draw = page(cols * w + 50, 2 * hgt + 110)
        draw.text((30, 20), 'Options menu: five looks in the placement family, with the real menus for comparison', font=font(30, True), fill=INK)
        draw.text((30, 62), 'Panels are mock overlays from the renderer\'s own parts on real compositor frames. All at the same scale.', font=font(18), fill=DIM)
        def tile(i, title, shot, box, note):
            x, y = 30 + (i % cols) * w, 100 + (i // cols) * hgt
            draw.text((x, y), title, font=font(21, True), fill=INK)
            crop = box_crop(shot, box, 2, 14)
            crop = crop.resize((crop.width // 2, crop.height // 2), Image.LANCZOS)
            crop = fitted(crop, w - 24, hgt - 74) if crop.width > w - 24 or crop.height > hgt - 74 else crop
            sheet.paste(crop, (x, y + 32))
            draw.text((x, y + hgt - 34), note, font=font(14), fill=DIM)
        tile(0, 'Today: the real options menu', Image.open(self.path('real-options-placement.png')), self.options_box, 'pixel font, fixed blue seed, header text collides')
        for n, d in enumerate(M.DESIGNS):
            r = self.results['menu'][d.slug]
            tile(n + 1, f'{d.number:02d} {d.name}', Image.open(self.path(f'menu-{d.slug}-real.png')), r['boxes']['real'], f'{d.w} x {d.h} panel px' + ('  same hit zones as today' if d.keeps_geometry else ''))
        sheet.save(self.out / 'menu' / self.contact_name)
        placement = Image.open(self.path('real-placement.png'))
        crop = box_crop(placement, self.placement_box, 2, 14)
        crop.resize((crop.width // 2, crop.height // 2), Image.LANCZOS).save(self.out / 'menu' / 'reference-placement-menu.png')

    # --- Maximize looks -----------------------------------------------------------------------------------------------
    def buttons(self, look, geo, hover=None, restore=False):
        out = []
        for i, bar in enumerate(geo['bars']):
            s = geo['scale'][i]
            r = look.rect(bar, s)
            p = look.draw(False, i == hover, self.fonts)
            out.append(X.placed(p, r[0], r[1], s, desktop=i, name=f'button:{i}'))
        return out

    def look_frames(self, look):
        frames = []
        for key, scene, lk in (('row', SCENES['row'], False), ('rolled', SCENES['rolled'], False), ('curved', SCENES['curved'], False), ('real', dict(setup=REAL, spacing=h.REAL_SPACING), True)):
            frame = self.stage.frame(self.path(f'look-{look.slug}-{key}.png'), selected=1, look=lk, pills=True, handles=True, **scene)
            geo = h.layout([frame])[0]
            frames.append(dict(frame, overlays=self.buttons(look, geo)))
        frame = self.stage.frame(self.path(f'look-{look.slug}-hover.png'), selected=1, pills=True, handles=True, **SCENES['row'])
        geo = h.layout([frame])[0]
        r = look.rect(geo['bars'][1], geo['scale'][1])
        tip = X.tooltip(self.fonts, 'Maximize')
        over = self.buttons(look, geo, hover=1) + [X.placed(tip, r[0] + r[2] / 2 - tip.w / 2, r[1] - 32, name='tooltip', desktop=1)]
        frames.append(dict(frame, overlays=over, dot=[r[0] + r[2] - 3, r[1] + r[3] + 1]))   # beside the icon, so the icon shows
        for i, name in ((1, 'xr2'), (0, 'xr1')):
            frame = self.stage.frame(self.path(f'look-{look.slug}-restore-{name}.png'), selected=i, zoom=1.0, pills=False)
            geo = h.layout([frame])[0]
            spot = geo['real_restore']   # today's resolved rect: the look centred on it
            x, y = spot[0] + (spot[2] - look.w) / 2, spot[1] + (spot[3] - look.h) / 2
            frames.append(dict(frame, overlays=[X.placed(look.draw(True, False, self.fonts), x, y, name='restore')]))
        return frames

    def looks(self):
        frames = [f for l in X.LOOKS for f in self.look_frames(l)]
        frames.append(self.stage.frame(self.path('look-today.png'), selected=1, pills=True, real_handles=True, **SCENES['row']))
        frames.append(self.stage.frame(self.path('look-today-restore.png'), selected=1, zoom=1.0, pills=False, real_restore=True))
        reports = self.frames(frames)
        by = {Path(f['out']).name: r for f, r in zip(frames, reports)}
        out = self.out / 'maximize'
        out.mkdir(parents=True, exist_ok=True)
        self.results['looks'] = {}
        tiles = self.tile_cases()
        for l in X.LOOKS:
            problems = [p for t in tiles for p in C.tile_problems(l, t)]
            self.results['looks'][l.slug] = dict(number=l.number, name=l.name, size=[l.w, l.h], problems=problems, maximize_offset=[float(v) for v in C.centred(l, False, self.fonts, self.accent, self.work)],
                                                 restore_offset=[float(v) for v in C.centred(l, True, self.fonts, self.accent, self.work)])
            self.look_page(l, by)
        self.by_look = by

    def tile_cases(self):
        """The real saved layout, the flat row, four desktops and a narrow row."""
        four = NEUTRAL + [dict(NEUTRAL[1])]
        frames = [dict(desktops=[dict(w=d['w'], h=d['h'], placement=d['placement']) for d in setup], spacing=sp, selected=0, zoom=0.0) for setup, sp in ((REAL, 0), (NEUTRAL, 40), (four, 40), (four, 0))]
        return h.layout(frames)

    def look_page(self, l, by):
        im, draw = page(2400, 1500)
        draw.text((40, 28), f'M{l.number}  {l.name}', font=font(42, True), fill=INK)
        caption(draw, 40, 88, l.blurb, 2300, 22)
        names = (('row', 'Overview, flat row'), ('hover', 'Hover, tooltip, dot on it'), ('rolled', 'Rolled 8 degrees'), ('curved', 'Curved 60 degrees'))
        x, y = 40, 150
        for key, title in names:
            shot = Image.open(self.path(f'look-{l.slug}-{key}.png'))
            b = by[f'look-{l.slug}-{key}.png']['overlays'][1]['box']
            r = (b[0] - 40, b[1] - 40, b[0] + 480, b[3] + 40)
            draw.text((x, y), title, font=font(18), fill=INK)
            cell(im, box_crop(shot, r, 2, 0), x, y + 30, 570, 300)
            x += 586
        y += 360
        real = Image.open(self.path(f'look-{l.slug}-real.png'))
        draw.text((40, y), 'Overview, Cody\'s saved layout (XR-2 at 45% distance scales the bar with it)', font=font(18), fill=INK)
        cell(im, real, 40, y + 30, 1160, 725)
        rest = Image.open(self.path(f'look-{l.slug}-restore-xr2.png'))
        draw.text((1220, y), 'Maximized XR-2: the look only, at today\'s inset spot. Where it goes is R1 to R4', font=font(18), fill=INK)
        cell(im, rest, 1220, y + 30, 1140, 712)
        im = im.crop((0, 0, 2400, y + 780))
        im.save(self.out / 'maximize' / f'M{l.number}-{l.slug}.png')

    # --- Restore places -----------------------------------------------------------------------------------------------
    def place_frames(self):
        s = self.stage
        L = LOOK_FOR_PLACES
        frames = []
        def max_frame(name, i=1, **extra):
            return s.frame(self.path(name), selected=i, zoom=1.0, pills=False, **extra)
        for i, tag in ((1, 'xr2'), (0, 'xr1')):
            f = max_frame(f'place-plain-{tag}.png', i)
            geo = h.layout([f])[0]
            t = geo['tiles'][i]
            frames.append(f)
            pill = C.corner_rect(t, CORNER, X.PILL_W, X.PILL_H)
            chip = C.corner_rect(t, CORNER, 26, 26)
            corner_xy = (t[0] + t[2] - 3, t[1] + t[3] - 3)
            near = (corner_xy[0] - 100, corner_xy[1] - 80)
            # R1: at rest, resting in the corner before the pill, the pill under the pointer.
            frames.append(dict(max_frame(f'place-hot-corner-dwell-{tag}.png', i), overlays=[X.arrow_overlay(corner_xy[0] - 24, corner_xy[1] - 30)]))
            frames.append(dict(max_frame(f'place-hot-corner-shown-{tag}.png', i), overlays=[X.placed(X.restore_pill(self.fonts, L), pill[0], pill[1], name='pill')], dot=[pill[0] + 112, pill[1] + 25]))
            # R2: alpha 0 is drawn, so "nothing at rest" is measured on a frame that really contains it.
            frames.append(dict(max_frame(f'place-fade-chip-rest-{tag}.png', i), overlays=[X.placed(X.round_key(self.fonts, L, False), chip[0], chip[1], alpha=0.0, name='chip')]))
            frames.append(dict(max_frame(f'place-fade-chip-near-{tag}.png', i), overlays=[X.placed(X.round_key(self.fonts, L, False), chip[0], chip[1], alpha=.55, name='chip'), X.arrow_overlay(*near)]))
            frames.append(dict(max_frame(f'place-fade-chip-on-{tag}.png', i), overlays=[X.placed(X.round_key(self.fonts, L, True), chip[0], chip[1], name='chip')], dot=[chip[0] + 24, chip[1] + 25]))
            # R3: the picture really stepped back (the compositor's own zoom), the pill centred in the band that opens above it.
            step = s.frame(self.path(f'place-step-back-band-open-{tag}.png'), selected=i, zoom=X.STEP_ZOOM, pills=False)
            st = h.layout([step])[0]['tiles'][i]
            top = self.frames([dict(step, overlays=[X.bounds(st, 'tile', i, 'picture')])], scale=1)[0]['overlays'][0]['box'][1]
            bx, by = st[0] + (st[2] - X.PILL_W) / 2, st[1] - X.PILL_H - (top - X.PILL_H) / 2
            frames.append(dict(step, overlays=[X.placed(X.restore_pill(self.fonts, L), bx, by, name='pill'), X.bounds([bx, by, X.PILL_W, X.PILL_H], 'tile', i, 'pill-box'), X.bounds(st, 'tile', i, 'picture')],
                               dot=[960 + X.PILL_W / 2 - 10, top / 2 + 12]))
        return frames

    def places(self):
        frames = self.place_frames()
        reports = self.frames(frames)
        by = {Path(f['out']).name: r for f, r in zip(frames, reports)}
        # 16:9: the band is already there, the picture keeps its size.
        beast = self.stage.frame(self.path('place-step-back-band-169.png'), setup=BEAST, spacing=20, selected=0, zoom=1.0, pills=False)
        t = h.layout([beast])[0]['tiles'][0]
        bx, byy = t[0] + (t[2] - X.PILL_W) / 2, t[1] - X.PILL_H - 17
        beast = dict(beast, overlays=[X.placed(X.restore_pill(self.fonts, LOOK_FOR_PLACES), bx, byy, name='pill'), X.bounds([bx, byy, X.PILL_W, X.PILL_H], 'tile', 0, 'pill-box'), X.bounds(t, 'tile', 0, 'picture')], dot=[bx + 112, byy + 25])
        report_169 = self.frames([beast])[0]
        # R4: the tooltip that teaches the keys, in the overview.
        f = self.stage.frame(self.path('place-keys-and-hint-tooltip.png'), selected=1, pills=True, handles=True, **SCENES['row'])
        geo = h.layout([f])[0]
        r = LOOK_FOR_PLACES.rect(geo['bars'][1], geo['scale'][1])
        tip = X.tooltip(self.fonts, 'Maximize. Ctrl+Alt+Down comes back')
        tip_frame = dict(f, overlays=self.buttons(LOOK_FOR_PLACES, geo, hover=1) + [X.placed(tip, r[0], r[1] - 32, name='tooltip', desktop=1)], dot=[r[0] + r[2] - 3, r[1] + r[3] + 1])
        tip_report = self.frames([tip_frame])[0]
        self.results['places'] = self.place_results(by, report_169)
        for p in X.PLACES: self.place_page(p, by, tip_report, report_169)

    def place_results(self, by, report_169):
        reach = C.restore_reach(CORNER)
        out = dict(corner=CORNER, reach=reach, corner_neighbours=C.corner_table(), letterbox=C.letterbox(h.OTHER_SIZES + [('XR-1', 2560, 1600), ('XR-2', 1920, 1200), ('XR-3', 1920, 1200)]))
        same = {}
        for tag in ('xr2', 'xr1'):
            a = np.asarray(Image.open(self.path(f'place-plain-{tag}.png')))
            for name in (f'place-fade-chip-rest-{tag}.png',):
                b = np.asarray(Image.open(self.path(name)))
                same[name] = int((a != b).any(axis=2).sum())
        out['pixels_changed_at_rest'] = same
        def band(report):
            pill, picture = report['overlays'][1]['box'], report['overlays'][2]['box']
            return dict(pill=pill, picture_top=picture[1], clear_of_picture=bool(pill[3] <= picture[1]), in_view=bool(pill[1] >= 0))
        out['band'] = {tag: band(by[f'place-step-back-band-open-{tag}.png']) for tag in ('xr2', 'xr1')}
        out['band']['16:9'] = band(report_169)
        out['panel_problems'] = [p for panel in (X.restore_pill(self.fonts, LOOK_FOR_PLACES), X.round_key(self.fonts, LOOK_FOR_PLACES, False), X.tooltip(self.fonts, 'Maximize'), X.tooltip(self.fonts, 'Maximize. Ctrl+Alt+Down comes back')) for p in C.panel_problems(panel)]
        return out

    def place_page(self, p, by, tip_report, report_169):
        im, draw = page(2400, 1500)
        tag = '  NEEDS NEW POINTER-HANDOFF CODE' if p.new_handoff else ''
        draw.text((40, 26), f'R{p.number}  {p.name}{tag}', font=font(40, True), fill=(235, 150, 120) if p.new_handoff else INK)
        y = caption(draw, 40, 84, p.blurb, 2300, 22, INK)
        y = caption(draw, 40, y + 8, 'How the pointer gets there: ' + p.reach, 2300, 20)
        y = caption(draw, 40, y + 4, 'What it covers: ' + p.covers, 2300, 20)
        y = caption(draw, 40, y + 4, 'To build: ' + p.build, 2300, 20)
        y = caption(draw, 40, y + 4, 'Test: ' + p.test, 2300, 20) + 14
        P = lambda n: Image.open(self.path(n))
        corner = (1920 - 420, 1200 - 260, 1920, 1200)
        shots = {'hot-corner': [('XR-2 maximized, at rest: nothing', 'place-plain-xr2.png', None), ('Resting in the corner, 350 ms', 'place-hot-corner-dwell-xr2.png', corner),
                                ('Pill under the pointer, XR-2 1920x1200', 'place-hot-corner-shown-xr2.png', corner), ('Same on XR-1 2560x1600', 'place-hot-corner-shown-xr1.png', None)],
                 'fade-chip': [('XR-2 maximized, at rest: nothing', 'place-fade-chip-rest-xr2.png', None), ('Pointer 100 px away: fading in', 'place-fade-chip-near-xr2.png', corner),
                               ('On it, XR-2 1920x1200', 'place-fade-chip-on-xr2.png', corner), ('Same on XR-1 2560x1600', 'place-fade-chip-on-xr1.png', None)],
                 'step-back-band': [('XR-2 at rest: nothing, and no band to use', 'place-plain-xr2.png', None), ('Stepped back to 84%: band above, XR-2', 'place-step-back-band-open-xr2.png', None),
                                    ('Stepped back, XR-1', 'place-step-back-band-open-xr1.png', None), ('1920x1080 (beast profile): band already there, 60 px', 'place-step-back-band-169.png', None)],
                 'keys-and-hint': [('XR-2 maximized: nothing, ever', 'place-plain-xr2.png', None), ('XR-1 maximized: nothing, ever', 'place-plain-xr1.png', None),
                                   ('Overview: Maximize\'s tooltip teaches the keys', 'place-keys-and-hint-tooltip.png', 'tip')]}[p.slug]
        x = 40
        for title, name, crop in shots:
            shot = P(name)
            if crop == 'tip':
                b = tip_report['overlays'][-1]['box']; shot = box_crop(shot, (b[0] - 60, b[1] - 60, b[2] + 60, b[3] + 200), 2, 0, 1.6)
            elif crop: shot = box_crop(shot, crop, 2, 0)
            draw.text((x, y), title, font=font(16), fill=INK)
            cell(im, shot, x, y + 28, 570, 356)
            x += 586
        im = im.crop((0, 0, 2400, y + 410))
        im.save(self.out / 'maximize' / f'R{p.number}-{p.slug}.png')

    def maximize_contact(self):
        cw, chh = 560, 350
        sheet, draw = page(30 + 4 * (cw + 16) + 14, 1560)
        draw.text((30, 18), 'Maximize / Restore: four looks (M) and four places for Restore off the picture (R)', font=font(30, True), fill=INK)
        draw.text((30, 58), 'M: mock button on real compositor frames. R: maximized at Cody\'s real sizes, 1920x1200 at 125%, the picture filling the view.', font=font(18), fill=DIM)
        y = 96
        for n, l in enumerate(X.LOOKS):
            x = 30 + n * (cw + 16)
            draw.text((x, y), f'M{l.number} {l.name}', font=font(21, True), fill=INK)
            shot = Image.open(self.path(f'look-{l.slug}-hover.png'))
            b = self.by_look[f'look-{l.slug}-hover.png']['overlays'][1]['box']
            cell(sheet, box_crop(shot, (b[0] - 60, b[1] - 60, b[0] + 320, b[3] + 40), 2, 0), x, y + 34, cw, 190)
            shot = Image.open(self.path(f'look-{l.slug}-rolled.png'))
            b = self.by_look[f'look-{l.slug}-rolled.png']['overlays'][1]['box']
            cell(sheet, box_crop(shot, (b[0] - 60, b[1] - 80, b[0] + 320, b[3] + 60), 2, 0), x, y + 34 + 200, cw, 190, 'rolled 8 degrees')
        y += 470
        draw.line((30, y - 14, sheet.width - 30, y - 14), fill=LINE)
        corner = (1920 - 420, 1200 - 260, 1920, 1200)
        shots = dict(zip((p.slug for p in X.PLACES), (
            [('place-plain-xr2.png', None), ('place-hot-corner-shown-xr2.png', corner)],
            [('place-fade-chip-rest-xr2.png', None), ('place-fade-chip-on-xr2.png', corner)],
            [('place-plain-xr2.png', None), ('place-step-back-band-open-xr2.png', None)],
            [('place-plain-xr2.png', None), ('place-keys-and-hint-tooltip.png', 'tip')])))
        for n, p in enumerate(X.PLACES):
            x = 30 + n * (cw + 16)
            draw.text((x, y), f'R{p.number} {p.name}', font=font(21, True), fill=INK)
            yy = y + 30
            if p.new_handoff: draw.text((x, yy), 'NEEDS NEW POINTER-HANDOFF CODE', font=font(15, True), fill=(235, 150, 120))
            else: draw.text((x, yy), 'existing Restore route' if p.slug != 'keys-and-hint' else 'keyboard only', font=font(15), fill=DIM)
            yy += 26
            for name, crop in shots[p.slug]:
                shot = Image.open(self.path(name))
                if crop == 'tip':
                    shot = box_crop(shot, (660, 380, 1260, 760), 2, 0)
                elif crop: shot = box_crop(shot, crop, 2, 0)
                cell(sheet, shot, x, yy, cw, 350)
                yy += 362
            yy += 4
        sheet = sheet.crop((0, 0, sheet.width, y + 30 + 26 + 2 * 362 + 16))
        sheet.save(self.out / 'maximize' / self.contact_name)

    # --- Page ---------------------------------------------------------------------------------------------------------
    def inventory_files(self):
        shown, missing = C.inventory_cover(self.snaps)
        info = self.stage.info
        c = info['constants']
        lines = ['# What was inventoried', '', 'Read from `spike/include/xr/glass.hpp`, `spike/src/glass.cpp`, `spike/include/xr/options_geometry.hpp`, `tools/native_spike/options_*.py`, and real renders in `menu/today-*.png`.', '',
                 '## The placement menu\'s language', '', '| What | Value |', '|---|---|',
                 f'| Panel | {c["panel_w"]:.0f} x {c["panel_h"]:.0f}, radius {c["panel_radius"]:.0f}, surface container (high) at {c["panel_fill"]} with a 1 px outline-variant edge, elevation 3 |',
                 f'| Header | {c["panel_header_h"]:.0f} tall, grabber {c["grabber_w"]:.0f} x {c["grabber_h"]:.0f} at {c["grabber_top"]:.0f}, title bold left, kind right, divider |',
                 f'| Rows | {c["panel_row_h"]:.0f} tall from {c["panel_rows_top"]:.0f}, pad {c["panel_pad"]:.0f}, text {c["text_px"]} px JetBrainsMono Nerd Font |',
                 '| Selected row | fully rounded secondary-container band, 10 in from the sides, 2 from its row, bold on-secondary-container text |',
                 f'| Slider | 4 px track, primary at 24%, active part primary, knob radius {c["knob_r"]:.0f}, 14 px focus halo at 10% |',
                 f'| Dropdown | {c["box_h"]:.0f} tall filled field, 4 px corners, surface container highest, 1 px indicator (2 px primary open), 18 px bold value, 24 px caret |',
                 f'| Switch | {c["switch_w"]:.0f} x {c["switch_h"]:.0f}, primary with on-primary handle radius 9, or outlined with outline handle radius 6 |',
                 f'| Buttons | {c["action_w"]:.0f} x {c["action_h"]:.0f} capsules, gap {c["action_gap"]:.0f}, filled tonal (secondary container), primary when on, error container when armed, 12% / 38% disabled, 18 px bold |',
                 f'| Pill | {c["pill_h"]:.0f} tall, share of the desktop, primary container, {c["pill_gap"]:.0f} under it |',
                 f'| Level bar | {c["bar_w"]:.0f} x {c["bar_h"]:.0f}, {c["bar_gap"]:.0f} above, primary container, hot segment primary, hit padding {c["bar_pad"]:.0f}, scales with its desktop |',
                 f'| 3D button | joined {c["immersive_gap"]:.0f} px to the bar, {c["immersive_main_w"]:.0f} wide, tail {c["immersive_tail_w"]:.0f}, inner corners {c["immersive_inner_r"]:.0f}, off secondary container, hover primary container, on primary |',
                 f'| OLED cap | every role under 191/255 (kGlassCap {c["glass_cap"]:.3f}) |', '',
                 '## Why today\'s options menu looks different', '',
                 '- `Presentation::set_options_menu` calls `render_options_panel(state)` with no font, so it falls back to the 5 x 7 pixel font, upper case.',
                 '- `render_options_panel` seeds its scheme with a fixed `{.45, .65, .8}`, so it never follows the theme.',
                 '- Its header strings overlap ("Workspace >" runs into "Next >").',
                 '- Text sizes 14 to 18 against the family\'s 18 and 24. Sliders radius 5, toggles an outline only, actions plain text with no button.',
                 '- Maximize / Restore: a 26 px rounded square, radius about 4.7, surface container high fill unlike the bar. Its glyph sits off-centre (the `xr-options-fix` branch handles that).', '',
                 '## Rows', '', f'{len(shown)} distinct rows across 14 tabs and two scopes come from the real `options_rows`. All are drawn by every option. The table below is every (tab, scope).', '',
                 '| Tab | Scope | Rows |', '|---|---|---|']
        for s in self.snaps:
            if s['rows']: lines.append(f'| {F.TAB_NAMES[s["category"]]} | {s["scope"]} | ' + ', '.join(r['label'] for r in s['rows']) + ' |')
        lines += ['', f'## In the 116-row inventory but not a menu row today ({len(missing)})', '', 'Keys, gestures, start-only or recovery-only rows of `tests/xr_options_acceptance/inventory.json`. The merged menu has no row for these, so no option draws them: adding one would be inventing a control.', '',
                  '| Identifier | Label | Planned section | Lifecycle |', '|---|---|---|---|']
        for r in missing: lines.append(f'| {r["identifier"]} | {r["exact_label"] or ""} | {r["proposed_menu_section"]} | {r["lifecycle"]} |')
        (self.out / 'INVENTORY.md').write_text('\n'.join(lines) + '\n')
        shutil.copy(self.path('real-options-placement.png'), self.out / 'menu' / 'today-options-menu.png')
        shutil.copy(self.path('real-placement.png'), self.out / 'menu' / 'today-placement-menu.png')
        shutil.copy(self.path('look-today.png'), self.out / 'maximize' / 'today-maximize.png')
        shutil.copy(self.path('look-today-restore.png'), self.out / 'maximize' / 'today-restore.png')

    def gallery(self):
        def card(src, title, body, flag=''):
            return f'<figure tabindex="0" data-src="{src}"><img loading="lazy" src="{src}" alt="{html.escape(title)}"><figcaption><b>{html.escape(title)}</b>{f"<em>{html.escape(flag)}</em>" if flag else ""}<span>{html.escape(body)}</span></figcaption></figure>'
        menu = ''.join(card(f'menu/{d.number:02d}-{d.slug}.png', f'{d.number:02d} {d.name}', d.blurb) + card(f'menu/{d.number:02d}-{d.slug}-all-tabs.png', f'{d.number:02d} {d.name}: all tabs', 'Every tab, scope, page, child screen and state.') for d in M.DESIGNS)
        looks = ''.join(card(f'maximize/M{l.number}-{l.slug}.png', f'M{l.number} {l.name}', l.blurb) for l in X.LOOKS)
        places = ''.join(card(f'maximize/R{p.number}-{p.slug}.png', f'R{p.number} {p.name}', p.reach, 'Needs new pointer-handoff code' if p.new_handoff else '') for p in X.PLACES)
        today = ''.join(card(s, t, b) for s, t, b in (('menu/today-placement-menu.png', 'The real placement menu', 'The family to match.'), ('menu/today-options-menu.png', 'Today\'s options menu', 'Pixel font, fixed blue.'),
                                                      ('maximize/today-maximize.png', 'Today\'s Maximize', 'Real compositor.'), ('maximize/today-restore.png', 'Today\'s Restore', 'On the desktop\'s top bar.')))
        page_html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>XR Options Restyle</title>
<style>:root{{--bg:#0b0b0d;--panel:#17171a;--ink:#e4e4e8;--dim:#9a9aa2;--line:#303036;--warn:#eb9678}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 "JetBrainsMono Nerd Font",ui-monospace,monospace;padding:24px 16px;max-width:1500px;margin:auto}}
h1{{font-size:26px;margin:0 0 6px}}h2{{font-size:20px;margin:36px 0 12px;border-top:1px solid var(--line);padding-top:18px}}p{{color:var(--dim);margin:0 0 12px;max-width:80ch}}nav a{{color:var(--ink);margin-right:18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}}figure{{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:zoom-in}}figure:focus{{outline:2px solid var(--ink)}}
img{{display:block;width:100%;height:210px;object-fit:cover;object-position:top;background:#000}}figcaption{{padding:10px 12px}}figcaption b{{display:block}}figcaption em{{display:block;color:var(--warn);font-style:normal;font-size:14px}}figcaption span{{color:var(--dim);font-size:14px}}
#big{{position:fixed;inset:0;background:rgba(0,0,0,.94);display:none;overflow:auto;padding:16px;cursor:zoom-out}}#big.on{{display:block}}#big img{{height:auto;width:auto;max-width:none;margin:auto}}#big.fit img{{max-width:100%;max-height:calc(100vh - 32px);object-fit:contain}}</style></head><body>
<h1>XR options restyle: mockups</h1><p>Mock designs on real compositor frames. Nothing here is built or installed. Click any picture to open it; click again for full size, Esc to close, arrows to move.</p>
<nav><a href="menu/contact.png">Menu contact sheet</a><a href="maximize/contact.png">Maximize contact sheet</a><a href="INVENTORY.md">Inventory</a><a href="checks.json">Checks</a></nav>
<h2>Today</h2><div class="grid">{today}</div><h2>Options menu</h2><div class="grid">{menu}</div><h2>Maximize button</h2><div class="grid">{looks}</div><h2>Restore off the picture</h2><div class="grid">{places}</div>
<div id="big" class="fit"><img alt=""></div>
<script>const big=document.getElementById('big'),im=big.firstElementChild,all=[...document.querySelectorAll('figure')];let at=-1;
function show(i){{at=(i+all.length)%all.length;im.src=all[at].dataset.src;im.alt=all[at].querySelector('b').textContent;big.className='on fit';big.scrollTo(0,0)}}
all.forEach((f,i)=>{{f.onclick=()=>show(i);f.onkeydown=e=>{{if(e.key==='Enter')show(i)}}}});
big.onclick=e=>{{if(e.target===im&&big.classList.contains('fit'))big.classList.remove('fit');else big.className='fit'}};
addEventListener('keydown',e=>{{if(!big.classList.contains('on'))return;if(e.key==='Escape')big.className='fit';if(e.key==='ArrowRight')show(at+1);if(e.key==='ArrowLeft')show(at-1)}});</script></body></html>'''
        (self.out / 'index.html').write_text(page_html)

    def go(self, contact_name='contact.png', primary=True):
        self.contact_name = contact_name
        self.measure_real()
        self.menu()
        self.looks()
        self.places()
        self.maximize_contact()
        if primary:
            self.inventory_files()
            self.gallery()
            (self.out / 'checks.json').write_text(json.dumps(self.results, indent=1, default=float))
        return self.results

    def measure_real(self):
        """Where the real panels land on the canvas: glass_mesh of their rects, through the tool's report."""
        base = dict(selected=1, look=True, setup=REAL, spacing=h.REAL_SPACING)
        probe = self.stage.frame(self.path('probe.png'), pills=True, real_handles=True, **base)
        geo = h.layout([probe])[0]
        t, pill = geo['tiles'][1], geo['pills'][1]
        boxes = {}
        for name, (w, hgt, key) in dict(placement=(480, 394, 'real_placement'), options=(600, 440, 'real_options')).items():
            x = t[0] + (t[2] - w) / 2
            empty = dict(w=w, h=hgt, ops=[], x=x, y=pill[1], space='glass', name=name)
            data = dict(desktop=1, field=0) if name == 'placement' else dict(self.snap('placement', 'monitor'), desktop=1)
            report = self.frames([dict(probe, overlays=[empty], **{key: data})])[0]
            boxes[name] = report['overlays'][0]['box']
        self.placement_box, self.options_box = boxes['placement'], boxes['options']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(h.OUT))
    ap.add_argument('--work', default=None)
    a = ap.parse_args()
    (Path(a.out) / 'menu').mkdir(parents=True, exist_ok=True)
    (Path(a.out) / 'maximize').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = a.work or tmp
        Run(h.VANTABLACK, a.out, Path(work) / 'grey').go()
        Run(h.TOKYO_NIGHT, a.out, Path(work) / 'blue').go('contact-tokyo-night.png', primary=False)


if __name__ == '__main__':
    main()
