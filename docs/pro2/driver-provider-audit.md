# Pro 2 tracking provider audit — 2026-11-04

Activation remains blocked. Installed SDK recognizes exact PID 35ca:1301; correct Pro 2 pose semantics are not established. No device was opened, SDK loaded, driver launched or hardware tested by this audit. All lifecycle executions use fake programs and temporary paths.

## Evidence and allowlists

Installed package: `xr-driver-git 2.11.9-1`. Cached PKGBUILD pins release `36e2139dd482f5984112d0c3ebafcc07a78f99c0`; cache git HEAD is later README revision `958908487a36aad15c6cbb5b7865e404c7b7dfac`. These are provenance evidence, not proof that installed files equal source artifacts.

SHA256 of installed files, read without executing:

| File | SHA256 |
|---|---|
| /usr/bin/xrDriver | 184bcbaf4b6beab0df785489abb115dead923234b39703fdb2e90de7be6c8fb0 |
| /usr/lib/xr-driver/libglasses.so | 8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e |
| /usr/lib/xr-driver/libcarina_vio.so | 9a23e3ede795de7f6d6137a71755c57d3c9aba6055e96298ca8e8b266d3d4784 |

`objdump -d --disassemble=xr_device_provider_is_product_id_valid /usr/lib/xr-driver/libglasses.so` shows compare to 0x1301 at 0x65f55 followed by `sete` and return. Exact PID acceptance is proven statically. Installed library differs from upstream/A/B libglasses SHA256 `a68cbef5e39fa97086fe6e8d63a2a3649165c78c298f85ba896ff12ef75404ad`; do not infer whole-library equivalence from a version label. Protocol stream separately records header/library provenance.

| Provider/path | Exact matching behavior | Pro 2 decision |
|---|---|---|
| SDK xrdriver | Upstream viture.c:707–735 requires VID35ca, SDK PID validity, recognized SDK market name | PID accepted, activation blocked |
| 3dof A/B | Same SDK selection; pinned binary, no standard fallback | Not a Pro2 fix; activation blocked |
| custom luma | transport.hpp:59–73 defaults VID35ca/PID1104; transport.cpp checks both before interface discovery | Never repurpose for1301 |
| permission precheck | tracking.py GLASSES_USB is only35ca:1104 | Not a provider capability allowlist; Pro2 rejected earlier |

Pinned [upstream source](https://github.com/wheaney/XRLinuxDriver/blob/36e2139dd482f5984112d0c3ebafcc07a78f99c0/src/devices/viture.c) names Pro2 separately from Pro, applies -8.5 pitch (62–71), marks FOV44.5 TBD, sets Pro2-only conversion (728), and converts EUS→NWU before publishing (449–469). [Official SDK reference](https://www.viture.com/developer/glasses-sdk/glasses) describes NWU output; [official release notes](https://www.viture.com/release-updates/sdk) include firmware-dependent Gen2 pose requirements. See protocol stream's shared `pro2-protocol.md` report for authoritative evidence/gaps. No correction was removed or guessed here.

## Lifecycle safety change

`tools/native_spike/tracking.py:_check_provider_compatibility` reads only sysfs text. Under VID35ca, either PID1301 or exact product `VITURE PRO 2 XR Glasses` rejects, including conflicting/missing product evidence. This is a provider refusal, not model/profile detection. It runs before `start` config/log/process work, before `ensure_ready` can reuse a calibrated old driver or stop one, and before `ensure_ready_luma` attempts either provider. A typed ValueError bypasses fallback if startup discovers Pro2 after the first scan. Direct CLI, reconnect, restart/watch and consent paths funnel through these entrypoints.

Readiness still uses latest connection markers and log offset, not an earlier calibration. Startup still polls exit before isolation, allows bounded namespace setup, and distinguishes a dead process from a live unisolated one. Existing PID/start-tick ownership and reconnect cooldown remain unchanged. No new axes, calibration, prediction or publisher transformations; no saved model settings edited.

Limits: this preflight cannot prevent an already-running external driver, or a previously started SDK waiting for devices, from discovering a subsequently attached Pro2. It does not stop existing processes. Parent must ensure no tracking driver is running before any future authorized connection/session. A future SDK wrapper needs an exact-device allowlist enforced within enumeration to close that hotplug race. This task does not claim runtime device confinement.

## Offline verification and wiring

Run from repo root with system Python: `python -m unittest tests.native_spike.test_tracking tests.native_spike.test_tracking_luma tests.native_spike.test_tracking_pro2 -q`. No pytest dependency (system pytest absent). New tests reproduce missing rejection before fix; post-fix six synthetic-sysfs cases cover all three providers and calibrated reuse, no fallback, mixed/conflicting/missing identity and unchanged Luma/Beast/unrelated identities. SYS_USB points to a temporary fixture, never live devices. Fake lifecycle fixtures likewise now point SYS_USB to temporary paths. Existing tests supply temporary runtime/config/log/binary paths; namespace probes replace the driver with Python. No model/profile environment wiring required by the guard. Startup-death and real namespace fake-process cases verify truthful isolation diagnostics. Lifecycle tests emit existing subprocess ResourceWarnings; result below records pass/fail.

## Safest staged bring-up path for parent (not performed)

1. Keep current fail-closed code installed only after parent's review/installation authorization. No live tracking starts at this stage. Review exact official SDK build/firmware contract with vendor: NWU vs Pro2 upstream EUS conversion, quaternion order/normalization, timestamp unit/wrap, pose opt-in, rate capability and stop/release fix.
2. Build a separately pinned SDK-backed Pro2 provider revision offline. Required change: exact VID/PID/product admission; Gen2 pose callback; no Carina raw/fusion/start commands; no -8.5 correction; no coordinate conversion until build-specific evidence establishes it; select only supported pose rate60/90/120 and reject empty capability; preserve IPC/calibration/recenter semantics. Replay axis, invalid/stale pose, teardown and reconnection tests first. No currently approved replacement download/revision exists in this audit. Latest released xrDriver2.11.9 already contains the unresolved behavior, so reinstalling/upgrading by name is not a fix.
3. Parent obtains explicit permission for installing that reviewed pinned artifact and, if needed, exact-device udev access from platform stream. Require ordinary-user USB/HID access only for documented provider interfaces, no blanket root execution; namespace support, external_only config and verified library resolution. Existing package's vendor-wide uaccess rule is not evidence of model support. No sudo/install/reload/replug done here.
4. Parent separately requests one minimal authorized SDK session with display unchanged, no Luma provider/fallback and no SBS. Verify exact firmware, selected pose rate, namespace isolation, still pose and small controlled yaw/pitch/roll/recenter, timestamp monotonicity and clean stop. Only then consider installing tracking integration. Hardware success remains unproven until this gate.

Final lifecycle result: 113 tests passed in 47.616s. `git diff --check` passed. Existing ResourceWarnings were emitted for fake subprocess lifecycle; no test failure remained.
