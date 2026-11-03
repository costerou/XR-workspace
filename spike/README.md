# Native renderer and ownership plugin

C++20. Builds:

- `xr-native-spike`: Wayland layer-shell renderer (EGL/GLES, dmabuf capture, HUD).
- `libxr-ownership.so`: ABI-pinned Hyprland plugin that owns the virtual outputs.
  Only built with `-DXR_ENABLE_OWNERSHIP_PLUGIN=ON`, and never auto-loaded.

```sh
cmake -S spike -B build/native-spike -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/native-spike
ctest --test-dir build/native-spike --output-on-failure
```

Tests never open a real display or device. Checks stay active in Release builds.

## Options

| Option | Default | Notes |
| --- | --- | --- |
| `XR_ENABLE_PRESENTATION` | ON | Renderer. Needs wayland-client, wayland-protocols, EGL/GLES, gbm, libdrm, freetype2, fontconfig. |
| `XR_ENABLE_IPC` | ON | Native IPC (local nlohmann_json). |
| `XR_ENABLE_OWNERSHIP_PLUGIN` | OFF | Hyprland plugin. Needs hyprland and aquamarine dev headers. |
| `XR_ENABLE_HARDWARE`, `XR_ENABLE_SDK` | OFF | Not implemented; fail closed if enabled. |

Dependencies are listed in [dependencies.md](dependencies.md); the vendored
protocol XML provenance is in [protocols/PROVENANCE.md](protocols/PROVENANCE.md).

## Glass placement pills

In the overview each desktop gets a frosted-glass pill under it (`include/xr/glass.hpp`, `src/glass.cpp`). The
supervisor owns the state and the hit testing (`tools/native_spike/free_pointer.py` mirrors the geometry; the
`glass_contract` test compares the two) and sends two display-only control frames:

- `move_handle` `{"visible": bool, "desktop": 0..2, "hover": bool, "active": bool}`: `visible` shows a pill under every
  desktop; `desktop` with `hover` or `active` is the one under the pointer or being dragged, drawn brighter.
- `placement_menu` `{"open": bool, "desktop": 0..2, "field": "x"|"y"|"distance"|"yaw"|"pitch"|"roll"|"curvature",
  "placements": [3 placements]}`: `open` expands that desktop's pill into its placement panel (a click on the pill or
  **Ctrl+Alt+L** for the selected desktop; Ctrl+Alt+P is pose prediction); `open: false` collapses it. Only one is open.
  With `open`, an optional `"surface": placement` (sent while the free pointer holds one of the panel's sliders: the
  desktop's placement at the press) is the placement the open panel is drawn on, while the desktop follows its live
  placement; the supervisor measures the drag on the same surface, so the knob stays under the pointer.

The renderer only draws them; placements are applied by the separate `placement` frame.

- Geometry, in canvas px on the desktop's own surface (a tilted or curved desktop tilts and curves its pill): the pill
  is 24% of the desktop's width (64–160 px) by 14 px, centred 10 px under it. The panel is 480×322 with 18 px corners,
  centred under the desktop with its top at the pill's top. Its 42 px top strip is still the pill (drag to move, click
  to collapse); below it, one 34 px row per field with label, slider and value, then a key-hint footer.
- Look: a dark translucent tint in the theme colour (`glass_theme.hpp`), lighter toward the top, a top gloss, a 1 px hairline
  brighter at the top, rounded corners and a soft drop shadow, from one GLSL fragment shader (`glass_fragment_shader`)
  with a CPU mirror (`glass_shade`). Hover and drag brighten it. Every pixel stays at or under `kCardMaxLevel`
  (191/255) for the micro-OLED panels, and the panel text uses the help card's dimmed colours.
- Animation: the pill morphs into the panel in 260 ms and back in 200 ms, eased; the panel's text fades in over the
  last quarter. An expansion is held while the view is zoomed in or still zooming out (`GlassPills::frame`), so a
  panel opened from a zoomed-in view morphs in full once the overview is reached. Nothing moves on its own.
- Drawn only in the overview (any zoom hides them), back to front with the open panel's desktop last (the supervisor
  hit-tests the open panel first). An open panel is drawn even when `move_handle.visible` is false. The CPU fallback
  renderer draws none of it.
- A frame with a malformed payload is dropped (`invalid_menu`, `invalid_move_handle`) and the run continues. Any other
  malformed or replayed frame still ends the run. The old head-locked menu card, its `menu_pointer` op (now rejected)
  and its `menu_geometry` status line are gone.
- `capture_report` includes `placement_menu` (`open`, `desktop`, `shown`, `hidden`, `renders`).
- Tests: `glass` (geometry, look, panel content, animation and its hold until the overview, draw order, a held
  slider's surface), `glass_render` (shader against its CPU mirror on a surfaceless EGL context; skipped without
  EGL/GLES) and `glass_contract`. Offline only; not yet tried in the glasses.
