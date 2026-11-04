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
| `XR_ENABLE_LUMA_DRIVER` | ON | Our own Luma Ultra head-tracking driver in `driver/`. Needs libc only. Tests never open a device. |
| `XR_ENABLE_HARDWARE`, `XR_ENABLE_SDK` | OFF | Not implemented; fail closed if enabled. |

Dependencies are listed in [dependencies.md](dependencies.md); the vendored
protocol XML provenance is in [protocols/PROVENANCE.md](protocols/PROVENANCE.md).

Side-by-side 3D (depth-based warp of each desktop, off until something calls `GlCompositor::set_stereo`): [docs/stereo-render.md](../docs/stereo-render.md).

## Capture and frame delivery benchmarks

`spike/bench/` measures the capture path offline. It never touches the desktop, the glasses or XRLinuxDriver.

- `capture_bench` times the CPU pixel paths (GPU-less composition, animated test pattern, the overview geometry each
  main-loop wake) against the previous implementations kept in `bench/reference_pixels.hpp`, and fails if any pixel
  differs.
- `capture_e2e_bench` runs the real `xr-native-spike` against a loopback Wayland compositor in the same process. The
  compositor has one presentation output, `XR-1..3` capture sessions, presentation feedback, linux-dmabuf v4 for
  Mesa, and a simulated 120 Hz vblank. Every capture frame carries a stamp, which is read back from the committed
  buffer to time capture→present. Modes: `cpu`, `gl-shm`, `gl-dmabuf`, `pattern`. `--tracking` runs the renderer
  under `bwrap` with a private `/tmp` and SysV IPC, so the pose feed is absent but every frame callback redraws, as
  in the glasses. `--malloc-shim build/libxr_malloc_count.so` counts allocations; add `--malloc-trace FILE` and
  read it with `bench/malloc_report.py`.
- `bench/compare.py --bench … --before OLD --after NEW` alternates two renderer builds and prints medians.
- ctest `capture_e2e_{cpu,pattern,gl-shm,gl-dmabuf}` are 1.5 s smoke runs: frames flow, captured frames reach the
  screen on the expected path, and the renderer exits 0. The GL ones skip (77) without a render node.
- ctest `capture_e2e_churn_{bounce,resize}_{cpu,gl-shm,gl-dmabuf}` replay what a Hyprland layout change or config
  reload sends to the capture sessions (`--constraint-churn`): `bounce` resends constraints as another size and straight
  back in one flush (as a reload does to each XR-n output), `resize` really changes the size. Both fail if a desktop
  that had shown a frame ever shows none, and `bounce` fails if a desktop's capture buffers were re-created at all
  (`reallocations` in the renderer's capture report). See `CaptureBuffers` in `include/xr/capture.hpp`.
- ctest `capture_e2e_maximize_{1,2,3,4}_gl-dmabuf` and `capture_e2e_maximize_4_gl-shm` run four desktops and send the
  supervisor's frames for maximizing XR-N (`--maximize N`). They fail unless every frame after the zoom lands has XR-N's
  own frame in the middle of the view: each stamp remembers its desktop. GL modes only, since CPU composition has no zoom.

These are a model of the compositor, not Hyprland. They do not show glasses latency or Hyprland's scheduling.

## Glass placement pills

In the overview each desktop gets a glass pill under it (a Material Design 3 tonal capsule) (`include/xr/glass.hpp`, `src/glass.cpp`). The
supervisor owns the state and the hit testing (`tools/native_spike/free_pointer.py` mirrors the geometry; the
`glass_contract` test compares the two) and sends two display-only control frames:

- `move_handle` `{"visible": bool, "desktop": 0..2, "hover": bool, "active": bool}`: `visible` shows a pill under every
  desktop; `desktop` with `hover` or `active` is the one under the pointer or being dragged, drawn brighter.
- `placement_menu` `{"open": bool, "desktop": 0..2, "field": "x"|"y"|"distance"|"curvature",
  "placements": [3 placements]}`: `open` expands that desktop's pill into its placement panel (a click on the pill or
  **Ctrl+Alt+L** for the selected desktop; Ctrl+Alt+P is pose prediction); `open: false` collapses it. Only one is open.
  With `open`, an optional `"surface": placement` (sent while the free pointer holds one of the panel's sliders: the
  desktop's placement at the press) is the placement the open panel is drawn on, while the desktop follows its live
  placement; the supervisor measures the drag on the same surface, so the knob stays under the pointer.

The renderer only draws them; placements are applied by the separate `placement` frame. A placement is exactly four
integers, `{"x", "y", "distance", "curvature"}` (an object with any other key, such as the removed `yaw`, `pitch` or
`roll`, is rejected): `x` / `y` are an orbit round you in tenths of a degree (`include/xr/view.hpp` `Placement`;
`tools/native_spike/orbit.py` mirrors `place_point`, the `placement_contract` test compares the two), shown on the
panel in whole degrees; `distance` is percent of the slot's distance, `curvature` degrees of arc. Every desktop always
faces you (a billboard toward the eye: at its slot, while dragged and anywhere round you it is square to the line of
sight and upright along its meridian), so there is no facing setting; a centred desktop at its slot is still exactly
the canvas plane, an off-centre one turns toward you about its centre. While zoomed in, every orbit fades back to its
row slot and the zoomed desktop comes flat in front of you (`zoomed_placement`).

- Geometry, in canvas px on the desktop's own surface (it faces you with its desktop; a curved desktop curves its
  pill): the pill is 24% of the desktop's width (64–160 px) by 14 px, centred 10 px under it, which is 4 px under the
  selected desktop's 6 px border. A resize keeps that 4 px as seen, because the border grows with the desktop and the pill
  does not (`view.hpp glass_to_desktop`). The panel is 480×220 with
  18 px corners, centred under the desktop with its top at the pill's top. Its 42 px top strip is still the pill (drag
  to move, click to collapse); below it, one 34 px row per field (Left / right, Down / up, Distance, Curvature) with
  label, slider and value, then a key-hint footer.
- Look: Material Design 3 (`material.hpp`) in an M3 dark scheme seeded by the theme's border colour (`glass_theme.hpp`):
  the pill a flat tonal capsule in the primary container at elevation 1, the open panel a surface container with an
  outline-variant edge at elevation 3, from one GLSL fragment shader (`glass_fragment_shader`) with a CPU mirror
  (`glass_shade`). Hover and drag lay M3's state layer over it (8%, 16%) and lift it. Every role stays at or under
  `kCardMaxLevel` (191/255) for the micro-OLED panels.
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
  EGL/GLES), `glass_contract` and `placement_contract`. Offline only; not yet tried in the glasses.
