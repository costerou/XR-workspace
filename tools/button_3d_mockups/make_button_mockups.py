#!/usr/bin/env python3
"""Render every 3D button design with the real compositor and lay out the PNGs to pick from (see README.md).

The theme colour is --accent, or Hyprland's active border when the shell can reach Hyprland (read-only). Nothing else on
the desktop is touched: the desktops in the frames are synthetic, nothing is captured, and rendering runs on llvmpipe at
low priority, so a preview uses neither GPU.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cairo
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from button_options import FONT, GLOBAL, OFF, ON, OPTIONS, Frame, Geo, Hot, Option, Sketch, Theme, mix  # noqa: E402
import make_mockups as mm  # noqa: E402  (handle_mockups: fonts, page parts, the synthetic desktops)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BINARY = REPO / "build/button3d/button_preview"
DEFAULT_OUT = Path.home() / ".hermes/profiles/coder/previews/3d-button"
sys.path.insert(0, str(REPO / "tools"))
from native_spike import view as run_view  # noqa: E402

OVERVIEW = (20, 330, 1880, 500)  # canvas px of a frame: all three desktops
ZOOM_W, ZOOM_H = 330, 70         # canvas px of a detail view
MARGIN = 80                      # tile px an overlay reaches past the tile
PAGE_W, PAGE_MARGIN, GUTTER = mm.PAGE_W, mm.PAGE_MARGIN, mm.GUTTER
INK, DIM, EDGE, PAGE = mm.INK, mm.DIM, mm.EDGE, mm.PAGE
# Help card: what wayland.cpp lays it out on for the glasses at Omarchy's 1.25 scale (help_card_test's first case).
HELP_SURFACE = (1536, 960)
# The key shares the Background row: 14 rows keep the card at its roomiest text, which help_card_test requires, and sharing
# a row is what every recent key did (view.py HELP_PAIRS). KEY_3D is free in view.BINDS and in Omarchy's default binds.
KEY_3D, SHARED_KEYS, SHARED_ACTION = "Ctrl+Alt+3", "Ctrl+Alt+E / 3", "Background on/off; 3D filter (Shift: all)"
OWN_ROW_ACTION = "3D filter: this desktop; Shift: all"
# The panel row: the Background row's parts at its offsets (glass.hpp), one kPanelRowH lower than Brightness.
PANEL_ROW_H, PANEL_BAND_TOP, PANEL_PAD, BOX_X, BOX_W, BOX_H, SWITCH_X, SWITCH_W, SWITCH_H = 34, 318, 22, 184, 216, 26, 412, 46, 26
PANEL_TEXT_PX, BUTTON_PX, HANDLE_ON, HANDLE_OFF = 24, 18, 9, 6


def render_env() -> dict[str, str]:
    # llvmpipe on four threads: a preview needs neither GPU, and the RX 7800 XT may be drawing the glasses.
    return dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", LP_NUM_THREADS="4")


def render(args: argparse.Namespace, desktops: Path, manifest: Path) -> str:
    done = subprocess.run(["nice", "-n", "19", str(args.binary), "render", args.accent, str(desktops), str(manifest), str(args.scale)],
                          capture_output=True, text=True, env=render_env())
    if done.returncode:
        raise SystemExit(f"button_preview failed: {done.stderr}")
    return done.stdout


def renderer_name(report: str) -> str:
    match = re.search(r'"renderer":"([^"]*)"', report)
    return match.group(1).split(" (")[0] if match else "unknown"


def read_frame(path: Path, width: int, height: int) -> Image.Image:
    image = Image.frombytes("RGBA", (width, height), path.read_bytes()).convert("RGB")
    path.unlink()
    return image


def sketch(option: Option, frame: Frame, W: float, H: float, theme: Theme, density: int) -> tuple[bytes, int, int]:
    s = Sketch(W, H, MARGIN, density, theme, Hot())
    option.draw(s, frame)
    return s.finish(), s.px_w, s.px_h


def render_option(option: Option, geo: dict, theme: Theme, args: argparse.Namespace, desktops: Path, work: Path) -> tuple[dict[str, Image.Image], str]:
    """Named frames of one option: desktop 1 is the one hovered; `rolled` is it levelled 9 degrees."""
    tile = geo["tiles"][1]
    W, H = tile[2], tile[3]
    hits = option.hits(Geo(W, H))
    tag = f"{option.number:02d}"

    def dot(control: str) -> tuple[float, float]:
        x, y, w, h = hits[control]
        return tile[0] + x + w * 0.92, tile[1] + y + h * 0.9

    second = "all" if "all" in hits else "3d"
    shots = {"off": (OFF, OFF, "", ""), "off_hover": (OFF, OFF, "3d", ""), "on": (OFF, ON, "", ""), "on_hover": (OFF, ON, "3d", ""),
             "global": (GLOBAL, GLOBAL, "", ""), "global_hover": (GLOBAL, GLOBAL, second, ""), "rolled": (OFF, ON, "", "9")}
    overlays: dict[Frame, Path] = {}
    size = (0, 0)

    def overlay(frame: Frame) -> Path:
        nonlocal size
        if frame not in overlays:
            px, ow, oh = sketch(option, frame, W, H, theme, args.density)
            path = work / f"{tag}-{frame.state}-{frame.hover or 'rest'}.raw"
            path.write_bytes(px)
            overlays[frame] = path
            size = (ow, oh)
        return overlays[frame]

    lines = []
    for name, (others, centre, hover, roll) in shots.items():
        rest = overlay(Frame(others))
        hot = overlay(Frame(centre, hover))
        dot_at = dot(hover) if hover else (-1, -1)
        lines.append((work / f"{tag}-{name}.rgba", rest, hot, dot_at, roll or "0"))
    text = "\n".join(f"{out} {' '.join(map(str, OVERVIEW))} {rest} {hot} {rest} {size[0]} {size[1]} {MARGIN} {x} {y} 0 {roll} 0"
                     for out, rest, hot, (x, y), roll in lines)
    manifest = work / f"{tag}.manifest"
    manifest.write_text(text + "\n")
    report = render(args, desktops, manifest)
    shape = (OVERVIEW[2] * args.scale, OVERVIEW[3] * args.scale)
    frames = {name: read_frame(work / f"{tag}-{name}.rgba", *shape) for name in shots}
    for path in (*overlays.values(), manifest):
        path.unlink()
    return frames, report


def crop(frame: Image.Image, box: tuple[float, float, float, float], width: int, height: int, scale: int) -> Image.Image:
    x, y, w, h = box
    area = ((x - OVERVIEW[0]) * scale, (y - OVERVIEW[1]) * scale, (x - OVERVIEW[0] + w) * scale, (y - OVERVIEW[1] + h) * scale)
    return frame.resize((width, height), Image.LANCZOS, box=area)


def zoom_box(geo: dict) -> tuple[float, float, float, float]:
    x, y, w, h = geo["level_bar"]
    return (x + w / 2 - 142, y + h / 2 - ZOOM_H / 2 - 4, ZOOM_W, ZOOM_H)


def option_page(option: Option, frames: dict[str, Image.Image], geo: dict, fonts: mm.Fonts, args: argparse.Namespace, path: Path) -> None:
    panel_w = (PAGE_W - 2 * PAGE_MARGIN - GUTTER) // 2
    panel_h = round(panel_w * OVERVIEW[3] / OVERVIEW[2])
    third_w = (PAGE_W - 2 * PAGE_MARGIN - 2 * GUTTER) // 3
    zoom_h = round(third_w * ZOOM_H / ZOOM_W)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    body = mm.wrap(probe, option.blurb, fonts.regular(25), PAGE_W - 2 * PAGE_MARGIN - 140)
    top = 104 + 32 * len(body) + 26
    rolled_h, real_h = 380, 150
    page_h = top + 38 + panel_h + 2 * (26 + 38 + zoom_h) + 26 + 38 + rolled_h + PAGE_MARGIN
    page = Image.new("RGB", (PAGE_W, page_h), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((PAGE_MARGIN, 26), f"{option.number:02d}", font=fonts.bold(72), fill=INK)
    draw.text((PAGE_MARGIN + 116, 30), option.name, font=fonts.bold(44), fill=INK)
    for i, line in enumerate(body):
        draw.text((PAGE_MARGIN + 116, 90 + i * 32), line, font=fonts.regular(25), fill=DIM)
    mm.theme_chip(draw, PAGE_W - PAGE_MARGIN, 40, fonts, args)
    y = top
    for k, (name, shot) in enumerate((("ONE DESKTOP ON (XR-2)", "on"), ("GLOBAL: EVERY DESKTOP ON", "global"))):
        x = PAGE_MARGIN + k * (panel_w + GUTTER)
        draw.text((x, y), name, font=fonts.bold(24), fill=INK)
        mm.put(page, crop(frames[shot], OVERVIEW, panel_w, panel_h, args.scale), x, y + 38)
    y += 38 + panel_h
    box = zoom_box(geo)
    global_hover = "Global, dot on the all-desktops control" if "all" in option.hits(Geo(*geo["tiles"][1][2:])) else "Global, hover"
    rows = ((("Off", "off"), ("Off, hover", "off_hover"), ("On", "on")), (("On, hover", "on_hover"), ("Global", "global"), (global_hover, "global_hover")))
    for row in rows:
        y += 26
        for k, (name, shot) in enumerate(row):
            x = PAGE_MARGIN + k * (third_w + GUTTER)
            draw.text((x, y), name, font=fonts.regular(24), fill=DIM)
            mm.put(page, crop(frames[shot], box, third_w, zoom_h, args.scale), x, y + 38)
        y += 38 + zoom_h
    y += 26
    bar_x, bar_y, bar_w, bar_h = geo["level_bar"]
    x = PAGE_MARGIN
    roll_w = round(rolled_h * 2)
    draw.text((x, y), "On, desktop rolled 9 degrees: it rides on the desktop", font=fonts.regular(24), fill=DIM)
    roll_box = (bar_x + bar_w / 2 - 190, bar_y - 34, 380, 190)
    mm.put(page, crop(frames["rolled"], roll_box, roll_w, rolled_h, args.scale), x, y + 38)
    x += roll_w + GUTTER
    draw.text((x, y), "Canvas size, 1:1 (the glasses are 1920 x 1200 per eye): off, on, global", font=fonts.regular(24), fill=DIM)
    real_w, gap = PAGE_W - PAGE_MARGIN - x, 12
    real_each = (rolled_h - 2 * gap) // 3
    real_box_w = min(real_w, 640)
    for k, shot in enumerate(("off", "on", "global")):
        area = (bar_x + bar_w / 2 - real_box_w / 2, bar_y + bar_h / 2 - real_each / 2, real_box_w, real_each)
        mm.put(page, crop(frames[shot], area, real_box_w, real_each, args.scale), x, y + 38 + k * (real_each + gap))
    x += real_box_w + GUTTER + 10
    hits = option.hits(Geo(*geo["tiles"][1][2:]))
    bar_left = (geo["tiles"][1][2] - bar_w) / 2
    right = max(r[0] + r[2] for r in hits.values()) - bar_left - bar_w
    left = bar_left - min(r[0] for r in hits.values())
    facts = [("Every desktop at once", option.all_how), ("Room taken beside the bar", f"{right:.0f} px right" + (f" and {left:.0f} px left" if left > 0 else "")),
             ("Key", KEY_3D + ", Shift for all")]
    for i, (name, value) in enumerate(facts):
        draw.text((x, y + 44 + i * 96), name, font=fonts.regular(24), fill=DIM)
        for j, line in enumerate(mm.wrap(draw, value, fonts.bold(25), PAGE_W - PAGE_MARGIN - x)):
            draw.text((x, y + 76 + i * 96 + j * 30), line, font=fonts.bold(25), fill=INK)
    page.save(path, optimize=True)


def contact_cell(option: Option, frames: dict[str, Image.Image], geo: dict, fonts: mm.Fonts, args: argparse.Namespace, cell_w: int) -> Image.Image:
    zoom_w = (cell_w - 10) // 2
    zoom_h = round(zoom_w * ZOOM_H / ZOOM_W)
    strip = (OVERVIEW[0], 355, OVERVIEW[2], 150)
    strip_h = round(cell_w * strip[3] / strip[2])
    head = 60
    cell = Image.new("RGB", (cell_w, head + 2 * (zoom_h + 8) + 30 + strip_h + 2), PAGE)
    draw = ImageDraw.Draw(cell)
    draw.text((0, 4), f"{option.number:02d}", font=fonts.bold(40), fill=INK)
    draw.text((70, 10), option.name, font=fonts.bold(30), fill=INK)
    box = zoom_box(geo)
    for i, (name, shot) in enumerate((("off", "off"), ("hover", "off_hover"), ("on", "on"), ("global", "global"))):
        x, y = (i % 2) * (zoom_w + 10), head + (i // 2) * (zoom_h + 8)
        cell.paste(crop(frames[shot], box, zoom_w, zoom_h, args.scale), (x, y))
        draw.text((x + 10, y + 6), name, font=fonts.regular(22), fill=DIM)
    y = head + 2 * (zoom_h + 8)
    draw.text((0, y), f"Global, all three desktops. Reach it: {option.all_how.lower()}", font=fonts.regular(21), fill=DIM)
    cell.paste(crop(frames["global"], strip, cell_w, strip_h, args.scale), (0, y + 30))
    return cell


def contact_sheet(cells: list[Image.Image], fonts: mm.Fonts, args: argparse.Namespace, renderer: str, path: Path) -> None:
    columns = 2
    cell_w, cell_h = cells[0].size
    rows = math.ceil(len(cells) / columns)
    top, gap, foot = 118, 30, 230
    page = Image.new("RGB", (PAGE_MARGIN * 2 + columns * cell_w + (columns - 1) * GUTTER, top + rows * (cell_h + gap) + foot + PAGE_MARGIN), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((PAGE_MARGIN, 26), "3D button", font=fonts.bold(48), fill=INK)
    mm.theme_chip(draw, page.width - PAGE_MARGIN, 44, fonts, args)
    for i, cell in enumerate(cells):
        page.paste(cell, (PAGE_MARGIN + (i % columns) * (cell_w + GUTTER), top + (i // columns) * (cell_h + gap)))
    y = top + rows * (cell_h + gap)
    notes = ["Pick by number." + (f" Recommended: {args.recommend}." if args.recommend else ""),
             f"Desktops, border, glow, pills, level bars and corner caps: {renderer}, the real compositor. Desktops are synthetic.",
             "Buttons: design overlays laid on each desktop's own surface, so they roll and scale with it. Not renderer code.",
             "Every colour comes from the theme's M3 scheme; nothing passes the 191/255 OLED cap (check_button_options.py).",
             "Panel row and help row: panel-and-help.png."]
    for i, line in enumerate(notes):
        draw.text((PAGE_MARGIN, y + 8 + i * 38), line, font=fonts.bold(27) if i == 0 else fonts.regular(25), fill=INK if i == 0 else DIM)
    page.save(path, optimize=True)


# --- Panel and help -------------------------------------------------------------------------------------------------------
def panel_row(scheme: Theme, width: int, height: int, scale: int, origin_x: float, word: str, on: bool) -> Image.Image:
    """The proposed row over a clear background: the Background row's dropdown + switch, its colours and metrics (glass.cpp)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    cr.translate(origin_x, 0)
    t = scheme
    cy = PANEL_ROW_H / 2
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(PANEL_TEXT_PX)
    cr.set_source_rgb(*t.on_surface)
    cr.move_to(PANEL_PAD, cy + 8.5)
    cr.show_text("3D filter")
    box_y = cy - BOX_H / 2
    Sketch.round_rect(cr, BOX_X, box_y, BOX_W, BOX_H, 4)
    cr.set_source_rgb(*t.surface_container_highest); cr.fill()
    cr.set_source_rgb(*t.on_surface_variant); cr.rectangle(BOX_X, box_y + BOX_H - 1, BOX_W, 1); cr.fill()
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(BUTTON_PX)
    cr.set_source_rgb(*t.on_surface)
    cr.move_to(BOX_X + 12, cy + 6.5)
    cr.show_text(word)
    arrow_x = BOX_X + BOX_W - 16
    cr.set_source_rgb(*t.on_surface_variant)
    cr.move_to(arrow_x - 5, cy - 2.5); cr.line_to(arrow_x + 5, cy - 2.5); cr.line_to(arrow_x, cy + 3); cr.close_path(); cr.fill()
    sw_y = cy - SWITCH_H / 2
    end = SWITCH_H / 2
    Sketch.round_rect(cr, SWITCH_X, sw_y, SWITCH_W, SWITCH_H, end)
    cr.set_source_rgb(*(t.primary if on else t.surface_container_highest)); cr.fill()
    if not on:
        Sketch.round_rect(cr, SWITCH_X + 1, sw_y + 1, SWITCH_W - 2, SWITCH_H - 2, end - 1)
        cr.set_source_rgb(*t.outline); cr.set_line_width(2); cr.stroke()
    cr.set_source_rgb(*(t.on_primary if on else t.outline))
    cr.arc(SWITCH_X + SWITCH_W - end if on else SWITCH_X + end, cy, HANDLE_ON if on else HANDLE_OFF, 0, math.tau); cr.fill()
    surface.flush()
    buffer = bytes(surface.get_data())
    return Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRa", surface.get_stride(), 1).copy()


def panel_shot(frame: Image.Image, panel: list[float], crop_box: tuple[int, int, int, int], scale: int, row: Image.Image) -> Image.Image:
    """The real open panel with one row more: cut under Brightness, stretch one empty row of glass by a row's height and lay
    the proposed row on that. The glass's edge, border and fill are the compositor's own pixels."""
    cut = round((panel[1] + PANEL_BAND_TOP + 3 - crop_box[1]) * scale)
    extra = PANEL_ROW_H * scale
    out = Image.new("RGB", (frame.width, frame.height + extra))
    out.paste(frame.crop((0, 0, frame.width, cut)), (0, 0))
    out.paste(frame.crop((0, cut, frame.width, cut + 1)).resize((frame.width, extra), Image.NEAREST), (0, cut))
    out.paste(frame.crop((0, cut, frame.width, frame.height)), (0, cut + extra))
    row_at = round((panel[1] + PANEL_BAND_TOP + 3 - crop_box[1]) * scale)
    out.paste(row, (0, row_at), row)
    return out


def help_cards(args: argparse.Namespace, work: Path) -> tuple[Image.Image, str, str, str]:
    """(the real card with the key on the Background row, then the fits of today's card, of that and of a row of its own)."""
    sections = [[list(r) for r in s] for s in run_view._help_sections()]

    def flat(table: list) -> list:
        return [(r, i == 0 and n > 0) for n, s in enumerate(table) for i, r in enumerate(s)]

    shared = [[([SHARED_KEYS, SHARED_ACTION] if r[0] == "Ctrl+Alt+E" else r) for r in s] for s in sections]
    own = [[list(r) for r in s] for s in sections]
    own[1].append([KEY_3D, OWN_ROW_ACTION])  # the modes section, after the background's row

    def card(rows: list, name: str) -> tuple[Path, str, list[int]]:
        file = work / f"{name}.tsv"
        file.write_text("".join(f"{r[0]}\t{r[1]}\t{1 if start else 0}\n" for r, start in rows))
        out = work / f"{name}.rgba"
        done = subprocess.run([str(args.binary), "help", args.accent, str(file), str(out), *map(str, HELP_SURFACE)],
                              capture_output=True, text=True, check=True, env=render_env())
        file.unlink()
        v = [int(n) for n in done.stdout.split()]
        return out, f"{len(rows)} rows, {v[2]} px text, {v[3]} page{'s' if v[3] != 1 else ''}", v

    results = {name: card(flat(table), f"help-{name}") for name, table in (("today", sections), ("shared", shared), ("own", own))}
    path, _, fit = results["shared"]
    image = Image.frombytes("RGBA", (fit[0], fit[1]), path.read_bytes())  # premultiplied, so over black it is its own colour
    for out, _, _ in results.values():
        out.unlink()
    black = Image.new("RGB", image.size, (0, 0, 0))
    black.paste(image.convert("RGB"), (0, 0))
    return black, results["today"][1], results["shared"][1], results["own"][1]


def panel_help_page(geo: dict, theme: Theme, fonts: mm.Fonts, args: argparse.Namespace, desktops: Path, work: Path) -> str:
    panel = geo["panel"]
    box = (int(panel[0]) - 14, int(panel[1]) - 28, int(panel[2]) + 28, int(panel[3]) + 12 + 28)
    box = (box[0], box[1], box[2], min(box[3], 1200 - box[1]))
    out = work / "panel.rgba"
    manifest = work / "panel.manifest"
    manifest.write_text(f"{out} {box[0]} {box[1]} {box[2]} {box[3]} - - - 0 0 0 -1 -1 0 0 1\n")
    render(args, desktops, manifest)
    manifest.unlink()
    scale = args.scale
    frame = read_frame(out, box[2] * scale, box[3] * scale)
    origin = panel[0] - box[0]
    states = (("Off", "This desktop", False), ("On, this desktop", "This desktop", True), ("On, all desktops", "All desktops", True))
    shots = [panel_shot(frame, panel, box, scale, panel_row(theme, frame.width, PANEL_ROW_H * scale, scale, origin, word, on)) for _, word, on in states]
    card, fit_today, fit_shared, fit_own = help_cards(args, work)
    assert fit_today == fit_shared, "the shared row must keep the card as it is"

    gap = 26
    shot_w = (PAGE_W - 2 * PAGE_MARGIN - 2 * gap) // 3
    shot_h = round(shot_w * shots[0].height / shots[0].width)
    card_w = min(PAGE_W - 2 * PAGE_MARGIN, 1500)
    card_h = round(card_w * card.height / card.width)
    top = 130
    page = Image.new("RGB", (PAGE_W, top + 38 + shot_h + 56 + 38 + card_h + 90 + PAGE_MARGIN), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((PAGE_MARGIN, 26), "Panel toggle and help row", font=fonts.bold(52), fill=INK)
    draw.text((PAGE_MARGIN, 88), "Same in every option. One row under Brightness, built like the Background row: a dropdown for where it applies, the panel's switch for on / off.",
              font=fonts.regular(25), fill=DIM)
    mm.theme_chip(draw, PAGE_W - PAGE_MARGIN, 40, fonts, args)
    for k, ((name, _, _), shot) in enumerate(zip(states, shots)):
        x = PAGE_MARGIN + k * (shot_w + gap)
        draw.text((x, top), name, font=fonts.bold(24), fill=INK)
        mm.put(page, shot.resize((shot_w, shot_h), Image.LANCZOS), x, top + 38)
    y = top + 38 + shot_h + 56
    draw.text((PAGE_MARGIN, y), f"Help card: {KEY_3D} shares the Background row. {fit_shared}, as today. A row of its own: {fit_own}.", font=fonts.bold(24), fill=INK)
    mm.put(page, card.resize((card_w, card_h), Image.LANCZOS), PAGE_MARGIN, y + 38)
    y += 38 + card_h + 16
    words = ("Panel: the compositor's own open panel with one row of glass stretched in (the panel grows 34 px, to 428), the row drawn with glass.cpp's "
             "metrics and colours. Help card: render_help_card itself, with the real rows from view.py.")
    for i, line in enumerate(mm.wrap(draw, words, fonts.regular(23), PAGE_W - 2 * PAGE_MARGIN)):
        draw.text((PAGE_MARGIN, y + i * 30), line, font=fonts.regular(23), fill=DIM)
    page.save(args.out / "panel-and-help.png", optimize=True)
    print(f"help card, today: {fit_today}; shared: {fit_shared}; own row: {fit_own}")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accent", help="theme colour rrggbb (default: Hyprland's active border)")
    parser.add_argument("--theme-name", help="shown on the pages (default: the active Omarchy theme)")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scale", type=int, default=4, help="buffer px per canvas px")
    parser.add_argument("--density", type=int, default=4, help="overlay px per tile px")
    parser.add_argument("--only", help="comma-separated option numbers (a contact sheet is written only for all of them)")
    parser.add_argument("--sheet", default="contact-sheet.png", help="contact sheet file name")
    parser.add_argument("--sheet-only", action="store_true", help="write the contact sheet, no option-NN.png or panel page")
    parser.add_argument("--extras-only", action="store_true", help="write only panel-and-help.png")
    parser.add_argument("--recommend", help="named on the contact sheet")
    args = parser.parse_args()
    live = args.accent is None
    args.accent = (args.accent or mm.active_accent()).lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", args.accent):
        parser.error("--accent takes rrggbb")
    args.theme_name = args.theme_name or (mm.active_theme_name() if live else "custom")
    if not args.binary.exists():
        raise SystemExit(f"{args.binary} is missing: build it first (README.md)")

    args.out.mkdir(parents=True, exist_ok=True)
    geo = json.loads(subprocess.run([str(args.binary), "info", args.accent], capture_output=True, text=True, check=True).stdout)
    theme = Theme(geo["scheme"])
    fonts = mm.Fonts()
    only = {int(n) for n in args.only.split(",")} if args.only else None
    chosen = [o for o in OPTIONS if not only or o.number in only]
    cell_w = (PAGE_W - 2 * PAGE_MARGIN - GUTTER) // 2
    cells, renderer = [], ""
    mm.DESKTOP_H = 1200  # the Luma Ultra profile's desktops
    with tempfile.TemporaryDirectory(prefix=".work-", dir=args.out) as name:  # raw frames, on disk: /tmp is RAM here
        work = Path(name)
        for i in range(3):
            mm.desktop_pixels(i, theme, work / f"desktop-{i}.bgra")
        if not args.sheet_only:
            panel_help_page(geo, theme, fonts, args, work, work)
        if args.extras_only:
            return 0
        for option in chosen:
            frames, report = render_option(option, geo, theme, args, work, work)
            renderer = renderer_name(report)
            if not args.sheet_only:
                option_page(option, frames, geo, fonts, args, args.out / f"option-{option.number:02d}.png")
            cells.append(contact_cell(option, frames, geo, fonts, args, cell_w))
            print(f"{option.number:02d} {option.name}: {renderer}", flush=True)
    if not only:
        contact_sheet(cells, fonts, args, renderer, args.out / args.sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
