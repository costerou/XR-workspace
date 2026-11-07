# Pro2 platform boundary

Identity evidence is the operator's 2026-11-04 passive inventory: VID:PID
`35ca:1301`, product exactly `VITURE PRO 2 XR Glasses`. This branch did not
repeat live inspection. Synthetic test inventory is not hardware validation.

`tools/xr_controller/detect.py:15` identifies only the exact pair and vendor;
lines 16–17 deliberately withhold output eligibility pending driver/display
proof. `tools/native_spike/panel_consent.py:50` rejects Pro2 start consent,
including a tracking-disabled scope. Integrator must retain this gate until
both owners supply evidence; identifying a model is not provider support.

`tools/native_spike/profiles.py:204` reads only the `pro2` profile, never the
legacy layout seed, and disables tracking and mode switching on load. The
inert driver selector defaults to xrdriver; this does **not** assert xrdriver
compatibility. Existing Luma/Beast migration/defaults remain intact. Reads do
not rewrite saved files; saving Pro2 preserves the Luma entry and vice versa.

## Access eligibility

`tools/luma_probe/udev.py:8` already supplies vendor-wide USB and hidraw rules
for 35ca; they include 1301 without a duplicate product rule. `covers(lines,
product_id='1301')` checks a rule's PID restriction on both routes, while its
default remains 1104 for existing Luma callers. This is a static rule coverage
heuristic, not evaluation of udev's full language, ACL proof, or permission to
open the device. No claim is made about the currently installed rules/ACLs.

If the orchestrator later proves rules absent and receives setup consent,
these are **written instructions only**, never executed by this branch:

```sh
sudo tee /etc/udev/rules.d/71-xr-luma.rules >/dev/null <<'RULES'
# XR Workspace: logged-in user access; already includes Pro2.
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="35ca", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"
RULES
sudo udevadm control --reload-rules
# Parent arranges a consented replug, then rechecks ACLs without opening nodes.
```

Do not run these if equivalent vendor-wide rules already exist. Do not add a
1301 duplicate, trigger devices, or use sudo to bypass an unsupported driver.

## Audio/input passive mapping boundary

No audio or input endpoints were opened, no playback/capture was attempted,
and no volume, microphone, routing or default-device settings were changed.
The repository's Luma-only probe uses `GLASSES_PRODUCT='1104'` in
`tools/luma_probe/sysfs.py:16`; it must not be treated as Pro2 audio/input
inventory. This branch deliberately does not broaden that probe's device
selection or assume the excluded microphone PID 1102 is a Pro2 endpoint.

Next parent-approved passive inventory can read `/proc/asound/cards`,
`/proc/asound/pcm`, `/proc/bus/input/devices`, and resolved ancestry of
`/sys/class/sound/card*/device` and `/sys/class/input/event*/device`.
Associate nodes only when their USB ancestor has **both** 35ca and 1301 and
the exact product; friendly names alone and old Luma microphone mappings do
not establish identity. Record interface class/subclass and existing node
ownership/ACLs with stat/getfacl, without opening `/dev/snd`, `/dev/input`,
USB or hidraw nodes. Missing ancestry means unknown mapping, not failure or
support. Audio playback, microphone capture and input behavior remain
unvalidated. The next live gate belongs to the parent, after exact provider
and display review; this branch authorizes no live tests.
