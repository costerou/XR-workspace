# Native spike: offline Tasks 1–3 (partial)

C++20 layout/control parsing, a compiled native Wayland test-pattern renderer,
and Python private IPC, raw Hyprland transport, journal and watchdog/recovery.
**Task 3 managed ownership/lifecycle is blocked**, not qualified. See
[the runbook](../docs/feasibility/native-spike/runbook.md).
Offline tests never open a real display or device; native IPC uses local nlohmann_json.

Run from the repository root:

```sh
python -m unittest discover -s tests/probe -v
python -m unittest discover -s tests/native_spike -v
cmake -S spike -B build/native-spike -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike
ctest --test-dir build/native-spike --output-on-failure
```

The native renderer is `build/native-spike/xr-native-spike`; only `--help` is an
offline invocation. It requires private inherited startup/Wayland sockets for
presentation. The Python run gate intentionally does not launch it while output
ownership is unresolved. Checks remain active with
`-DCMAKE_BUILD_TYPE=Release`; no external test framework is fetched.

`xr/types.hpp` contains the shared internal contracts (not compositor/vendor ABI)
and `validate_layout(std::span<const OutputSpec>) -> Status`. Validation rejects
empty/duplicate IDs, zero unsigned dimensions/refresh and nonpositive/nonfinite
scale. It does not assign IDs or mutate layouts. The owner supplies stable UUIDs;
removing the middle element must not renumber the others. Placement relative to
existing physical rectangles is now planned offline; managed creation remains
blocked on incarnation proof. All test coordinates are synthetic.

The Python diagnostics retain up to 256 original `availableModes` entries, each
string at most 64 characters. Non-string/oversized entries become null holes so
indexes never shift; oversized strings are dropped, **not shortened**. The
additive `availableModes_status` describes collection (`ok`, `unknown`, or
`truncated`). Missing input stays absent. Parsing in `report.py` validates the
whole ASCII `WIDTHxHEIGHT@DECIMALHz` token, positive dimensions/rate and finite
rate. Report `available_modes` exposes bounded exact `raw` fields and original
`availableModes[index]` provenance, including unknown parsed fields for malformed
tokens. An incomplete list retains `unknown`/`truncated` even when some entries
are valid. Existing structured `available_modes` fixtures remain compatible;
if the original field is present it takes precedence. No token is rounded to
120 Hz for evidence. Advertisement does not establish sustained performance or
tracking quality; those remain untested. Historical reports are not rewritten.

## Permission boundaries

- No downloads, package installation, device access or SDK loading in this build.
- `XR_ENABLE_HARDWARE` and `XR_ENABLE_SDK` default OFF and fail closed if enabled:
  those components are not implemented in Task 1.
- Later dependencies and the component-scoped dependency ruling are documented in
  [dependencies.md](dependencies.md). Native JSON is locally available; IPC is
  built by default (`XR_ENABLE_IPC=ON`; layout-only also requires
  `-DXR_ENABLE_PRESENTATION=OFF`).
- Exact adapter/control contracts and offline evidence are in
  `../.superpowers/sdd/2026-10-31-viture-native-spike/task-3-report.md`.
  Raw socket transport exists, but no automatic desktop startup is authorized.
- Later desktop/capture work needs explicit selected physical session and recovery
  output approval. Input activity needs nominated-device permission. SDK work needs
  vendor-terms approval, approved absolute `XR_SDK_ROOT`, package hash and named-pair
  authorization. Mode changes require explicit rollback and separate permission.
