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
