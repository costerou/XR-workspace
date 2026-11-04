# PRO 2 parent-led bring-up — NOT authorization to execute

## Current decision

Do not install or launch Pro 2 tracking yet. Exact identity and plain display enumeration are evidenced; head tracking, axes, calibration, 3D and stability remain unverified. The installed SDK statically accepts PID 1301, but this does not clear its Pro 2 coordinate correction, firmware, timestamps or calibration semantics. No proprietary protocol has been adopted. See [hardware inventory](hardware-inventory.md), [provider evidence](protocol-evidence.md), and [shared contract](integration-contract.md).

Installed/live workspace source reported by parent: detached feature-placement-menu-integrate at `ff7b26a`. Local master is a separate checkout and is not changed here. B1/C1UI (`xr-options-build` ebe4303) and reporting (`drop-incident-evidence` c19f4f9) are finished uninstalled siblings, excluded from this batch. This integration does not replace their functionality.

## Identity and display pin

Require `35ca:1301` AND exact product `VITURE PRO 2 XR Glasses`, canonical key `pro2`. Never accept old Pro, Luma Ultra `1104` or Beast `1201` as equivalent. Inventory captured November 4: bus1/device13, port1-5, HID interface 0 (usbhid), hidraw7. These addresses are transient; re-read passive sysfs/udev identity rather than pinning node numbers.

Physical DP-2 was 1920x1080@60, scale1.25; preserve the user's current mode. EDID offers 60/90/120 variants but is generic VITURE and is not model identity. No 1200p, Luma mode switch, Luma restore, optical pitch correction or Pro 2 SBS command is cleared. DisplayPort ELD stereo PCM is separate from USB; no USB audio/input descendants were captured.

## Installation gate, parent only

1. Parent reviews integrated revision, artifact SHA256 manifest, installed-source backup and excluded batch branches. Do not run setup scripts or package builds as a substitute for reviewing their effects.
2. Resolve provider gaps from exact SDK/build + firmware evidence: quaternion frame, ordering/handedness, timestamp units/wrap, calibration/reset and the upstream Pro 2 pitch transformation. Do not remove or copy transformations speculatively. Exact installed library hashes and cached SDK/source pins are in protocol-evidence.md; cached source is not proven installed executable provenance.
3. If an update is needed, use only the vendor official SDK/release distribution linked in the evidence. Record immutable revision, architecture, archive and library hashes, API/header compatibility, dynamic dependencies and exact PID support before building an adapter offline. No guessed minimum firmware, unverified download hash, automatic update or installation command is approved. The provider gate must remain closed until this is complete.
4. Parent obtains Cody's explicit installation consent for a concrete artifact and backup/restore plan. Install renderer, Python controller/supervisor and QML consistently from this revision only if the later batch plan authorizes it. Do not merge origin/master or deploy B1/C1UI implicitly.
5. Existing vendor-wide udev coverage must be inspected first; do not duplicate a 1301 rule. Evidence captured effective Cody rw access on USB/HID. An install that actually changes rules requires parent consent for the rule write and reload; a separate replug/targeted trigger decision follows. Never use broad triggers, chmod/chown, USB reset, driver detach or set-configuration to force access. Verify passive ACL metadata afterwards; this is not an interface-open test.

## Desk and axis gates, separately consented

Parent records identity, exact app/provider/library hashes, firmware evidence, action and outcome at each gate. Unknown means stop.

* G0: passive exact identity, unique output correlation and effective access metadata; no interface claims.
* G1: existing plain 2D image upright/readable and ordinary DisplayPort audio routing. Preserve baseline mode and physical controls. No XR/provider start.
* G2: after provider contract reconciliation and explicit desk consent, provider-only stationary IMU test, no XR compositor. Check fresh finite normalized pose and timestamp progression with documented units, calibration state and no alternate-model fallback. Parent must have reviewed exact start/stop mechanism before consent.
* G3: separate axis consent. Small left, center, right, center, up, center, down, center and gentle roll; compare raw SDK, provider and published reference frames. Confirm signed yaw/pitch from the documented contract, upright roll, center return and reset semantics. Do not infer signs from an older model. Stop on inversion, drift, stale/nonfinite data or disconnect.
* G4: only after G1–G3 and offline recovery green, parent authorizes XR start/stop. Keep an accessible normal desktop/fallback. Verify output ownership, window placement, normal stop and a second lifecycle. Do not inject a live crash or unplug without separate consent.
* G5: reconnect replay passes offline first. Any physical disconnect experiment requires separate parent approval. Preserve cooldown, explicit retry and safe access to windows.
* G6: high refresh and SBS are deferred optional gates. Official Pro 2 instructions use hold L1 for 2D/3D; this is information, not permission to do so. No R1 instructions or Luma switching commands. Verify actual mode and rollback independently.

## Backout

Before installation parent records installed.json, binary/library checksums, service state, current physical mode/scale, per-model profiles and a recoverable known-good artifact. Backups must preserve private permissions. On a failed gate, stop the exact test process using its reviewed lifecycle, preserve sanitized logs, restore the backed-up complete app/provider artifact and profile if changed, and confirm ordinary windows/display/audio remain available. Do not reset USB, replace firmware or apply another model's mode restoration. Parent authorizes any service/compositor or installation rollback actions; this thread performs none. Never describe offline success as hardware validation.

## Existing provider and hotplug limitation

Installed package is `xr-driver-git 2.11.9-1`, cached package recipe source pin `36e2139dd482f5984112d0c3ebafcc07a78f99c0` (not a proven source-to-installed-executable mapping); installed `/usr/bin/xrDriver` SHA256 `184bcbaf4b6beab0df785489abb115dead923234b39703fdb2e90de7be6c8fb0`, installed `libglasses.so` SHA256 `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e`. See [driver audit](driver-provider-audit.md) for every provider path and limitations. There is currently no approved replacement pin; reinstalling the same release does not resolve the coordinate question. A new provider needs the vendor Gen2 pose API and exact admission plus build-specific quaternion/timestamp/calibration evidence before review.

The passive launch guard cannot confine a driver already waiting for a future hotplug. Before a later authorized live session, parent must verify no old tracking process can claim Pro 2. This thread does not stop existing processes. Closing the hotplug race needs a provider-level exact-device admission contract, not a wider sysfs permission rule.
