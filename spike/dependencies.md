# Local dependencies and qualification boundary

## Task 1 (implemented)

- CMake >=3.25 with CTest.
- A C++20 compiler and standard library (`std::span`, `std::variant`, `std::optional`).
- Python 3 standard library for diagnostic tests (the existing code requires Python >=3.10).

Verified locally: CMake 4.4.4, GNU C++ 16.2.1 (20260810), Python 3.14.9.
CMake checks the compiler and required C++20 feature level; unsupported toolchains
fail configuration/build rather than downloading replacements.

**Narrow component-scoped ruling:** the plan's global dependency list describes
future runtime components. The Task 1 layout/type test has no JSON, Wayland, GPU
or SDK dependency. It must build independently with hardware/SDK OFF. This does
not waive any later dependency or authorize a substitute JSON implementation.

## Native IPC and future components

| Component | Local prerequisite / target | Gate |
| --- | --- | --- |
| Native IPC (Task 2) | nlohmann_json >=3.12, CMake config and headers | Available after parent-authorized installation: Arch nlohmann-json 3.12.0-2, pkg-config 3.12.0; CMake discovery succeeds. Implemented and linked in Task 2. |
| Wayland/capture/presentation | Wayland client target 1.26.0, wayland-scanner, EGL 1.5, GLES 3.2, GBM, libdrm and pinned protocol XML with provenance | Recheck local versions when implementing; never private Hyprland headers. Hardware execution requires selected-session/capture approval. |
| SDK worker | Authorized matching SDK package (qualification target 2.4.0), approved absolute XR_SDK_ROOT and recorded package hash | Vendor terms/privacy approval and named-pair authorization before linking/loading. No vendor headers/binaries in Git. |

For an explicit, offline dependency check:

```sh
cmake -S spike -B build/native-spike-ipc-check -DXR_ENABLE_IPC=ON \
  -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
```

Before the parent-authorized package installation, this correctly failed with
`XR_ENABLE_IPC requires local nlohmann_json >=3.12 (nlohmann_jsonConfig.cmake)`.
Task 2 replaces the former implementation stop gate with the real `xr_ipc`
static library and `ipc_test` CTest executable. IPC is ON by default. On another
host lacking JSON, the actionable missing package error remains; an approved installation can be supplied via
`CMAKE_PREFIX_PATH` or `nlohmann_json_DIR`. Hardware/SDK ON also fail closed as
unimplemented; they do not inspect or load devices/binaries.

No configuration path installs packages, fetches network content or changes the
desktop. No real frame-rate or tracking measurements have been made in Task 1.
