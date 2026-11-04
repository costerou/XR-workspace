Renderer view continuity, offline evidence (2026-11-04)

The new continuity test initially failed at `b.decode(a.encode())`; red.txt
records the failure before implementation. `ctest -R continuity` now tests
recenter pending/learned reference, world yaw anchor, a moving follow spring,
maximize hold and return to the saved room, roll values, edge-pan easing and
landing, centred-turn direction/serial, and once-only landed-turn delivery.
It compares resumed animation with the uninterrupted timeline, offset by a
three-second display outage. Corrupt snapshots must be rejected without
partially replacing live state.

The supervisor holds one private anonymous memfd per run and passes it to
each renderer. On orderly output loss, the renderer saves PoseState,
ModeController, ViewAnimator, rolls, maximize state and centred-turn context.
Only bounded value fields are copied: no Wayland, GL, driver or vector-storage
pointers. The replacement restores before drawing or replaying view controls.
Animation clocks move forward by the outage duration so the transition resumes
from the same point. The pose's centre and learned up direction survive; the
absence of a renderer is not treated as a tracking recalibration.

The snapshot is private same-build process state, not a persistent file or a
public wire format. Its version, size and checksum are checked. A successful
read consumes it, preventing a later crash from replaying an old view. The
parent discards it for ordinary recovery or USB identity changes, including a
change while waiting for the display. Stop closes the memfd. An abrupt kill
before saving falls back to ordinary startup; expected Wayland output removal
exits through the normal renderer cleanup and saves state.

`tests/native_spike/test_continuity.py` starts three real renderer processes
against private CPU loopback compositors, sharing the same anonymous memfd.
The first saves a snapshot; the second and third report successful restoration.
Additional tests check retention for expected display switches and discard
for ordinary loss or USB re-enumeration before/during the wait.

All hardware-facing tests remain offline. GPU checks use the iGPU and nice 19.
No installation, running XR/driver/engine, unit or desktop configuration was
changed. Live first-picture timing has not been measured.

Validation also exposed unrelated fake-driver test races. Lifecycle tests now
mock the host process scan (the dedicated scanner test still uses it), so a
concurrent test job's throwaway driver cannot interfere. The Luma restart test
waits for calibration as well as a new PID before asserting readiness. These
are test-only changes; production tracking code was not changed.
