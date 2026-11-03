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
3. A video on the web: copy a stream link or a video page link, then **Ctrl+Alt+U** (or the panel's *Paste from
   browser*); see [web-video.md](web-video.md).
4. Any client of the controller socket can send `{"op":"video_open","path":"/abs/path.mp4"}`, optionally with
   `"format":{"projection":"180","stereo":"sbs","fov":180}` to skip detection.

While a video plays, these keys exist (bare keys, like an open placement pill's; they are removed when you exit):

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

The desktop keys (zoom, switch, spacing, viewing mode, cursor follow, placement pill) do nothing while a video plays.
Opening a video closes an open placement pill and turns the overview free pointer, its dot and the pills off until you
return to the desktops. The panel shows the title, the detected format and how it was detected, the state, any
warnings, and buttons for the same actions.

The **controls card** appears head-locked near the bottom of the view when a video opens and on any video key. It shows
the title, playing/paused/ended, the position and length, a progress bar, the format and the key hints. It follows the
OLED rules of the help card and the placement pills. It uses the help card's dimmed text and accent, and no channel goes
above 191/255 (measured: 186). It never moves (no pixel orbit or drift). It closes after 30 s without a video key
(`view.OVERLAY_IDLE_CLOSE_S`). The video itself is your content and is shown as is. A paused frame stays on screen
like any paused video, so don't leave a bright frame paused for long.

At the end of the file the last frame stays and the card says *Ended*. Left/Right seek back into it, Esc leaves. A file
that cannot be played shows *Cannot play this video* with the reason for 4 s, then the desktops come back. Files refused
before playing (missing, not a regular file, empty, not a video, too large, unsupported projection) send nothing to the
glasses; the panel shows why.

### Web video

A stream link or a video page link (resolved with yt-dlp) plays in this same mode through an ffmpeg relay; the
renderer is unchanged. Design, limits and evidence: [web-video.md](web-video.md).

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

Hardware decode: `hwdec=vaapi,vaapi-copy,auto-safe`, on the GPU that draws the video. The player finds the render node
of the GPU its GL context draws on: the context's device UUID (`GL_EXT_memory_object`) matched against EGL's devices
(`EGL_EXT_device_persistent_id`, `EGL_EXT_device_drm_render_node`). It does not use the EGL display's own device,
which names the other GPU under PRIME (`DRI_PRIME`); without UUIDs it uses a node only if there is a single GPU. mpv
gets that node (`MPV_RENDER_PARAM_DRM_DISPLAY_V2`, `render_fd` only). `vaapi` then decodes there and imports each frame
into GL as a dmabuf, with no copy, with or without a Wayland display. If that interop is unavailable, `vaapi-copy`
decodes on the same GPU (`vaapi-device`) and copies through system memory; failing both, mpv decodes in software. The
Wayland display is used for VA-API only when no node is found, as before. mpv would try it first, and libva then
opens the compositor's main device, which need not be the renderer's GPU. The path taken is in the renderer's exit report on
stderr (`"hwdec"`, `"va_device"`); the panel does not show it yet. Offline, on surfaceless EGL, both GPUs of this machine
decode with `vaapi` (zero copy), the iGPU by default and the RX 7800 XT with `DRI_PRIME=pci-0000_03_00_0`; see
[Performance](#performance). The glasses' outputs (DP-1, DP-2) are on the RX 7800 XT.

mpv is locked down inside the renderer: no config files, scripts, ytdl, OSC, key bindings or IPC socket, no sibling
subtitle/audio files, no shader or ICC cache (no disk writes), no clipboard. mpv 0.40+ would otherwise open its own
Wayland connection and bind `wl_seat` for the clipboard, which this renderer never does. Only exact
`loadfile fdclose://N`, `cycle pause`, `seek`, `stop` and `set pause no` commands are sent, all asynchronous, and events
are polled without blocking, so rendering never waits on mpv. `XR_MPV_LOG=v` in the renderer's environment prints mpv's
log to stderr for diagnosis.

### Frame timing

mpv gives each decoded frame a target time, the moment it should be seen in sync with the sound, and hands it over up
to `video-timing-offset` (50 ms) earlier. The player renders with `MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME` off, so the
timing is its own job. It used to draw every frame as soon as mpv handed it over, which put each frame on screen during
its predecessor's slot: about one video frame ahead of the sound (25–33 ms at 30 fps, 33–42 ms at 24 fps).

Now `VideoFramePacer` (`video.hpp`) holds the frame until its display frame; mpv keeps it pending meanwhile:

- A frame drawn on a display frame is taken to be on screen one display interval later (the next vblank after the
  swap). The interval is the median of the last 15 gaps between display frames, so a missed vsync does not move it.
- A frame goes on the display frame whose vblank is nearest its target, or at once if it is late.
- After one frame, the next follows after the video's own frame spacing (2 display frames at 60 fps on 120 Hz, 4 at 30)
  as long as that keeps it at most one display interval early. Without this, a target halfway between two vblanks flips
  between them with timing noise: judder.
- So every frame is on screen between half a display interval late and one interval early, against the 1-interval
  latency assumption. Redraws (pause) and frames without a target are drawn at once; no frame is held over 100 ms.

The renderer has no presentation-latency figure for the video path, so the one-interval assumption is not measured in
the glasses. If the compositor adds a frame, the picture is that much later than the sound (8 ms at 120 Hz).

### Reopening and closing

A web video's seek restarts its stream, and the run sends the renderer a fresh open (web-video.md). Opening another
video while one plays does the same. `VideoMode::open` then reuses the playing `VideoPlayer` (`reopen`): mpv stops the
previous file, the player hands back the frames mpv still has (never shown) until mpv reports idle, which it does after
destroying its video output, and then loads the next file into the same mpv instance. The render context, its shaders
and the VA-API interop are kept, and nothing of the previous file can be shown: the texture stays black until the new
file's first frame, as with a new player. A player that failed, or whose file has not started, is replaced as before.
So is one that has drawn about an hour of 60 fps frames: mpv 0.41's libmpv GL path keeps a GL fence per rendered frame
until its render context is freed (about 175 bytes each, 37 MB an hour at 60 fps; an mpv leak this code cannot reach),
and a fresh player bounds that to what one long file always had.

Closing (Esc, or a reopen that cannot reuse) first stops mpv the same way, then frees it. Before, `mpv_render_context_free`
sat out mpv's own 200 ms wait (`flip_page`) for the frame it had handed over, on the renderer thread, so the glasses
froze for a fifth of a second on every close and every web seek.

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
  installed, the first video key pressed afterwards removes them (`xr_control.drop_stale_video_binds`), as for an
  open placement pill's keys.
- **Wire:** one renderer op, `video`, with `{"action": open|format|close|pause|seek|controls, …}`. The shared fixture
  `tests/native_spike/fixtures/video_payloads.json` is decoded by both the renderer and the supervisor tests. A
  malformed `video` frame stops the renderer like any other malformed frame (only the placement pill, resize handle
  and overview pointer redraws are droppable). The
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

## Performance

Timing harnesses, offline (no display, glasses or compositor), run by hand because their numbers depend on machine load:

- `build/native-spike/video_bench [--gpu /dev/dri/renderD128] [--seconds 6] [--restarts 8]` (not run by ctest) drives the
  real `VideoPlayer` and `VideoSphere` at a simulated 120 Hz display on surfaceless EGL. It generates clips with a
  frame-number barcode (hardware encoder where it can) and reports the decode path, CPU time of `update()`, GPU time of
  each new frame, display frames over the 8.33 ms budget (`glFinish` each frame, a missed vsync in the glasses), and the
  video frames shown, dropped and juddered, read back from the texture.
- `python3 tests/native_spike/bench_web_video.py` times the relay: pump throughput and CPU, `StreamRelay.stop()` with the
  real ffmpeg downloading from a 200 Mbit/s local server, a seek's supervisor-loop stall, and time to first byte.

Before → after on the RX 7800 XT (the glasses' GPU): the harness-only commit and this one, two interleaved rounds each,
6 s windows, 2026-11-02, load average 14–18 from other work on the machine:

| Clip | Decode | GPU per new frame | Display frames over budget | Video frames shown (dropped) | Judder |
| --- | --- | --- | --- | --- | --- |
| 4K60 H.264 | vaapi-copy → vaapi | 1.7–2.1 → 0.48 ms | 0–3 → 0–6 of ~715 | 360 → 359–360 of 360 | 6–7 → 1–14 |
| 5.7K60 HEVC | vaapi-copy → vaapi | 3.5–3.9 → 1.09 ms | 2–6 → 0–1 | 360 → 359–360 | 11–33 → 0–2 |
| 8K30 HEVC | vaapi-copy → vaapi | 6.7–7.1 → 2.2 ms | 27–86 → 1–2 | 180 → 179–180 of 180 | 42–105 → 2–4 |
| 8K60 HEVC 10-bit | vaapi-copy → vaapi | 11.3–12.0 → 2.2 ms | 60–81 → 1–6 | **45–46 (88–97) → 346–359 (0–13)** of 359 | 0 → 2–8 |
| 8K30 AV1 | vaapi-copy → vaapi | 17.1 → 2.2 ms | 122–140 of ~300 → 0 of 720 | **39–43 (94–108) → 180 (0)** of 179 | 0 → 0 |

"Over budget": CPU + GPU of one display frame above 8.33 ms, a missed vsync in the glasses. Judder: a gap between new
video frames that is not the clip's cadence. The remaining misses follow the machine's load: in the same rounds the
harness thread itself woke late 1–20 times. Before, 8K60 and 8K AV1 showed one frame in four, so there was no cadence
left to judder.

- Frame timing (same build, holding off vs on): 30 fps frames were on screen 15–18 ms early on average (up to 25 ms)
  beyond the one-interval allowance, 179–204 per 6 s more than an interval early. Now: mean −4 to +7 ms, at most
  +8.3 ms (one interval), none beyond.
- Close while playing: 155–220 ms → 2–30 ms. Web seek in the renderer (reopen, 6 per round): close 155 ms (max 220) +
  first frame 247 ms with a 17–19 ms worst display frame → no close, first frame 34–35 ms, worst display frame 8–10 ms.
- Web relay (web-video.md): `StreamRelay.stop()` while ffmpeg downloads 3,003–3,008 ms → 4–13 ms; a seek blocked the
  supervisor loop 3,004–3,009 ms → 4–98 ms, the new stream's first byte 3,053–3,081 ms → median 118 ms; pump CPU
  0.47–0.55 → 0.25 s per GiB, renderer reads per MiB 16 → 1.9.
- On the Ryzen iGPU, `vaapi-copy` decoded on the RX 7800 XT (mpv's default `renderD128`) and uploaded every frame to the
  iGPU. It now decodes on the iGPU with zero copy, but mpv's default scaling passes are too heavy there from 4K60 up
  (GPU 10 ms per 4K frame); the glasses do not use it. mpv's cheaper single-pass path (`profile=fast`) would fit, but it
  changes the picture (bilinear instead of lanczos chroma), so it is not used.

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
  | 1024×512 | H.264, MP4 | 360° mono | vaapi (zero copy) |
  | 1024×512 | HEVC, MKV | 180° side-by-side | vaapi (zero copy) |
  | 512×512 | VP9, WebM (Opus audio) | 360° top-bottom | vaapi (zero copy) |
  | 1024×512 | AV1 (SVT), MKV | fisheye 190° side-by-side | vaapi (zero copy) |
  | 7680×3840 | H.264, MP4 | 360° mono | software (beyond H.264 hardware limits) |
  | 8192×4096 | HEVC, MP4 | 180° side-by-side | vaapi (zero copy) |
  | not a video | — | — | reported as an error |

  Each clip is checked upright (top-left quadrant of the picture at the top-left of the texture), projected (looking
  up-left lands in the top-left quadrant), paused and resumed, seeked forward, played to the end (last frame held,
  *ended*), and seeked back. Each must not decode with `vaapi-copy` when the render node is known, and none of its
  frames may go on screen more than a display interval early. `reopen` plays a second file (a blue clip: its first
  frame shown is blue, never the previous picture) and then a relay pipe while paused (it plays). A failed player is not
  reused. Closing a playing video takes under 150 ms (2 ms measured; it was over 200 ms).
- `video` also runs `VideoFramePacer` on a simulated 120 Hz display with ±1 ms timing noise and an mpv-like decoder:
  24, 30 and 60 fps keep a steady cadence at 16 phases each (at most one adjustment), 25 and 50 fps use only the two
  neighbouring spacings, a 0.1 % clock drift slips at most 4 times in 10 s, and every frame is on screen between half
  an interval late and one interval early. Without the cadence rule the same checks fail 29 times.
- `tests/native_spike/test_video.py`: requests, the shared payload fixture, detection (metadata, names, shape), limits,
  file checks, a real ffprobe of a generated Matroska file with a `stereo_mode` tag, the control state machine
  (open closes the help card and an open placement pill, key handling, ignored desktop keys, idle close, error and no-support exits, request-id
  order, bind install/removal, config reload), descriptor passing over a socketpair, and the bind helper and picker.
- `tests/xr_controller/test_video.py`: the controller forwards opens and keys only while running and writes no new consent.
- `omarchy-plugin/.../tests/test_video_controls.py`: the panel component in offscreen Quickshell with a stand-in picker.
