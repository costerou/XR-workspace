# Pro 2 passive hardware inventory — 2026-11-04

Scope: canonical `pro2`, exact `35ca:1301` and `VITURE PRO 2 XR Glasses`. Read the integrator contract at `/home/cody/.hermes/profiles/coder/previews/pro2-setup/pro2-integrate-contract.md`. No support or provider compatibility is inferred from enumeration.

Capture began 19:35:46 UTC (14:35:46 EST). Exact USB identity was present at the start and end of USB capture; DP-2 was connected during display capture. No disconnection was observed. This is a short snapshot, not stability evidence. USB device number 13 and hidraw7 are transient.

## Observed evidence

All paths below are relative to `fixtures/hardware-2026-11-04/`.

* `usb.json`: exact product, bus 1 device 13, port `1-5`, full-speed **12 Mbit/s** (USB version descriptor 2.01 does not mean high-speed). One configuration; device class 00, single interface class 03 HID, subclass/protocol 00, `usbhid` bound. Two interrupt endpoints: OUT 0x01 and IN 0x81, 64-byte maximum packet each, 1 ms interval. No vendor transport semantics established.
* `usb.json` contains the 59-byte kernel-cached USB descriptor sequence and 34-byte kernel-cached HID report descriptor. HID report descriptor describes vendor usage page 0xff00, 64-byte input and output reports. This is a descriptor observation only; no reports were read or written and no feature/control requests issued.
* Ancestry: directly beneath `usb1` root hub, controller PCI `0000:10:00.0`, AMD `1022:43f7`, class `0x0c0330`, driver `xhci_hcd`. PCI bridge ancestry is preserved. Device power control `on`, runtime `active`, suspended time 0 at snapshot; descriptor reports self-powered (`c0`) and 100 mA MaxPower. These are advertised/configured states, not measured power draw.
* Only matching descendant character node is `/dev/hidraw7`. No matching input or sound class nodes found. hidraw and USB node `/dev/bus/usb/001/013` are root:root 0660, ACL gives Cody read/write; collector effective access checks both true. No node was opened. Permissions do not authorize protocol use.
* `drm.json`, `hyprland.json`, `dp-2.sanitized.edid`, `edid-decoded.txt`: physical `card1-DP-2` connected/enabled, DPMS On. EDID 256 bytes, both original checksums valid. Visitech AS / VITURE; advertised 1920×1080 at 60, 120, 119.88, 90 and 59.94 Hz. Current verified compositor mode 1920×1080 at 60 Hz, scale 1.25, transform 0. No SBS/mode switch performed or established.
* Audio: `/proc/asound/cards` names HDA ATI HDMI (card0), two HD-Audio Generic cards and unrelated Razer USB audio. VITURE ELD at `/proc/asound/card0/eld#0.1`: monitor present, ELD valid, DisplayPort, LPCM stereo, 32000/44100/48000 Hz, 16/20/24 bits. No Pro 2 USB audio interface appears. ELD association is by VITURE name/product metadata, not proof of audible playback or a persistent ALSA/DRM numbering relationship.

## Actual desktop selection

Queried `hyprctl -j instances` with `XDG_RUNTIME_DIR=/run/user/1000`. The sole returned instance had PID 1660, verified `/proc/1660/comm` = Hyprland and process owner UID 1000 (Cody). Explicitly selected that instance for `hyprctl -j monitors all`; its socket was `wayland-1`. Did not rely on inherited bot session variables or a previous login signature. Only DP-2 display metadata was retained; workspace/activity fields and serial-bearing description were removed.

## Old Luma comparison

Prior measured Luma evidence is `spike/driver/docs/6dof-feasibility.md:26` (not a current measurement): `5-2` QinHeng `1a86:8091` hub, with `5-2.1` Luma Ultra `35ca:1104` vendor interface/seven endpoints, `5-2.2` Sonix `0c45:636b` UVC camera, and `5-2.3` `35ca:1102` audio/three HID interfaces.

Current complete USB topology in `usb.json` instead shows Pro 2 as **one USB device directly on root port 1-5**, with one HID interface. No `1a86:8091`, `0c45:636b`, `35ca:1102` or `35ca:1104` device appears in this snapshot. Current `5-2` is an unrelated mouse receiver. This verifies a different exposed USB topology in this connection; it does not prove internal camera hardware is absent. Do not reuse Luma packets, correction, SDK enum or startup sequence.

## Exact safe next diagnostics

Remain read-only. Re-resolve identity every time; stop and preserve the last observations if it vanishes. Do not assume these transient paths survive reconnect.

1. Recheck identity and speed using `cat /sys/bus/usb/devices/1-5/{idVendor,idProduct,product,speed,busnum,devnum}`. Continue only for exact `35ca`, `1301`, `VITURE PRO 2 XR Glasses`.
2. Inspect only kernel cached metadata: `cat /sys/bus/usb/devices/1-5/power/{control,runtime_status,runtime_active_time,runtime_suspended_time}` and `cat /sys/class/drm/card1-DP-2/{status,enabled,modes}`. Compare timestamps with saved fixture; do not write attributes.
3. Rediscover compositor using `XDG_RUNTIME_DIR=/run/user/1000 hyprctl -j instances`; verify returned PID's UID/comm through proc, then `XDG_RUNTIME_DIR=/run/user/1000 HYPRLAND_INSTANCE_SIGNATURE='<verified-current-instance>' hyprctl -j monitors all`. Retain only DP-2 display fields and strip serial/description/workspaces before saving.
4. Read `cat /proc/asound/card0/eld\#0.1` only after rediscovering VITURE ELD among current cards. No playback test, capture stream, hidraw read, libusb claim, `lsusb -v`, SDK initialization, driver launch or XR action is part of this gate.
5. Next non-hardware gate: protocol stream must identify an official provider revision explicitly supporting exact Pro 2, including documented transport and coordinate/calibration semantics. Unknown support rejects before launch. Any active experiment needs a separately approved plan from the parent; this inventory grants no such approval.

## Collection and privacy

Only sysfs/proc metadata, permission/stat/ACL checks, cached descriptors and read-only hyprctl queries were used. No sudo, installs, device-node opens, interface claims, USB requests, configuration writes, service changes or modesets. Kernel caches were read as files; USB string descriptors were not requested. No journal or private application logs were collected. Numeric EDID serial zeroed; serial text descriptors redacted and EDID checksums regenerated. Raw EDID was never saved. USB serial attributes and HID unique identifiers were excluded; cached USB descriptors contain string indices only. HID descriptor bytes encode report structure, not captured report payloads. Fixtures include transient topology addresses and public device names.

Offline validation is recorded in `fixtures/hardware-2026-11-04/validation.txt`; it checks structural consistency and sanitization, not hardware behavior. No production code changed.
