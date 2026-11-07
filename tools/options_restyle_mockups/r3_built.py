"""The built Step-Back Band, frame by frame, from the real compositor: rest, easing back, the band open, the dot on Restore, restored.

Each stage is a real frame: `restore_step` is what wayland.cpp's RestoreStep would have reached, the placement stepped as resolved_placements
steps it, and the pill and its hover are gl.cpp's own, lit by the dot on the same mesh the supervisor presses."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import harness as h
from .harness import PRO2, REAL

FONT = '/usr/share/fonts/TTF/JetBrainsMonoNerdFont-{}.ttf'
BG, INK, DIM = (10, 10, 12), (224, 224, 228), (150, 150, 156)
FLAT = [dict(d, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0)) for d in REAL]
CURVED = [dict(d, placement=dict(d['placement'], curvature=60)) for d in FLAT]
ROLLED = [dict(d, placement=dict(d['placement'], roll=800)) for d in FLAT]
SCENES = (('luma', 'Luma Ultra, XR-2, 1920x1200 at 125%', dict(setup=REAL, spacing=h.REAL_SPACING), 1),
          ('pro2-wide', 'Pro 2, XR-1, 5120x1440 at 125%: the band is already there', dict(setup=PRO2, spacing=20), 0),
          ('pro2-169', 'Pro 2, XR-2, 2560x1440 at 200%: the band is already there', dict(setup=PRO2, spacing=20), 1),
          ('rolled', 'Rolled 8 degrees: maximized sits level', dict(setup=ROLLED, spacing=40), 1),
          ('curved', 'Curved 60 degrees', dict(setup=CURVED, spacing=40), 1))
STAGES = (('1-rest', 'At rest', 0.0, False), ('2-easing', 'Easing back, half way (220 ms in all)', .5, False), ('3-band', 'Band open, the dot just above the picture', 1.0, 'top'),
          ('4-hover', 'Dot on Restore: the pill lights', 1.0, 'pill'), ('5-restored', 'Restored: the overview', None, False))
PAGES = [('sequence.png', 'The sequence on the Luma sizes', 'Rest, easing back, the band, hover on Restore, restored. Real compositor.'),
         ('sequence-pro2.png', 'The sequence on the Pro 2 ultrawide', 'The band is already there, so only the pill comes.'),
         ('all-scenes.png', 'Band open on every size and shape', 'Luma, both Pro 2 desktops, rolled and curved, with the dot on Restore.')]


def font(size, bold=False):
    return ImageFont.truetype(FONT.format('Bold' if bold else 'Regular'), size)


def page(name, title, shots, out, cols=3, width=800):
    rows = (len(shots) + cols - 1) // cols
    height = int(width * 1200 / 1920)
    sheet = Image.new('RGB', (cols * (width + 16) + 30, rows * (height + 40) + 70), BG)
    d = ImageDraw.Draw(sheet)
    d.text((24, 16), title, font=font(26, True), fill=INK)
    for i, (label, path) in enumerate(shots):
        x, y = 24 + i % cols * (width + 16), 62 + i // cols * (height + 40)
        d.text((x, y), label, font=font(16), fill=DIM)
        sheet.paste(Image.open(path).resize((width, height), Image.LANCZOS), (x, y + 26))
    sheet.save(out / name)


def go(stage, path, out, results, render, suffix=''):
    out.mkdir(parents=True, exist_ok=True)
    frames, names, checks = [], {}, {}
    for key, title, scene, selected in SCENES:
        probe = stage.frame(path('r3-probe.png'), selected=selected, zoom=1.0, pills=False, restore_step=1.0, **scene)
        geo = h.layout([probe])[0]
        tile, rect = geo['tiles'][selected], geo['restore_rect']
        report = render([dict(probe, overlays=[h.bounds(rect, 'tile', selected, 'pill'), h.bounds(tile, 'tile', selected, 'picture')])], scale=1)[0]['overlays']
        pill, picture = report[0]['box'], report[1]['box']
        middle_top = (pill[3] + picture[1]) / 2
        checks[key] = dict(step_scale=geo['step_scale'], pill=pill, picture=picture, clear=bool(pill[3] <= picture[1] + 1e-3 or tile[1] > 0), in_view=bool(pill[1] >= 0))
        for name, label, step, dot in STAGES:
            extra = dict(restore_step=step) if step is not None else dict(zoom=0.0, restore_step=0.0, pills=True, real_handles=True)
            if dot == 'top': extra['real_dot'] = [(pill[0] + pill[2]) / 2 + 240, middle_top]
            if dot == 'pill': extra['real_dot'] = [pill[2] - 14, pill[3] - 2]
            args = dict(selected=selected, zoom=1.0, pills=False, **scene)
            args.update(extra)
            frames.append(stage.frame(path(f'r3-{key}-{name}.png'), **args))
    render(frames, scale=2)
    results['r3_built'] = checks
    shots = lambda key: [(label, path(f'r3-{key}-{name}.png')) for name, label, _, _ in STAGES]
    page('sequence.png'.replace('.png', suffix + '.png'), 'Step-Back Band, Luma Ultra XR-2', shots('luma'), out)
    page('sequence-pro2.png'.replace('.png', suffix + '.png'), 'Step-Back Band, Pro 2 ultrawide', shots('pro2-wide'), out)
    page('all-scenes.png'.replace('.png', suffix + '.png'), 'Band open, dot on Restore, every size and shape', [(title, path(f'r3-{key}-4-hover.png')) for key, title, _, _ in SCENES], out)
    if not suffix:
        for key, *_ in SCENES:
            for name, *_ in STAGES:
                Image.open(path(f'r3-{key}-{name}.png')).resize((1920, 1200), Image.LANCZOS).save(out / f'{key}-{name}.png')
