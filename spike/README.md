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
