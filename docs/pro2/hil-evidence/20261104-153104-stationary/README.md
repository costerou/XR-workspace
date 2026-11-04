# G2 stationary capture: parent-run hardware evidence, offline analysis

Parent reports Cody's explicit authorization, ordinary-user pinned SDK diagnostic (candidate prepared at5374eb5; deployed source hash not independently captured here), network namespace and45s timeout,15s stationary session, no XR. Capture reference: parent private state directory `pro2-hil/20261104-153104-stationary`. Firmware getter status0, version30.0.00.002_20260804. Parent reports exit0, no USB1-5disconnect logged during capture and exact Pro2 still detected. These live observations are parent supplied; integrator performed only offline JSONL analysis, not a hardware session or independent live inspection.

Provenance: SDK SHA2568dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e. Private raw JSONL SHA2567d555ff784bf539f9e3e7069728a4345a8e34e7609da592daf22e8958d312211. Raw stream, private SDK stdout/stderr and parent analysis.json remain in place; none is copied or committed. Sanitized-summary.json is independently generated from stationary.jsonl with the new offline stationary module and exports aggregate/provenance fields only. It contains no raw poses, absolute host clock, serial, SDK output or USB payload.

## Independent offline result

902 valid-shape finite samples across14.977123966s receipt span; effective60.1584124Hz. Every raw SDK and host timestamp increases, zero logged issue samples. Cleanup error count0 and dropped queue count0. Quaternion norm range0.9999999596–1.0000000732. Angular distance from first sample spans0–0.5494055degrees. This is an orientation-change measurement relative to the first pose, not a drift, calibration, optical-tilt or fixture-motion attribution.

The median receipt interval15.003773ms is not enough to infer units: p95 is20.010204ms and effective mean is about16.62ms. Paired host/device analysis instead fits `SDK_delta = slope * host_elapsed_seconds + intercept` over the entire stream:

* OLS slope1,000,000,731.292 SDK ticks/host second (+0.731ppm relative to1e9).
* Endpoint slope999,996,396.771 ticks/second (-3.603ppm).
* Five disjoint approximately3s block slopes range999,987,285.371–1,000,020,170.586 ticks/second (about-12.7 to+20.2ppm).
* Detrended receipt residuals range-0.51425 to+0.51926ms. These combine device scheduling and host callback receipt behavior; they are not sensor latency or clock-accuracy bounds.

This supports a **tentative nanosecond-scale device timestamp hypothesis for this SDK/firmware/session**. It rejects using the15-million median as milliseconds/microseconds without scaling, but does not prove absolute units, epoch, wrap, reset, cross-session continuity, long-run stability, or accuracy against an independent hardware clock. No production timestamp conversion was enabled. A later adapter may test rawdelta/1e6 for millisecond ABI compatibility on replay, retaining uint64 until boundary, only after clock/reset contracts are reviewed.

## Display and scope caveat

Parent's current DP-2 readback is1920x1080@120. Immediate pretest mode was not recorded. Therefore do not claim unchanged mode, a60→120SDK change, or SDK causation from the older14:33@60inventory. Candidate invokes no display API; that source fact does not prove absence of SDK-internal effects. Next session should record immediate before/after readbacks through parent passive inspection. No modeset is requested.

G2 establishes this bounded raw vendor pose stream and cleanup on this run. **G3 mapping/frame, signed axes, optical tilt, recenter, publisher integration, rendered behavior and XR lifecycle remain unvalidated.** Short absence of logged disconnect is not a stability guarantee. Existing app/provider Pro2 activation guards remain intact. Installation is not approved by this evidence.

Reproduce offline only from repository root:

```
python -m tools.pro2_diagnostic.stationary PRIVATE_STATIONARY_JSONL
python -m unittest discover -s tests/pro2_diagnostic -v
```

17offline tests pass, including paired clocks with receipt jitter/large offsets, bad-order/payload rejection, zero quaternion handling, metadata filtering, synthetic opposite-direction hypothesis checks, original C ABI/lifecycle fixtures. No vendor library/device is loaded by analysis/tests.
