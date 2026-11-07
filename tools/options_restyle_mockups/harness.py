"""The preview binary, the scenes and the real menu data every mockup is drawn from. Pure tooling: nothing here talks to a run.

Rendering runs on llvmpipe with /dev/dri hidden by bwrap, so no frame can open either GPU even while XR is live.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / 'build/restyle/restyle_preview'
OUT = Path.home() / '.hermes/profiles/coderbotxr/previews/xr-options-restyle'
VANTABLACK, TOKYO_NIGHT = '8d8d8d', '7aa2f7'   # Cody's theme accent (memory: viture-3d-button-mockups) and a theme with a hue

# Cody's saved Luma Ultra setup, ~/.config/viture-workspace/profiles.json on 2026-11-06: output px, scale, placement, roll.
REAL = [dict(w=2560, h=1600, scale=1.25, placement=dict(x=-67, y=44, distance=36, curvature=0, roll=-47)),
        dict(w=1920, h=1200, scale=1.25, placement=dict(x=160, y=16, distance=45, curvature=0, roll=-165)),
        dict(w=1920, h=1200, scale=1.25, placement=dict(x=-167, y=-152, distance=100, curvature=0, roll=-183))]
REAL_SPACING = 0
# Other saved sizes, for the band a maximized picture leaves: the beast and unknown profiles.
OTHER_SIZES = [('beast', 1920, 1080), ('unknown XR-1', 3840, 2160), ('unknown XR-2', 2560, 1440)]
# The Pro 2 session's desktops (fix/restore-current-geometry.md): an ultrawide and a 16:9, so these do leave a band.
PRO2 = [dict(w=5120, h=1440, scale=1.25, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0)),
        dict(w=2560, h=1440, scale=2.0, placement=dict(x=0, y=0, distance=100, curvature=0, roll=0))]


def bounds(rect, space, desktop, name):
    """An overlay that draws nothing: asks the renderer where `rect` lands on the canvas."""
    return dict(w=rect[2], h=rect[3], x=rect[0], y=rect[1], ops=[], space=space, desktop=desktop, name=name)


def run_env():
    return dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', EGL_PLATFORM='surfaceless')


def tool(*args, gl=False, binary=None):
    """Run the binary. `gl`: with /dev/dri hidden, at the lowest priority, so a frame costs a live run nothing."""
    command = [str(binary or BINARY), *map(str, args)]
    if gl:
        command = ['bwrap', '--dev-bind', '/', '/', '--tmpfs', '/dev/dri', 'nice', '-n', '19', *command]
    done = subprocess.run(command, capture_output=True, text=True, env=run_env())
    if done.returncode:
        raise SystemExit(f'restyle_preview {args[0]} failed: {done.stderr[-2000:]}')
    return done.stdout


def info(accent):
    return json.loads(tool('info', accent))


def job_call(mode, job, gl=False, binary=None):
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(job, f)
    try:
        return tool(mode, f.name, gl=gl, binary=binary)
    finally:
        os.unlink(f.name)


def layout(frames):
    return json.loads(job_call('layout', dict(frames=frames)))


def measure(texts):
    """[(px, bold, text)] -> widths, from HudFont::measure."""
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        for px, bold, text in texts:
            f.write(f'{px} {int(bold)} {text}\n')
    try:
        return json.loads(tool('measure', f.name))
    finally:
        os.unlink(f.name)


def render(frames, accent, scale=2, binary=None):
    """Render `frames` (each with `out`, a PNG path) and return one report per frame."""
    import numpy as np
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        sized = []
        for i, f in enumerate(frames):
            crop = f.get('crop', [0, 0, 1920, 1200])
            sized.append(dict(f, out=f'{tmp}/{i}.rgba', crop=crop))
        lines = job_call('render', dict(accent=accent, scale=scale, sources=frames[0]['desktops'], frames=sized), gl=True, binary=binary).splitlines()
        reports = [json.loads(line) for line in lines if line.startswith('{"out"') or line.startswith('{"overlays"')]
        renderer = next((line for line in lines if line.startswith('{"report"')), '')
        for i, f in enumerate(frames):
            w, h = sized[i]['crop'][2] * scale, sized[i]['crop'][3] * scale
            pixels = np.frombuffer(Path(f'{tmp}/{i}.rgba').read_bytes(), np.uint8).reshape(h, w, 4).copy()
            pixels[..., 3] = 255
            Path(f['out']).parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(pixels).save(f['out'])
    return reports, renderer


# --- Desktops -------------------------------------------------------------------------------------------------------
def _hex(c):
    return tuple(c)


def desktop_pixels(index, name, w, h, scale, scheme, path):
    """A synthetic desktop in Omarchy's shape at its output size: a bar on top, tiled windows under it. Nothing is captured."""
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    lw, lh = w / scale, h / scale
    s = scheme
    cr.set_source_rgb(*s['surface_container']); cr.paint()
    cr.select_font_face('JetBrainsMono Nerd Font', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    # The bar, 26 px of logical height, as the saved layout's windows at y = 38 imply.
    cr.set_source_rgb(*s['surface']); cr.rectangle(0, 0, lw, 26); cr.fill()
    cr.set_font_size(14); cr.set_source_rgb(*s['on_surface'])
    cr.move_to(14, 18); cr.show_text('1  2  3  4  5')
    cr.move_to(lw / 2 - 40, 18); cr.show_text('Fri 23:40')
    cr.move_to(lw - 120, 18); cr.show_text('wifi  vol  bat')
    gap, top = 12, 38
    windows = [(gap, top, lw * .33 - gap * 1.5, lh - top - gap), (lw * .33 + gap / 2, top, lw * .67 - gap * 1.5, lh - top - gap)]
    text = [['~/work/demo $ git log --oneline -5', '9f2c1ab tune the overview spacing', '71e0d3c keep the pill under the desktop',
             'a14be09 settle the bump', '~/work/demo $ ls', 'README.md  scene.hpp  src  tests', '~/work/demo $'],
            ['#include "scene.hpp"', '', 'namespace demo {', 'Frame build(const Scene& scene) {', '    Frame frame;',
             '    for (const auto& item : scene.items)', '        frame.add(item);', '    return frame;', '}', '}  // namespace demo']]
    for n, (x, y, ww, wh) in enumerate(windows):
        cr.set_source_rgb(*s['surface_container_low']); cr.rectangle(x, y, ww, wh); cr.fill()
        cr.set_source_rgb(*(s['primary'] if n == 1 else s['outline_variant'])); cr.set_line_width(2); cr.rectangle(x, y, ww, wh); cr.stroke()
        cr.set_font_size(19)
        for i, line in enumerate(text[n]):
            cr.set_source_rgb(*(s['primary'] if line.startswith('~') else s['on_surface']))
            cr.move_to(x + 22, y + 40 + i * 30); cr.show_text(line)
    cr.set_font_size(15); cr.set_source_rgb(*s['on_surface_variant'])
    cr.move_to(windows[1][0] + 22, lh - gap - 16); cr.show_text(f'{name}  {w}x{h} at {int(scale * 100)}%')
    surface.flush()
    path.write_bytes(bytes(surface.get_data()))


class Stage:
    """The desktops' files and the frame builder for one accent."""
    def __init__(self, accent, work):
        self.accent, self.work = accent, Path(work)
        self.info = info(accent)
        self.scheme, self.c = self.info['scheme'], self.info['constants']
        self.work.mkdir(parents=True, exist_ok=True)
        self._made = {}

    def desktop(self, i, d):
        path = self.work / f'd{i}-{d["w"]}x{d["h"]}.bgra'
        if path not in self._made:
            desktop_pixels(i, f'XR-{i + 1}', d['w'], d['h'], d['scale'], self.scheme, path)
            self._made[path] = True
        return dict(w=d['w'], h=d['h'], bgra=str(path), placement=d['placement'])

    def frame(self, out, setup=REAL, spacing=REAL_SPACING, selected=1, zoom=0.0, **extra):
        return dict(out=str(out), desktops=[self.desktop(i, d) for i, d in enumerate(setup)], spacing=spacing, selected=selected, zoom=zoom, **extra)


def with_placement(setup, index, **changes):
    out = [dict(d, placement=dict(d['placement'])) for d in setup]
    out[index]['placement'].update(changes)
    return out


# --- The real menu ---------------------------------------------------------------------------------------------------
def control(setup=REAL, typing=False):
    """A real ViewControl with stub owners: what options_rows reads on a run."""
    from tools.native_spike.view import ViewControl, ViewState
    from tools.native_spike.video import VideoControl
    names = [f'XR-{i + 1}' for i in range(len(setup))]
    state = ViewState(names)
    c = ViewControl(state, 'run', 'session', Mock(), Mock(), Mock())
    state.sizes = [dict(width=d['w'], height=d['h'], scale=d['scale']) for d in setup]
    for i, d in enumerate(setup):
        state.placements[i] = {k: d['placement'][k] for k in ('x', 'y', 'distance', 'curvature')}
    c.sender, c.privacy, c.keys, c.overview_pointer, c.desktops = Mock(), Mock(), Mock(open=False), Mock(), Mock()
    c.options_video = VideoControl(c, threads=False)
    c.supervisor = Mock(status=dict(presented_hz=90, missed=0, capture_path='dmabuf', pose_age_ms=3, tracking='luma'))
    return c


def snapshots():
    """Every (category, scope) as the supervisor would send it: real OptionsMenu, real options_rows."""
    from tools.native_spike.options_menu import CATEGORIES, SCOPES, OptionsMenu
    c = control()
    menu = OptionsMenu(c)
    c.options = menu
    menu.open('XR-2')
    out = []
    for category in CATEGORIES:
        for scope in SCOPES:
            menu.intent('options_scope', scope=scope)
            menu.intent('options_category', category=category)
            menu.refresh()
            out.append(dict(menu.snapshot(), focus=-1))
    return out


def child_snapshots():
    """The menu's child screens through the real owners: confirmation, Enter video URL, Choose local video."""
    from tools.native_spike.options_editors import NativeEditors
    from tools.native_spike.options_menu import OptionsMenu
    c = control()
    menu = OptionsMenu(c)
    c.options = menu
    menu.open('XR-2')
    out = {}
    for row_id, category, scope in (('stop', 'session', 'workspace'), ('layout_reset', 'layout', 'workspace'), ('remove_monitor', 'output', 'monitor')):
        menu.intent('options_scope', scope=scope); menu.intent('options_category', category=category); menu.refresh()
        menu.intent('options_activate', row_id=row_id, monitor='XR-2', scope=scope, revision=menu.data['revision'], value='') 
        out['confirm_' + row_id] = dict(menu.snapshot())
        menu.intent('options_cancel'); menu.confirmation = None; menu.child = None; menu.refresh()
    with tempfile.TemporaryDirectory() as tmp:
        for n in ('Movies', 'trip-360.mp4', 'concert-sbs.mkv', 'launch-180.mp4'):
            (Path(tmp) / n).mkdir() if n == 'Movies' else (Path(tmp) / n).write_bytes(b'')
        c.options_editor = NativeEditors(c, tmp)
        for kind in ('url', 'file'):
            c.options_editor.open(kind)
            rows = c.options_editor.rows()
            for r in rows:
                if r['id'] == 'editor.path': r['value'] = '/home/cody/Videos'
            base = dict(menu.snapshot())
            out['child_' + kind] = dict(base, category='video', scope='workspace', rows=rows, focus=0, scroll=0)
        c.options_editor.close()
    return out
