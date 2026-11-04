#!/usr/bin/env python3
"""Render every text cursor option with the real compositor and lay out the PNGs to pick from (see README.md).

Reads the theme colour from Hyprland (read-only, `hyprctl getoption`) unless --accent gives one. Touches nothing else on
the desktop: the desktops in the frames are synthetic, nothing is captured, and rendering runs on llvmpipe at low priority
on a few threads, so a preview uses neither GPU and leaves the CPU to a run that may be going.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cairo
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "handle_mockups"))
from cursor_options import LEVEL, OPTIONS, Option, Shape, write_shape  # noqa: E402
from make_mockups import Fonts, active_accent, active_theme_name, wrap  # noqa: E402

REPO = HERE.parents[1]
DEFAULT_BINARY = REPO / "build/text-cursor/cursor_preview"
DEFAULT_OUT = Path.home() / ".hermes/profiles/coder/previews/text-cursor"

DOT_X, DOT_Y = 960, 600              # canvas px, the middle of the centre desktop: a word in each window's text
TILT = 3.0                           # degrees of head roll in the "head tilted" frame
LOOK_PLAIN, LOOK_RESIZE, LOOK_MOVE, LOOK_ROTATE = 0, 1, 5, 6  # view.hpp PointerLook

COL_W, ROW_H, MARGIN, GUTTER = 576, 320, 40, 20
PAGE_W = 2 * MARGIN + 4 * COL_W + 3 * GUTTER
ZOOM = 4
INK, DIM, EDGE, PAGE = (226, 228, 233), (146, 149, 158), (46, 48, 54), (11, 12, 14)
MONO, SANS = "JetBrainsMono Nerd Font", "Liberation Sans"


# --- Synthetic desktops (nothing is captured) ---------------------------------------------------------------------------
TERMINAL = ["~/work/demo $ git log --oneline -14",
            "9f2c1ab tune the overview spacing so three desktops fit the view and the pills still clear the bottom edge",
            "71e0d3c keep the pill under the desktop while it is being dragged, and bring it back when the drag ends",
            "3ab94e2 follow the theme colour when the desktop switches theme, without redrawing the panels by hand",
            "c08f7d1 draw the panel on its own surface and leave the desktops alone, so a slider never touches them",
            "e41b6a0 read the cursor position on the sampler thread and hand the main loop only the newest sample",
            "6d0c2f8 hold the dot still for one frame after a warp, so the jump never shows up as a streak of motion",
            "b77e913 stop the level bar from taking a press on a corner, which belongs to the resize cap beside it",
            "02fa6c4 show the resize arrows only where a press would resize, never over a window's own content",
            "d91e4ba give the desktop under the dot the keyboard as soon as the dot lands on its content",
            "48c3a7f save the placement the moment a drag ends, so a crash a second later still keeps it",
            "aa12e60 let a window keep its size when it comes back to its desktop at the start of a run",
            "5e70b1d draw the help card under the dot, never over it, so the dot can still reach its close button",
            "f3c92d7 first light: one captured desktop on one tile, drawn at 120 Hz with nothing around it yet",
            "~/work/demo $ ctest --test-dir build --output-on-failure",
            "100% tests passed, 0 tests failed out of 64",
            "~/work/demo $ "]
ARTICLE = ["Reading on a wall of screens", "",
           "A pointer that changes shape tells you what a press will do before you",
           "press. Over a link it is a hand, over a window's edge a pair of arrows,",
           "and over text a thin beam that shows you can select it.",
           "",
           "The beam has one more job in the overview, where every desktop is shown",
           "at a third of its size. Each line of text is only ten pixels tall there,",
           "so a cursor that hides a whole word makes careful selection hard. A thin",
           "stem leaves the letters on either side of it in plain view, and the click",
           "lands on the exact spot in the middle of the stem.",
           "",
           "The same grey as the dot keeps it easy on the panel. Nothing here needs",
           "to be bright to be found, because the eye follows what moves."]


def wall_and_window(cr: cairo.Context, W: int, H: int, wall, window, frame) -> tuple[float, float, float, float]:
    cr.set_source_rgb(*wall); cr.paint()
    cr.select_font_face(MONO, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(20); cr.set_source_rgb(0.62, 0.63, 0.66)
    cr.move_to(26, 28); cr.show_text("1  2  3")
    cr.move_to(W / 2 - 34, 28); cr.show_text("XR-2")
    cr.move_to(W - 92, 28); cr.show_text("20:15")
    x, y, w, h = 40, 56, W - 80, H - 96
    cr.set_source_rgb(*window); cr.rectangle(x, y, w, h); cr.fill()
    cr.set_source_rgb(*frame); cr.set_line_width(2); cr.rectangle(x, y, w, h); cr.stroke()
    return x, y, w, h


def desktop_pixels(kind: str, W: int, H: int, path: Path) -> None:
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surface)
    if kind.startswith("flat"):                     # measuring surfaces for check_cursor_options.py
        level = int(kind[4:]) / 255
        cr.set_source_rgb(level, level, level); cr.paint()
    elif kind == "dark":                            # light text on a dark window: a terminal, 22 px text in 30 px lines
        x, y, _, _ = wall_and_window(cr, W, H, (0.05, 0.05, 0.06), (0.07, 0.075, 0.09), (0.3, 0.31, 0.34))
        cr.set_font_size(22)
        for i in range(36):
            text = TERMINAL[(i + 5) % len(TERMINAL)]  # line 17 is a long one, through the middle of the desktop
            cr.set_source_rgb(*((0.5, 0.75, 0.95) if text.startswith("~") else (0.86, 0.87, 0.89)))
            cr.move_to(x + 30, y + 42 + i * 30); cr.show_text(text)
    elif kind == "light":                           # dark text on a light window: a page, 26 px text in 40 px lines
        x, y, _, _ = wall_and_window(cr, W, H, (0.05, 0.05, 0.06), (0.97, 0.965, 0.95), (0.3, 0.31, 0.34))
        cr.select_font_face(SANS, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(46); cr.set_source_rgb(0.08, 0.08, 0.1)
        cr.move_to(x + 300, y + 190); cr.show_text(ARTICLE[0])
        cr.select_font_face(SANS, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(26); cr.set_source_rgb(0.13, 0.13, 0.15)
        for i, text in enumerate(ARTICLE[2:]):  # line 7 goes through the middle of the desktop
            cr.move_to(x + 300, y + 272 + i * 40); cr.show_text(text)
    else:                                           # the neighbours, dim
        x, y, w, h = wall_and_window(cr, W, H, (0.05, 0.05, 0.06), (0.07, 0.075, 0.09), (0.3, 0.31, 0.34))
        cr.set_font_size(22); cr.set_source_rgb(0.5, 0.51, 0.54)
        for i in range(36):
            cr.move_to(x + 30, y + 42 + i * 30); cr.show_text(TERMINAL[(i * 3 + 1) % len(TERMINAL)])
    surface.flush()
    path.write_bytes(bytes(surface.get_data()))


# --- Frames ---------------------------------------------------------------------------------------------------------
def render_env() -> dict[str, str]:
    # llvmpipe on four threads: a preview never needs a GPU, and the RX 7800 XT may be drawing the glasses.
    return dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", LP_NUM_THREADS="4")


class Batch:
    """Frames to draw at one scale; run() gives them as images by key."""

    def __init__(self, binary: Path, accent: str, work: Path, primary, scale: int):
        self.binary, self.accent, self.work, self.primary, self.scale = binary, accent, work, primary, scale
        self.lines: list[str] = []
        self.sizes: dict[str, tuple[Path, int, int]] = {}
        self.shapes: dict[tuple[int, float], Path] = {}

    def shape_file(self, shape: Shape, level: float) -> str:
        key = (id(shape), level)
        if key not in self.shapes:
            self.shapes[key] = self.work / f"shape-{len(self.shapes)}-{self.scale}.txt"
            write_shape(self.shapes[key], shape, level, self.primary)
        return str(self.shapes[key])

    def add(self, key: str, desktop: str, shape: Shape | None, crop: tuple[int, int, int, int], look: int = LOOK_PLAIN, level: float = LEVEL,
            x: float = DOT_X, y: float = DOT_Y, roll: float = 0.0, cursor: bool = True) -> None:
        out = self.work / f"frame-{len(self.lines)}-{self.scale}.rgba"
        name = self.shape_file(shape, level) if shape else "-"
        self.lines.append(f"{out} {self.work / (desktop + '.bgra')} {name} {x if cursor else -1} {y} {look} {level} {roll} {crop[0]} {crop[1]} {crop[2]} {crop[3]}")
        self.sizes[key] = (out, crop[2] * self.scale, crop[3] * self.scale)

    def run(self) -> tuple[dict[str, Image.Image], str]:
        manifest = self.work / f"manifest-{self.scale}.txt"
        manifest.write_text("\n".join(self.lines) + "\n")
        done = subprocess.run(["nice", "-n", "19", str(self.binary), "render", self.accent, str(self.work / "side.bgra"), str(manifest), str(self.scale)],
                              capture_output=True, text=True, env=render_env())
        if done.returncode:
            raise SystemExit(f"cursor_preview failed: {done.stderr}")
        images = {}
        for key, (path, w, h) in self.sizes.items():
            images[key] = Image.frombytes("RGBA", (w, h), path.read_bytes()).convert("RGB")
            path.unlink()
        match = re.search(r'"renderer":"([^"]*)"', done.stdout)
        return images, match.group(1) if match else "unknown"


def centred(w: int, h: int, x: float = DOT_X, y: float = DOT_Y) -> tuple[int, int, int, int]:
    return round(x - w / 2), round(y - h / 2), w, h


# --- Pages ----------------------------------------------------------------------------------------------------------
def put(page: Image.Image, image: Image.Image, x: int, y: int) -> None:
    page.paste(image, (x, y))
    ImageDraw.Draw(page).rectangle((x - 1, y - 1, x + image.width, y + image.height), outline=EDGE)


def theme_chip(draw: ImageDraw.ImageDraw, x_right: int, y: int, fonts: Fonts, args: argparse.Namespace) -> None:
    text = f"{args.theme_name}  #{args.accent}"
    font = fonts.regular(24)
    width = draw.textlength(text, font=font)
    draw.text((x_right - width, y), text, font=font, fill=DIM)
    swatch = tuple(int(args.accent[i:i + 2], 16) for i in (0, 2, 4))
    draw.rounded_rectangle((x_right - width - 44, y + 3, x_right - width - 14, y + 27), 8, fill=swatch)


def row_title(draw: ImageDraw.ImageDraw, y: int, title: str, note: str, fonts: Fonts) -> None:
    draw.text((MARGIN, y), title, font=fonts.bold(26), fill=INK)
    draw.text((MARGIN + draw.textlength(title, font=fonts.bold(26)) + 24, y + 1), note, font=fonts.regular(24), fill=DIM)


def option_page(option: Option, real: dict, zoom: dict, fonts: Fonts, args: argparse.Namespace, path: Path) -> None:
    tag = f"{option.number:02d}"
    head_top, label_h, title_h, gap = 168, 36, 44, 30
    rows_h = 3 * (title_h + label_h + ROW_H + gap)
    extra = (title_h + label_h + ROW_H + gap) * (2 if option.frames else 0)
    page = Image.new("RGB", (PAGE_W, head_top + rows_h + extra + MARGIN - gap), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((MARGIN, 26), tag, font=fonts.bold(72), fill=INK)
    draw.text((MARGIN + 116, 30), option.name, font=fonts.bold(44), fill=INK)
    for i, line in enumerate(wrap(draw, option.blurb, fonts.regular(25), PAGE_W - 2 * MARGIN - 140)):
        draw.text((MARGIN + 116, 92 + i * 32), line, font=fonts.regular(25), fill=DIM)
    theme_chip(draw, PAGE_W - MARGIN, 40, fonts, args)

    def row(y: int, title: str, note: str, cells: list[tuple[str, Image.Image]]) -> int:
        row_title(draw, y, title, note, fonts)
        y += title_h
        width = (PAGE_W - 2 * MARGIN - (len(cells) - 1) * GUTTER) // len(cells)
        for k, (label, image) in enumerate(cells):
            x = MARGIN + k * (width + GUTTER)
            draw.text((x, y), label, font=fonts.regular(23), fill=DIM)
            put(page, image, x, y + label_h)
        return y + label_h + ROW_H + gap

    y = row(head_top, "REAL SIZE", "One pixel here is one pixel in the glasses. Hover spot: the middle of the frame.",
            [("Dark window, the dot today", real["dot-dark"]), (f"Dark window, option {tag}", real[f"{tag}-dark"]),
             ("Light window, the dot today", real["dot-light"]), (f"Light window, option {tag}", real[f"{tag}-light"])])
    y = row(y, "ZOOMED 4X", "The same spots, four times larger.",
            [("Dark window, the dot today", zoom["dot-dark"]), (f"Dark window, option {tag}", zoom[f"{tag}-dark"]),
             ("Light window, the dot today", zoom["dot-light"]), (f"Light window, option {tag}", zoom[f"{tag}-light"])])
    y = row(y, "WITH TODAY'S LOOKS", "Zoomed 4x. The first four are the renderer's own cursor. The last is real pixels, enlarged, with the head tilted 3 degrees.",
            [("Dot", zoom["look-plain"]), ("Resize", zoom["look-resize"]), ("Move", zoom["look-move"]), ("Turn", zoom["look-rotate"]),
             (f"Option {tag}", zoom[f"{tag}-family"]), (f"Option {tag}, head tilted", real[f"{tag}-tilt"])])
    if option.frames:
        names = ["Frame 1: the dot", "Frame 2", "Frame 3", "Frame 4: the beam"]
        y = row(y, "ANIMATION, DARK WINDOW", "Zoomed 4x. About a tenth of a second from the first frame to the last, then the same way back.",
                [(n, zoom[f"{tag}-f{i}-dark"]) for i, n in enumerate(names)])
        row(y, "ANIMATION, LIGHT WINDOW", "Zoomed 4x.", [(n, zoom[f"{tag}-f{i}-light"]) for i, n in enumerate(names)])
    page.save(path, optimize=True)


def contact_cell(number: str, name: str, panels: list[tuple[str, Image.Image]], fonts: Fonts, cell_w: int) -> Image.Image:
    panel_w, panel_h, head = 288, 160, 92
    cell = Image.new("RGB", (cell_w, head + panel_h + 2), PAGE)
    draw = ImageDraw.Draw(cell)
    draw.text((0, 4), number, font=fonts.bold(40), fill=INK)
    draw.text((72, 10), name, font=fonts.bold(30), fill=INK)
    for k, (label, image) in enumerate(panels):
        x = k * (panel_w + 6)
        draw.text((x, 56), label, font=fonts.regular(21), fill=DIM)
        cell.paste(image, (x, head))
    return cell


def contact_sheet(cells: list[Image.Image], fonts: Fonts, args: argparse.Namespace, renderer: str, path: Path) -> None:
    columns = 2
    cell_w, cell_h = cells[0].size
    rows = (len(cells) + 2) // columns
    top, gap = 116, 30
    page = Image.new("RGB", (MARGIN * 2 + columns * cell_w + (columns - 1) * GUTTER, top + rows * (cell_h + gap) + MARGIN - gap), PAGE)
    draw = ImageDraw.Draw(page)
    draw.text((MARGIN, 26), "Text cursor, overview", font=fonts.bold(48), fill=INK)
    theme_chip(draw, page.width - MARGIN, 44, fonts, args)
    for i, cell in enumerate(cells):
        page.paste(cell, (MARGIN + (i % columns) * (cell_w + GUTTER), top + (i // columns) * (cell_h + gap)))
    x, y = MARGIN + (len(cells) % columns) * (cell_w + GUTTER), top + (len(cells) // columns) * (cell_h + gap)
    notes = ["Pick by number.", "Real size: one pixel here is one pixel in the glasses.",
             f"Desktops, pills, bars, dot: {renderer}.", "Options: mock shapes drawn over that frame, in the dot's grey and ring."]
    for i, line in enumerate(notes):
        draw.text((x, y + 8 + i * 36), line, font=fonts.regular(25), fill=INK if i == 0 else DIM)
    page.save(path, optimize=True)


# --- Main -----------------------------------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accent", help="theme colour rrggbb (default: Hyprland's active border)")
    parser.add_argument("--theme-name", help="shown on the pages (default: the active Omarchy theme)")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", help="comma-separated option numbers (a contact sheet is written only for all of them)")
    parser.add_argument("--sheet", default="contact-sheet.png", help="contact sheet file name")
    parser.add_argument("--sheet-only", action="store_true", help="write the contact sheet but no option-NN.png")
    args = parser.parse_args()
    live = args.accent is None
    args.accent = (args.accent or active_accent()).lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", args.accent):
        parser.error("--accent takes rrggbb")
    args.theme_name = args.theme_name or (active_theme_name() if live else "custom")
    if not args.binary.exists():
        raise SystemExit(f"{args.binary} is missing: build it first (README.md)")

    args.out.mkdir(parents=True, exist_ok=True)
    geo = json.loads(subprocess.run([str(args.binary), "info", args.accent], capture_output=True, text=True, check=True).stdout)
    primary = tuple(geo["scheme"]["primary"])
    W, H = geo["desktop"]
    fonts = Fonts()
    only = {int(n) for n in args.only.split(",")} if args.only else None
    chosen = [o for o in OPTIONS if not only or o.number in only]

    real_crop = centred(COL_W, ROW_H)
    zoom_crop = centred(COL_W // ZOOM, ROW_H // ZOOM)
    family_w = (PAGE_W - 2 * MARGIN - 5 * GUTTER) // 6
    family_crop = centred(family_w // ZOOM, ROW_H // ZOOM)
    sheet_real, sheet_zoom = centred(288, 160), centred(72, 40)

    with tempfile.TemporaryDirectory(prefix=".work-", dir=args.out) as name:  # raw frames, on disk: /tmp is RAM here
        work = Path(name)
        for kind in ("dark", "light", "side"):
            desktop_pixels(kind, W, H, work / f"{kind}.bgra")
        real, zoom = Batch(args.binary, args.accent, work, primary, 1), Batch(args.binary, args.accent, work, primary, ZOOM)
        for desk in ("dark", "light"):
            real.add(f"dot-{desk}", desk, None, real_crop); zoom.add(f"dot-{desk}", desk, None, zoom_crop)
            real.add(f"sheet-dot-{desk}", desk, None, sheet_real); zoom.add(f"sheet-dot-{desk}", desk, None, sheet_zoom)
        for key, look in (("plain", LOOK_PLAIN), ("resize", LOOK_RESIZE), ("move", LOOK_MOVE), ("rotate", LOOK_ROTATE)):
            zoom.add(f"look-{key}", "dark", None, family_crop, look=look)
        for o in chosen:
            tag = f"{o.number:02d}"
            for desk in ("dark", "light"):
                real.add(f"{tag}-{desk}", desk, o.shape, real_crop); zoom.add(f"{tag}-{desk}", desk, o.shape, zoom_crop)
                real.add(f"sheet-{tag}-{desk}", desk, o.shape, sheet_real); zoom.add(f"sheet-{tag}-{desk}", desk, o.shape, sheet_zoom)
            zoom.add(f"{tag}-family", "dark", o.shape, family_crop)
            real.add(f"{tag}-tilt", "dark", o.shape, family_crop, roll=TILT)  # no re-render: the glasses' own stair-steps, enlarged
            for i, frame in enumerate(o.frames):
                for desk in ("dark", "light"):
                    zoom.add(f"{tag}-f{i}-{desk}", desk, frame, zoom_crop)
                zoom.add(f"sheet-{tag}-f{i}", "dark", frame, sheet_zoom)
        real_images, renderer = real.run()
        zoom_images, _ = zoom.run()
        for o in chosen:
            tag = f"{o.number:02d}-tilt"
            real_images[tag] = real_images[tag].resize((real_images[tag].width * ZOOM, real_images[tag].height * ZOOM), Image.NEAREST)
        if not args.sheet_only:
            for o in chosen:
                option_page(o, real_images, zoom_images, fonts, args, args.out / f"option-{o.number:02d}.png")
                print(f"{o.number:02d} {o.name}: {renderer}", flush=True)
        if not only:
            def panels(tag: str) -> list[tuple[str, Image.Image]]:
                return [("Dark, real size", real_images[f"sheet-{tag}-dark"]), ("Light, real size", real_images[f"sheet-{tag}-light"]),
                        ("Dark, 4x", zoom_images[f"sheet-{tag}-dark"]), ("Light, 4x", zoom_images[f"sheet-{tag}-light"])]
            cell_w = (PAGE_W - 2 * MARGIN - GUTTER) // 2
            cells = [contact_cell("--", "The dot today (renderer)", panels("dot"), fonts, cell_w)]
            for o in OPTIONS:
                tag = f"{o.number:02d}"
                shown = [(f"Frame {i + 1}, 4x", zoom_images[f"sheet-{tag}-f{i}"]) for i in range(len(o.frames))] if o.frames else panels(tag)
                cells.append(contact_cell(tag, o.name, shown, fonts, cell_w))
            contact_sheet(cells, fonts, args, f"real compositor on {renderer.split(' (')[0]}", args.out / args.sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
