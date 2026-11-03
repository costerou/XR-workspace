# Handle mockups

Preview tooling for choosing a new look for the overview's rotate and resize handles. It changes nothing in the renderer:
it is a separate CMake project next to `spike/`, nothing links it, and `spike/CMakeLists.txt` does not know about it.

## What is real

Every frame comes from the real `GlCompositor` on surfaceless EGL (`create_offscreen`): the overview layout, the desktops'
surfaces, the border and glow, and the glass pills. The desktops in the frames are synthetic and nothing is captured.

The handles are not renderer code yet. Each option is drawn with cairo in tile px (`handle_options.py`), then laid on the
desktop's surface through `view.hpp` `tile_mesh`, the path the border and glow use, with the compositor's own camera. The
dot is redrawn on top from `direction_disc` and `direction_marks`, the geometry the compositor uses. The `--` row of the
contact sheet is the compositor drawing today's brackets by itself, as a reference.

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
- `--out DIR`: somewhere else.

It needs Python with `pycairo`, `numpy` and `Pillow`, the JetBrainsMono Nerd Font, and `spike/`'s build dependencies.

`check_options.py` measures the drawn pixels of every option and exits non-zero if one breaks a limit: at rest, no more
than 18 px past a left or right edge (desktops sit 40 px apart); no solid shape on a desktop's content; the 191/255 OLED
cap; and a hue that follows the theme on three colours with no tint of its own on a grey one.

## Adding an option

Write a function of a `Sketch` in `handle_options.py` and add it to `OPTIONS`, with where the dot sits on its hot rotate
and resize handles. Draw in tile px with `s.t` roles only; `s.corner(c)` gives a frame that fits all four corners.

## What a picked option still needs

A picked design has to be built in the renderer, and its shapes have to replace the rotate zone in `free_pointer.py`
(`on_roll`) and its mirror in `glass.hpp`, with the `glass_contract` ctest and `test_pointer_zones.py` following. These
mockups do not change any hit zone, and how the handles read on the OLED panels can only be judged in the glasses.
