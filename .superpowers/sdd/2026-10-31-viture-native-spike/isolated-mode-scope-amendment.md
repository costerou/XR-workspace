# Isolated managed-mode scope / acceptance amendment

## Authority and scope

The user explicitly approved an isolated VM test mode for the full managed CLI and supervisor, with separate run consent and verified VM identity. This supplements Task 3B of `docs/superpowers/plans/2026-10-31-viture-native-spike.md`; it does not replace physical/glasses acceptance or authorize host plugin loading. This task is offline only, with no VM boot, device action, compositor connection, plugin load or subagents. Independent review must precede runtime.

Normal `run`, `recover` and `present` guards must remain unchanged. Isolated behavior must require an explicit mode, never an environment flag, implicit connector detection or fallback after a normal-mode rejection. Existing P2/P2-presentation-only consent must not authorize it, nor vice versa.

## Mandatory acceptance

- Separate private single-link 0600 run consent binds exact guest boot, verified compositor session/instance, canonical runtime, run ID, target and independent fallback. Capture/input/SDK/mode transitions remain false. Reject the known physical instance `efb50993780079460b0cbed1363e2166a2de1d9f_1793311125_1142154332` before connection and reject ordinary host operation by positive VM proof, not just that denylist.
- VM proof must use actual kernel/virtual-hardware evidence. Consent, guest marker, connector name, DMI label or an environment variable alone is insufficient. Missing, inconsistent or inaccessible evidence fails closed.
- Both selected output objects must be bound to actual virtio DRM connectors on the verified guest device. They must remain distinct, enabled, DPMS-on and nonmirrored. A sysfs connector with the same name as a compositor monitor is not by itself that binding. Virtual outputs must never be labeled physical or glasses.
- Preserve IPC/Wayland peer identity; canonical journal/lifetime lock; durable saved broker epoch and exact handles; no fresh authority adoption on recovery. Persist mode/proof scope so normal and isolated recovery cannot exchange authority. Revalidate proof on startup, spawned guard execution and recovery; carry explicit serializable policy, never ambient fallback. Retain watchdog deadlines and exact child/worker reaping.
- Required RED/GREEN cases: cross-mode consent and journal misuse; missing/false VM evidence; physical host rejection; valid synthetic injected evidence; wrong-device/same-name connector substitution; lost/changed target and fallback; DPMS/mirror loss; spawned guard revalidation; unchanged normal behavior. Synthetic proof must remain a test-only injected boundary, not a runtime file/flag bypass.
- Build actual native targets and run explicit Python suites plus CTest. Keep these results separate from VM and physical qualification.

## Approach decision

A Python-only mode layered over current schema-1 broker replies is not sufficient: the broker has neither a target handle nor a DRM identity observation for the selected pair, and marks its fallback internally `physical=true`. Do not broaden the physical regex, fabricate EDID, reinterpret that flag silently or turn a sysfs inventory into object identity.

Recommended next implementation boundary: a narrowly versioned, additive native pair-proof contract retaining both actual output objects, exposing DRM device/connector identity, and distinguishing virtual DRM eligibility from physical classification. Join that observation to independently collected guest/kernel evidence. Preserve the existing normal wire and guard path exactly. This requires a reviewed replacement module; the previously reviewed module hash cannot supply the new proof.

**Disposition:** return the user's expressly permitted architecture-risk decision rather than implement a weaker gate. The companion report records the concrete contract gaps and next steps. This amendment records acceptance requirements, not implementation or runtime completion.
