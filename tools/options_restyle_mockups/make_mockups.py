"""python3 -m tools.options_restyle_mockups.make_mockups: native before / after of the Options pill, menu and Maximize, and the Restore mockups.

`--before BINARY` is restyle_preview built from the tree before the change (see README.md): the "before" column comes from that binary,
the "after" column from build/restyle, so both are the real compositor and the real render_options_panel of their commit. Only the
Restore pages are mockups: Cody picks where Restore goes, so nothing of them is built."""
import argparse
import html
import json
import math
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import checks as C, family as F, harness as h, maximize_options as X
from .harness import REAL, Stage, with_placement

NEUTRAL = [dict(d, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0)) for d in REAL]
SCENES = dict(real=dict(setup=REAL, spacing=h.REAL_SPACING, look=True), rolled=dict(setup=with_placement(NEUTRAL, 1, roll=800), spacing=40),
              curved=dict(setup=with_placement(NEUTRAL, 1, curvature=60), spacing=40))
TITLES = dict(real='Overview, Cody\'s saved layout (XR-2, 1920x1200 at 125%)', rolled='Overview, monitor rolled 8 degrees', curved='Overview, monitor curved 60 degrees')
BEAST = [dict(w=1920, h=1080, scale=1.0, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0))]
CORNER = 'bottom-right'
LOOK = X.LOOKS[0]
BG, INK, DIM, LINE, WARN = (10, 10, 12), (224, 224, 228), (150, 150, 156), (54, 54, 60), (235, 150, 120)
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


def crop(im, box, scale=2):
    """Canvas `box` (x0, y0, x1, y1), kept inside the frame."""
    return im.crop((int(max(box[0], 0) * scale), int(max(box[1], 0) * scale), int(min(box[2], im.width / scale) * scale), int(min(box[3], im.height / scale) * scale)))


def pad(box, n):
    return box[0] - n, box[1] - n, box[2] + n, box[3] + n


def fitted(im, w, hgt):
    k = min(w / im.width, hgt / im.height)
    return im.resize((max(int(im.width * k), 1), max(int(im.height * k), 1)), Image.LANCZOS)


def cell(sheet, im, x, y, w, hgt):
    small = fitted(im, w, hgt)
    sheet.paste(small, (x, y))
    ImageDraw.Draw(sheet).rectangle((x - 1, y - 1, x + small.width, y + small.height), outline=LINE)
    return small


def union(*boxes):
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


class Run:
    def __init__(self, accent, out, work, before):
        self.accent, self.out, self.work, self.before = accent, Path(out), Path(work), before
        self.stage = Stage(accent, self.work / 'desktops')
        self.fonts = F.Fonts()
        self.snaps = h.snapshots()
        self.children = h.child_snapshots()
        self.results = dict(accent=accent)

    def snap(self, category, scope, **extra):
        return dict(next(s for s in self.snaps if s['category'] == category and s['scope'] == scope), **extra)

    def list_of(self, row_id):
        """The snapshot with `row_id`'s choice list open, on its current value."""
        s = next(x for x in self.snaps if any(r['id'] == row_id for r in x['rows']))
        i, r = next((i, r) for i, r in enumerate(s['rows']) if r['id'] == row_id)
        scroll = min(max(0, (i + 1) * 34 - 238), i * 34)
        return dict(s, focus=i, list=r['choices'].index(r['value']) if r['value'] in r['choices'] else 0, scroll=scroll)

    def frames(self, frames, scale=2, binary=None):
        reports, renderer = h.render(frames, self.accent, scale, binary=binary)
        self.results['renderer'] = json.loads(json.loads(renderer)['report'])['renderer'] if renderer else ''
        return reports

    def path(self, name):
        return self.work / 'frames' / name

    def native_set(self, tag, W, H):
        """Every scene closed, hovered and open, with the placement menu open for comparison, on one binary."""
        frames, names = [], {}
        for key, scene in SCENES.items():
            base = dict(selected=1, pills=True, real_handles=True, **scene)
            probe = self.stage.frame(self.path('x.png'), **base)
            geo = h.layout([probe])[0]
            t, pill = geo['tiles'][1], geo['pills'][1]
            rect = [t[0] + (t[2] - W) / 2, pill[1], W, H]
            place = [t[0] + (t[2] - 480) / 2, pill[1], 480, 394]
            sample = self.snap('placement', 'monitor', focus=6, scroll=0)
            frames.append(self.stage.frame(self.path(f'{tag}-{key}-closed.png'), overlays=[X.bounds(pill, 'glass', 1, 'pill'), X.bounds(geo['bars'][1], 'tile', 1, 'bar')], **base))
            frames.append(self.stage.frame(self.path(f'{tag}-{key}-placement.png'), real_placement=dict(desktop=1, field=0, res_w=1920, res_h=1200), overlays=[X.bounds(place, 'glass', 1, 'panel')], **base))
            frames.append(self.stage.frame(self.path(f'{tag}-{key}-options.png'), real_options=dict(sample, desktop=1), overlays=[X.bounds(rect, 'glass', 1, 'panel')], **base))
            frames.append(self.stage.frame(self.path(f'{tag}-{key}-options-3d.png'), real_options=dict(self.snap('3d', 'workspace', focus=-3), desktop=1), overlays=[X.bounds(rect, 'glass', 1, 'panel')], **base))
        for i, name in ((1, 'xr2'), (0, 'xr1')):
            frames.append(self.stage.frame(self.path(f'{tag}-maximized-{name}.png'), selected=i, zoom=1.0, pills=False, real_restore=True))
        reports = self.frames(frames, binary=self.before if tag == 'before' else None)
        return {Path(f['out']).name: r for f, r in zip(frames, reports)}

    def hover_set(self):
        """After only: the pills and Maximize under the real dot, through the renderer's own hover."""
        frames = []
        base = dict(selected=1, pills=True, real_handles=True, **SCENES['rolled'])
        row = dict(base, **dict(setup=NEUTRAL))
        geo = h.layout([self.stage.frame(self.path('x.png'), **row)])[0]
        gear = geo['gear'][1]
        maximize = geo['maximize'][1]
        pill = geo['pills'][1]
        spots = {'pill-hover': dict(hover_pill=1, real_dot=[pill[0] + pill[2] / 2, pill[1] + 7]), 'gear-hover': dict(real_dot=[gear[0] + gear[2] * .8, gear[1] + 7]),
                 'maximize-hover': dict(real_dot=[maximize[0] + maximize[2] / 2, maximize[1] + 13]), 'idle': {}}
        for name, extra in spots.items():
            frames.append(self.stage.frame(self.path(f'after-{name}.png'), overlays=[X.bounds(pill, 'glass', 1, 'pill')], **row, **extra))
        self.frames(frames)
        return dict(pill=pill, gear=gear, maximize=maximize, bar=geo['bars'][1])

    def side(self, sheet, draw, x, y, w, hgt, title, shot, box, scale=1.0):
        """`box` of `shot` at `scale` page px per canvas px."""
        draw.text((x, y), title, font=font(18), fill=INK)
        part = crop(shot, box)
        part = part.resize((max(int(part.width / 2 * scale), 1), max(int(part.height / 2 * scale), 1)), Image.LANCZOS)
        part = fitted(part, w, hgt) if part.width > w or part.height > hgt else part
        sheet.paste(part, (x, y + 30))
        return part

    def before_after_menu(self, b, a):
        """Before and after of the open menu beside the placement menu, every scene, 1 canvas px = 1 page px."""
        sheet, draw = page(2020, 1760)
        draw.text((30, 20), 'Options menu: before and after, next to the placement menu', font=font(30, True), fill=INK)
        draw.text((30, 62), 'Every panel is the real renderer on the real compositor, nothing mocked. Same scale in every column.', font=font(18), fill=DIM)
        y = 106
        for key in SCENES:
            draw.text((30, y), TITLES[key], font=font(21, True), fill=INK)
            yy = y + 34
            for n, (title, name, report) in enumerate((('Placement menu', 'after-%s-placement.png' % key, a), ('Before', 'before-%s-options.png' % key, b), ('After', 'after-%s-options.png' % key, a))):
                box = report[name]['overlays'][0]['box']
                self.side(sheet, draw, 30 + n * 660, yy, 640, 440, title, Image.open(self.path(name)), pad(box, 12))
            y = yy + 500
        sheet = sheet.crop((0, 0, sheet.width, y))
        sheet.save(self.out / 'menu' / self.name('before-after-menu.png'))

    def before_after_pill(self, b, a, spots):
        sheet, draw = page(2020, 1400)
        draw.text((30, 20), 'Options pill and Maximize: before and after', font=font(30, True), fill=INK)
        draw.text((30, 62), 'Real compositor, 2.5x. Under each monitor: the drag pill, then the Options pill. Over each: Maximize beside the level bar.', font=font(18), fill=DIM)
        y = 106
        for key in ('rolled', 'curved'):
            draw.text((30, y), TITLES[key], font=font(21, True), fill=INK)
            for n, tag in enumerate(('before', 'after')):
                r = (b if tag == 'before' else a)[f'{tag}-{key}-closed.png']['overlays']
                pill, bar = r[0]['box'], r[1]['box']
                self.side(sheet, draw, 30 + n * 990, y + 34, 970, 150, f'{tag.capitalize()}: pills', Image.open(self.path(f'{tag}-{key}-closed.png')), (pill[0] - 60, pill[1] - 24, pill[2] + 240, pill[3] + 24), 2.5)
                self.side(sheet, draw, 30 + n * 990, y + 214, 970, 170, f'{tag.capitalize()}: Maximize, level bar, 3D', Image.open(self.path(f'{tag}-{key}-closed.png')), (bar[0] - 90, bar[1] - 20, bar[2] + 120, bar[3] + 24), 2.5)
            y += 440
        draw.text((30, y), 'States, after: the drag pill and the Options pill share one look', font=font(21, True), fill=INK)
        y += 36
        p = spots['pill']
        for n, (name, title) in enumerate((('idle', 'Idle'), ('pill-hover', 'Drag pill hovered'), ('gear-hover', 'Options pill hovered'), ('maximize-hover', 'Maximize hovered, its hint'))):
            box = (p[0] - 30, p[1] - 20, p[0] + 2 * p[2] + 42, p[1] + 34) if name != 'maximize-hover' else (spots['maximize'][0] - 40, spots['maximize'][1] - 40, spots['bar'][0] + 170, spots['bar'][1] + 40)
            self.side(sheet, draw, 30 + n * 495, y, 480, 200, title, Image.open(self.path(f'after-{name}.png')), box, 1.2)
        sheet = sheet.crop((0, 0, sheet.width, y + 180))
        sheet.save(self.out / 'menu' / self.name('before-after-pill.png'))

    def maximized(self):
        sheet, draw = page(2020, 800)
        draw.text((30, 20), 'Maximized: what the renderer draws', font=font(30, True), fill=INK)
        caption(draw, 30, 62, 'The pills and the menu are overview-only: the renderer has never drawn them over a maximized desktop, and that is unchanged, so "after" has no menu here either. Restore has its new capsule at today\'s spot until Cody picks a place (maximize/R1 to R4).', 1960, 18)
        for n, (tag, title) in enumerate((('before', 'Before'), ('after', 'After'))):
            self.side(sheet, draw, 30 + n * 990, 130, 970, 610, f'{title}, XR-2 1920x1200', Image.open(self.path(f'{tag}-maximized-xr2.png')), (0, 0, 1920, 1200), .5)
        sheet.save(self.out / 'menu' / self.name('before-after-maximized.png'))

    def all_tabs(self):
        """The built menu on every tab, both scopes, every scroll stop, child and state, from render_options_panel through the compositor."""
        cases = []
        for s in self.snaps:
            limit = max(0, len(s['rows']) * 34 - 238)
            for scroll in range(0, limit + 238, 238):
                scroll = min(scroll, limit)
                cases.append((f'{F.TAB_NAMES[s["category"]]} / {s["scope"]}' + (f' scrolled {scroll}' if scroll else ''), dict(s, scroll=scroll)))
                if scroll == limit: break
        for name, s in self.children.items():
            cases.append((name.replace('_', ' '), dict(s, focus=0, scroll=0)))
        for title, s in (('Section list open', self.snap('video', 'workspace', focus=-2)), ('Resolution list open', self.list_of('resolution')), ('Scale list open', self.list_of('scale')), ('Filter list open', self.list_of('quality_filter')), ('Close focused', self.snap('view', 'monitor', focus=-1)), ('Scope focused', self.snap('view', 'workspace', focus=-4)),
                         ('Section focused', self.snap('layout', 'workspace', focus=-3)), ('Pending', self.snap('stabilization', 'workspace', status='pending', message='Waiting for owner readback', focus=0)),
                         ('Error', self.snap('output', 'monitor', status='error', message='Owner rejected change', focus=2))):
            cases.append((title, s))
        base = dict(selected=1, pills=False, setup=NEUTRAL, spacing=40, crop=[640, 790, 640, 410])
        geo = h.layout([self.stage.frame(self.path('x.png'), **base)])[0]
        t, pill = geo['tiles'][1], geo['pills'][1]
        rect = [t[0] + (t[2] - 560) / 2, pill[1], 560, 394]
        frames = [self.stage.frame(self.path(f'tab-{i}.png'), real_options=dict(s, desktop=1), overlays=[X.bounds(rect, 'glass', 1, 'panel')], **base) for i, (_, s) in enumerate(cases)]
        reports = self.frames(frames)
        cols, w, hgt = 4, 600, 450
        rows = (len(cases) + cols - 1) // cols
        sheet, draw = page(cols * w + 40, rows * hgt + 110)
        draw.text((30, 20), f'The built options menu: every tab, scope, scroll stop, child and state ({len(cases)} frames)', font=font(28, True), fill=INK)
        brightest = 0
        for i, (title, _) in enumerate(cases):
            x, y = 30 + i % cols * w, 80 + i // cols * hgt
            draw.text((x, y), title, font=font(16), fill=DIM)
            box = reports[i]['overlays'][0]['box']
            part = crop(Image.open(self.path(f'tab-{i}.png')), (box[0] - 640, box[1] - 790, box[2] - 640, box[3] - 790))
            brightest = max(brightest, int(np.asarray(part)[..., :3].max()))
            sheet.paste(part.resize((part.width // 2, part.height // 2), Image.LANCZOS), (x, y + 26))
        sheet.save(self.out / 'menu' / 'all-tabs.png')
        self.results['all_tabs'] = dict(frames=len(cases), brightest=brightest, in_view=all(r['overlays'][0]['box'][3] <= 1200 for r in reports))

    def contact(self, a):
        cols, w = 3, 660
        sheet, draw = page(cols * w + 40, 1000)
        draw.text((30, 18), 'Options pill, menu and Maximize, built', font=font(30, True), fill=INK)
        draw.text((30, 58), 'Real compositor, same scale. For Cody\'s approval: nothing is installed.', font=font(18), fill=DIM)
        for n, (title, name) in enumerate((('Placement menu', 'after-real-placement.png'), ('Options menu: Placement section', 'after-real-options.png'), ('Options menu: 3D, sliders and dropdown', 'after-real-options-3d.png'))):
            box = a[name]['overlays'][0]['box']
            self.side(sheet, draw, 30 + n * w, 96, w - 20, 440, title, Image.open(self.path(name)), pad(box, 12))
        shot = Image.open(self.path('after-rolled-closed.png'))
        r = a['after-rolled-closed.png']['overlays']
        self.side(sheet, draw, 30, 560, w - 20, 340, 'The pills under a monitor, rolled', shot, (r[0]['box'][0] - 60, r[0]['box'][1] - 30, r[0]['box'][2] + 240, r[0]['box'][3] + 30), 1.7)
        self.side(sheet, draw, 30 + w, 560, w - 20, 340, 'Maximize beside the bar, rolled', shot, (r[1]['box'][0] - 90, r[1]['box'][1] - 30, r[1]['box'][2] + 110, r[1]['box'][3] + 30), 1.7)
        c = Image.open(self.path('after-curved-options.png'))
        self.side(sheet, draw, 30 + 2 * w, 560, w - 20, 400, 'Menu on a curved monitor', c, pad(a['after-curved-options.png']['overlays'][0]['box'], 12), .9)
        sheet.save(self.out / 'menu' / self.name('contact.png'))

    def name(self, base):
        return base.replace('.png', self.suffix + '.png')

    # --- Restore mockups ----------------------------------------------------------------------------------------------
    def place_frames(self):
        s = self.stage
        frames = []
        def max_frame(name, i=1, **extra): return s.frame(self.path(name), selected=i, zoom=1.0, pills=False, **extra)
        for i, tag in ((1, 'xr2'), (0, 'xr1')):
            f = max_frame(f'place-plain-{tag}.png', i)
            t = h.layout([f])[0]['tiles'][i]
            frames.append(f)
            pill = C.corner_rect(t, CORNER, X.PILL_W, X.PILL_H)
            chip = C.corner_rect(t, CORNER, 26, 26)
            cx, cy = t[0] + t[2] - 3, t[1] + t[3] - 3
            frames.append(dict(max_frame(f'place-hot-corner-dwell-{tag}.png', i), overlays=[X.arrow_overlay(cx - 24, cy - 30)]))
            frames.append(dict(max_frame(f'place-hot-corner-shown-{tag}.png', i), overlays=[X.placed(X.restore_pill(self.fonts, LOOK), pill[0], pill[1], name='pill')], dot=[pill[0] + 112, pill[1] + 25]))
            # Alpha 0 is really drawn, so "nothing at rest" is measured on a frame that contains it.
            frames.append(dict(max_frame(f'place-fade-chip-rest-{tag}.png', i), overlays=[X.placed(X.round_key(self.fonts, LOOK, False), chip[0], chip[1], alpha=0.0, name='chip')]))
            frames.append(dict(max_frame(f'place-fade-chip-near-{tag}.png', i), overlays=[X.placed(X.round_key(self.fonts, LOOK, False), chip[0], chip[1], alpha=.55, name='chip'), X.arrow_overlay(cx - 100, cy - 80)]))
            frames.append(dict(max_frame(f'place-fade-chip-on-{tag}.png', i), overlays=[X.placed(X.round_key(self.fonts, LOOK, True), chip[0], chip[1], name='chip')], dot=[chip[0] + 24, chip[1] + 25]))
            step = s.frame(self.path(f'place-step-back-band-open-{tag}.png'), selected=i, zoom=X.STEP_ZOOM, pills=False)
            st = h.layout([step])[0]['tiles'][i]
            probe = dict(step, overlays=[X.bounds(st, 'tile', i, 'picture')])
            top = self.frames([probe], scale=1)[0]['overlays'][0]['box'][1]
            self.results.setdefault('band_hold_px', {})[tag] = round(max(X.BAND_PX - top, 0), 1)
            if top < X.BAND_PX:   # a desktop placed high rides up as it steps back: the built view would hold it down (R3's cost)
                half_h = h.layout([step])[0]['canvas'][3]
                step = dict(step, view_pitch=-math.atan((X.BAND_PX - top) / 600 * half_h))
                top = self.frames([dict(step, overlays=probe['overlays'])], scale=1)[0]['overlays'][0]['box'][1]
            bx, by = st[0] + (st[2] - X.PILL_W) / 2, st[1] - X.PILL_H - (top - X.PILL_H) / 2
            frames.append(dict(step, overlays=[X.placed(X.restore_pill(self.fonts, LOOK), bx, by, name='pill'), X.bounds([bx, by, X.PILL_W, X.PILL_H], 'tile', i, 'pill-box'), X.bounds(st, 'tile', i, 'picture')],
                               dot=[960 + X.PILL_W / 2 - 10, top / 2 + 12]))
        return frames

    def places(self):
        frames = self.place_frames()
        by = {Path(f['out']).name: r for f, r in zip(frames, self.frames(frames))}
        beast = self.stage.frame(self.path('place-step-back-band-169.png'), setup=BEAST, spacing=20, selected=0, zoom=1.0, pills=False)
        t = h.layout([beast])[0]['tiles'][0]
        bx, byy = t[0] + (t[2] - X.PILL_W) / 2, t[1] - X.PILL_H - 17
        beast = dict(beast, overlays=[X.placed(X.restore_pill(self.fonts, LOOK), bx, byy, name='pill'), X.bounds([bx, byy, X.PILL_W, X.PILL_H], 'tile', 0, 'pill-box'), X.bounds(t, 'tile', 0, 'picture')], dot=[bx + 112, byy + 25])
        report_169 = self.frames([beast])[0]
        f = self.stage.frame(self.path('place-keys-and-hint-tooltip.png'), selected=1, pills=True, real_handles=True, setup=NEUTRAL, spacing=40)
        geo = h.layout([f])[0]
        m = geo['maximize'][1]
        tip = X.tooltip(self.fonts, 'Maximize. Ctrl+Alt+Down comes back')
        tip_report = self.frames([dict(f, overlays=[X.placed(tip, m[0] + m[2] / 2 - tip.w / 2, m[1] - 34, name='tooltip', desktop=1)], real_dot=[m[0] + m[2] - 3, m[1] + m[3] + 1])])[0]
        self.pro2_frames()
        self.results['places'] = self.place_results(by, report_169)
        for p in X.PLACES: self.place_page(p, tip_report)
        self.maximize_contact(tip_report)

    def pro2_frames(self):
        """The Pro 2 session's ultrawide, at the places a band or a corner gives it."""
        f = self.stage.frame(self.path('place-pro2-plain.png'), setup=h.PRO2, spacing=20, selected=0, zoom=1.0, pills=False)
        t = h.layout([f])[0]['tiles'][0]
        pill = C.corner_rect(t, CORNER, X.PILL_W, X.PILL_H)
        chip = C.corner_rect(t, CORNER, 26, 26)
        bx, by = t[0] + (t[2] - X.PILL_W) / 2, t[1] - X.PILL_H - 12
        self.frames([f, dict(f, out=str(self.path('place-pro2-hot-corner.png')), overlays=[X.placed(X.restore_pill(self.fonts, LOOK), pill[0], pill[1], name='pill')], dot=[pill[0] + 112, pill[1] + 25]),
                     dict(f, out=str(self.path('place-pro2-fade-chip.png')), overlays=[X.placed(X.round_key(self.fonts, LOOK, True), chip[0], chip[1], name='chip')], dot=[chip[0] + 24, chip[1] + 25]),
                     dict(f, out=str(self.path('place-pro2-band.png')), overlays=[X.placed(X.restore_pill(self.fonts, LOOK), bx, by, name='pill')], dot=[bx + 112, by + 25])])

    def place_results(self, by, report_169):
        out = dict(corner=CORNER, reach=C.restore_reach(CORNER), reach_pro2=C.restore_reach(CORNER, h.PRO2), corner_neighbours=C.corner_table(), letterbox=C.letterbox(h.OTHER_SIZES + [('XR-1', 2560, 1600), ('XR-2', 1920, 1200), ('XR-3', 1920, 1200), ('Pro 2 XR-1', 5120, 1440), ('Pro 2 XR-2', 2560, 1440)]))
        out['pixels_changed_at_rest'] = {tag: int((np.asarray(Image.open(self.path(f'place-plain-{tag}.png'))) != np.asarray(Image.open(self.path(f'place-fade-chip-rest-{tag}.png')))).any(axis=2).sum()) for tag in ('xr2', 'xr1')}
        def band(report):
            pill, picture = report['overlays'][1]['box'], report['overlays'][2]['box']
            return dict(pill=pill, picture_top=picture[1], clear_of_picture=bool(pill[3] <= picture[1]), in_view=bool(pill[1] >= 0))
        out['band'] = {tag: band(by[f'place-step-back-band-open-{tag}.png']) for tag in ('xr2', 'xr1')}
        out['band']['16:9'] = band(report_169)
        out['panel_problems'] = [p for panel in (X.restore_pill(self.fonts, LOOK), X.round_key(self.fonts, LOOK, False), X.tooltip(self.fonts, 'Maximize. Ctrl+Alt+Down comes back')) for p in C.panel_problems(panel)]
        return out

    def place_shots(self, p, tip_report):
        corner = (1920 - 420, 1200 - 260, 1920, 1200)
        b = tip_report['overlays'][-1]['box']
        tip = (b[0] - 60, b[1] - 60, b[2] + 60, b[3] + 200)
        return {'hot-corner': [('XR-2 maximized, at rest: nothing', 'place-plain-xr2.png', None), ('Resting in the corner, 350 ms', 'place-hot-corner-dwell-xr2.png', corner),
                               ('Pill under the pointer, XR-2 1920x1200', 'place-hot-corner-shown-xr2.png', corner), ('Pro 2 ultrawide, 5120x1440 at 125%', 'place-pro2-hot-corner.png', None)],
                'fade-chip': [('XR-2 maximized, at rest: nothing', 'place-fade-chip-rest-xr2.png', None), ('Pointer 100 px away: fading in', 'place-fade-chip-near-xr2.png', corner),
                              ('On it, XR-2 1920x1200', 'place-fade-chip-on-xr2.png', corner), ('Pro 2 ultrawide, 5120x1440 at 125%', 'place-pro2-fade-chip.png', None)],
                'step-back-band': [('XR-2 at rest: nothing, and no band to use', 'place-plain-xr2.png', None), ('Stepped back: band above, XR-2', 'place-step-back-band-open-xr2.png', None),
                                   ('Stepped back, XR-1', 'place-step-back-band-open-xr1.png', None), ('Pro 2 ultrawide: band already there, 330 px', 'place-pro2-band.png', None)],
                'keys-and-hint': [('XR-2 maximized: nothing, ever', 'place-plain-xr2.png', None), ('XR-1 maximized: nothing, ever', 'place-plain-xr1.png', None),
                                  ('Overview: Maximize\'s tooltip teaches the keys', 'place-keys-and-hint-tooltip.png', tip)]}[p.slug]

    def place_page(self, p, tip_report):
        im, draw = page(2400, 1500)
        tag = '  NEEDS NEW POINTER-HANDOFF CODE' if p.new_handoff else ''
        draw.text((40, 26), f'R{p.number}  {p.name}{tag}', font=font(40, True), fill=WARN if p.new_handoff else INK)
        draw.text((40, 76), 'MOCKUP. Cody picks where Restore goes; nothing here is built.', font=font(18), fill=WARN)
        y = caption(draw, 40, 108, p.blurb, 2300, 22, INK)
        for head, text in (('How the pointer gets there: ', p.reach), ('What it covers: ', p.covers), ('To build: ', p.build), ('Test: ', p.test)):
            y = caption(draw, 40, y + 6, head + text, 2300, 20)
        y += 14
        x = 40
        for title, name, box in self.place_shots(p, tip_report):
            shot = Image.open(self.path(name))
            if box: shot = crop(shot, box)
            draw.text((x, y), title, font=font(16), fill=INK)
            cell(im, shot, x, y + 28, 570, 356)
            x += 586
        im.crop((0, 0, 2400, y + 410)).save(self.out / 'maximize' / self.name(f'R{p.number}-{p.slug}.png'))

    def maximize_contact(self, tip_report):
        cw = 560
        sheet, draw = page(30 + 4 * (cw + 16) + 14, 1000)
        draw.text((30, 18), 'Restore: four places that do not sit on the desktop', font=font(30, True), fill=INK)
        draw.text((30, 58), 'MOCKUPS for Cody to pick from. Maximized at his real sizes, 1920x1200 at 125%, the picture filling the view.', font=font(18), fill=DIM)
        corner = (1920 - 420, 1200 - 260, 1920, 1200)
        b = tip_report['overlays'][-1]['box']
        tip = (b[0] - 60, b[1] - 60, b[2] + 60, b[3] + 200)
        shots = dict(zip((p.slug for p in X.PLACES), ([('place-plain-xr2.png', None), ('place-hot-corner-shown-xr2.png', corner)], [('place-fade-chip-rest-xr2.png', None), ('place-fade-chip-on-xr2.png', corner)],
                                                      [('place-plain-xr2.png', None), ('place-step-back-band-open-xr2.png', None)], [('place-plain-xr2.png', None), ('place-keys-and-hint-tooltip.png', tip)])))
        y = 100
        for n, p in enumerate(X.PLACES):
            x = 30 + n * (cw + 16)
            draw.text((x, y), f'R{p.number} {p.name}', font=font(21, True), fill=INK)
            label, fill = ('NEEDS NEW POINTER-HANDOFF CODE', WARN) if p.new_handoff else ('keyboard only', DIM) if p.slug == 'keys-and-hint' else ('existing Restore route', DIM)
            draw.text((x, y + 30), label, font=font(15, p.new_handoff), fill=fill)
            yy = y + 58
            for name, box in shots[p.slug]:
                shot = Image.open(self.path(name))
                if box: shot = crop(shot, box)
                cell(sheet, shot, x, yy, cw, 350)
                yy += 362
        sheet.crop((0, 0, sheet.width, y + 58 + 2 * 362 + 16)).save(self.out / 'maximize' / self.name('contact.png'))

    def gallery(self):
        def card(src, title, body, flag=''):
            return f'<figure tabindex="0" data-src="{src}"><img loading="lazy" src="{src}" alt="{html.escape(title)}"><figcaption><b>{html.escape(title)}</b>{f"<em>{html.escape(flag)}</em>" if flag else ""}<span>{html.escape(body)}</span></figcaption></figure>'
        built = ''.join(card(*c) for c in (('menu/contact.png', 'Contact sheet', 'The built pill, menu and Maximize, with the placement menu.'), ('menu/before-after-menu.png', 'Menu before and after', 'Beside the placement menu: Cody\'s layout, rolled, curved.'),
                                           ('menu/before-after-pill.png', 'Pill and Maximize before and after', 'Idle and hover, rolled and curved.'), ('menu/before-after-maximized.png', 'Maximized', 'The menu is overview-only. Restore keeps today\'s spot for now.'),
                                           ('menu/all-tabs.png', 'Every tab', 'Every tab, scope, scroll stop, child and state, from the renderer.')))
        places = ''.join(card(f'maximize/R{p.number}-{p.slug}.png', f'R{p.number} {p.name}', p.reach, 'Needs new pointer-handoff code' if p.new_handoff else '') for p in X.PLACES)
        (self.out / 'index.html').write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>XR Options Restyle</title>
<style>:root{{--bg:#0b0b0d;--panel:#17171a;--ink:#e4e4e8;--dim:#9a9aa2;--line:#303036;--warn:#eb9678}}
*{{box-sizing:border-box}}body{{margin:auto;background:var(--bg);color:var(--ink);font:16px/1.45 "JetBrainsMono Nerd Font",ui-monospace,monospace;padding:24px 16px;max-width:1500px}}
h1{{font-size:26px;margin:0 0 6px}}h2{{font-size:20px;margin:36px 0 12px;border-top:1px solid var(--line);padding-top:18px}}p{{color:var(--dim);margin:0 0 12px;max-width:80ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}}figure{{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:zoom-in}}figure:focus{{outline:2px solid var(--ink)}}
img{{display:block;width:100%;height:210px;object-fit:cover;object-position:top;background:#000}}figcaption{{padding:10px 12px}}figcaption b{{display:block}}figcaption em{{display:block;color:var(--warn);font-style:normal;font-size:14px}}figcaption span{{color:var(--dim);font-size:14px}}
#big{{position:fixed;inset:0;background:rgba(0,0,0,.94);display:none;overflow:auto;padding:16px;cursor:zoom-out}}#big.on{{display:block}}#big img{{height:auto;width:auto;max-width:none;margin:auto}}#big.fit img{{max-width:100%;max-height:calc(100vh - 32px);object-fit:contain}}</style></head><body>
<h1>XR options restyle</h1><p>Top: built, real renderer, for Cody's approval before anything is installed. Bottom: mockups of where Restore could go. Click to open, click again for full size, Esc to close, arrows to move.</p>
<h2>Built: Options pill, menu and Maximize</h2><div class="grid">{built}</div><h2>Mockups: Restore off the picture</h2><div class="grid">{places}</div>
<div id="big" class="fit"><img alt=""></div>
<script>const big=document.getElementById('big'),im=big.firstElementChild,all=[...document.querySelectorAll('figure')];let at=-1;
function show(i){{at=(i+all.length)%all.length;im.src=all[at].dataset.src;im.alt=all[at].querySelector('b').textContent;big.className='on fit';big.scrollTo(0,0)}}
all.forEach((f,i)=>{{f.onclick=()=>show(i);f.onkeydown=e=>{{if(e.key==='Enter')show(i)}}}});
big.onclick=e=>{{if(e.target===im&&big.classList.contains('fit'))big.classList.remove('fit');else big.className='fit'}};
addEventListener('keydown',e=>{{if(!big.classList.contains('on'))return;if(e.key==='Escape')big.className='fit';if(e.key==='ArrowRight')show(at+1);if(e.key==='ArrowLeft')show(at-1)}});</script></body></html>''')

    def go(self, primary=True):
        self.suffix = '' if primary else '-tokyo-night'
        (self.out / 'menu').mkdir(parents=True, exist_ok=True)
        (self.out / 'maximize').mkdir(parents=True, exist_ok=True)
        b = self.native_set('before', 600, 440)
        a = self.native_set('after', 560, 394)
        spots = self.hover_set()
        self.before_after_menu(b, a)
        self.before_after_pill(b, a, spots)
        self.maximized()
        self.contact(a)
        self.results['menu_in_view'] = {k: bool(a[k]['overlays'][0]['box'][3] <= 1200 and a[k]['overlays'][0]['box'][1] >= 0) for k in a if 'options' in k}
        self.places()
        if primary:
            self.all_tabs()
            self.gallery()
            (self.out / 'checks.json').write_text(json.dumps(self.results, indent=1, default=float))
        return self.results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--before', required=True, help='restyle_preview built before the change')
    ap.add_argument('--out', default=str(h.OUT))
    ap.add_argument('--work', default=None)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        work = a.work or tmp
        Run(h.VANTABLACK, a.out, Path(work) / 'grey', a.before).go()
        Run(h.TOKYO_NIGHT, a.out, Path(work) / 'blue', a.before).go(primary=False)


if __name__ == '__main__':
    main()
