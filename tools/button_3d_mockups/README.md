# 3D button mockups

Preview tooling for choosing the 3D toggle next to the level bar above each desktop in the overview. It changes nothing in
the renderer: it is a separate CMake project next to `spike/`, nothing links it, and `spike/CMakeLists.txt` does not know
about it. It reuses `tools/handle_mockups` (Sketch, page parts, synthetic desktops) and reads `free_pointer.py` and `view.py`
without changing them.

## What is real

- Every option frame is the real `GlCompositor` on surfaceless EGL (`create_offscreen`): the Luma Ultra overview layout
  (three 1920 x 1200 desktops), the border, glow, pills, level bars and corner caps. The desktops are synthetic and nothing
  is captured.
- A button is not renderer code. It is drawn with cairo in tile px (`button_options.py`), then laid on its desktop's surface
  through `view.hpp` `tile_mesh`, the path the border and glow use, so it rolls and scales with its desktop. The dot comes
  from `direction_disc`. Its size and place start from the real bar (`free_pointer.level_bar_rect`).
- Colours are `glass_theme_scheme()`'s, the call that colours the pills, so an option follows the theme as they do.
- Help card: `render_help_card` itself, real font, with the real rows from `view.py` and the key added.
- Panel: the compositor's own open panel. One row of its glass is stretched by a row's height and the proposed row is laid
  on it, with `glass.cpp`'s metrics and colours (the Background row's). So the row itself is a mock on a real panel.

Rendering runs on llvmpipe at low priority on four threads, so a preview uses neither GPU. Nothing here starts, stops or
talks to a run.

## Running it

```
cmake -S tools/button_3d_mockups -B build/button3d -G Ninja
cmake --build build/button3d --target button_preview
python3 tools/button_3d_mockups/check_button_options.py
python3 tools/button_3d_mockups/make_button_mockups.py --accent 8d8d8d --theme-name Vantablack
python3 -m unittest discover -s tests/button_3d_mockups -p 'test_*.py'
```

`make_button_mockups.py` writes `option-NN.png`, `contact-sheet.png` and `panel-and-help.png` to
`~/.hermes/profiles/coder/previews/3d-button/`. Without `--accent` it asks Hyprland for the theme colour, which needs a shell
that can reach it. Flags: `--accent`, `--theme-name`, `--only 2,6`, `--sheet NAME --sheet-only`, `--extras-only`,
`--recommend TEXT`, `--out DIR`.

`check_button_options.py` measures the drawings and exits non-zero if one breaks a limit: the real hit zones (bar, edge band,
corners, and the narrowest desktop it still fits), no solid pixel on content or past a side edge, the 191/255 OLED cap, the
theme's hue on three colours and no tint on grey, that off, on, hover and global differ on the pixels, and 3:1 label contrast.
The tests break each limit on purpose, so a check that cannot fail would show.

## Adding an option

Write a function of a `Sketch` and a `Frame` in `button_options.py` and add it to `OPTIONS`, with the rects its controls
take. Use `s.t` roles and `Geo` lengths only.
