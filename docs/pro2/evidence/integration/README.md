# Integrated offline result

All six PRO 2 streams merged. Functional safety changes through 1f66af1; QD1 fixed40f1dd5 and independently reproduced by QA against that exact head (qa-driver-review.md). No installation, live device command, provider start, compositor hook change or master update.

| Gate | Actual result |
| --- | --- |
| Fresh native build, ownership ON | Passed, 269 build steps; final rebuild no work |
| CTest, explicit renderD129/fake Wayland | 103 passed, zero failures/skips |
| Full integrated native Python, cached pytest/depth venv | 2466 passed,9 skipped,10836 subtests |
| PRO 2 acceptance + managed guard | 8 passed,13 subtests |
| XR controller | 534 passed,53 subtests |
| Luma capture / probe / soak | 234 /135 /128 passed |
| Generic probe / stereo evaluation | 62 /51 passed |
| Depth engine (no discrete GPU opt-in) | 58 passed,5 skipped |
| Plugin Python / QML | 11 /46 passed |
| Driver lifecycle (original113 + new regression) | 114 passed,61 subtests |
| Real decoder/control panel fuzz selection | 57 passed |
| Existing stabilization recordings, offline | 3 passed |
| Built/source no-cursor-hook check | 7 passed |
| Driver research / public wire tools | 17 /20 passed |
| Bind check via offline verify-config | No clash |

Logs alongside. Subtest counts are runner assertions, not extra hardware tests. Skips cover optional prerequisites and never prove model/device support. ResourceWarnings about existing test sockets/fake subprocesses remain; no test failure in the final native run. The failed preliminary unguarded launcher run is superseded; see commands.md for root cause and corrected exact runner.

Build hash manifest sha256.txt pins renderer, ownership plugin, Luma driver and quiet shim. Ownership ABI uses Hyprland0.56.2/Aquamarine0.15.1. Binary unchanged after passing CTest; subsequent changes are Python safety fixes, isolated test fixtures and evidence. Functional-head.txt records QD1 review head, not the later documentation commit.

Offline safety integration is verified. Pro 2 tracking hardware is NOT validated or enabled. Installed SDK statically accepts PID1301; no approved replacement download/revision resolves exact quaternion/pitch/timestamps/firmware/calibration evidence. SBS disabled; physical control HOLD L1; parent alone authorizes installation/desk/axis/XR gates. Hotplug into an already-running external SDK remains outside launch-preflight confinement.
