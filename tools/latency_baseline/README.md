# latency_baseline

Phase 0 baseline of the Rust rebuild ([plan](../../docs/rust-rebuild-plan.md), section 4): pose age and
read-to-present p50/p95 of one live run, read-only, from the renderer's `managed_output_capture` exit report in the
run's journal (`journalctl --user -u viture-xr-<run_id>`).

```sh
python3 -m tools.latency_baseline --latest --out docs/evidence/latency-baseline/luma-YYYY-MM-DD.json
```

`--latest` picks the newest finished run whose consent has `tracking: "luma"`. Exit 0 is PASS: a Luma run whose
renderer ran at least 30 minutes (`--min-minutes`), on a CLOCK_MONOTONIC compositor clock, with read-to-present and
pose age samples. Exit 2 is INCONCLUSIVE, with the reasons in the record.

What the numbers are:

- `read_to_present_ms`: pose read to the compositor's presented timestamp. Software time only; not motion-to-photon.
- `pose_age_ms` (`basis: relative`): host read time minus the pose's device time, above the smallest such value in the
  last 8 s of device time. It is not an absolute age: the device and host clocks have an unknown offset.
- Both are whole-run histograms in 0.25 ms buckets. There is no p99 in the report.

The renderer writes the report only when it exits cleanly. A renderer that crashed has none, and a run that ended early
has a short one; run again. A respawned renderer leaves one report per life, and the longest is the measurement.
