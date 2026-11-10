# Standalone Pro 2 Gen2 SDK diagnostic candidate

632503d is a safety integration, **not working Pro 2 support to install**. This separate tool path prepares the next finite experiment without changing the installed setup, app guards, XR lifecycle, display, Luma parser or publisher. Candidate is offline-tested, not hardware-qualified. No vendor response is required to conduct the bounded diagnostic after parent approval; observations will determine the next adapter contract.

## Design and API evidence

Chosen approach: Python standard-library ctypes binding to the existing pinned vendor C ABI, plus a compiled synthetic C SDK fixture. This keeps the diagnostic independent of xrDriver's conversion/pitch stage and avoids a new driver install. An upstream xrDriver run would mix raw SDK evidence with disputed transforms; a custom HID parser would require unapproved protocol assumptions. Neither is used.

[Official SDK reference](https://www.viture.com/developer/glasses-sdk/glasses) identifies Pro 2 as Gen2, specifies lifecycle and pose callbacks. Local header/source hashes and locations are in diagnostic-evidence/header-source-manifest.json. Relevant declarations: viture_glasses_provider.h lifecycle/create, Gen2=1, product/rate queries; viture_device.h callback float[7], uint64 timestamp, open/close IMU; viture_protocol_public.h pose mode1, frequency index0=60Hz, get_glasses_version. Source viture.c initialization/open/stop stages were inspected separately. Headers are evidence for the ABI, not proof that the installed binary was built from that source.

Installed pin: `/usr/lib/xr-driver/libglasses.so`, SHA256 `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e`, package xr-driver-git2.11.9-1. Exact symbols and readelf dependencies are checked statically in diagnostic-evidence. Dependencies: libudev.so.1, libstdc++.so.6, libm.so.6, libgcc_s.so.1, libc.so.6, ld-linux-x86-64.so.2; RUNPATH $ORIGIN. Standard Python3 (no pytest/numpy/runtime install needed). No new SDK download required for this candidate. A changed hash requires review, not automatic repinning.

Before SDK loading: read sysfs text only, reject zero/multiple VITURE devices, any PID/product mismatch or incomplete evidence; require exact35ca:1301 + `VITURE PRO 2 XR Glasses`. CLI requires explicit parent-authorized-session flag. SDK is loaded from a held O_NOFOLLOW, hash-checked file descriptor to prevent a pathname replacement between hash and dlopen. No default import loads it. Test-only dependency injection substitutes the simulated .so; CLI exposes no library override.

The loaded SDK's exact-PID and pose60 capability queries run before create. These are documented PID queries without a handle, distinct from physical-device getters. Then recheck identity and use only:

1. create(0x1301); check nonnull and get_device_type()==Gen2.
2. initialize(handle,NULL,NULL), then start(handle), checking every result.
3. register_imu_pose_callback before open; no raw/Carina callback, DOF/reset, display or calibration call.
4. get_glasses_version into a bounded buffer: record return status/version. Query failure is evidence, not a firmware update instruction.
5. open_imu(handle,POSE=1,LOW=0), corresponding to capability-checked60Hz. This starts device streaming; no guessed bytes or retries.
6. Copy each callback's seven float fields and uint64 timestamp into a bounded queue. Main thread logs raw Euler+scalar-first quaternion, receipt monotonic_ns and validation issues; no SDK lifecycle call from callback.
7. close_imu(handle,POSE), stop, shutdown, destroy, in order. Try every cleanup stage even when one fails; record errors and fail session. Initialize/start failure also attempts stop/shutdown/destroy per lifecycle guidance. No automatic retry/fallback.

No wire packet layout is inferred. The public callback has no length field: seven floats are the ABI contract; a bad native pointer cannot safely be length-validated in Python. The synthetic fixture verifies declared ABI/order, not vendor memory safety. Unexpected type/length/nonfinite/norm or nonincreasing timestamp is flagged; original quaternion and timestamp are retained. Quaternion norm tolerance0.05 is a diagnostic quality threshold, not calibration. No raw capture quaternion normalization/sign changes. Zero/invalid/dropped samples fail CLI completion.

Timestamp **units, epoch and wrap remain unknown**. Raw uint64 preserved; host time is separate. Official documented frame is NWU; observed frame remains unverified. Upstream converts EUS→NWU and applies a pitch adjustment in other stages; neither stage is invoked or replicated. Offline compare only sign-aligns/normalizes medians for mathematical relative-quaternion comparison, explicitly separate from raw records, without selecting renderer axes or pitch correction.

## Concrete parent authorization request: first stationary session

No command below was executed against hardware. Parent reviews this tool, hashes and evidence, confirms no existing SDK/xrDriver/Luma process can claim glasses, and obtains Cody consent for **one 15-second stationary vendor SDK stream plus firmware getter and clean stop**. This authorizes active SDK initialization/USB commands, not merely a read-only log. No install, XR, display switch, firmware update, calibration/reset or axis movement is included. Ordinary-user access only; no sudo, udev changes or interface forcing.

Parent keeps glasses stationary, current1080p physical image/mode unchanged, other VITURE devices absent, ordinary desktop available. Require unprivileged network namespace support; no network is needed or allowed for this diagnostic. Do not remove the namespace restriction to make a failed command work.

From reviewed source root, parent may authorize this exact command (replace output with a new private filename; umask0077 first):

```
umask 077
unshare -Urn -- timeout --signal=INT --kill-after=5s 45s python -m tools.pro2_diagnostic.candidate --parent-authorized-sdk-session --seconds 15 --output /tmp/pro2-stationary.jsonl 2>/tmp/pro2-stationary.private.stderr
```

`--rate 60|90|120|240` picks the SDK pose rate (codes 0 to 3; default 60). The capability check and the open both use it, and a rate the SDK refuses stops the capture. 240 is for the D12 replay ([smooth-live-test.md](smooth-live-test.md), Run C).

Output uses exclusive creation,0600; never overwrite a prior capture. Keep SDK stderr private: vendor initialization may fetch a serial internally and vendor errors may include identifiers. No stat-reporter API is called. SDK internals/cache writes are not proven absent; NULL config/cache follows official instructions. Namespace isolates network, not USB enumeration/filesystem. SDK handle takes PID rather than a physical port, so recheck is not atomic hotplug confinement. Parent must prevent topology changes/competing providers during the finite experiment; this tool makes no general device-confinement claim.

Pass to next gate only if log has exact contract, firmware status (unknown version recorded honestly), fresh finite plausible unit-quaternion data, strictly increasing timestamps, no drop/cleanup error, process exited and plain display still works. Examine raw timestamp deltas versus receipt intervals to establish a tentative scale; do not assert nanoseconds from upstream division alone. If no pose, capability/open failure, firmware getter failure associated with streaming error, disconnect or cleanup hang: stop, preserve private evidence, no auto-repeat/update. Failure of firmware getter alone does not prove stream incompatibility; parent reviews bounded valid pose evidence before next gate.

A45s wall timeout bounds initialization/stream/teardown; INT requests cleanup. If vendor code hangs, hard kill5s later cannot guarantee graceful hardware shutdown. Do not reset/clear-halt/detach/replug automatically. This is an SDK lifecycle risk to approve, not an offline guarantee. Parent reviews process exit and device presence before another session.

## Second separately authorized session: four-direction comparison

After reviewing stationary capture, parent obtains consent for one30s stream (same command, --seconds30, new file, wall timeout60s) with small controlled movements. No XR start. Timing uses host receipt time of first pose; parent can watch the growing log in a separate read-only terminal. Once first pose appears, follow a timer:0–5s center;5–10left;10–15right;15–20up;20–25down;25–30center. Hold each pose during the last3s. If timing uncertain, label it inconclusive rather than interpreting the wrong segment.

Offline only afterwards:

```
python -m tools.pro2_diagnostic.analyze /tmp/pro2-four-direction.jsonl
```

Analyzer uses receipt-time windows2–5,7–10,12–15,17–20,22–25,27–30 and requires10 valid samples each. It reports relative wxyz in the **unverified SDK frame**, preserving raw capture. Parent compares opposite signed dominant components for left/right and up/down, distinct rotation axes, center return, approximate motion magnitude and uprightness. Quaternion q/-q are equivalent; sign alignment is analysis only. Compare measured axes against documented NWU and the pinned upstream conversion as separate hypotheses; do not pick a correction merely because it looks familiar. Roll/recenter/prediction and rendered motion remain separate later gates.

Finite next implementation decision: stationary + directional evidence yields observed firmware/pose/timing/frame behavior. Add a separately reviewed Pro2 adapter with demonstrated mapping/time conversion and offline replay of these captures into the unchanged publisher ABI; validate invalid/stale/disconnect/cleanup. Only then ask parent for adapter installation and an XR lifecycle test. If SDK60pose is unavailable, retain exact errors/firmware and use the official SDK desktop quick-start/glasses-demo under the same parent authorization boundaries; inspect its actual build/source before running it. No blanket vendor-response dependency or guessed firmware update is imposed.

## Offline verification / review limits

`python -m unittest discover -s tests/pro2_diagnostic -v` compiles a synthetic shared library using cc and tests ABI/callback layout, exact mismatch before loader, library hash mismatch, callback validation, cleanup on failure, stop event and unsupported family/rate. No installed SDK load/device access. Static symbols/dependencies checked with nm/readelf only. Test logs alongside. Review checked lifecycle/order, shared callbacks retained through destroy, queue bound and truthful units, and absence of app/publisher changes. Independent hardware behavior remains untested. Candidate is source artifact under tools/pro2_diagnostic, not a replacement driver binary or install instruction.

## G2 parent hardware result and next gate

Parent-authorized stationary logging completed; sanitized independent offline analysis is in [G2 evidence](hil-evidence/20261104-153104-stationary/README.md). This establishes a finite raw pose session, not frame/mapping/optical/XR support. The separately consented next experiment is specified in [G3 direction plan](directional-g3-plan.md). Integrator has not executed a live session or removed activation guards.
