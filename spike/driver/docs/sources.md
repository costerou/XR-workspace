# Sources

What `protocol.md` rests on, read 2026-11-03. Commits are pinned, so the line numbers stay valid.

## Public sources

<a id="s1"></a>
**S1: XRLinuxDriver.** `wheaney/XRLinuxDriver` at
[36e2139](https://github.com/wheaney/XRLinuxDriver/tree/36e2139dd482f5984112d0c3ebafcc07a78f99c0),
the commit the installed package 2.11.9 was built from. GPL-3.0. Used for facts only, and no code is copied from it.
[`src/devices/viture.c`](https://github.com/wheaney/XRLinuxDriver/blob/36e2139dd482f5984112d0c3ebafcc07a78f99c0/src/devices/viture.c),
[`src/driver.c`](https://github.com/wheaney/XRLinuxDriver/blob/36e2139dd482f5984112d0c3ebafcc07a78f99c0/src/driver.c),
[`src/state.c`](https://github.com/wheaney/XRLinuxDriver/blob/36e2139dd482f5984112d0c3ebafcc07a78f99c0/src/state.c),
[`udev/70-viture-xr.rules`](https://github.com/wheaney/XRLinuxDriver/blob/36e2139dd482f5984112d0c3ebafcc07a78f99c0/udev/70-viture-xr.rules).
A local copy is the bare repository at `~/.cache/yay/xr-driver-git/XRLinuxDriver`.

<a id="s2"></a>
**S2: VITURE SDK headers as published in S1.**
[`include/sdks/`](https://github.com/wheaney/XRLinuxDriver/tree/36e2139dd482f5984112d0c3ebafcc07a78f99c0/include/sdks),
SDK version 2.4.0. Copyright VITURE, all rights reserved. Read for API meaning and constants only.
Do not copy them. The binaries next to them were not read or run.

<a id="s3"></a>
**S3: XRLinuxDriver issues.**
[#122 "Issues with crashing for the Viture Luma Ultra"](https://github.com/wheaney/XRLinuxDriver/issues/122)
and [#126](https://github.com/wheaney/XRLinuxDriver/issues/126). User reports and the maintainer's
comments on SDK segfaults.

<a id="s4"></a>
**S4: go-viture/luma.** [`go-viture/luma`](https://github.com/go-viture/luma) at cc37e96, BSD-3-Clause.
The main public source for this device: endpoints, both envelopes, CRC, recorded real replies.
Measured on real Luma Ultra hardware. **Provenance:** its authors derived the envelope and endpoints
from `libcarina_vio.dylib` inside VITURE's SpaceWalker, citing EU Directive 2009/24/EC Article 6, and
from VITURE's web updater module. We read their published findings and did not repeat that work. Cody
should know this when judging "open knowledge only". Its test fixtures are the judge for our tests.

<a id="s5"></a>
**S5: viture-v2.** [`elasticjava/viture-v2`](https://github.com/elasticjava/viture-v2) at 8b29df0, MIT.
[`PROTOCOL.md`](https://github.com/elasticjava/viture-v2/blob/8b29df0/PROTOCOL.md): Gen2 protocol from
`usbmon` on a Pro 2. Not this device. Useful for method and for the "no handshake" comparison. Its
`src/usbfs.rs` is a worked example of a usbfs transport.

<a id="s6"></a>
**S6: go-macos/iokit.** [`go-macos/iokit`](https://github.com/go-macos/iokit) at ad6ef75, BSD-3-Clause,
package `viture`: the classic envelope and Beast observations.

<a id="s7"></a>
**S7: viture-webxr-extension.** [`bfvogel/viture-webxr-extension`](https://github.com/bfvogel/viture-webxr-extension)
at 7b27af0,
[`VITURE_PROTOCOL.md`](https://github.com/bfvogel/viture-webxr-extension/blob/7b27af0/VITURE_PROTOCOL.md):
the Gen1 write-up and PID table. Licence not checked.

<a id="s8"></a>
**S8: viture_virtual_display.** [`mgschwan/viture_virtual_display`](https://github.com/mgschwan/viture_virtual_display)
at 6d50d39, `viture_connection.c`: a Gen1 implementation for the Pro XR from a decompiled SDK. Used
only to show that Gen1 sources disagree on the CRC.

<a id="s9"></a>
**S9: VITURE developer documentation.** [Glasses SDK guide](https://www.viture.com/developer/glasses-sdk/glasses)
and [SDK for Linux](https://first.viture.com/developer/viture-sdk-for-linux). States that the Luma
Ultra's control interface is USB bulk, not HID, and gives the Carina polling model. The Linux page
shows no licence text.

<a id="s10"></a>
**S10: Spatial-Screens.** [`SalvatoreMastrangelo/Spatial-Screens`](https://github.com/SalvatoreMastrangelo/Spatial-Screens):
states that the SDK runs VIO on the host and that the Carina callback has no magnetometer or
temperature. Its claim that the IMU is on the 1102 HID interfaces conflicts with S4 and S6.

<a id="s11"></a>
**S11: viture-ultra-ar-color-helper.** [`AchromaAssist/viture-ultra-ar-color-helper`](https://github.com/AchromaAssist/viture-ultra-ar-color-helper)
at 7a5a14a: lists Luma Ultra PIDs `0x1101` and `0x1104`.

<a id="s12"></a>
**S12: omarchy-xr.** [PR #48](https://github.com/afruth/omarchy-xr/pull/48): runs Carina in 3DOF through
the SDK at 120 Hz, not yet proven on hardware. It shows 3DOF needs no cameras, VIO or OpenCV at the API
level, which is relevant to the suspects in `protocol.md`.

## Sibling threads

Read 2026-11-03 for cross-checks: `spike/driver/docs/upstream-findings.md` on `drv-upstream-issues`,
`6dof-feasibility.md` on `drv-6dof-research`, `features.md` on `drv-features` and `display-control.md`
on `drv-display-control`. The wear sensor conflict and the reconnect limit come from the first.

## Local observations

None of these touched the device. All were read-only.

<a id="l1"></a>
**L1: driver log.** `~/.local/state/xr_driver/driver.log`, a snapshot taken 2026-11-03 23:33 with
76 driver starts from 2026-10-31 on. The SDK's info-level lines appear only in the sessions of 23:28:04 and 23:28:51 on
2026-11-03. Most lines from the SDK have no timestamp. It is not committed, because it holds a
hardware id.

**L2: sysfs.** `/sys/bus/usb/devices/5-2*` and `/sys/class/hidraw/*/device`. This reads the kernel's
cached descriptors. Nothing is sent to the device and its node is not opened.

<a id="l3"></a>
**L3: kernel log.** `journalctl -k -b -1` (ending 2026-11-03 23:30, 54 hub disconnects) and `-b -2`
(ending 2026-11-01 00:13, 16).
A reboot at 23:31 moved the first of those boots from `-b 0` to `-b -1`, so the
numbers will shift again after the next reboot.

## Looked for and not found

- Any public Carina IMU packet layout or stream start command. Searched GitHub, the VITURE docs and the
  go-viture, viture-v2 and iokit sources.
- `taowen/ar-glass-lib`, returned by a search: the repository does not exist or is private (clone
  returned "not found"), so nothing from that result was used.
- VITURE's SDK licence text.

## Reproducing the numbers

```bash
L=~/.local/state/xr_driver/driver.log
grep -a -c 'get exp flag status: Host Command Invalid' "$L"
grep -a -c 'do sync data transfer :-7' "$L"
grep -a -c 'remain sync data transfer' "$L"
grep -a -c 'returned -7' "$L"
grep -a -o -E 'Built command - MsgID: 0x[0-9A-Fa-f]+' "$L" | sort | uniq -c
for boot in -1 -2; do journalctl -k -b $boot --no-pager | grep -c 'usb 5-2: USB disconnect'; done
```

Counts in `protocol.md` are from that snapshot, and the log keeps growing. The timing comparison of
`Retrying` lines with disconnects was a throwaway script: match each line to the latest earlier
disconnect in the kernel log.

The wire tool's tests:

```bash
uv run --no-project --with pytest python -m pytest -q -p no:cacheprovider spike/driver/docs/tools
```
