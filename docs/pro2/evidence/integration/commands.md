# Exact offline commands and interpreter

Working directory: pro2-integrate repository root. No dependency installation.

`python -c 'import pytest'` and `/home/cody/.cache/viture-depth/venv/bin/python -c 'import pytest'` failed: ModuleNotFoundError. `tests/native_spike/test_stabilization.py` and `tests/stabilization/test_recordings.py` use pytest fixtures/marks. This is a missing dependency in those interpreters, not a repository import defect. unittest discovery alone also cannot execute pytest function tests.

Existing cached pytest 9.2.0 + pluggy/iniconfig/packaging/pygments packages are loaded by `run-cached-pytest.py`. The interpreter is the existing depth venv Python 3.12.16; cached pure Python packages are read, not installed, copied or activated globally. Run each suite separately because historical test modules share basenames and import local helper modules.

```bash
cmake -S spike -B build-pro2 -G Ninja -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build-pro2 -j 4
mkdir -p /tmp/pro2-offline-runtime
chmod 700 /tmp/pro2-offline-runtime
env XDG_RUNTIME_DIR=/tmp/pro2-offline-runtime WAYLAND_DISPLAY=pro2-no-live DISPLAY= DRI_PRIME=pci-0000_12_00_0! XR_CAPTURE_TEST_NODE=/dev/dri/renderD129 XR_DEPTH_TEST_NODE=/dev/dri/renderD129 ctest --test-dir build-pro2 --output-on-failure -j 2
```

renderD129 resolves to PCI 0000:12:00.0. Fake loopback compositor honors XR_CAPTURE_TEST_NODE and does not fall back to a different node. Explicit nonexistent-node negative check exits 77 with `no render node`. No compositor hooks added.

For each of `tests/depth_engine tests/luma_capture tests/luma_probe tests/luma_soak tests/native_spike tests/pro2 tests/probe tests/stabilization tests/stereo_eval tests/xr_controller omarchy-plugin/cody.viture-workspace/tests`, substituting SUITE:

```bash
env PYTHONPATH="$PWD:$PWD/SUITE" XDG_RUNTIME_DIR=/tmp/pro2-offline-runtime WAYLAND_DISPLAY=pro2-no-live DISPLAY= QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software CONTROL_WIRE_CHECK="$PWD/build-pro2/control_wire_check" /home/cody/.cache/viture-depth/venv/bin/python docs/pro2/evidence/integration/run-cached-pytest.py -q SUITE
```

```bash
env XDG_RUNTIME_DIR=/tmp/pro2-offline-runtime WAYLAND_DISPLAY=pro2-no-live DISPLAY= QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software /usr/lib/qt6/bin/qmltestrunner -input omarchy-plugin/cody.viture-workspace/tests
env XDG_RUNTIME_DIR=/tmp/pro2-offline-runtime WAYLAND_DISPLAY=pro2-no-live DISPLAY= python -m tools.native_spike.bind_check
```

Opt-in ROCm real-model tests are deliberately not enabled: existing backend defaults to the discrete GPU. This session permits only renderD129. Skips remain explicit, never reported as device/model validation. Offline recording checks require existing replay logs and stabilization_trace, without recording any live USB traffic.

The cached pytest launcher must have a `__main__` guard: multiprocessing spawn reimports it. The first unguarded temporary launcher recursively entered pytest in workers and produced watchdog timing failures; those results are superseded by the guarded final run. The merged reconnect fixtures now patch SYS_USB to an empty offline tree, as the other driver fixtures already do, so attached live Pro 2 does not contaminate simulated old-model reconnect tests. Production provider rejection is unchanged. Native function-style stabilization tests separately passed 8/8; recorded stabilization tests passed 3/3 using existing replay files and the built trace binary.
