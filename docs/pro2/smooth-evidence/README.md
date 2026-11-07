# Offline evidence for docs/pro2/smoothness.md

- `prediction-root-cause-red.log`: the feed test failing on the old bridge. History fine, `horizon_ok 0`, `cap_ms 0`.
- `replay-aggregates.json`: `tools/pro2_runtime/smooth_acceptance.py` on the three private captures and a synthetic head. Aggregates only; the raw captures stay in `~/.local/state/viture-workspace/pro2-hil/`.

To regenerate, from a build with `pro2_smooth_replay`:

    python3 -m tools.pro2_runtime.smooth_acceptance --replay BUILD/pro2_smooth_replay --captures STATIONARY.jsonl DIRECTIONAL.jsonl ROLL.jsonl --synthetic

GPU tests run in fresh user, IPC and network namespaces on `renderD129` only, with a private HOME and runtime directory. `tests/xr_options_integration` and `tests/stabilization` need pytest, numpy and pillow, which `/usr/bin/python3` lacks, so they run from a private venv of that same interpreter, with `CONTROL_WIRE_CHECK`, `XR_RESTORE_GEOMETRY_BINARY` and `XR_NATIVE_REPORT_BINARY` set (`tools/xr_options_evidence/README.md`).
