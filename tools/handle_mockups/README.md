# Handle mockups

Preview tooling for choosing a new look for the overview's rotate and resize handles. It changes nothing in the renderer:
it is a separate CMake project next to `spike/`, nothing links it, and `spike/CMakeLists.txt` does not know about it.

## What is real

Every frame comes from the real `GlCompositor` on surfaceless EGL (`create_offscreen`): the overview layout, the desktops'
surfaces, the border and glow, and the glass pills. The desktops in the frames are synthetic and nothing is captured.

An option's handles are not renderer code. Each is drawn with cairo in tile px (`handle_options.py`), then laid on the
desktop's surface through `view.hpp` `tile_mesh`, the path the border and glow use, with the compositor's own camera. The
dot is redrawn on top from `direction_disc` and `direction_marks`, the geometry the compositor uses. The `--` row of the
contact sheet is the compositor drawing the built level bar by itself, as a reference.

Colours come from `glass_theme_scheme()`, the call that colours the pills, so an option follows the theme as they do.
`handle_preview info <rrggbb>` prints the scheme and the layout.

Rendering runs on llvmpipe (`LIBGL_ALWAYS_SOFTWARE`), so a preview uses neither GPU. The theme colour is read with
`hyprctl getoption`, which only reads. Nothing here starts, stops or talks to a run.

## Running it

```
cmake -S tools/handle_mockups -B build/mockups -G Ninja
cmake --build build/mockups --target handle_preview
python3 tools/handle_mockups/check_options.py
python3 tools/handle_mockups/make_mockups.py
```

`make_mockups.py` writes `options-NN.png` and `contact-sheet.png` to `~/.local/share/code-bot/previews/handles/`. Useful
flags:

- `--accent 7aa2f7 --theme-name "Tokyo Night"`: another theme colour instead of the active one.
- `--sheet contact-sheet-tokyo-night.png --sheet-only`: only a contact sheet, under that name.
- `--only 3,5`: just those options, without a contact sheet, for working on a design.
- `--built [NAME]`: only the page of the renderer's own bar and caps (default `level-bar-built.png`).
- `--out DIR`: somewhere else.

It needs Python with `pycairo`, `numpy` and `Pillow`, the JetBrainsMono Nerd Font, and `spike/`'s build dependencies.

`check_options.py` measures the drawn pixels of every option and exits non-zero if one breaks a limit: at rest, no more
than 18 px past a left or right edge (desktops sit 40 px apart); no solid shape on a desktop's content; the 191/255 OLED
cap; and a hue that follows the theme on three colours with no tint of its own on a grey one.

## Adding an option

Write a function of a `Sketch` in `handle_options.py` and add it to `OPTIONS`, with where the dot sits on its hot rotate
and resize handles. Draw in tile px with `s.t` roles only; `s.corner(c)` gives a frame that fits all four corners.

## The pick

Cody picked option 10, Level Bar, and it is built: `glass.hpp` (geometry and its atlas), `gl.cpp` (the draw) and
`free_pointer.py` (the hit zones). See [Level bar and corner caps](../../docs/technical.md#level-bar-and-corner-caps). The
options here stay as mockups: they show the designs that were on the table, not what a run draws.

`make_mockups.py --built` writes `level-bar-built.png`, every frame the compositor's own with nothing laid over it: idle, a
rolled and resized desktop, each segment hovered and a corner cap hovered. `check_options.py` does not apply to it: the
renderer's own tests (`roll_level`, `glass_theme`, `glass_contract`, `gl_frame`) cover the built bar.
