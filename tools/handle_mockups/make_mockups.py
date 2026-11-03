#!/usr/bin/env python3
"""Render every handle design option with the real compositor and lay out the PNGs to pick from (see README.md).

Reads the theme colour from Hyprland (read-only, `hyprctl getoption`) unless --accent gives one. Touches nothing else on
the desktop: the desktops in the frames are synthetic, nothing is captured, and rendering runs on llvmpipe.
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
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handle_options import FONT, OPTIONS, Hot, Option, Sketch, Theme, mix  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BINARY = REPO / "build/mockups/handle_preview"
DEFAULT_OUT = Path.home() / ".local/share/code-bot/previews/handles"

DESKTOP_W, DESKTOP_H = 1920, 1080
CROP = (530, 335, 860, 500)          # canvas px of every frame: the same camera and framing for every option
SHEET_CROP = (628, 363, 664, 437)    # the contact sheet's tighter view inside CROP
ZOOM_W, ZOOM_H = 216, 120            # canvas px of a detail view
MARGIN = 80                          # tile px an overlay reaches past the tile
LOOK_ROTATE, LOOK_RESIZE_NWSE = 6, 3 # view.hpp PointerLook
BASELINE_IDLE, BASELINE_HOT = 1, 2

PAGE_W, PAGE_MARGIN, GUTTER = 2440, 40, 20
INK, DIM, EDGE, PAGE = (226, 228, 233), (146, 149, 158), (46, 48, 54), (11, 12, 14)


def active_accent() -> str:
    """The active Omarchy theme's border, as view.py parse_border reads it: a gradient's first colour."""
    out = subprocess.run(["hyprctl", "getoption", "general:col.active_border", "-j"], capture_output=True, text=True, check=True).stdout
    reply = json.loads(out)
    if isinstance(reply.get("gradient"), str):
        value = int(reply["gradient"].split()[0], 16)
    else:
        value = int(reply["int"])
    return f"{value & 0xffffff:06x}"


def active_theme_name() -> str:
    try:
        return subprocess.run(["omarchy-theme-current"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "active theme"


def font_file(style: str) -> str:
    return subprocess.run(["fc-match", "-f", "%{file}", f"{FONT}:{style}"], capture_output=True, text=True, check=True).stdout


class Fonts:
    def __init__(self) -> None:
        self.bold_path, self.regular_path = font_file("bold"), font_file("regular")

    def bold(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.bold_path, size)

    def regular(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.regular_path, size)


# --- Synthetic desktops (nothing is captured) ---------------------------------------------------------------------------
TERMINAL = ["~/work/demo $ git log --oneline -5", "9f2c1ab tune the overview spacing", "71e0d3c keep the pill under the desktop",
            "3ab94e2 follow the theme colour", "c08f7d1 draw the panel on its own surface", "e41b6a0 first light",
            "~/work/demo $ ctest --test-dir build", "100% tests passed, 0 tests failed out of 71", "~/work/demo $ _"]
EDITOR = ['#include "scene.hpp"', "", "namespace demo {", "Frame build(const Scene& scene) {", "    Frame frame;",
          "    for (const auto& layer : scene.layers)", "        frame.push(layer.draw());", "    return frame;", "}", "}  // namespace demo"]
NOTES = ["Notes", "", "Drag a desktop by the pill under it.", "Turn it from the handles at its top corners.",
         "Pull a corner to resize it.", "Look round: it stays put in the room."]


def desktop_pixels(index: int, t: Theme, path: Path) -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, DESKTOP_W, DESKTOP_H)
    cr = cairo.Context(surface)
    wall = mix(t.surface_container, t.primary_container, 0.10)
    window = t.surface_container_low
    cr.set_source_rgb(*wall); cr.paint()
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(19); cr.set_source_rgb(*t.on_surface_variant)
    cr.move_to(26, 26); cr.show_text("1  2  3")
    cr.move_to(DESKTOP_W / 2 - 40, 26); cr.show_text(f"XR-{index + 1}")
    cr.move_to(DESKTOP_W - 92, 26); cr.show_text("17:07")
    x, y, w, h = 70, 70, DESKTOP_W - 140, DESKTOP_H - 130
    cr.set_source_rgb(*window); cr.rectangle(x, y, w, h); cr.fill()
    cr.set_source_rgb(*t.primary); cr.set_line_width(2); cr.rectangle(x, y, w, h); cr.stroke()
    lines, size = ((TERMINAL, 33), (EDITOR, 33), (NOTES, 36))[index]
    cr.set_font_size(size)
    for i, text in enumerate(lines):
        cr.set_source_rgb(*(t.primary if (index == 2 and i == 0) or text.startswith("~") else t.on_surface))
        cr.move_to(x + 44, y + 74 + i * size * 1.42); cr.show_text(text)
    surface.flush()
    path.write_bytes(bytes(surface.get_data()))


# --- Frames ---------------------------------------------------------------------------------------------------------
def render_env() -> dict[str, str]:
    # llvmpipe: a preview never needs either GPU, and the RX 7800 XT may be drawing the glasses.
    return dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe")


def render(binary: Path, accent: str, desktops: Path, manifest: Path, scale: int) -> str:
    done = subprocess.run([str(binary), "render", accent, str(desktops), str(manifest), str(scale), *map(str, CROP)],
                          capture_output=True, text=True, env=render_env())
    if done.returncode:
        raise SystemExit(f"handle_preview failed: {done.stderr}")
    return done.stdout


def read_frame(path: Path, scale: int) -> Image.Image:
    size = (CROP[2] * scale, CROP[3] * scale)
    image = Image.frombytes("RGBA", size, path.read_bytes()).convert("RGB")
    path.unlink()
    return image


class Frames:
    """Option frames: idle, hot with the dot on the rotate handle, hot with the dot on the resize handle."""

    def __init__(self, idle: Image.Image, rotate: Image.Image, resize: Image.Image):
        self.idle, self.rotate, self.resize = idle, rotate, resize


def render_option(option: Option | None, geo: dict, theme: Theme, args: argparse.Namespace, desktops: Path, work: Path) -> tuple[Frames, str]:
    tile = geo["tiles"][1]
    W, H = tile[2], tile[3]
    margin = MARGIN
    tag = f"{option.number:02d}" if option else "00"
    rotate_out, resize_out, idle_out = work / f"{tag}-rotate.rgba", work / f"{tag}-resize.rgba", work / f"{tag}-idle.rgba"
    manifest = work / f"{tag}.manifest"
    made: list[Path] = [manifest]
    if option:
        def sketch(hot: Hot) -> tuple[bytes, int, int]:
            s = Sketch(W, H, margin, args.density, theme, hot)
            option.draw(s)
            return s.finish(), s.px_w, s.px_h
        idle_px, ow, oh = sketch(Hot())
        hot_px, _, _ = sketch(Hot(frozenset({1}), frozenset({3})))
        idle_path, hot_path = work / f"{tag}-overlay-idle.raw", work / f"{tag}-overlay-hot.raw"
        idle_path.write_bytes(idle_px); hot_path.write_bytes(hot_px)
        made += [idle_path, hot_path]
        rx, ry = option.rotate_at(W, H)
        sx, sy = option.resize_at(W, H)
        lines = [f"{idle_out} {idle_path} {idle_path} {ow} {oh} {margin} -1 -1 0 0",
                 f"{rotate_out} {idle_path} {hot_path} {ow} {oh} {margin} {tile[0] + rx} {tile[1] + ry} {LOOK_ROTATE} 0",
                 f"{resize_out} {idle_path} {hot_path} {ow} {oh} {margin} {tile[0] + sx} {tile[1] + sy} {LOOK_RESIZE_NWSE} 0"]
    else:  # the built level bars and caps, from the compositor itself
        lines = [f"{idle_out} - - 0 0 0 -1 -1 0 {BASELINE_IDLE}",
                 f"{rotate_out} - - 0 0 0 {tile[0] + W + 12} {tile[1] + 22} {LOOK_ROTATE} {BASELINE_HOT}",
                 f"{resize_out} - - 0 0 0 {tile[0] + W + 2} {tile[1] + H + 2} {LOOK_RESIZE_NWSE} {BASELINE_HOT}"]
    manifest.write_text("\n".join(lines) + "\n")
    report = render(args.binary, args.accent, desktops, manifest, args.scale)
    for p in made:
        p.unlink()
    return Frames(read_frame(idle_out, args.scale), read_frame(rotate_out, args.scale), read_frame(resize_out, args.scale)), report


# --- Pages ----------------------------------------------------------------------------------------------------------
def view(frame: Image.Image, box: tuple[float, float, float, float], width: int, height: int, scale: int) -> Image.Image:
    x, y, w, h = box
    crop = ((x - CROP[0]) * scale, (y - CROP[1]) * scale, (x - CROP[0] + w) * scale, (y - CROP[1] + h) * scale)
    return frame.resize((width, height), Image.LANCZOS, box=crop)


def put(page: Image.Image, image: Image.Image, x: int, y: int) -> None:
    page.paste(image, (x, y))
    ImageDraw.Draw(page).rectangle((x - 1, y - 1, x + image.width, y + image.height), outline=EDGE)


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line); line = word
    return lines + [line]


def zoom_boxes(option: Option | None, geo: dict) -> tuple[tuple[float, ...], tuple[float, ...]]:
    x, y, W, H = geo["tiles"][1]
    rx = option.rotate_view(W) if option and option.rotate_view else W - 30
    return ((x + rx - ZOOM_W / 2, y - 14 - ZOOM_H / 2, ZOOM_W, ZOOM_H),
            (x + W - 10 - ZOOM_W / 2, y + H - ZOOM_H / 2, ZOOM_W, ZOOM_H))


def theme_chip(draw: ImageDraw.ImageDraw, x_right: int, y: int, fonts: Fonts, args: argparse.Namespace) -> None:
    text = f"{args.theme_name}  #{args.accent}"
    font = fonts.regular(24)
    width = draw.textlength(text, font=font)
    draw.text((x_right - width, y), text, font=font, fill=DIM)
    swatch = tuple(int(args.accent[i:i + 2], 16) for i in (0, 2, 4))
    draw.rounded_rectangle((x_right - width - 44, y + 3, x_right - width - 14, y + 27), 8, fill=swatch)


def option_page(option: Option, frames: Frames, geo: dict, fonts: Fonts, args: argparse.Namespace, path: Path) -> None:
    panel_w = (PAGE_W - 2 * PAGE_MARGIN - GUTTER) // 2
    panel_h = round(panel_w * CROP[3] / CROP[2])
    zoom_w = (PAGE_W - 2 * PAGE_MARGIN - 3 * GUTTER) // 4
    zoom_h = round(zoom_w * ZOOM_H / ZOOM_W)
    top = 158
    page_h = top + 38 + panel_h + 26 + 38 + zoom_h + PAGE_MARGIN
    page = Image.new("RGB", (PAGE_W, page_h), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((PAGE_MARGIN, 26), f"{option.number:02d}", font=fonts.bold(72), fill=INK)
    draw.text((PAGE_MARGIN + 116, 30), option.name, font=fonts.bold(44), fill=INK)
    for i, line in enumerate(wrap(draw, option.blurb, fonts.regular(25), PAGE_W - 2 * PAGE_MARGIN - 140)):
        draw.text((PAGE_MARGIN + 116, 90 + i * 32), line, font=fonts.regular(25), fill=DIM)
    theme_chip(draw, PAGE_W - PAGE_MARGIN, 40, fonts, args)
    label = fonts.bold(24)
    y = top
    for k, (name, frame) in enumerate((("IDLE", frames.idle), ("HOVER", frames.rotate))):
        x = PAGE_MARGIN + k * (panel_w + GUTTER)
        draw.text((x, y), name, font=label, fill=INK)
        put(page, view(frame, CROP, panel_w, panel_h, args.scale), x, y + 38)
    y += 38 + panel_h + 26
    rotate_box, resize_box = zoom_boxes(option, geo)
    for k, (name, frame, box) in enumerate((("Rotate, idle", frames.idle, rotate_box), ("Rotate, hover", frames.rotate, rotate_box),
                                            ("Resize, idle", frames.idle, resize_box), ("Resize, hover", frames.resize, resize_box))):
        x = PAGE_MARGIN + k * (zoom_w + GUTTER)
        draw.text((x, y), name, font=fonts.regular(24), fill=DIM)
        put(page, view(frame, box, zoom_w, zoom_h, args.scale), x, y + 38)
    page.save(path, optimize=True)


def built_page(geo: dict, fonts: Fonts, args: argparse.Namespace, desktops: Path, work: Path) -> str:
    """The renderer's own level bars and corner caps, every frame the compositor's alone: no overlay. Returns its report."""
    x, y, W, H = geo["tiles"][1]
    bar_x, bar_y, bar_w, _ = geo["level_bar"]
    mid = bar_x + bar_w / 2
    frames = {"idle": (-1, -1, 0, 1), "ccw": (mid - bar_w / 3, bar_y - 7, LOOK_ROTATE, 2), "level": (mid, bar_y - 7, LOOK_ROTATE, 3),
              "cw": (mid + bar_w / 3, bar_y - 7, LOOK_ROTATE, 4), "cap": (x + W + 16, y + H + 16, LOOK_RESIZE_NWSE, 5)}
    rolled_path = work / "rolled.rgba"
    lines = [f"{work / f'{name}.rgba'} - - 0 0 0 {dx} {dy} {look} {base}" for name, (dx, dy, look, base) in frames.items()]
    lines.append(f"{rolled_path} - - 0 0 0 -1 -1 0 1 9 1.18")  # rolled 9 degrees and shrunk by a corner
    manifest = work / "built.manifest"
    manifest.write_text("\n".join(lines) + "\n")
    report = render(args.binary, args.accent, desktops, manifest, args.scale)
    shots = {name: read_frame(work / f"{name}.rgba", args.scale) for name in frames}
    rolled = read_frame(rolled_path, args.scale)

    panel_w = (PAGE_W - 2 * PAGE_MARGIN - GUTTER) // 2
    panel_h = round(panel_w * CROP[3] / CROP[2])
    zoom_w = (PAGE_W - 2 * PAGE_MARGIN - 3 * GUTTER) // 4
    zoom_h = round(zoom_w * ZOOM_H / ZOOM_W)
    top = 130
    page = Image.new("RGB", (PAGE_W, top + 38 + panel_h + 26 + 38 + zoom_h + PAGE_MARGIN), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((PAGE_MARGIN, 26), "Level Bar, built", font=fonts.bold(52), fill=INK)
    draw.text((PAGE_MARGIN, 88), "Every frame is the renderer's own compositor; nothing is laid over it.", font=fonts.regular(25), fill=DIM)
    theme_chip(draw, PAGE_W - PAGE_MARGIN, 40, fonts, args)
    for k, (name, frame) in enumerate((("IDLE", shots["idle"]), ("ROLLED 9 DEGREES AND RESIZED", rolled))):
        px = PAGE_MARGIN + k * (panel_w + GUTTER)
        draw.text((px, top), name, font=fonts.bold(24), fill=INK)
        put(page, view(frame, CROP, panel_w, panel_h, args.scale), px, top + 38)
    bar_box = (mid - ZOOM_W / 2, y - 62 - ZOOM_H / 2 + 26, ZOOM_W, ZOOM_H)
    cap_box = zoom_boxes(None, geo)[1]
    row = top + 38 + panel_h + 26
    for k, (name, shot, box) in enumerate((("Turn left, hover", "ccw", bar_box), ("Level, hover", "level", bar_box),
                                           ("Turn right, hover", "cw", bar_box), ("Corner cap, hover", "cap", cap_box))):
        px = PAGE_MARGIN + k * (zoom_w + GUTTER)
        draw.text((px, row), name, font=fonts.regular(24), fill=DIM)
        put(page, view(shots[shot], box, zoom_w, zoom_h, args.scale), px, row + 38)
    page.save(args.out / args.built, optimize=True)
    return report


def contact_cell(number: str, name: str, frames: Frames, fonts: Fonts, args: argparse.Namespace, cell_w: int, panel_h: int, head: int) -> Image.Image:
    cell = Image.new("RGB", (cell_w, head + panel_h + 2), PAGE)
    draw = ImageDraw.Draw(cell)
    draw.text((0, 6), number, font=fonts.bold(40), fill=INK)
    draw.text((70, 12), name, font=fonts.bold(30), fill=INK)
    panel_w = (cell_w - 12) // 2
    for k, frame in enumerate((frames.idle, frames.rotate)):
        x = k * (panel_w + 12)
        cell.paste(view(frame, SHEET_CROP, panel_w, panel_h, args.scale), (x, head))
        # In the strip under the desktop, between its corner handles and the pill.
        draw.text((x + panel_w * 0.17, head + panel_h - 31), ("idle", "hover")[k], font=fonts.regular(22), fill=DIM)
    return cell


def contact_sheet(cells: list[Image.Image], fonts: Fonts, args: argparse.Namespace, renderer: str, path: Path) -> None:
    columns = 2
    cell_w, cell_h = cells[0].size
    rows = math.ceil((len(cells) + 1) / columns)
    top = 118
    gap = 30
    page = Image.new("RGB", (PAGE_MARGIN * 2 + columns * cell_w + (columns - 1) * GUTTER, top + rows * (cell_h + gap) + PAGE_MARGIN - gap), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((PAGE_MARGIN, 26), "Rotate and resize handles", font=fonts.bold(48), fill=INK)
    theme_chip(draw, page.width - PAGE_MARGIN, 44, fonts, args)
    for i, cell in enumerate(cells):
        x = PAGE_MARGIN + (i % columns) * (cell_w + GUTTER)
        y = top + (i // columns) * (cell_h + gap)
        page.paste(cell, (x, y))
    x = PAGE_MARGIN + (len(cells) % columns) * (cell_w + GUTTER)
    y = top + (len(cells) // columns) * (cell_h + gap)
    notes = ["Pick by number.", "",
             "Each cell: idle, then hover.",
             "Hover lights one rotate and one resize handle",
             "together; live, only the one under the dot lights.",
             "",
             f"Desktops, border, glow, pills: {renderer}.",
             "Handles: design overlays on each desktop's surface.",
             "Every colour comes from the theme's M3 scheme."]
    for i, line in enumerate(notes):
        draw.text((x, y + 8 + i * 34), line, font=fonts.regular(25), fill=INK if i == 0 else DIM)
    page.save(path, optimize=True)


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
    parser.add_argument("--sheet-only", action="store_true", help="write the contact sheet but no options-NN.png")
    parser.add_argument("--built", nargs="?", const="level-bar-built.png", help="only this page of the renderer's own bar and caps")
    args = parser.parse_args()
    live = args.accent is None
    args.accent = (args.accent or active_accent()).lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", args.accent):
        parser.error("--accent takes rrggbb")
    args.theme_name = args.theme_name or (active_theme_name() if live else "custom")
    if not args.binary.exists():
        raise SystemExit(f"{args.binary} is missing: build it first (README.md)")

    args.out.mkdir(parents=True, exist_ok=True)
    info = subprocess.run([str(args.binary), "info", args.accent], capture_output=True, text=True, check=True).stdout
    geo = json.loads(info)
    theme = Theme(geo["scheme"])
    fonts = Fonts()
    only = {int(n) for n in args.only.split(",")} if args.only else None
    chosen = [o for o in OPTIONS if not only or o.number in only]
    cell_w, head = (PAGE_W - 2 * PAGE_MARGIN - GUTTER) // 2, 62
    panel_h = round((cell_w - 12) // 2 * SHEET_CROP[3] / SHEET_CROP[2])
    cells, renderer = [], ""
    with tempfile.TemporaryDirectory(prefix=".work-", dir=args.out) as name:  # raw frames, on disk: /tmp is RAM here
        work = Path(name)
        for i in range(3):
            desktop_pixels(i, theme, work / f"desktop-{i}.bgra")
        if args.built:
            report = built_page(geo, fonts, args, work, work)
            print(re.search(r'"renderer":"([^"]*)"', report).group(1))
            return 0
        if not only:
            frames, _ = render_option(None, geo, theme, args, work, work)
            cells.append(contact_cell("--", "Built (renderer)", frames, fonts, args, cell_w, panel_h, head))
        for option in chosen:
            frames, report = render_option(option, geo, theme, args, work, work)
            match = re.search(r'"renderer":"([^"]*)"', report)
            renderer = match.group(1) if match else "unknown"
            if not args.sheet_only:
                option_page(option, frames, geo, fonts, args, args.out / f"options-{option.number:02d}.png")
            cells.append(contact_cell(f"{option.number:02d}", option.name, frames, fonts, args, cell_w, panel_h, head))
            print(f"{option.number:02d} {option.name}: {renderer}", flush=True)
    if not only:
        contact_sheet(cells, fonts, args, f"real compositor on {renderer.split(' (')[0]}", args.out / args.sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
