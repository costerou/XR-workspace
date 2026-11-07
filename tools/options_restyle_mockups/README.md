# Options restyle previews

Native before / after pictures of the Options pill, the options menu and Maximize, and the Step-Back Band frame by frame. It changes
nothing in the renderer: it is a separate CMake project next to `spike/`, and nothing links it.

## What is real

Every frame is `GlCompositor::draw_and_swap` on surfaceless EGL, with Cody's saved Luma Ultra layout (`harness.py REAL`, read from
`profiles.json` on 2026-11-06) or a flat row of the same sizes. The desktops are synthetic and nothing is captured. The options menu is the
real `render_options_panel`, the snapshots are the real `OptionsMenu` and `options_rows` with stub owners. "Before" comes from
`restyle_preview` built on the tree before the change, "after" from the current tree.

`r3_built.py` renders the Step-Back Band through the same compositor: each stage is the eased value the renderer would have reached.

Rendering runs on llvmpipe with `/dev/dri` hidden by bwrap and at the lowest priority, so a frame cannot open either GPU, even while XR runs.

## Running it

```
cmake -S tools/options_restyle_mockups -B build/restyle -G Ninja -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/restyle --target restyle_preview
PYTHONPATH=. /usr/bin/python3 -m tools.options_restyle_mockups.make_mockups --before /path/to/restyle_preview.before
```

For `--before`, build `restyle_preview` at the commit to compare against and copy the binary somewhere. It writes `menu/`, `maximize/`,
`index.html` and `checks.json` to `~/.hermes/profiles/coderbotxr/previews/xr-options-restyle/`.
`restyle_preview placement FILE ACCENT` dumps the placement panel in 16 states: compare two builds to prove a change left it alone.
