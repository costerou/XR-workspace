# Immersive 3D

Any desktop in XR Workspace shown in stereo 3D, made from its ordinary 2D picture by an AI depth model. A 3D button beside each desktop's
level bar turns that desktop on, and the button's tail turns on all of them.

Status: **built and proven off screen. It has never run on a live compositor or in the glasses.** Default off. `3d-assemble` joins the
four pieces that were built apart: [the depth engine](depth-engine.md), [the stereo renderer](stereo-render.md), the
toggle and [the comfort settings](3d-comfort.md).

## How the glasses get into 3D

**You hold R1 on the glasses. XR sends them nothing.**

| Question | Answer | Evidence |
| --- | --- | --- |
| Does the Luma Ultra have 3840x1200 at all? | Yes, at 60 and 90 Hz, and nothing faster | VITURE's [SDK table](https://www.viture.com/developer/glasses-sdk/glasses): `VITURE_DISPLAY_MODE_3840_1200_60HZ` and `_90HZ` are both marked for the Luma Ultra |
| Can DisplayPort alone get there? | No | DP-2's EDID, read from sysfs: five 2D modes and no wide one. Its limit is 300 MHz, and 3840x1200@60 needs 310.5 MHz. The glasses would have to be told first |
| Is there a USB command we could send? | Not one we may send | The only frames ever captured from VITURE's software are the tracking ones (`spike/driver/docs/capture-findings.md`). XRLinuxDriver never sets a mode on this device, so no mode set has been on the wire. Firmware flashing shares that channel, and an id is never guessed at the glasses |
| Then how does VITURE do it? | The button | VITURE's [Luma page](https://www.viture.com/academy/xr-glasses/luma-series): "Press and hold R1 to switch between 2D and 3D display modes." The SDK can lock that button on the Luma and Luma Pro only, so it cannot be locked out on the Ultra |

So the path has **no USB command at all**, and the driver's command gate and its 5-minute rule never come into it. It needs a person,
which is the one thing software cannot do for you, and it is also why it is safe.

An aside on the EDID, since it looks like a way in and is not: its 1920x1080@120 timing carries the "field sequential L/R" stereo flag.
The kernel does not take stereo timings, which is the likely reason neither its mode list nor Hyprland's has it, and a forced 1080p120 distorted when it was tried. It is 1080
lines at half the rate per eye, so it was not pursued.

### What happens when you click 3D

1. The panel says *Loading the depth model* while the engine starts, about 5 seconds.
2. A toast says **Hold R1 on the glasses to switch them to 3D**, and the panel says the same. XR waits up to 45 seconds.
3. You hold R1. The glasses change mode, and DisplayPort follows.
4. XR sees Hyprland offer 3840x1200, asks for 90 Hz through the same Lua `hl.monitor` rule it uses for 1920x1200@120, and reads it back.
5. The renderer draws both eyes, and the desktops are 3D.

Going back is the same button. Turning the last 3D desktop off goes flat at once, and a toast asks you to hold R1. XR never waits for that:
the picture is already flat, the same in each eye, so staying costs you 120 Hz and nothing else.

### What nobody has seen yet

This is the real risk, and only the glasses can settle it. **Nobody has seen what holding R1 does on DisplayPort.**

- If the link drops and comes back, it looks exactly like a USB drop to XR. The renderer ends and a new one starts. That path is built:
  `survive.py` holds through it without the "dropped off USB" toast, and its restore puts back whichever mode the glasses came back in. It
  has been tested with fakes only.
- If the link stays up and only the EDID changes, Hyprland may never re-read the mode list, because the output never left. **Then XR cannot
  follow, and 3D fails after 45 seconds with a toast.** That is the case to watch for, and it cannot be fixed from here without a test.
- The glasses may land on 3840x1080 rather than 3840x1200. VITURE's `switch_dimension` call does. XR then says which modes it saw and goes back.

Run `python3 -m tools.native_spike.sbs_probe` first, with XR stopped. It only reads, and it tells you which of these you have in one
minute. See steps S1-S4 in [placement-menu-test-plan.md](placement-menu-test-plan.md).

Other risks, smaller ones:

| Risk | What happens |
| --- | --- |
| 90 Hz instead of 120 while side by side | A frame takes 11.1 ms and not 8.3, which adds about 1.4 ms on average before a frame is seen. Prediction uses the measured refresh, so it compensates |
| USB drop during the switch | The driver and hold logic are the ones on master. The panel can then show a toast about either |
| R1 pressed by accident in a 2D run | A hold, then the same picture in each eye at 90 Hz. Hold R1 again. The run does not end |
| A run with no 3D scope and glasses in 3D | 120 Hz cannot come back, so the hold runs out after 120 s and XR stops the ordinary way, windows restored |
| A plugin hook | None. `tests/native_spike/test_no_cursor_hooks.py` still allows one, the same one as before |

## How the chain fits together

```
                    renderer (xr-native-spike)                          depth engine (its own process)
 XR-n capture  ->  DepthFeed: shrink to 420x266 on the GPU                Depth Anything V2 Small, half precision,
                   -> memfd, input side  ---------------------------->    ROCm on the RX 7800 XT
                                                                          black bars cropped, smoothed over time
 compositor    <-  DepthLink: newest map, screen plane  <-------------    memfd, output side
   text mask, guide map, warp per eye
   3840x1200 buffer, one 1920x1200 canvas
```

- **`spike/src/depth_link.*`** is the join. It runs once a frame, before the pose is read, so 3D's work never ages the pose.
- **The memory is a memfd** the supervisor makes once per run and hands to every renderer and the engine (`immersive.DepthMemory`). It has no
  name on any filesystem, so a desktop's pictures stay in memory as the capture grant says, and a renderer started after a drop finds
  the engine still attached.
- **The canvas does not change.** A surface at least 3:1 wide keeps a canvas of one eye (`canvas_width`), so no placement, card, hit test
  or supervisor geometry knows about 3D. At the glasses' scale of 1.25 that is a 3072x960 surface, a 1536x960 canvas and a 3840x1200 buffer.
- **The screen plane follows the scene.** `DepthLink` takes a percentile of each map and eases it, as the comfort study recommends.

## What protects a run

| Promise | How |
| --- | --- |
| The engine can never take XR down | Another process, behind shared memory. The renderer reads a heartbeat and never waits |
| A crash goes flat | No heartbeat for 500 ms: nothing is fed, nothing is vouched for, and those desktops ease flat by themselves. A toast follows from the supervisor. Proven with frames still being drawn: `depth_link_render`, `capture_e2e_immersive_engine_dies` |
| A hung engine counts as dead | The supervisor reads the same heartbeat: 3 s of silence from a live process ends it |
| A still desktop stays 3D | The engine sends nothing for an unchanged picture, so the link vouches for the last map while the heartbeat is alive |
| 3D has a GPU budget | At most two shrinks and two map uploads a frame, whatever the number of desktops |
| 2D is unchanged | No link exists while nothing is 3D. A 2D frame costs one comparison, and `stereo_render` compares 2D after 3D with 2D before, to the bit. A consent, a command line and a run's frames with 3D off are what they were |
| Nothing starts until it is wanted | The engine and its 3.5 GB exist only while a desktop is 3D |
| A press cannot end a run | The hold above, and a config reload puts the wide mode back |

## Controls

| Where | What |
| --- | --- |
| 3D segment beside a desktop's level bar | That desktop |
| The button's tail | All of them; lit, none |
| Ctrl+Alt+I | All of them: anything on, all off |
| Panel, 3D section | Each desktop, Desktop / Video / Games, Soft / Standard / Enhanced, Screen plane, and what is happening now |

The look is saved per glasses model. Desktop is the default because it is the one that keeps text safe. Its numbers are the comfort study's,
held equal to `tools/stereo_eval/presets.json` by a test.

| Preset | In front | Behind | Screen plane | Text parallax |
| --- | --- | --- | --- | --- |
| Desktop | 4′ | 14′ | 92nd percentile | none |
| Video | 14′ | 36′ | 70th | all of it |
| Games | 10′ | 30′ | 75th | none |

## Numbers

All from `tools/immersive_proof`: the real compositor and `DepthLink`, the real engine on the RX 7800 XT over a real memfd, Depth Anything V2 Small at
420x266, real content, off screen. Measured 2026-11-04 with XR stopped, at `nice -n 19`. The report is `report.json` beside the previews.

**Frame time on the render thread, with the GPU's work finished (`glFinish`), in ms, p50 / p99.** Every desktop scrolling text at 60 Hz, the overview on
show, 3D on every desktop, 1080 frames at 90 Hz each.

| Desktops | 2D, 120 Hz | Side by side, flat | 3D | Of that, the link | Frames over 11.1 ms | Maps a second, each desktop | Age of a map when used |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.50 / 0.66 | 0.58 / 0.69 | 0.70 / 0.80 | 0.05 / 0.14 | 0 | 44.5 | 22 / 22 |
| 2 | 0.41 / 0.62 | 0.47 / 1.40 | 0.56 / 0.71 | 0.10 / 0.19 | 0 | 34.8 | 22 / 33 |
| 3 | 0.39 / 0.58 | 0.45 / 0.59 | 0.57 / 0.95 | 0.12 / 0.22 | 0 | 29.4 | 22 / 33 |
| 4 | 0.39 / 0.59 | 0.46 / 0.59 | 0.60 / 0.95 | 0.14 / 0.27 | 0 | 28.3 | 23 / 33 |

- **Head tracking.** 3D adds about 0.2 ms to a frame at p50 and 0.35 ms at p99, on a frame that has 11.1 ms. The pose is read after all of it, so
  none of that ages the pose. What does change is 90 Hz instead of 120: about 1.4 ms more on average before a frame is seen.
- **2D.** With 3D off the frame is the 2D frame, to the bit, for the cost of one comparison (`stereo_render`: 0/255 from the first 2D frame).
- **The focused desktop** gets its map rate from the 60 Hz cap, cut to 45 by the 90 Hz frame. The others are capped at 30, which is what the third and
  fourth columns of desktops reach. A still desktop costs nothing, so a real session is lighter than this table.
- **The same on the Radeon 610M iGPU** (renderer there, engine on the dGPU): 3D 5.42 / 6.10, 4.27 / 5.08, 3.96 / 4.65 and 4.01 / 4.79 ms, again no frame over.
- **The engine:** `ready` in 4.4 to 5.8 s through the installed launcher, 3.5 GB of memory, gone 0.9 s after it is told to stop.
- **Through the real renderer process** on a loopback compositor, with real dmabuf captures: 133 of 133 pictures reached the engine lit. With the engine dying
  1.5 s in, commits held at 8.33 ms mean, 8.54 ms p99, to the end.

What these do not cover: a real compositor, real scanout, or a game on the GPU. The engine was alone with the renderer.

### Comfort, on what the renderer really drew

`tools/stereo_eval sbs` on 30 recorded frames of each scene, against the comfort study's own reference pipeline through the same scorer. The scorer has no
depth map: it block-matches the two eyes in steps of about 3′. So it reads small numbers roughly, and a reference that meets its targets can read as missing
them. The comparison is what means something (`tools/immersive_proof/yardstick.py`).

| Scene, preset | | In front / behind | Border violations | Text sharpness, mean / worst tenth | Text wobble | Still flicker | Jitter |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Text, Desktop | Renderer | 0′ / 15′ | 0 | 0.986 / 0.987 | 0.12′ | 0.003′ | 0.007′ |
| | Reference | 0′ / 15′ | 0 | 0.997 / 1.00 | 0.02′ | 0.001′ | 0.001′ |
| Photo, Video | Renderer | 3′ / 36′ | 0 | no real text | | 0 | 0 |
| | Reference | 15′ / 30′ | 0 | no real text | | 0 | 0 |
| Video, Video | Renderer | 0′ / 9′ | 0 | no real text | | 0.14′ | 0.30′ |
| | Reference | 9′ / 12′ | 1.2% | no real text | | 0.44′ | 1.11′ |
| Game, Games | Renderer | 9′ / 30′ | 0 | 0.931 / 0.825 | 0.63′ | 0.07′ | 1.75′ |
| | Reference | 9′ / 24′ | 4.1% | 0.983 / 1.00 | 0.01′ | 0.05′ | 0.66′ |

- **Every scene stays inside its limits**, with no border violations and no pixel brighter than the source.
- **Text, Desktop:** just short of the targets (0.99 mean, 0.10′ wobble), and clean by eye at 1:1 in both eyes. Its flicker and jitter meet theirs. The
  gap to the reference is the renderer's mask working at the depth map's resolution, where the reference works on full pixels.
- **Video:** better than the reference on flicker and jitter, so its two small target misses are the scorer, not the picture.
- **Game:** a real gap. There is no HUD lock in the renderer, so a game's HUD text gets depth, and jitter is 2.6 times the reference's. It is the preset to
  look at first in the glasses.

Previews, in `~/.hermes/profiles/coder/previews/3d/`: `*-sbs.png` is what the glasses would be sent, `*-anaglyph.png` is for red-cyan glasses, `*-eye-difference.png` shows
where the eyes differ (flat panels stay black), and `3d-chain.mp4` is all four scenes, 4 seconds, side by side.

## Not known yet

- Everything in the glasses: see above. No comfort number has met an actual eye.
- A game holding the GPU at 100%. Depth will lag. How far is unmeasured.
- HUD lock for games: the comfort study recommends it, and the renderer does not have it. A game's HUD gets depth.
- Per-desktop presets: there is one preset for the run.
- A real compositor's capture of a real desktop through this chain. The dmabuf path is proven against the loopback compositor.
- Hours of running. The longest proof is minutes.

## Install

Not done yet: nothing here has been installed. With XR stopped and the controller idle, following the
[install rules](technical.md#build-and-install): build and test in a merge worktree, re-check that no `viture-xr-*` unit exists right before the checkout, then
checkout, install and restart in one step.

```sh
# Once, if ~/.cache/viture-depth is not there. No sudo. It is there on this machine.
tools/depth_engine/setup.sh

# In the merge worktree. Only the renderer changed: the ownership plugin (9587ca18) and the Luma driver (66028d8c) build to the bytes
# already installed, so Start asks for no new plugin accept.
cmake -S spike -B build/release -G Ninja -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
ninja -C build/release
STAGE=$(mktemp -d); cp build/release/xr-native-spike build/release/libxr-ownership.so build/release/driver/xr-luma-driver build/release/libxr-quiet-driver.so "$STAGE"/

# In the live checkout, because the launcher install writes names that checkout:
python3 -m tools.xr_controller install --build "$STAGE"
systemctl --user restart viture-workspace-controller.service
omarchy-restart-shell   # the panel's new 3D section is QML: rescanPlugins does not reload it
```

Check it took: `ls ~/.local/share/viture-workspace/bin/*/xr-depth-engine`, and the Start box lists the R1 line.

Then, before any 3D in a run: `python3 -m tools.native_spike.sbs_probe`, steps S1-S4.

To turn 3D off without a rollback, delete that `xr-depth-engine`: the consent then has no 3D scope, and the button says the engine is not installed.
Rollback is the usual one: restore `installed.json` and the unit, point the checkout back, restart the controller and the shell.

## Tests

```sh
python -m unittest discover -s tests/native_spike -p 'test_immersive.py'   # the setting, the worker, the real engine process
python -m unittest discover -s tests/native_spike -p 'test_sbs.py'         # the button from every side, the probe
python -m unittest discover -s tests/depth_engine                          # the engine, the memory, the proof tool
ctest --test-dir build/release -R 'depth|stereo|immersive'                 # the join, the shaders, the real renderer
python3 -m tools.immersive_proof --out DIR                                 # the whole chain on real content; refuses under a session
```

### R1 display handover (2026-11-04)

Pending enter and exit are exposed to incident monitoring, including the wait
for the engine after clicking 3D. On an output loss, the supervisor restarts
only the renderer inside the existing run, without the ordinary three-second
USB settling delay. It leaves the tracking driver alone. The panel displays a
switching card; DP cannot display that card during its physical outage.

The incident monitor waits up to eight seconds from an observed display
change for a supported new wide/narrow mode, then logs `mode-switch` rather
than filing `video-lost`. An unresolved outage still reports video loss. USB,
tracking and crash detection continue normally. The timer starts at the
outage, so taking time to find R1 does not consume it.

When R1 makes the glasses wide without a 3D click, a toast offers desktop
3D. The AI engine is not started automatically, since the person may be
switching for SBS video. R1 returning an active 3D desktop to narrow still
falls back to flat rendering through the existing path.

[Offline evidence and limitations](evidence/3d-switch-smooth/README.md).
Renderer-local pose/recenter state now travels across the fast restart in
private run-scoped memory: yaw anchor, follow spring, maximize/restore room,
edge-pan/landed turns and level/roll references survive. Animation pauses
through the display outage; initial control replay updates the restored view
rather than creating a new anchor. Ordinary recovery and USB re-enumeration
discard this state. See [continuity evidence](evidence/3d-switch-smooth/continuity/README.md).
