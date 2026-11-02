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

## Placement menu overlay

The supervisor owns the in-glasses placement menu (toggled with **Ctrl+Alt+L** during a run; Ctrl+Alt+P is pose
prediction) and sends its state as a `placement_menu` control frame whenever it changes:
`{"open": bool, "desktop": 0..2, "field": "x"|"y"|"distance"|"yaw"|"pitch"|"roll"|"curvature", "placements": [3 placements]}`.
The renderer only draws it; placements are applied by the separate `placement` frame.

- Looks like the help card (same font, sizes, padding, colours, fade). It shows the desktop tabs, the seven
  settings with values and units, the selected setting highlighted, and the in-menu keys: Up/Down, Left/Right,
  Shift for a big step, Tab, R to reset to flat, Esc to close.
- Laid out for the output size without scaling text. Smaller outputs first move the tabs beside the title and
  shorten the key hints. On scale-2 outputs it shows a window of rows around the selected setting, with arrows
  where rows are hidden.
- A frame with a malformed payload is dropped (`invalid_menu`) and the run continues. Any other malformed or
  replayed frame still ends the run.
- `capture_report` includes `placement_menu` (`visible`, `shown`, `hidden`, `renders`, `fits`).
