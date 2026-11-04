# Upstream findings: Luma Ultra USB drops under XRLinuxDriver

Research date: 2026-11-03. Docs only. Nothing here opened or wrote to the glasses, and no driver,
XR session or service was started or stopped.

## The short answer

**Upgrading XRLinuxDriver will not fix this, because there is nothing to upgrade to.** The installed
build is commit `36e2139`, which is exactly v2.11.9, the latest release. The one commit on `main`
since then changes only the README. The newer "2.12.1" development build is RayNeo work and leaves
the VITURE code and vendor SDK as they are. Breezy Desktop 2.12.3, released today, ships the same
`36e2139` driver.

**Nobody has published this bug or a fix for it.** No public report contains `get_display_mode` or
`-7`. The closest match is [Breezy #192](https://github.com/wheaney/breezy-desktop/issues/192): a
Luma Ultra goes black for under a second every few minutes, and the maintainer says the glasses are
"briefly losing connection". It has no diagnosis.

**The most useful thing we can do next is a cheap A/B test.** The driver never asks for 3DoF, so, going by
VITURE's docs, the SDK runs its default 6DoF mode, with the tracking cameras and camera-based
tracking running all session. We only use orientation, so that work may be for nothing. A five-line patch switches it
off (see [Experiment](#experiment-3dof-versus-6dof)). If the drops stop, the cause is load from the
6DoF session, and it also backs the plan for our own IMU-only driver.

## Comparing 2.11.9 with everything newer

| Thing | Version | Commit | VITURE SDK | Differs for Luma Ultra? |
|---|---|---|---|---|
| Installed (`/usr/bin/xrDriver`) | 2.11.9 | `36e2139` | 2.4.0 | baseline |
| Latest release | [v2.11.9](https://github.com/wheaney/XRLinuxDriver/releases/tag/v2.11.9), 2026-09-14 | `36e2139` | 2.4.0 | identical |
| `main` | still 2.11.9 | `9589084`, 2026-10-21 | 2.4.0 | no, README only |
| AUR `xr-driver-git` | 2.11.9-1 | pins `36e2139` | 2.4.0 | identical |
| Breezy Desktop [v2.12.3](https://github.com/wheaney/breezy-desktop/releases/tag/v2.12.3) | bundles driver | submodule `36e2139` | 2.4.0 | identical |
| `dev` tag / `rayneo_open` branch | 2.12.1 | `fdb0b7f`, 2026-10-23 | 2.4.0 | no, see below |

Version numbers collide across projects. Breezy v2.11.9 and XRLinuxDriver v2.11.9 are unrelated
releases, and Breezy 2.12.x does not contain driver 2.12.x.

`rayneo_open` changes RayNeo support, a `uint16_t` type fix, a new `debug_imu` flag, and one line in
`handle_control_flags_update` so an SBS change request reads cached state instead of asking the
device. That line only runs when SBS is requested, and the 1 Hz poll stays. The branch changes no file
under `lib/x86_64/viture/` and does not touch `src/devices/viture.c`. The `dev`
tag's `xrDriver` binary (built 2026-10-27) reports version 2.12.1; I did not run it.

What already landed before 2.11.9: SDK 2.4.0 arrived in
[v2.11.0, PR #138](https://github.com/wheaney/XRLinuxDriver/pull/138). The maintainer hoped it would
fix the Carina segfaults, and a user reported they then happen only
[occasionally](https://github.com/wheaney/XRLinuxDriver/issues/122). Cody's log has **0 segfaults**,
so this is not the same failure. v2.11.1 fixed a libusb clash, and 2.11.2 to 2.11.9 are not relevant
to Luma Ultra USB ([maintainer on 2.11.0 to 2.11.5](https://github.com/wheaney/breezy-desktop/issues/186)).

### Open upstream PRs (none merged)

| PR | What it does | Helps us? |
|---|---|---|
| [#148](https://github.com/wheaney/XRLinuxDriver/pull/148) | Fixes a use-after-free when a device leaves during a blocking worker | Possibly survival after a drop; cannot prevent one. No crash seen in our log |
| [#147](https://github.com/wheaney/XRLinuxDriver/pull/147) | Keeps `uaccess` on later udev events | Unlikely: our reconnects already work. Only matters if a reconnect ever fails on permissions |
| [#145](https://github.com/wheaney/XRLinuxDriver/pull/145) | XREAL null check | No, wrong device |

## What the evidence says

Sources are local and read-only: the v2.11.9 source, the previous boot's kernel log and
`~/.local/state/xr_driver/driver.log`. These numbers come from one read on 2026-11-03 and are not
monitored.

### `get_display_mode -7` is a symptom, not the cause

- VITURE defines `-7` as `DEVICE_REJECTED`, "Device rejected the command at firmware level"
  ([SDK docs](https://www.viture.com/developer/glasses-sdk/glasses)). Our log disagrees with that
  reading: each `-7` comes straight after `Failed to send USB command - MsgID: 0x0007, error code: -1`
  (449 send failures, 439 `-7`). The command never got out, so the glasses cannot have refused it.
- The caller is a roughly 1 Hz loop: `driver.c:546-551` calls `update_state_from_device`, which
  reaches `viture_device_is_sbs_mode` and then `get_display_mode`. In the session I sampled, one
  failure pair sits right before `Retrying`. So this looks like the poll noticing the device has gone.
  `[E]` lines have no timestamps, so that placement comes from log order alone.
- `Retrying driver connection` is not an error from the glasses. `viture_block_on_device` exits when
  the IMU has been silent for 1 s (`is_imu_alive`, `outputs.c:645`), and `driver.c:361` logs the retry.

Polling is still unwelcome traffic, so the "no display-mode polling" rule in the design contract
stands, but nothing shows the poll causes a drop.

### The hub disappears cleanly

- The previous boot has 54 `usb 5-2: USB disconnect` lines: 6 on 11-01, 11 on 11-02, 37 on 11-03.
- There are no `error -71`, `-110` or over-current lines around them, no xHCI failure, and only one
  stray `can't read configurations`. The whole hub leaves, so it looks like the far end vanishing,
  which points away from the host's USB controller and toward the glasses, the connector or power.
- There are **0** `reset high-speed USB device` lines. `libcarina_vio.so` and `libGlassSDK.so` do
  import `libusb_reset_device`, but no USB-level reset from the host is visible at these drops.
- Time from calibration to drop ranges from 6 s to 6646 s. There is no fixed timer. Across 18 clean
  driver runs the median was 344 s.

### Which came first? It is mixed, and many cases are unknown

| Group | Drops | Reading |
|---|---|---|
| Driver `Retrying` 0.4-2.1 s **after** the disconnect | 15 | USB went first. Matches the 1 s IMU timeout plus the 1 s loop |
| `Retrying` **before** the disconnect, within 9 s, none just after | 9 | Often the IMU stream failed first, the device stayed on USB for about 7-12 s, then the hub dropped |
| No `Retrying` within 15 s | 30 | **Undetermined** |

Three cautions for whoever reads these figures:

1. **The 9 could be cascades.** A drop's retry can sit just ahead of the next drop, so the second
   may be caused by the reconnect itself. At least 4 fit stream-loss-then-drop, such as 23:00:01
   retry followed by the 23:00:08 drop.
2. **The 30 unmatched drops qualify "drops only while the driver runs".** The driver logs nothing
   on exit, and a real unplug or power-off writes the same kernel line. On 11-02, drops at 08:46,
   08:54 and 09:34 have no driver log activity near them. I could not establish whether the driver
   was alive then. This needs a proper per-session count from the `usb-drop-fix` thread.
3. **Drops cluster.** 18 of 53 came within 30 s of the previous one, so 37 a day is fewer than 37
   independent events.

### Not 120 Hz

The driver does contain a forced 1920x1200@120 Hz override (`viture.c:351-369`), and it is tempting.
It **does not run on a Luma Ultra**. It is guarded by `viture_supports_native_dof()`, and
`xr_device_provider_is_product_support_native_dof(0x1104)` returns 0 from the installed SDK. No public
report ties a Luma Ultra USB drop to refresh rate. The nearest items are DisplayPort problems:

- [VitureBD](https://github.com/zerobitfulladder/VitureBD) says its Luma Ultra (firmware 12.0.02.002)
  accepts 1200p at 120 Hz "but never comes up in it".
- [viture-beast-linux](https://github.com/blchinezu/viture-beast-linux) fixes amdgpu DP link
  training for 120 Hz on the Beast, on a different model.

Neither explains a USB hub leaving during a session.

## Ranked likely causes

These are judgments from indirect evidence. No item is proven.

| # | Cause | Likelihood | For | Against / unknown |
|---|---|---|---|---|
| 1 | **6DoF session load**: stereo cameras and host-side camera tracking add USB traffic and glasses power draw until the hub browns out or the firmware resets | Medium, best candidate | Explains why a plain display is fine. `viture.c` never calls `xr_device_provider_set_dof_type_carina`, and the [header](https://github.com/wheaney/XRLinuxDriver/blob/main/include/sdks/viture_device_carina.h) says the default is 6DoF. The maintainer has [raised USB power draw](https://github.com/wheaney/breezy-desktop/issues/186) as affecting Luma Ultra sessions, though that was about drift | No public report confirms it. The camera rate is only 25 Hz, so raw traffic may be modest. Power figures are unknown |
| 2 | **Glasses firmware fault under SDK traffic**: firmware hangs or restarts, taking the hub with it | Medium | The IMU seems to die before the hub in some cases. A clean hub exit fits a restart. A user in [#122](https://github.com/wheaney/XRLinuxDriver/issues/122) saw it work "for about 10 minutes" before crashing again | The firmware version here is unknown, and no public Luma Ultra changelog could be read |
| 3 | **Marginal link or power**: magnetic connector, cable, port current | Medium | The maintainer's first guess in [#122](https://github.com/wheaney/XRLinuxDriver/issues/122) and [Breezy #192](https://github.com/wheaney/breezy-desktop/issues/192). [Repair guidance](https://www.madlabsrepair.com/blog/viture-xr-glasses-repair-luma-beast-pro-one-usb-c-display-audio-help) lists reseating it. Kernel lines look like a loss of link | Alone, it does not explain the plain-display result, unless the driver's load is what exposes it. Then 1 and 3 are the same problem |
| 4 | **Startup or reconnect churn**: SDK setup or teardown knocks the glasses over again | Medium for the *clusters*, low for first drops | 18 of 53 drops follow one within 30 s. [Breezy #181](https://github.com/wheaney/breezy-desktop/issues/181) shows the same hub `1a86:8091` dropping 3.7 s and about 10 s after enumeration during repeated failed starts | That run was crash-looping on a libusb bug fixed in 2.11.1 |
| 5 | **USB autosuspend** | Low | Sensible to rule out. Owned by `usb-drop-fix` | A 1 kHz stream keeps the device busy, and no upstream report mentions it |
| 6 | **Our launch differs from everyone else's**: network namespace, stubbed DNS and identity | Low | Nobody upstream runs this way. The log shows `curl` and `hardwareId` errors each start | No path from these to a USB hub loss is visible. Not tested |
| 7 | **Memory pressure** | Very low | One `xrDriver: page allocation failure` on 11-01, reached through `usb_alloc_urb`. [Breezy #190](https://github.com/wheaney/breezy-desktop/issues/190) reports 10 GB use | A single event, with 54 drops. The maintainer's possible leak there is unconfirmed |
| 8 | **SDK segfault** | Ruled out here | Well known upstream | 0 segfaults in Cody's log |
| 9 | **Forced 120 Hz mode** | Ruled out for the override | | The override never runs on `0x1104` |

## Fixes and workarounds that exist

| Option | Source | Status for us |
|---|---|---|
| Keep virtual desktops across a blip | [Breezy #192](https://github.com/wheaney/breezy-desktop/issues/192) | Same idea as the survival work; Breezy-only setting |
| Reseat the magnet, change cable, use a direct port | [#122](https://github.com/wheaney/XRLinuxDriver/issues/122), repair guide | Free. Worth one try. Needs Cody |
| Update firmware | [VITURE](https://www.viture.com/firmware/update) | Page names Windows and Mac and Chrome 98+, and warns not to unplug during the update. Luma Ultra on Windows needs SpaceWalker first. Linux not mentioned. Cody's version unknown |
| `xr_driver_setup --tag vX` to pin a version | [Breezy #190](https://github.com/wheaney/breezy-desktop/issues/190) | Avoid: it conflicts with the AUR install, and downgrading is untested for drops |
| Delete `config.ini` when `xrDriver` exits at once | [#122](https://github.com/wheaney/XRLinuxDriver/issues/122) | Different symptom |
| Replug and retrigger udev on cold boot | [#129](https://github.com/wheaney/XRLinuxDriver/issues/129) | Different symptom |
| VITURE Adapter Ultra (65 W passthrough) | [VITURE](https://www.viture.com/academy/xr-glasses/luma-series) | Only adapter of this kind listed for Luma Ultra. Not reported as a drop fix, but a fair test for cause 1 or 3 |

## Experiment: 3DoF versus 6DoF

Needs Cody, because it runs the glasses. XR must be off.

The patch makes the SDK skip 6DoF. Compile-tested only (with PRs #147 and #148 merged in); it has
never run on glasses, so `set_dof_type_carina` might refuse or behave differently in practice. Yaw
will probably drift more without the cameras, since they feed tracking. The call must sit after
`create` and before `initialize`, and it needs a full restart to change
([SDK docs](https://www.viture.com/developer/glasses-sdk/glasses)).

```diff
--- a/src/devices/viture.c
+++ b/src/devices/viture.c
@@ viture_initialize_provider_locked, right after the provider NULL check
+    if (xr_device_provider_get_device_type(viture_provider) == XR_DEVICE_TYPE_VITURE_CARINA) {
+        int dof_status = xr_device_provider_set_dof_type_carina(viture_provider, 0);
+        log_message("VITURE: requested 3DoF (status %d)\n", dof_status);
+    }
```

Run each arm for 2 hours, glasses on, in a similar room and with the same cable and port. The
longest clean run seen was 71 minutes, so one hour proves little. Repeat the winner once.

| Arm | Binary |
|---|---|
| A, baseline | `/usr/bin/xrDriver` |
| B, 3DoF | the per-user build below |

Count with `journalctl -k --since "<start>" | grep -c 'usb 5-2: USB disconnect'`. Stay plugged in, so
no count is a deliberate unplug.

Reading the result:

- **B has no drops and A keeps dropping:** the 6DoF session is the trigger (causes 1 or 2). A patched
  driver becomes a stopgap, and an IMU-only driver looks right.
- **Both drop:** it is not the cameras. Suspect firmware, the link, or IMU streaming itself. In that
  case our own driver may drop too, and the swarm should know before relying on it.
- **Neither drops, or both are low:** the condition changed. The machine moved from kernel 7.2.8 to
  7.2.15 at 23:31 on 11-03, so measure the baseline again on this kernel first.

## Per-user build

No `sudo`, and nothing touches `/usr`. Needs `cmake` and a C compiler plus the libraries
`xr-driver-git` already pulled in. I ran these
steps in a scratch folder on 2026-11-03: `main` plus PRs #147, #148 and the 3DoF patch compiled and
linked with no errors. **The result was never run.**

```sh
mkdir -p ~/.local/src && cd ~/.local/src
git clone https://github.com/wheaney/XRLinuxDriver.git xrld-test
cd xrld-test
git checkout 958908487a36aad15c6cbb5b7865e404c7b7dfac    # main today

# Optional, for the 3DoF arm: apply the patch above by hand in src/devices/viture.c
# Optional, reconnect fixes:
#   git fetch origin pull/148/head:pr148 pull/147/head:pr147
#   git -c user.name=t -c user.email=t@t merge --no-edit pr148 pr147

git submodule update --init --recursive modules/xrealInterfaceLibrary
mkdir -p build && cd build
cmake ..
make -j"$(nproc)"
readelf -d xrDriver | grep -E 'RPATH|RUNPATH'   # must point inside xrld-test
ldd xrDriver | grep -E 'glasses|carina|not found'   # no "not found"
```

Keep the `xrld-test` folder: the binary finds its vendor libraries there through RUNPATH.

Do **not** use `xr_driver_setup` or `bin/setup`. They install a second copy into `~/.local`, and the
AUR package refuses to build alongside one (`PKGBUILD`, `prepare()`).

`tools/native_spike/tracking.py` hard-codes `DRIVER = '/usr/bin/xrDriver'`. Options:

- Run the experiment in a throwaway worktree where `DRIVER` points at the new binary, and start it
  the normal way. This is the fair test: same fake MAC, tmpfs and namespaces.
- Do not overwrite `/usr/bin/xrDriver`. The next `pacman` update would silently undo it.

A live session uses the `tracking.py` the controller was installed from, so pointing it elsewhere is
a deliberate step for Cody to approve.

## Notes for the other driver threads

- **The Luma Ultra may not be HID.** VITURE says its control interface
  "uses USB bulk transfer rather than HID, so hidapi may not enumerate it"
  ([SDK docs](https://www.viture.com/developer/glasses-sdk/glasses)). Our log shows 512-byte packets,
  a "V1" protocol path and `MsgID 0x0007`. `transport.hpp` should expect usbfs bulk first, with
  hidraw only as a fallback. Confirm this from the real device's interface listing.
- **[elasticjava/viture-v2](https://github.com/elasticjava/viture-v2) is the best open protocol
  reference, but it was never run on a Luma Ultra.** It is MIT licensed, has no vendor SDK, and
  documents HID frames on a Pro 2 (`35ca:1301`) including `0x0301` IMU control and a "no handshake
  required" result. Its author lists Luma and Luma Pro as candidates and does not list the Ultra. On
  our device the transport appears different, so treat its message IDs as hypotheses to verify.
  It also shows how: match a `usbmon` capture to the SDK's own log lines.
- **Rates.** The installed SDK reports all six IMU rates as supported on `0x1104`, and the driver
  treats Carina as a fixed 1 kHz (`VITURE_CARINA_CYCLES_PER_S`). Each sample also calls
  `xr_device_provider_get_gl_pose_carina`, so our pose comes from the SDK's tracker, not raw IMU.
- **Other public Luma Ultra users exist.** VitureBD uses the SDK and polls pose about 1000 times a
  second. Its README does not mention drops. Its 3DOF or 6DOF note says the choice is fixed at start.

## What I could not check

- **r/VITURE.** Reddit refused the direct fetch, and searches returned no Luma Ultra disconnect
  thread. Absence here is weak evidence. Someone logged in could search it in a minute.
- **VITURE's firmware notes.** The Luma Ultra entries did not load in the fetched pages, and the
  only copy found was a third-party page with no Luma Ultra entries. I found no firmware changelog.
- **Attached logs** in GitHub issues were not opened.
- **VITURE support, Discord and other private forums** were not reachable.
- **Cody's firmware version.** It is not in `driver.log`, and reading it means talking to the device.
- **Exact power draw** in 3DoF, 6DoF and plain-display use. VITURE publishes none that I could find.

## Sources

Issues and PRs: XRLinuxDriver
[#105](https://github.com/wheaney/XRLinuxDriver/issues/105),
[#122](https://github.com/wheaney/XRLinuxDriver/issues/122),
[#125](https://github.com/wheaney/XRLinuxDriver/issues/125),
[#126](https://github.com/wheaney/XRLinuxDriver/issues/126),
[#129](https://github.com/wheaney/XRLinuxDriver/issues/129),
[#139](https://github.com/wheaney/XRLinuxDriver/issues/139),
[PR #138](https://github.com/wheaney/XRLinuxDriver/pull/138),
[PR #145](https://github.com/wheaney/XRLinuxDriver/pull/145),
[PR #147](https://github.com/wheaney/XRLinuxDriver/pull/147),
[PR #148](https://github.com/wheaney/XRLinuxDriver/pull/148);
Breezy Desktop
[#149](https://github.com/wheaney/breezy-desktop/issues/149),
[#181](https://github.com/wheaney/breezy-desktop/issues/181),
[#186](https://github.com/wheaney/breezy-desktop/issues/186),
[#190](https://github.com/wheaney/breezy-desktop/issues/190),
[#192](https://github.com/wheaney/breezy-desktop/issues/192).

Vendor: [SDK docs](https://www.viture.com/developer/glasses-sdk/glasses),
[firmware update](https://www.viture.com/firmware/update),
[Luma series](https://www.viture.com/academy/xr-glasses/luma-series).

Other projects: [viture-v2](https://github.com/elasticjava/viture-v2),
[VitureBD](https://github.com/zerobitfulladder/VitureBD),
[viture-beast-linux](https://github.com/blchinezu/viture-beast-linux).

Community: [Luma Pro desktop guide](https://cktechcheck.com/viture-luma-pro-desktop-pc-connection-guide/)
(sibling model with random reconnects; a search summary says the writer freed USB current and
disabled USB power saving on Windows. The page refused a direct fetch, so whether that cured it is
unconfirmed).
