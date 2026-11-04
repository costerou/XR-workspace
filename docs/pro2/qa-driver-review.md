# Driver preflight review — 2026-11-04

Reviewed uncommitted `pro2-driver` worktree diff of tracking.py and its new
`test_tracking_pro2.py`; branch had no committed diff yet. Review applies to that
snapshot, not an immutable final head. No driver files edited, no live sysfs read,
no provider launched. Ran `python -m unittest tests.native_spike.test_tracking_pro2
-v` in sibling worktree: **6 tests passed**, 0.003s.

## Blocker QD1: exact product rejection bypassed by unreadable PID

`tools/native_spike/tracking.py`, `_check_provider_compatibility`, reads idVendor
and idProduct in the same try block. An OSError reading idProduct continues to the
next device before product is inspected. With readable vendor `35ca` and exact
`VITURE PRO 2 XR Glasses`, missing idProduct silently returns success. This breaks
the stated PID OR exact-product guard, and could allow launch/reuse on incomplete
passive evidence. Missing product with known PID is covered; the converse is not.

Offline reproduction performed: import sibling tracking module, create temporary
`1-1/idVendor` containing `35ca`, `1-1/product` containing exact PRO 2 name, omit
`idProduct`, patch module SYS_USB to temporary directory, call the gate. Result:
returned without raising. No mocked provider or real device was needed.

Required fix: read PID and product independently after vendor; reject if either
readable field identifies PRO 2. Add missing/unreadable PID plus exact-product
case and assert no status/config/log/spawn/stop calls across entry points. Driver
owner should implement; QA will not edit transport/provider files.

## Remaining limits

Entry checks and typed exception correctly prevent generic Luma fallback for the
six covered cases. No axis, publisher or calibration changes seen in reviewed diff.
A preflight is a snapshot: current start checks occur before config/access/log/env
preparation and do not pin device identity through spawn or monitor subsequent
attachment. The hotplug test covers an UnsupportedTrackingProvider exception
surfacing from ensure_ready, not arbitrary physical hotplug while a provider is
already running. Do not claim general hotplug protection from that test. Provider
activation remains blocked and native coordinate/correction review remains open.
