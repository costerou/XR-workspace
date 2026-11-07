# G3 directional: parent hardware run, independently analyzed offline

Parent states Cody authorized ONE30s G3capture and confirmed all center/left/right/up/down/center prompts, right-side-up glasses and normal physical picture. Parent verified exact35ca:1301+product, pinned SDK SHA2568dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e, no XR/plugin/units/competing driver, ordinary-user access, unshare-Urn and bounded execution. Parent's result.json independently read exit0; no private SDK output was opened. Parent bounded kernel check reports no disconnect. These are bounded observations, not general stability/support proof.

Only private directional.jsonl, cues.json, parent-result.json and display-before/after.json were read for this independent analysis; raw poses/serials/SDK stdout/stderr were never copied or committed. Parent quality/analysis files were inspected for structure, not used as substitutes for recomputation. Provenance rawcapture SHA256fbd926c5d442555adbee03f8fb7f6cc1e326cd9dcdd59e3aa7b8cfbfaa094b15; private directory suffix20261104-154541-directional. Sanitized aggregates are quality.json/directional-aggregate.json/private-replay.json here. Axes below are aggregate relative-rotation axes, not raw poses.

## Quality and timing

1805samples over29.988194210s receipt span,60.1570066996Hz. All payloads finite and timestamps strictly increasing; zero issues/drops/cleanup errors. Firmware query0:30.0.00.002_20260804. Norm range0.9999998925–1.0000000519. Parent display readbacks independently inspected DP-2:1920x1080@120 before AND after this session; physical picture normal per Cody. This supports unchanged readback for G3 specifically, not a historical mode-causation claim about G2.

Cue offsets from first pose:0.002771,5.002292,10.001051,15.019599,20.018250,25.016619s. Analyze from each actual cue+2s to the earlier of cue+5s, next cue or last sample; this excludes transitions and does not assume perfect five-second notification times. Cody confirms cue compliance; notification timestamps alone cannot establish exact human reaction/hold onset.

| Hold | Samples | Actual first-to-last settled-sample span | Rotation from initial center |
| --- | --- | --- | --- |
|center|181|2.992107s|0° reference|
|left|180|2.977105s|19.1365°|
|right|180|2.972111s|23.1973°|
|up|181|2.992098s|33.2000°|
|down|181|2.992114s|25.7005°|
|return|179|**2.957119s**|2.1007°|

Return requested bounds27.016619–30.016619s; lastrecord29.988194s truncates it to2.971576s eligible time, with2.957119s actual sample span. Do not claim a full3s final hold or a full30s first-to-last record span. Motion magnitudes are measured, not instrumented intended-angle accuracy; Cody's return need not reproduce the initial orientation exactly.

## Body/reference composition and H0/H1

Computed both body-relative `conj(q0)*q` and reference-relative `q*conj(q0)` using separately sign-aligned normalized aggregate medians. Neither replaces raw capture. Orthogonal basis change preserves rotation angles and inter-axis dot products. Left/right dot=-0.956528, up/down=-0.990972, left/up=-0.065525 (roughly opposed yaw/pitch pairs and near-orthogonal direction families). These departures from ideal±1/0 include human cross-axis motion and measurement/convention effects; they are not isolated sensor error.

Reference-relative raw axes are predominantlyY for yaw andX for pitch. H0(identity interpreted as documented NWU) gives yaw/pitch off-expected-axis fractions0.99944/0.99683/0.99361/0.99977. H1 exact pinned upstream basis(w,x,y,z)→(w,-z,-x,y) yields:

| Label | H1 reference-relative axis(x,y,z) | Off expected NWU yawZ/pitchY fraction |
| --- | --- | --- |
|left|(0.03346,-0.04748,+0.99831)|0.05808|
|right|(-0.07954,-0.24208,-0.96699)|0.25481|
|up|(0.00065,-0.99361,-0.11291)|0.11291|
|down|(0.00435,+0.99977,-0.02123)|0.02167|

Fraction is sqrt(1-axis_expected²), dimensionless, not degrees/error percentage. Right-turn cross-axis motion is material; do not hide it. Body-relative H1 fractions are0.32341/0.34169/0.13627/0.00983. Reference-relative left yaw is substantially cleaner than body-relative yaw, consistent with a tilted neutral/body frame affecting composition. It does not establish a fixed physical/optical tilt, pure yaw execution, or a correction angle.

Under standard active body-to-reference NWU conventions, left+Z/right-Z and up-Y/down+Y are sign-consistent with Cody's labels. The raw/identity interpretation does not put those two motion families on NWU yaw/pitch axes. This plus the exact upstream implementation supports **H1 as a specific OFFLINE adapter candidate**. It is not unique proof of active/passive, handedness, all-axis behavior or renderer convention: inverse conventions can be paired with other axis definitions, and no independent roll/instrumented orientation/optical measurement was captured. No production mapping selected or pitch offset inferred. No8.5° correction applied.

## Clock corroboration

Paired full-stream OLS999,972,289.814SDKticks/hostsecond; endpoint999,976,783.864;3s blocks999,927,726.569–999,994,046.788 (approximately-72.3 to-6.0ppm versus1e9). This independently corroborates the G2 nanosecond-scale hypothesis on this firmware/SDK, while receipt scheduling and oscillator differences remain. No reset/wrap/epoch or long-run contract is proven; no actual rollover/reset injected into hardware.

## Adapter and test result

`tools/pro2_diagnostic/adapter.py` is a replay-only pure Python candidate. Requires explicit replay_only=True; capture replay additionally pins model/library/firmware and clean completion. No SDK/device/process/network/IPC import or write. Applies only exact H1basis; normalizes small quaternion magnitude error/sign continuity for packet comparison, no optical pitch, neutral recenter, prediction or field calibration. At60Hz mirrors existing publisher three-sample history with repeated bootstrap samples, current/older/oldest xyzw and relative fractional milliseconds,0padding, encoded as16IEEEfloat32 values. Existing source contract: spike/driver/include/luma/publish.hpp (format/history/time), spike/src/pose.cpp (body-to-reference driverframe), upstream imu.c:60–61 basis. No Luma class/default mount_pitch/FOV is instantiated or adopted.

Explicit ns-scale assumption is limited to offline replay; timestamps retain uint64 before relative conversion. Duplicate/backward/wrap or invalid payload closes candidate rather than guessing unwrap/reset; new object required. Host receipt reversal or>250ms gap closes it (candidate-only policy, not a measured glasses threshold). begin_session resets history/epoch on an open object; close is idempotent and clears active/history. Float-time epoch resets below2^24ms as existing publisher contract requires, with history reset. Active flag means valid offline packet, **not hardware calibrated**. No recenter policy or FOV provided. This is not a runnable tracking provider or installer.

RED: test_adapter initially failed missing module. GREEN:26offline diagnostic/analysis/adapter tests passed, including local replay of all1805private samples; G2private replay also passed902samples separately. Private replay exports aggregate count/time/error/teardown only. Output max norm-squared float32 error6.35e-8; no IPC publication. App acceptance8/providerguard7/controller534/nohook7 passed (logs here). No render/GPU/live tests run; no native code/ABI/production behavior changed. Existing per-model settings and Luma/Beast provider behavior untouched.

## Next exact approval gate

**No install/XR/guard removal yet.** Smallest missing directional evidence: one separately parent-authorized bounded pure roll-left/center/roll-right/center session with right-side-up glasses, explicit labels and minimal yaw/pitch, using the same raw SDK candidate/noXR/network/timeout/display-preservation boundaries. Confirm H1roll maps raw-Z→NWU+X with signed behavior consistent with user roll and test active/passive interpretation across all three axes. Include a comfortable center/reference return; do not infer optical tilt from it.

After that, review the candidate convention and timestamp policy, replay into native PoseState/publisher contract (private IPC only if separately approved; no running compositor), establish FOV/recenter/stale behavior and bounded SDK teardown ownership, then parent requests a specifically reviewed adapter installation and supervised XR lifecycle. Optical tilt needs a physical/instrumented or supervised visual reference and remains separately unvalidated; never silently borrow Luma correction. If roll/axis convention conflicts, retain guard and design the one smallest discriminating reference experiment rather than expanding live scope automatically.
