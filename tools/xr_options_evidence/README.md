Offline evidence collection
===========================

Run from the repository root (requires pytest and the normal native C++ build dependencies):

```
cmake -S spike -B /tmp/xr-options-qa-build -DBUILD_TESTING=ON
cmake --build /tmp/xr-options-qa-build --target control_wire_check -j2
python tools/xr_options_evidence/collect.py --output /tmp/xr-options-evidence --decoder /tmp/xr-options-qa-build/control_wire_check
```

Each run needs a new output directory. The manifest records exact test commands,
exit codes, HEAD and decoder hash; logs retain failures. A missing decoder is a
failure, never a silent native-test skip. Existing recovery/input suites are reused,
not copied. `--suite recovery` and `--suite input_geometry` select those bundles.
A green legacy suite does not establish options-specific integration.

Supply native-owner PNG/MP4 with repeated `--artifact /absolute/path` arguments.
The collector hashes supplied files and checks container signatures only. It does
not create renderer captures, assert native provenance, inspect visual correctness,
or invoke any live compositor, XR device, service, installer or configuration tool.
HTML design prototypes cannot stand in for native evidence. Record native capture
commands/build/scene provenance separately with the owner's artifacts.

Integration suite uses the contract's adapter signature and the renderer's real
control decoder. OptionsMenu constructor and geometry helper API were unspecified;
lead must coordinate those tests with owners rather than inventing a second owner.
Coverage ledger keeps all 116 rows pending until integrated route-specific evidence
is linked. V2 maximize is an additional gate, not an invented inventory row.

For offline native pose, stabilization, transformed glass geometry and transport
retry/backoff regressions, build and include these existing targets:

```
cmake --build /tmp/xr-options-qa-build --target pose_test stabilization_test view_test glass_test luma_transport_test -j2
```

Then add `--native-build /tmp/xr-options-qa-build` to the collector command. Its
ctest allowlist selects only those five existing offline tests. Native captures
remain the renderer worker's responsibility. The `maximize_rect(tile)` test uses
that proposed supervisor helper name; reconcile naming with the lead at integration.

V4 typed native report and actual Restore geometry
------------------------------------------------

Build the CPU serializer against the matching existing native build:

```
python tools/xr_options_evidence/build_native_report.py --build build/integration-release --output /tmp/xr-qa-native-report
```

This uses actual `VideoDefaults::report()` with sharp/deband requested. It creates
no player, VideoMode, GL context, compositor or device. When source/build live in
a different existing checkout, pass `--source-root /absolute/matching/checkout`.
No CMake or production file is changed. The compiled executable prints actual
native off-state JSON, including null applied properties.

The integration suite requires these explicit CPU-native test artifacts:

```
CONTROL_WIRE_CHECK=/absolute/build/control_wire_check
XR_RESTORE_GEOMETRY_BINARY=/absolute/build/restore_band_test
XR_NATIVE_REPORT_BINARY=/tmp/xr-qa-native-report
```

For `collect.py` use `--decoder`, `--restore-geometry`, and `--native-report` to set
them. Missing artifacts fail the release gate, never silently skip it. Actual
Restore meshes come from the production native serializer fixture, then traverse
the real sampler/input path; no reachable mesh is invented in QA. The native
report test traverses strict Supervisor status validation and real VideoControl
completion. Stateful report fakes elsewhere assert schema acceptance immediately
before exercising desired/applied/error predicates.

Use the lead-owned `run_release.py` for final isolation and GPU-FD enforcement.
`collect.py` remains an explicit offline evidence collector; it does not establish
final release-equivalence by itself. A passing per-route JUnit record stays partial
until its C1/preserved input, typed readback/failure and cleanup gates pass review.
