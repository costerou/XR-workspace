# Text cursor mockups

Preview tooling for choosing how the overview's dot looks over text. It changes nothing in the renderer: it is a separate
CMake project next to `spike/`, nothing links it, and `spike/CMakeLists.txt` does not know about it. Whether the overview
can tell it is over text at all is in [FEASIBILITY.md](FEASIBILITY.md).

## What is real

Every frame comes from the real `GlCompositor` on surfaceless EGL (`create_offscreen`): the overview layout of the Luma Ultra
profile (three 1920 x 1200 desktops, a third of their size), the desktops' surfaces, the border, glow, pills, level bars and
corner caps. The desktops are synthetic and nothing is captured. A "today" frame is the compositor alone, with the dot and
its looks from `set_free_pointer`.

An option is not renderer code. It is a mock drawn on top of that frame, in the compositor's context, the way `gl.cpp` draws
the dot and its marks: black triangles RING px wider, then flat grey ones, on the canvas plane, under the same view and FOV,
with no anti-aliasing. `cursor_options.py` holds the shapes; its constants are `view.hpp`'s, and `check_cursor_options.py`
stops if they drift. Only option 6's halo blends, as the brighter of the two like `gl.cpp`'s glow over the grid (`GL_MAX_EXT`).

Two things in the brief are worth saying plainly. The dot has no theme colour: it is neutral grey at 170/255, 184 while held,
so only the Glow Bar's halo uses the M3 scheme. And the dot has no soft edge in the renderer: no anti-aliasing at all, so at
real size it has hard pixels, and so do these. Any softness in the glasses is the optics'.

Rendering runs on llvmpipe at low priority on four threads (`make_cursor_mockups.render_env`), so a preview uses neither GPU
and leaves the CPU to a run that may be going. The theme colour is read with `hyprctl getoption`, which only reads. Nothing
here starts, stops or talks to a run.

## Running it

```
cmake -S tools/text_cursor_mockups -B build/text-cursor -G Ninja
cmake --build build/text-cursor --target cursor_preview
python3 tools/text_cursor_mockups/check_cursor_options.py
python3 tools/text_cursor_mockups/make_cursor_mockups.py
```

`make_cursor_mockups.py` writes `option-NN.png` and `contact-sheet.png` to `~/.hermes/profiles/coder/previews/text-cursor/`.
Useful flags:

- `--accent 7aa2f7 --theme-name "Tokyo Night"`: another theme colour instead of the active one.
- `--sheet contact-sheet-tokyo-night.png --sheet-only`: only a contact sheet, under that name.
- `--only 3,5`: just those options, without a contact sheet, for working on a design.
- `--out DIR`: somewhere else.

It needs Python with `pycairo`, `numpy` and `Pillow`, the JetBrainsMono Nerd Font, Liberation Sans, and `spike/`'s build
dependencies. It imports a few helpers from `tools/handle_mockups/make_mockups.py`.

An option's page has the dot and the option over light text on a dark window and dark text on a light one, at real size and
4x; the option beside today's four looks; and the option with the head tilted 3 degrees, as real pixels enlarged, because
that is where a thin stem with no anti-aliasing shows stair-steps. In the 4x rows the frame is drawn again at four times the
resolution, so the shape is clear, and those frames do not show the hard pixels of the real size row.

## What the check measures

`check_cursor_options.py` measures real frames on flat test desktops and exits non-zero if an option breaks a limit: no wider
than the dot (16 px), no taller than the move look (35 px), within 1 px of the click point, no pixel over the 191/255 OLED cap
at the held level, and the same stem width at four sideways positions between two pixels, because the head moves the cursor by
fractions of a pixel and a width like 2.5 would flip between 2 and 3.

| Option | Size (px) | Peak | Light, dot = 100% | Covers, dot = 100% |
| --- | --- | --- | --- | --- |
| The dot today | 16 x 16 | 184 | 100% | 100% |
| 1 Serif Beam | 14 x 28 | 184 | 71% | 123% |
| 2 Dot Beam | 14 x 28 | 184 | 104% | 144% |
| 3 Hollow Pill | 14 x 30 | 184 | 96% | 117% |
| 4 Stretched Dot | 10 x 24 | 184 | 104% | 104% |
| 5 Caret Bar | 10 x 27 | 184 | 48% | 82% |
| 6 Glow Bar | 6 x 26 | 184 | 114% | 73% |
| 7 Morph Beam | 10 x 26 | 184 | 57% | 87% |
| 8 Marks Beam | 16 x 32 | 184 | 134% | 174% |

Light is measured with a blue theme colour, which is what option 6's halo is measured on. Covers counts pixels that go solid,
the black ring included, which is why a thin I-beam with serifs covers more than the dot. Those pixels are spread out, though:
the letters beside the stem stay visible.

## Adding an option

Add an `Option` to `OPTIONS` in `cursor_options.py`, built from the primitives there, and a row in `STEM_ROW` in
`check_cursor_options.py` (px below the click point, on a plain piece of stem). Keep stems a whole number of px wide.
