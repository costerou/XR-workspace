# G3 parent-only directional gate after stationary G2

Status: G2 raw logging succeeded for the parent-run session (see hil-evidence/20261104-153104-stationary). **No direction session, install or guard removal authorized to integrator.** Parent is separately obtaining Cody consent. Continue with the same pinned candidate, library and ordinary-user/noXR/network-namespace boundaries; analysis changes do not change capture or SDK operations.

## Exact finite scope for parent's consent

One30s vendor Gen2 pose60 stream with firmware getter, small left/right/up/down motions and final center return, followed by close/stop/shutdown/destroy. No calibration/reset/optical correction, raw/HID protocol, display controls, firmware update, Luma command, XR launch or install. Parent checks no competing provider, no other VITURE device and no topology changes. Parent passively records **immediate** display readback before and after; preserve current mode (currently reported1080p120), do not force older60Hz baseline.

After consent only, from the reviewed source root:

```
umask 077
unshare -Urn -- timeout --signal=INT --kill-after=5s 60s python -m tools.pro2_diagnostic.candidate --parent-authorized-sdk-session --seconds 30 --output PRIVATE_NEW_DIRECTIONAL_JSONL 2>PRIVATE_NEW_DIRECTIONAL_STDERR
```

Use new exclusive filenames. Capture keeps SDK stdout private too, as parent did for G2. No raw SDK output/poses are committed. If timeout/cleanup fails, record failure, stop experiment and follow parent recovery review; no automatic retry/reset/replug/update. Explicit timeout cannot guarantee graceful cleanup inside a hung proprietary SDK.

## Timing and labels

Receipt time of the **first pose** is t=0, not command launch. SDK initialization duration is variable. Parent should watch the private JSONL, coordinate a timer with first pose, and record actual motion/hold times separately if timer start is delayed. If timing cannot be aligned, the fixed-window analyzer must not interpret mislabeled motion; mark inconclusive and use parent-confirmed host-monotonic labels in a later offline analysis.

| Receipt-relative time | Cody action | Analyzer settled hold |
| --- | --- | --- |
|0–5s|Comfortable forward center, upright|2–5s baseline|
|5–10s|Small left turn, hold|7–10s|
|10–15s|Small right turn, hold|12–15s|
|15–20s|Small upward look, hold|17–20s|
|20–25s|Small downward look, hold|22–25s|
|25–30s|Return to original comfortable center|27–30s|

No forced angle or long motion: approximately10–15degrees if comfortable, parent marks actual intent and unintended roll. User comfort overrides timing. Avoid interpreting transition samples. Six settled windows should each contain around180samples near60Hz; software minimum10 is only a malformed/incomplete-data floor, not sufficient quality evidence. A desktop timer/observer's explicit labels are preferred to guessing from waveform peaks. If final27–30s hold is truncated, report actual span and do not assert a full three-second return window.

## Offline comparisons and bounded adapter hypotheses

```
python -m tools.pro2_diagnostic.stationary PRIVATE_NEW_DIRECTIONAL_JSONL
python -m tools.pro2_diagnostic.analyze PRIVATE_NEW_DIRECTIONAL_JSONL
```

The first report's orientation-change metric now describes deliberate motion, not stationary jitter. Both reports preserve unknown timestamp/frame labels. The directional analyzer reports sign-aligned normalized comparison medians and `q_center^-1 * q_segment`, separately from untouched raw capture. It also lists **unselected analysis-only** hypotheses:

| Hypothesis | Analysis operation | Evidence needed |
| --- | --- | --- |
|H0 documented SDK NWU|Keep rawrelative wxyz|Yaw pair should occupy one opposed axis and pitch pair a distinct opposed axis; test against documented Z-up/Y-west axes and observed transform direction. Do not assume signs before matching user labels.|
|H1 pinned upstream EUS→NWU|Map relative(w,x,y,z) to(w,-z,-x,y), source imu.c:60–61|This exact basis permutation is upstream code, not approved device truth. Check whether it improves axis agreement or adds an erroneous second conversion. No live application.|
|H2 constant pitch/optical correction|No operation implemented|Relative motion cannot establish a fixed optical mounting tilt. An upright instrumented/optical reference is needed; do not copy-8.5degrees or infer it from neutral Euler pitch.|

For H0, NWU would nominally place physical yaw around Z and pitch around Y, subject to active/passive orientation and body/world convention. Under H1, rawY becomes mappedZ and rawX becomes mapped-Y. Those are discriminating expected components, not proof of which frame actually arrives. Compare left/right relative vector directions (opposition), up/down opposition, yaw-vs-pitch separation/cross-axis leakage, sensible magnitude and final center agreement. q and -q are equivalent; check hemisphere before interpreting a sign. Publish only aggregate axis/angle/quality/provenance, never private raw data. No automatic winning hypothesis or renderer mapping is selected by this tool.

Separately evaluate paired device/host clock fits over held and moving windows; check no reversals, duplicates, reset, discontinuity, nonfinite samples or quaternion norm fault. G2supports approximately1e9rawticks/hostsecond for its15s run, not a universal SDK time contract. Compare cross-session timestamp starts privately to test reset/continuity; no absolute timestamp need be published. Timestamp wrap/long-run behavior remains untested by two short captures.

## Decision after the capture

If timing/labels and opposed distinct axes are clear, document the observed transform direction and supported time scale for this pinned SDK/firmware, then implement a separate replay-tested adapter into the unchanged publisher ABI. Test yaw/pitch/roll/recenter contracts, timestamp reset/wrap/staleness and bounded teardown/reconnect before parent reviews installation. Any candidate mapping remains behind the current guard until explicit review/activation work; a four-direction pass alone is not rendered XR/optical validation. If both hypotheses are ambiguous, record exact ambiguity and propose one additional bounded single-axis/reference experiment; do not silently choose the older model transform. No indefinite vendor response is required to review the captured observations.
