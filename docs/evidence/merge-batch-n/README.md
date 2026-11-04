# Merge batch N

Merged 728b85f, 5da8d96, a4ff861, in that order, over 00c2328.
The private renderer handover now includes the full stabilization filter.
Recovery retains both the pending-event guard and the explicit retry latch.
The decoder regression checks stabilization and per-desktop depth strengths at their bounds.

Clean release build: cmake -S spike -B build/release -G Ninja -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON; cmake --build build/release --clean-first -j 8.
CTest: DRI_PRIME=pci-0000_12_00_0 (Radeon 610M), 103/103 passed, no skips.
The control_wire test includes the real control_wire_check decoder and panel-frame fuzz; rerun after adding merged-field coverage passed.
QML: offscreen/software, 46 passed. bind_check: no clash.
Real-model GPU opt-in: 3 passed. Offline stabilization recordings: 3 passed.
Python suites run independently with /tmp/viture-switch-venv/bin/python -m pytest; logs alongside this file.
Optional native tests retain their normal skips; the decoder contract, model GPU and stabilization recording checks were separately enabled.

Ownership plugin matches the installed 9587ca186ac1. Full artifact digests are in sha256.txt.
No installation or changes to live XR, services, controller, compositor, glasses or driver.
