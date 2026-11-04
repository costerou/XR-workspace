# 6DoF on the Luma Ultra under Linux: feasibility

Research only, 2026-11-03. Nothing here streamed from a camera or opened the glasses. One
`v4l2-ctl --list-devices` woke the colour camera for a moment; I then stopped using v4l2 and read the
same facts from the kernel's cached descriptors. Tools are in `spike/driver/research/`. Each claim below is marked **measured** (read on this machine), **source**
(read in a public header or page), or **inferred** (my reasoning; check it before building on it).

## Answer

**Yes, position tracking on Linux is possible, but not from the camera we were looking at, and it
should not be switched on in our driver yet.**

1. The camera at 5-2.2 is the front colour camera. It is the wrong sensor for tracking. Tracking uses
   two separate grayscale side cameras that are not UVC devices.
2. Position is computed on this PC, by VITURE's closed library. It is not computed on the glasses.
3. **XRLinuxDriver very probably runs those tracking cameras in every session today**, because 6DoF
   is the SDK default and the driver never changes it. We throw the position away. This is a new
   suspect for the USB drops that nobody has tested: it is on only while the driver runs, which is
   exactly when the drops happen.

**Recommendation:** build our driver as 3DoF with every camera off, and leave that as the default.
First run one cheap test (A/B below) to learn whether the tracking cameras cause the drops. Treat
6DoF as a later flagged feature that must pass a soak test, and only after Cody decides on the SDK
licence. Do not use the colour camera for tracking.

## What the hardware exposes

Decoded from the kernel's cached descriptors, which send nothing on the bus
(`usb_descriptors.py`; copies in `research/fixtures/`).

| Port | Device | What it is |
|---|---|---|
| 5-2 | `1a86:8091` hub | Multi-TT USB 2 hub, says it is self-powered |
| 5-2.1 | `35ca:1104` glasses | One vendor interface, 7 endpoints, no UVC |
| 5-2.2 | `0c45:636b` Sonix "USB 2.0 Camera" | Ordinary UVC 1.00 webcam, front colour camera |
| 5-2.3 | `35ca:1102` microphone | Full-speed audio plus 3 HID interfaces |

**The Sonix camera (measured)** has one streaming interface and two formats:

| Format | Best modes |
|---|---|
| MJPEG | 1920x1080 down to 160x120, all at 30 fps |
| YUY2 | 1080p at 5 fps, 720p at 10, 800x600 at 20, 640x480 at 25 |

There is no stereo mode, no side-by-side size and no GREY/Y8 format, so nothing suggests infrared or
a tracking sensor. It carries a Sonix extension unit, which is normal for this chip; I did not read
its controls because that would wake the camera. VITURE's SDK page confirms this is the pass-through
camera, separate hardware from the stereo pair (**source**). Whether it is rolling shutter is not
published, but cheap UVC modules usually are (**inferred**).

**The tracking cameras (source: `viture_device_carina.h`, VITURE SDK page)** are two grayscale side
cameras, 8-bit, 25 Hz, delivered as four buffers per frame (`left0, right0, left1, right1`). The
docs give both 25 Hz and "about 30 Hz" in different places. Resolution is not published. What the
two exposures per frame are for is not stated. Exposure is 0.01 to 8 ms, auto by default.

They do not appear as a UVC device, so their frames must come through the vendor interface on
5-2.1, which has three bulk IN endpoints: `0x82`, `0x85`, `0x87` (**inferred**; which one is
unknown). The two interrupt endpoints (`0x81` every 500 µs, `0x83` every 1 ms) are the obvious IMU
and event paths (**inferred**). The protocol threads own that question.

No magnetometer reaches the Carina IMU callback (`[ax, ay, az, gx, gy, gz]` in the header). This
matters for yaw drift below.

## Where position is computed

**On the host, inside the closed SDK.** VITURE's page says "onboard Carina VIO engine", and one wiki
says the glasses do the work, but the evidence points the other way:

- The SDK ships a 27 MB `libcarina_vio.so` with OpenCV for x86-64 and aarch64 (**measured** for the
  size). A `.so` for the host has no reason to exist if the glasses did the work.
- The SDK hands raw stereo frames to the host (`XRCameraCallback`) (**source**).
- The Spatial-Screens project reports VIO running on the host via that library (**source**).
- VITURE's own wording elsewhere is that 6DoF needs the Pro Neckband or SpaceWalker on Mac/Windows,
  so a compute host is always involved (**source**, consumer pages).

"Onboard" may mean the IMU fusion or sync is on the glasses. I could not confirm that the library is
actually mapped into `xrDriver`; `session_probe.py` prints this on the next session.

## Today's driver is probably already in 6DoF

- `viture_device_carina.h`: `set_dof_type_carina` "Default is 6DOF" (**source**).
- XRLinuxDriver 2.11.9 has no call to it anywhere in `src/` (**measured**, `git grep`).
- `src/devices/viture.c` polls `get_gl_pose_carina` at each IMU callback and declares
  `provides_position = true` for Carina (**source**).
- `pose_position` is written to shared memory beside the orientation (`src/outputs.c:549`). A sample
  left over from the session before 23:29:58 held about 32, 36 and 22 µm, not zero. The reset value
  is zero, so the last write came through the pose path (**measured**, one sample). That fits a live
  tracker on a still pair of glasses, but it is not proof: I do not know what 3DoF mode writes.

**Not yet proven:** that the cameras stream. A 75-second live measurement was running when the PC
was restarted and its result was lost. The transfer counter gave roughly 1,100 transfers a second
across two sessions (**inferred** from 102,922 transfers over about 95 s of driver time), which is
IMU rate plus a bit, but one transfer can hold anything from 64 bytes to 16 KB or more, so it does
not settle it.

`spike/src/pose.cpp` reads only the orientation, so nothing downstream uses position today.

## What this means for the drops

The current thinking is that the driver's USB command traffic (`get_display_mode`, error -7) trips
the drops. The evidence so far cannot separate that from the new suspect, because both are present
only while the driver runs:

| Suspect | On as a plain display? | On while xrDriver runs? |
|---|---|---|
| USB command polling | No | Yes |
| Tracking cameras on, bulk video through the hub (probable) | No | Yes |
| Extra power draw from those cameras and their processing on the glasses (probable) | No | Yes |

Our planned driver removes all three if the cameras stay off, so it should help whichever is true.
The A/B test tells us which one it was, and therefore whether 6DoF can ever return.

Also seen, for the `usb-drop-fix` thread: every driver start logs either
`do sync data transfer :-7 512 0` (33 of 69 starts) or `remain sync data transfer :15872 15872`
(36 of 69). Error -7 is a timeout, it looks like a bulk read, and I do not know what it means.

### The 500 mA question

The colour camera's "500 mA" is not what is hurting us:

- **It was asleep during sessions (measured).** Across two driver sessions after 23:18 it logged 15 s
  awake out of 660 s, some of that my own query, and its transfer count stayed flat. The SDK does not use it. Drops happen with
  it suspended.
- The figure is a stock descriptor value, a maximum and not a measurement. The glasses declare 2 mA
  while running two OLED panels, so these numbers mean little.
- The kernel enforces no budget here, because both the hub and the glasses say they are self-powered.
  All claims together come to 702 mA, and nothing refuses them.
- Over-current counters are 0 on the root port and all four hub ports (**measured**).
- This PC has no `/sys/class/typec`, so software cannot see the USB-C power contract or real draw.
  **Only an inline USB-C power meter can answer this.** No public wattage figure exists for the
  Luma Ultra.

Streaming the colour camera would add real load whatever the true number is, and it can reserve up
to 24.6 MB/s of isochronous bandwidth, about half of what a high-speed bus allows for periodic
traffic, on the same hub as the IMU. On a link that already drops, that is the wrong direction.

## Options

| | Route | Extra USB risk | Latency / quality | Licence | Verdict |
|---|---|---|---|---|---|
| A | **3DoF, cameras off** | Lowest possible | IMU rate; yaw can drift | Clean | **Default** |
| B | A plus a **head-neck model**: small position derived from rotation | None | Instant, but fake: leaning does nothing | Clean | Good flagged feature |
| C | Run VITURE's SDK for 6DoF | Same as today | Vendor-tuned; accuracy unmeasured | **Needs Cody** | Only as a separate experiment |
| D | **Own VIO** (Basalt) on the stereo cameras | Same cameras, same load | Best open option, big job | Clean code; the stream format is the problem | Later, if the gates pass |
| E | Own VIO on the colour camera | **Highest** | Mono, 30 fps, likely rolling shutter, no IMU sync | Clean | **Rejected** |

I found no independent measurement of Carina's position accuracy, and reviews call the feature
promising but unfinished, so route C's quality is not a known quantity.

Routes C and D cannot run alongside our driver as designed: both need the same USB interface, so
6DoF would have to be one process owning the device. Spatial-Screens also found that the SDK segfaults
when USB open fails, so it is a poor thing to have in the reconnect path.

**The honest cost of route A:** Cody's bar includes "no drift". With no magnetometer and no cameras,
nothing corrects yaw against the outside world; a good fusion filter slows it but cannot end it.
Pitch and roll are fine, gravity holds them. How much yaw drift is a measurement for `drv-fusion`.
If it is visible, a recentre key or occasional low-rate correction is the mitigation. This is the
strongest reason to keep the 6DoF route open.

### What open projects do

- **Monado** has no VITURE driver that I could find. It supports XREAL Air glasses at 3DoF and has
  experimental camera tracking through external systems including Basalt.
- **Basalt** is the natural engine: BSD-3 licensed and described as very fast. A Monado WMR merge
  request reported Basalt working "at frame rate (30fps) thus very clunky", which is the lesson: raw
  VIO output is too slow to show, so the IMU has to carry the pose between camera frames.
- **ORB-SLAM3** is GPLv3 and unmaintained since 2020. **OpenVINS** and **Kimera-VIO**: I could not
  confirm their licences. Check before considering them.
- **Spatial-Screens, Viture_AR_Playground, DroidDeck** all use the closed SDK. DroidDeck ships hand
  tracking as experimental and off by default, and reported removing USB reset experiments. Nobody
  has published an open protocol for the stereo stream.

### Why route D is a big job

An open VIO needs four things the public material does not give us:

1. The command that starts the stereo stream, and its frame layout.
2. Camera calibration for each unit. The SDK fetches this at startup
   (`VITURE_GLASSES_ERROR_CALIB_INIT`), so it probably lives on the glasses (**inferred**).
3. Camera-to-IMU timing.
4. A resolution and format good enough to track.

The only practical way to get 1 to 3 is to watch the SDK's USB traffic. Whether that is allowed
under VITURE's licence is a question for Cody; I have not seen the licence text. Note the contract
says no VITURE SDK. This research used the open-source repo's headers and public pages, plus the
symbol names of the installed library to confirm the shim below can work. `dof3_preload.c` is an
experiment on the existing XRLinuxDriver setup and is not part of our driver.

## Cost

**CPU (measured).** Whole sessions, renderer and driver together, used 30 to 42% of one core across
18 of 19 long runs on Nov 3 (one outlier 5%). If the tracker runs in `xrDriver`, it fits inside that.
So the vendor tracker costs well under half a core of the Ryzen 7 9700X, and CPU is not the obstacle.
Exact split: pending `session_probe.py`.

**Own front end, on synthetic frames (measured, `frontend_bench.cpp`).** One core, stereo pair, load
average about 6 at the time:

| Image size | Corners | Mean | p99 | One core at 25 Hz |
|---|---|---|---|---|
| 640x480 | 150 | 3.0 ms | 6.3 ms | 7.5% |
| 640x480 | 300 | 5.1 ms | 10.2 ms | 12.8% |
| 1280x800 | 150 | 5.7 ms | 11.8 ms | 14.2% |
| 1280x800 | 300 | 9.8 ms | 16.4 ms | 24.5% |

This leaves out the optimiser, so it is a floor. I did not measure a whole tracker; my guess is two to
three times these figures, which is still under one core on this 16-thread CPU. **GPU:** none
needed; these trackers are CPU-only, and the RX 7800 XT should not be touched.

**Latency (inferred).** Orientation stays gyro-driven at IMU rate, so 6DoF need not slow it. Position
corrections arrive at 25 Hz, so at least 40 ms apart plus exposure, USB and processing: perhaps 50 to
70 ms old. That is too old to show directly, so position must be carried by the IMU between frames.
That works over short gaps and fails over long ones. With a leftover accelerometer error of
0.02 m/s²:

| Time without a camera fix | Position error |
|---|---|
| 40 ms (one frame) | 0.016 mm |
| 1 s | 1 cm |
| 10 s | 1 m |

So position cannot come from the IMU alone, and tracking loss (dark room, blank wall) must fall back
to holding position, not extrapolating.

**Value (inferred).** For fixed desktops, 6DoF means leaning in reads closer, sideways moves show
parallax, and yaw stops drifting. That is good, but it is not required to work. A neck model gives
some of the parallax for nothing: a 30° head turn with eyes 10 cm in front of the neck moves them
5 cm, about 2.9° of parallax on a screen 1 m away.

## Test: are the tracking cameras causing the drops?

Baseline from the journals, Nov 3 (**measured**): 6.9 session hours, 22 drop clusters (37 drops),
and 16 of 31 sessions ended by a drop. That is about 3.2 clusters an hour.

**Stop rule:** at that rate, three session hours with no drop would happen by luck about 1 time in
15,000, though drops come in bursts, so treat that as a rough figure. It is a clear result. Any drop cluster inside the first hour means the cameras are not the
whole story. Use the same cable, port and desk habits as a normal day.

`dof3_preload.c` makes one session 3DoF with no rebuild. It wraps `initialize` and calls
`set_dof_type_carina(handle, 0)` first, as the header requires. It is tested only against a fake
library. **It has never run on the real driver or glasses**, and 3DoF mode's behaviour on this SDK
version is unknown. Tracking may feel different. These are Cody's steps; start from XR stopped.

```bash
cd ~/.t3/worktrees/viture-workspace/drv-6dof-research/spike/driver/research
mkdir -p ~/.local/lib
cc -O2 -Wall -shared -fPIC -o ~/.local/lib/dof3_preload.so dof3_preload.c -ldl
systemctl --user set-environment LD_PRELOAD=$HOME/.local/lib/dof3_preload.so
# Start XR from the panel as normal, wait for tracking, then:
cat "$XDG_RUNTIME_DIR/xr-dof3-applied"          # expect: dof_type=0 result=0
python3 -I session_probe.py --seconds 60        # compare with a normal session
# No file? The setting did not reach the driver, so this session is an ordinary one.
# When finished, whatever the result:
systemctl --user unset-environment LD_PRELOAD
rm -f "$XDG_RUNTIME_DIR/xr-dof3-applied"
```

`set-environment` applies to every user service started while it is set. The shim does nothing in
any program but the driver, because no other program calls that function, but do unset it afterwards.

Reading a normal session versus a 3DoF one:

| `session_probe.py` field | Cameras on | Cameras off |
|---|---|---|
| `position_moved` while you move your head | true | false |
| `glasses_urbs_per_s` | higher | lower |
| `threads` | an image-processing thread stands out | gone |
| `libraries` | `libcarina_vio.so` mapped | either |

**Best extra evidence (needs sudo):** per-endpoint traffic during a normal session. This only listens.

```bash
sudo modprobe usbmon
sudo timeout 20 cat /sys/kernel/debug/usb/usbmon/5u > /tmp/usb5.txt
awk '$3=="C"{n[$4]++; b[$4]+=$6} END{for(k in n) printf "%-16s %8d urbs %12d bytes\n", k, n[k], b[k]}' /tmp/usb5.txt | sort
```

A bulk IN line (`Bi:`) with megabytes a second means the cameras are streaming, and bytes per frame
gives the resolution. Tens of kilobytes means they are not.

**Power reading:** an inline USB-C power meter, in the glasses' cable, in four states: plain display;
XR with the normal driver; XR 3DoF; and at the moment of a drop if one can be caught.

## Phased plan

Each phase has a gate; do not start the next until it passes.

| Phase | Work | Who | Gate |
|---|---|---|---|
| **0. Facts** | `session_probe.py` on one normal session; usbmon and meter if convenient | Cody, 5 minutes | Know: do the cameras stream, and what they cost |
| **1. A/B** | Three session hours of 3DoF | Cody | Answers "are cameras a drop cause?" and yes or no on `set_dof_type` working |
| **2. Own driver** | Swarm's driver, 3DoF, no camera traffic. Default stays xrdriver until Cody accepts | Swarm | Zero drops over a full working day |
| **3. Cheap features** | Neck model behind a flag; yaw drift measured; recentre | `drv-features`, `drv-fusion` | Drift number agreed with Cody |
| **4. Decide** | Cody picks: stay at 3DoF, or go on. Needs the licence decision | Cody | A decision |
| **5. Camera soak** | Cameras only, no tracking: stereo stream on for hours with meter attached, flag off by default | Swarm | **Zero drops.** One drop and 6DoF stops here |
| **6. Tracker** | Basalt in its own process; poses fed into the fusion as low-rate corrections; hold on tracking loss; a new position IPC (existing feed stays quaternion-only); renderer parallax behind a flag | Swarm | Lean test passes; no drops; latency unchanged |

If phase 1 shows the cameras cause the drops, phases 5 and 6 are only worth attempting if power or a
different cable or port can fix that, so the meter reading then decides. If phase 1 shows they do
not, 6DoF becomes much safer and the same steps apply.

**A low-risk peek, any time after phase 0:** while xrDriver stays in use, the renderer could log the
`pose_position` feed it already ignores, to judge the vendor tracker's quality on this desk before
anyone invests. Units are 2.54 m per 1.0, relative to the calibration pose. No new USB load. This
only tells us something if the driver's 6DoF stays in use, so it comes before phase 2.

## Design notes for other threads

- **USB access after a boot (found today, affects everything).** The installed
  `70-viture-xr.rules` tags `uaccess` only on `ACTION=="add"`. After the restart at 23:31 the glasses
  node lost its user ACL (`CURRENT_TAGS=:seat:`, no `uaccess`), and `xrDriver` segfaulted right after
  finding the device, four times at 23:33. The link between the two is **inferred**, from Spatial-Screens
  reporting the same SDK crash when USB open fails. Replugging should fix it, or:
  `sudo udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=35ca`.
  Our own driver's rule should use `ACTION!="remove"`, and it must report a clean "no permission"
  and never crash.
- **Keep the camera asleep.** Do not open `/dev/video*`, not even to query it: `open()` wakes it.
  Nothing in the driver should touch 5-2.2. Do not reset it, and do not unbind it.
- **Enumerate by VID/PID, not port.** After the reboot the glasses are still 5-2.1, but device numbers
  change.
- `ImuSample` and `PoseOut` need no changes for 3DoF. Position would be a new optional field later.
- **Open question for `drv-protocol-*`:** can the IMU stream start without the cameras on the wire?
  `set_dof_type(0)` suggests the hardware can, but it is the SDK deciding. On Carina `open_imu` is
  "no effect", so the start sequence may be shared with camera startup.
- Spatial-Screens notes a constant pitch offset of about 35° because the tracker reports the
  downward-angled camera rig's pose, not gaze. Expect this if a VIO pose is ever used.

## Unknowns

- Whether the tracking cameras really stream during sessions, and how much traffic and power they take.
- Whether 3DoF mode truly turns them off. A VITURE FAQ quoted by search results says the side cameras
  are only activated for 6DoF, but I could not read that page myself.
- Stereo resolution; the meaning of the four buffers; which bulk endpoint carries them.
- Real power draw, and how much the 6DoF path adds.
- Whether libcarina_vio is mapped into xrDriver, and its CPU share.
- Carina's position accuracy and the size of 3DoF yaw drift.
- The licence terms.

## Sources

Local: XRLinuxDriver 2.11.9 (`9589084`), bare repo in the yay cache: `include/sdks/viture_device_carina.h`,
`viture_glasses_provider.h`, `src/devices/viture.c`, `src/outputs.c`, `src/driver.c`; sysfs under
`/sys/bus/usb/devices`; kernel and user journals; `~/.local/state/xr_driver/driver.log`.

Public:
- [VITURE XR Glasses SDK](https://www.viture.com/developer/glasses-sdk/glasses)
- [VITURE: choosing your glasses](https://www.viture.com/blog/the-ultimate-guide-to-choosing-your-new-viture-glasses-luma-pro-luma-ultra-luma-and-the-beast)
- [Spatial-Screens](https://github.com/SalvatoreMastrangelo/Spatial-Screens) (host-side VIO, HID notes, 35° offset, segfault without udev rule)
- [Viture_AR_Playground](https://github.com/brianhasquestions/Viture_AR_Playground)
- [DroidDeck PR 1035](https://github.com/Droid-Deck/DroidDeck/pull/1035)
- [omarchy-xr issue 21](https://github.com/afruth/omarchy-xr/issues/21)
- [Monado](https://monado.freedesktop.org/), [WMR SLAM merge request](https://gitlab.freedesktop.org/monado/monado/-/merge_requests/1035)
- [Monado SLAM, Mateo de Mayo](https://mateosss.github.io/blog/monado-slam) (Basalt's licence and speed)
- [ORB-SLAM3 paper](https://arxiv.org/pdf/2007.11898)
- [HybVIO paper](https://arxiv.org/pdf/2106.11857) (Basalt among the most efficient on a desktop)
