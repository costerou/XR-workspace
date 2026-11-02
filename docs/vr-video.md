# VR video in the glasses

Play 360° and 180° videos in the Luma Ultra (or Beast) the way a VR headset does: the picture surrounds you, stays put
in space, and you look around it with the head tracking the desktops already use. Status: **offline tests only.** The
decoding, projection, controls and wiring are covered by tests on this machine (see [Evidence](#evidence)). Nothing here
has run in the glasses yet.

## What it does

- **Formats:** 360° and 180° equirectangular, and 180°–220° fisheye (equidistant lens model). Each can be mono or stereo:
  side-by-side (left eye on the left) or top-bottom (left eye on top). VR180 is normally 180° side-by-side equirectangular.
  Dual-fisheye camera files (Canon RF 5.2 mm, `_FISHEYE190`, `_MKX200`) use the fisheye mode.
- **Head tracking:** every output pixel is re-projected from the current video frame with the latest head pose at the
  display rate (120 Hz on the Luma's 1920×1200@120 session mode), whatever the video's frame rate. Pose prediction
  (Ctrl+Alt+P) applies as it does for the desktops.
- **World-locked:** the video stays fixed in space relative to the recentre reference in every viewing mode. Anchor,
  follow and locked apply only to the desktops; a head-locked 360° video would make no sense. Straight ahead is the
  centre of the video. Ctrl+Alt+Space (the existing recenter) turns the video so its centre is where you look.
- **Desktops are hidden** while a video plays. Rendering both at once (desktops floating inside the sphere) is possible
  but was left out of this version (see [Not in this version](#not-in-this-version)). Capture of the XR desktops keeps
  running in the background, so exiting the video shows them again at once.
- **Audio** plays through PipeWire (Pulse, then ALSA, as fallbacks) to the default output. Choose the glasses' speakers
  or headphones in the Omarchy audio menu as for any other app. The volume keys work as usual.

## Using it

Start an XR session from the panel as usual. Then open a video in one of four ways:

1. **Ctrl+Alt+O** in the glasses opens a file picker (zenity) on the desktop you are looking at. Pick a file and it
   plays. It is listed on the help card (Ctrl+Alt+H). Pressed during a video, it first returns to the desktops so the
   picker is visible.
2. **Panel → VR video → Browse…** shows the same picker. Or type a path and press **Play**.
3. A video on a web page (DelightXR or a stream link): **Ctrl+Alt+U** after the *Play in VITURE* bookmark; see
   [delightxr.md](delightxr.md).
4. Any client of the controller socket can send `{"op":"video_open","path":"/abs/path.mp4"}`, optionally with
   `"format":{"projection":"180","stereo":"sbs","fov":180}` to skip detection.

While a video plays, these keys exist (bare keys, like the placement menu's; they are removed when you exit):

| Keys | Action |
| --- | --- |
| Space | Play / pause |
| Left / Right | Back / forward 10 s |
| Shift+Left / Shift+Right | Back / forward 60 s |
| Tab | Next format (if detection got it wrong: 360 mono → 360 top-bottom → 360 side-by-side → 180 side-by-side → 180 top-bottom → 180 mono → fisheye 180/190/200 side-by-side → fisheye mono) |
| Up / Down | Show / hide the controls card |
| Esc | Back to the desktops |
| Ctrl+Alt+Space | Recenter (the existing bind) |
| Ctrl+Alt+H | Show / hide the controls card (the desktop help card is not shown during video) |
| Ctrl+Alt+P | Pose prediction on/off (the existing bind) |
| Ctrl+Alt+O | Open another video: the desktops come back so you can see the picker |

The desktop keys (zoom, switch, spacing, viewing mode, cursor follow, placement menu) do nothing while a video plays. The
panel shows the title, the detected format and how it was detected, the state, any warnings, and buttons for the same
actions.

The **controls card** appears head-locked near the bottom of the view when a video opens and on any video key. It shows
the title, playing/paused/ended, the position and length, a progress bar, the format and the key hints. It follows the
OLED rules of the help card and placement menu. It uses the help card's dimmed text and accent, and no channel goes
above 191/255 (measured: 186). It never moves (no pixel orbit or drift). It closes after 30 s without a video key
(`view.OVERLAY_IDLE_CLOSE_S`). The video itself is your content and is shown as is. A paused frame stays on screen
like any paused video, so don't leave a bright frame paused for long.

At the end of the file the last frame stays and the card says *Ended*. Left/Right seek back into it, Esc leaves. A file
that cannot be played shows *Cannot play this video* with the reason for 4 s, then the desktops come back. Files refused
before playing (missing, not a regular file, empty, not a video, too large, unsupported projection) send nothing to the
glasses; the panel shows why.

## Design

### Where it lives

```
xr_control.py (Ctrl+Alt+O: zenity picker) ─┐
panel VideoControls.qml (Browse / Play) ───┼─> controller ─> run control socket ─> video.VideoControl (supervisor)
                                           │                                          │ open + fstat + ffprobe the fd
modal keys (Space, arrows, Tab, Esc) ──────┘                                          │ detect format, check limits
                                                                                      v
                                     renderer: `video` frame + the file as an SCM_RIGHTS descriptor
                                       VideoMode ─ VideoPlayer (libmpv → GL texture)
                                                 ─ VideoSphere (per-pixel projection with the head pose)
                                                 ─ controls card (GlHud, head-locked)
```

- `tools/native_spike/video.py` holds `VideoControl`, which wraps the run's `view.ViewControl`. It owns what is shown:
  open/close, format, card visibility, the idle close and the modal binds. Everything that is not about video passes
  through unchanged, so the desktop view code did not change apart from one bind (Ctrl+Alt+O) in `view.BINDS`.
- `spike/src/video_mode.cpp` holds the renderer side: decoder, projection pass and card. When a video is open,
  `wayland.cpp` draws it with `GlCompositor::draw_scene_and_swap` instead of the desktops. Frame pacing, presentation
  feedback, metrics and the pose read are the same code as for the desktops.
- `spike/include/xr/video.hpp` holds the pure maths (`video_uv`, `view_ray`, `eye_viewports`), mirrored exactly by the
  GLSL in `video_gl.cpp`. A test compares the two over thousands of pixels.

### Projection

For each output pixel the shader builds the view ray `d = viewᵀ · (x·tan(hfov_x), y·tan(hfov_y), −1)`, using the
same world→eye rotation and FOV the desktops use (the driver's published diagonal FOV, else 46°). It then maps `d` into
the frame:

- 360° equirectangular: `u = 0.5 + atan2(dx, −dz) / 2π` (wrapping), `v = 0.5 − asin(dy) / π`.
- 180° equirectangular: `u = 0.5 + lon / π` for |lon| ≤ 90°, black outside.
- Fisheye (equidistant): `r = angle from forward / (fov / 2)`, `(u, v) = 0.5 + 0.5·r·(cos φ, −sin φ)`, black outside
  the circle.
- Stereo selects the eye's half of the frame (side-by-side: left half for the left eye; top-bottom: top half).

A full-screen pass is exact (no sphere tessellation, no seams at the poles) and costs almost nothing: **0.02 ms per
1920×1200 frame on the RX 7800 XT** from an 8K texture (0.71 ms on the Ryzen iGPU).

### Stereo output

The renderer draws one viewport per eye when its surface is at least 3:1 wide. That is the glasses' side-by-side 3D
mode (3840×1200 on the Luma, 3840×1080 on the Beast). Otherwise it draws one viewport with the left eye, so a 3D video
plays flat but correct in the normal 2D mode. The renderer has no stereo output today: the glasses run in 2D, and both
the session-mode grant and the consent cover 1920×1200@120 only. Turning the 3D mode on means switching the glasses
to SBS (button or driver) and the output to 3840×1200. That needs its own consent scope and a physical test, and is not
part of this version. The eye split is implemented and unit-tested, so it works as soon as the surface is 3840 wide.

### Decode path

Options considered:

| Option | Zero-copy into GL | Audio + A/V sync | Seeking, formats | Work |
| --- | --- | --- | --- | --- |
| **libmpv render API** (chosen) | VA-API → EGL dmabuf import inside mpv (`vaapi`), or `vaapi-copy` | yes | everything ffmpeg plays, exact seeks | small: one wrapper |
| ffmpeg libav* directly | VA-API surfaces exported as dmabufs, imported with the renderer's existing EGL import | no (write it) | write it | large |
| GStreamer (`vah265dec ! glupload`, appsink) | yes, via GstGL/dmabuf caps | yes | yes, more pipeline plumbing | medium; second GL context or GstGL sharing |

libmpv was chosen. It is installed (mpv 0.41, libmpv 2.5), it renders straight into a GL framebuffer on the renderer's
own EGL context (`mpv_render_context`), and it brings audio, A/V sync, exact seeking and hardware decode selection in
one dependency. Each new video frame is drawn once into an RGBA texture the size of the video. That is a GPU
YUV→RGB conversion, not a CPU copy. The projection pass then samples that texture at the display rate.

Hardware decode: `hwdec=vaapi,vaapi-copy,auto-safe`. With the renderer's Wayland display passed to mpv, `vaapi`
decodes on the GPU and imports the frames into GL as dmabufs, with no copy. If that interop is unavailable, `vaapi-copy`
still decodes on the GPU and copies through system memory; failing both, mpv decodes in software. The path taken is
in the renderer's exit report on stderr (`"hwdec"`); the panel does not show it yet. The offline tests have no Wayland
display, so they exercised `vaapi-copy` and software. The zero-copy `vaapi` path is untested until the first physical
run.

mpv is locked down inside the renderer: no config files, scripts, ytdl, OSC, key bindings or IPC socket, no sibling
subtitle/audio files, no shader or ICC cache (no disk writes), no clipboard. mpv 0.40+ would otherwise open its own
Wayland connection and bind `wl_seat` for the clipboard, which this renderer never does. Only exact
`loadfile fdclose://N`, `cycle pause` and `seek` commands are sent, all asynchronous, and events are polled without
blocking, so rendering never waits on mpv. `XR_MPV_LOG=v` in the renderer's environment prints mpv's log to stderr
for diagnosis.

### Session, consent and journal

- A video is a **mode of a running capture session**, not a new run. The run's consent already covers drawing in the
  glasses. A video changes only what the renderer draws and adds audio output and the reading of one user-chosen file.
  No outputs, plugins, display modes or captures are created or changed, so there is nothing to journal and nothing
  for recovery to undo. The controller refuses `video_open` unless a session is running, and writes no new consent.
- **Files:** the user picks the file. The supervisor checks the path is absolute, then opens it read-only
  (`O_NOCTTY`, non-blocking open so a FIFO cannot hang it) and requires a non-empty regular file. It runs `ffprobe` on
  *that descriptor* (`/proc/self/fd/N`, file protocol only, 10 s timeout) and hands the same descriptor to the renderer
  over the run's private control socket (`SCM_RIGHTS`, attached to the `video` open frame). The renderer never
  receives or opens a path. mpv reads `fdclose://N` with lavf's nested protocols limited to `file` and `fd`, so a
  playlist-like file cannot reach the network.
- **Binds:** Ctrl+Alt+O is a run bind like the other Ctrl+Alt keys. The bare video keys exist only while a video plays,
  are re-added after a Hyprland config reload, and are removed on exit and at run end. If the run dies with them
  installed, the first video key pressed afterwards removes them (`xr_control.drop_stale_video_binds`), as for the
  placement menu.
- **Wire:** one renderer op, `video`, with `{"action": open|format|close|pause|seek|controls, …}`. The shared fixture
  `tests/native_spike/fixtures/video_payloads.json` is decoded by both the renderer and the supervisor tests. A
  malformed `video` frame stops the renderer like any other malformed frame (only menu redraws are droppable). The
  renderer reports `"video": loading|playing|paused|ended|error` in its once-a-second status. The supervisor uses it to
  leave the video after an error (4 s) or if the renderer never reports video at all (5 s: a renderer built without
  libmpv, or CPU composition).

## Format detection

In order, the first that answers wins, separately for projection and stereo layout:

1. **Metadata in the file** (ffprobe): spherical mapping (`equirectangular`; `tiled equirectangular` with bounds
   covering ≤ 200° → 180°; `half equirectangular` → 180°; `fisheye`), the stereo 3D side data (`side by side`,
   `top and bottom`), and Matroska `stereo_mode` tags.
2. **File name** (DeoVR/SkyBox conventions, case-insensitive tokens): `360`, `180`/`VR180`/`180x180`, `LR`/`SBS`/`3dh`,
   `TB`/`OU`/`3dv`, `mono`/`2D`, `FISHEYE`/`FISHEYE190`/`FISHEYE200` (180/190/200°), `MKX200`, `MKX220`, `RF52` (190°).
3. **Picture shape:** 2:1 → 360° mono; 1:1 → 360° top-bottom; 4:1 → 360° side-by-side. With one part known, the other
   follows from the shape: 180° at 2:1 → side-by-side, 1:1 → mono, 1:2 → top-bottom. `_SBS` at 2:1 means each eye is
   1:1, so 180°.
4. Otherwise 360° mono.

The panel shows the result and its source. Tab cycles the format live if detection was wrong. Ambiguous cases do
exist: a 2:1 file with no hints may be 360° mono or 180° side-by-side; detection picks 360° mono and Tab fixes it.
**Refused:** cubemap and EAC (YouTube's equi-angular cubemap), and Google's VR180 *mesh* projection; ffmpeg exposes no
usable mapping for these.

## Limits

- **Resolution:** frames up to 8192 px on either side are shown at full size. Larger ones (up to 16384) are scaled down
  to fit 8192 before projection; above 16384 they are refused. The GPU's texture limit is 16384, and VCN 4 decodes at
  most 8192 wide anyway.
- **Hardware decode on this machine** (AMD VCN 4: RX 7800 XT, also the Ryzen iGPU): HEVC, VP9 and AV1 up to 8192 wide;
  H.264 up to 4096 wide. Anything wider or in another codec (ProRes, …) decodes in software, and the panel warns that
  it may stutter. Measured offline (decode only, not end to end, synthetic noise content at ~108 Mbit/s, 8192×4096
  HEVC): **78 fps** on the RX 7800 XT's VCN with frames left on the GPU, 56 fps with a copy back to system memory,
  40 fps on the iGPU's VCN, and 56 fps in software on the Ryzen 7 9700X. 8K30 HEVC therefore has about 2.5× headroom on
  the RX 7800 XT. 8K60 is close to the limit; the panel warns above 4K at more than 60 fps. 8K H.264 (7680×3840, synthetic) decoded in
  software at real time in the tests, but real 8K H.264 at high bitrate will likely not keep up.
- **Sharpness:** the Luma Ultra shows about 48 px per degree at the centre (1920 px over ~40° horizontal, from the 46°
  diagonal fallback). An 8K 360° video has 7680 / 360 ≈ 21 px per degree, a 4K 360° one only ≈ 11. Video is always
  magnified, never minified, so no mipmaps are needed, but resolution is the main quality limit: prefer 8K 360° or
  180° side-by-side at 8K (≈ 23 px per degree per eye).
- **HDR** video is tone-mapped to SDR by mpv. No subtitles, playlists or audio-track switching in this version.

## 120 Hz vs 60 Hz

The projection runs every display refresh with a fresh pose, so head motion is smooth at the display rate even for a
24 fps film. Only motion *inside* the video steps at the video's frame rate.

- **120 Hz** (the Luma session mode, 8.3 ms per frame): projection 0.02 ms plus, once per video frame, mpv's
  conversion into the texture. The frame budget is large. 24, 30 and 60 fps divide 120 evenly, so each frame is shown
  5, 4 or 2 refreshes with no judder. 25 and 50 fps alternate (4/5 or 2/3 refreshes) with slight judder. Head-motion
  latency and persistence are those of the desktops at 120 Hz.
- **60 Hz** (Beast, or the Luma without the mode switch): the same per-refresh work in 16.7 ms. 30 and 60 fps are even;
  24 fps gets 3:2 pulldown judder, and 25/50 fps judder too. Head motion has twice the frame time, so more smear on
  fast turns and up to ~8 ms more pose-to-photon latency. Prediction helps.
- The decode rate is set by the video, not the display. The display rate only changes how often the same decoded frame
  is re-projected, which is cheap.

## Not in this version

- **Desktops and video together** (a desktop floating in the 360° scene). It would need the video drawn as a background
  pass before the desktop tiles; it is left out to keep the first version simple and the OLED/brightness behaviour
  predictable.
- **Stereo output** needs the glasses' 3D mode and a 3840-wide session mode with its own consent (see above).
- **Playback across a run restart:** a video stops when the run stops; nothing is resumed.
- **Zero-copy VA-API** is implemented through mpv but untested until it runs in the glasses.
- **Physical acceptance:** not done. First physical checks: orientation (centre ahead, up is up), 180° edges,
  side-by-side eye choice, recenter, audio routing, the controls card and its 30 s close, Esc back to the desktops,
  measured presentation rate with an 8K HEVC file, and which `hwdec` the renderer reports.

## Evidence

Offline only (no display, glasses or compositor):

- `ctest -R video` runs `video` (projection maths, eye viewports, texture caps, wire payloads, the card's brightness
  limit) and `video_render`. `video_render` runs on a surfaceless EGL context, on both the Ryzen iGPU and the RX 7800 XT
  (`DRI_PRIME=pci-0000_03_00_0`). It checks the projection pass against the CPU maths (18,090 samples, every projection
  and stereo layout, both eyes, turned heads), the card through the real `GlHud` (brightest channel 186/255), and real
  decoding through `VideoPlayer` of clips it generates with ffmpeg:

  | Clip | Codec, container | Layout | Decoded with |
  | --- | --- | --- | --- |
  | 1024×512 | H.264, MP4 | 360° mono | vaapi-copy |
  | 1024×512 | HEVC, MKV | 180° side-by-side | vaapi-copy |
  | 512×512 | VP9, WebM (Opus audio) | 360° top-bottom | vaapi-copy |
  | 1024×512 | AV1 (SVT), MKV | fisheye 190° side-by-side | vaapi-copy |
  | 7680×3840 | H.264, MP4 | 360° mono | software (beyond H.264 hardware limits) |
  | 8192×4096 | HEVC, MP4 | 180° side-by-side | vaapi-copy |
  | not a video | — | — | reported as an error |

  Each clip is checked upright (top-left quadrant of the picture at the top-left of the texture), projected (looking
  up-left lands in the top-left quadrant), paused and resumed, seeked forward, played to the end (last frame held,
  *ended*), and seeked back.
- `tests/native_spike/test_video.py`: requests, the shared payload fixture, detection (metadata, names, shape), limits,
  file checks, a real ffprobe of a generated Matroska file with a `stereo_mode` tag, the control state machine
  (open closes help/menu, key handling, ignored desktop keys, idle close, error and no-support exits, request-id
  order, bind install/removal, config reload), descriptor passing over a socketpair, and the bind helper and picker.
- `tests/xr_controller/test_video.py`: the controller forwards opens and keys only while running and writes no new consent.
- `omarchy-plugin/.../tests/test_video_controls.py`: the panel component in offscreen Quickshell with a stand-in picker.
