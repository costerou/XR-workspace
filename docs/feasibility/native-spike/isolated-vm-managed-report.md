# Isolated-VM managed mode: qualification report

Scope: the explicit `isolated-run` / `isolated-recover` mode in a disposable KVM guest. This is **virtual launcher/lifecycle evidence only**. It says nothing about physical glasses, comfort, head tracking, rendering performance or sustained 120 FPS. No plugin was loaded on the host desktop, and no host DRM/VT/input/render node was used.

## Code under test

| Item | Identity |
|---|---|
| Native pair-proof contract | `bec4929` (reviewed; no blocking findings) |
| Python isolated mode | `d61b193`, review fixes `306d153`, real-sysfs fix `671aaf5` (reviewed; no blocking findings) |
| Broker module | `build/native-spike-pair-proof/libxr-ownership.so`, SHA256 `4d4fc81cb056d5cb8762a784d8a3ed816637d55f81c3758edd6a7fcda595a63f` |
| Renderer | `build/native-spike/xr-native-spike`, SHA256 `8a3826c2d3c465db17fd760543c6294d8c4d15da4096987ae3010a6f726f38f9` |
| Previously reviewed module | `build/native-spike-offset-fix/libxr-ownership.so` (`2be43536…e31`), unchanged and not used here |

Offline before the VM: 121 native Python tests, 62 probe tests and 14/14 CTest pass. Ten compile-valid mutants of the isolated-mode guards are each caught.

## Guest envelope (both trials)

- q35/KVM, two vCPUs, 4096 MiB, `-nodefaults -no-user-config -nic none -no-reboot`, `snapshot=on`, `-display none -monitor none -serial stdio`, under an outer TERM/KILL timeout.
- One non-GL `virtio-gpu-pci` with `max_outputs=2` and per-output `xres`/`yres` 1280x800. The EDID names are `VM-present` and `VM-recovery`, not glasses identities.
- Each trial used a fresh reflink clone of the preserved `xr-presentation` image; prior evidence was untouched. Guest files were written with debugfs and read back byte-exact.
- Host audit of the QEMU process's FDs (11 and 16 samples): only `/dev/kvm`, its own image, its serial log, memfds and KVM anonymous inodes. There were no host DRM, input, TTY, Wayland, compositor or socket descriptors, and no QEMU survivors.

## Trial A: two-scanout topology and rendering (plugin-free)

The first attempt (`xr-isolated-twohead`) found a real bug in my evidence collector. On a real guest, `cardN/device` is the virtio-pci PCI function, and its single `virtioN` child is the device bound to `virtio_gpu`. My synthetic test tree had that inverted, so the collector rejected the real guest. This was fixed in `671aaf5`, with the test tree rebuilt to the observed layout.

The repeat (`xr-isolated-twohead-2`, `isolated-vm-twohead-live.json`) passed:
- Two compositor outputs, `Virtual-1` at 0,0 and `Virtual-2` at 1280,0, each 1280x800 at 74.994 Hz, enabled, DPMS-on and `mirrorOf none`.
- The production `collect_guest_evidence` on the real guest kernel gave Virtual-1 → `226:0` connector 40 and Virtual-2 → `226:0` connector 47.
- The production `_launch` supervisor and renderer drew a 3 s generated pattern on each output, return code 0: 208 and 187 frames presented, 0 discarded, 0 invalid timing. No layer surface remained afterwards.

## Trial B: full managed CLI (`xr-isolated-managed`, `isolated-vm-managed-live.json`)

The pair-proof module was loaded only into the guest compositor. Plugin load left outputs unchanged. Each run used its own `VM-isolated` consent (0600), bound to the guest boot id, verified session, runtime, run, `Virtual-1` target and `Virtual-2` fallback.

| Run | Action | Result |
|---|---|---|
| iso1 | SIGTERM renderer (normal teardown) | CLI exit 0, `complete`, renderer return code 0, journal 3x `done`, outputs back to baseline |
| iso2 | SIGKILL renderer | Supervisor recovery: `complete`, renderer return code -9 (CLI exit 2 by design), outputs back to baseline |
| iso3 | SIGKILL the CLI/controller | 3 owned outputs and a 3x `confirmed` journal persisted. Normal `recover` refused (`invalid consent fields`), journal byte-identical. Fresh `isolated-recover` completed with saved epoch/handles, outputs back to baseline |

While each run was live:
- The journal held `mode: isolated-vm` with the native proof, and the target and fallback each matched the guest sysfs connector (device `226:0`, connectors 40/47).
- Three owned headless outputs were present at 1920x1080@120 (refresh readback), x = 2560/4480/6400.
- The renderer layer was present on the target.
- The run survived several spawned-guard periods.

All three runs shared one broker epoch, and each got a distinct fallback handle. At the end, the compositor had no config errors, the plugin was still loaded, and outputs were identical to the pre-load baseline.

## Not established

- **Glasses:** physical Beast/Luma managed operation.
- **Compositor-level gates:** compositor restart, plugin unload/reload, authority loss, same-name replacement and fallback loss at runtime (native unit tests only).
- **Desktop state:** populated windows during isolated runs, and special/pinned windows.
- **Performance and comfort:** sustained 120 FPS, capture, tracking, focus and controls.
- **Host side of the VM boundary:** the isolated-mode evidence is guest-local. The no-network/no-share/no-passthrough envelope is verified host-side only for these trials.
- **Rendering at 120 Hz:** the 120 Hz figure here is the headless outputs' mode readback, not rendering.
