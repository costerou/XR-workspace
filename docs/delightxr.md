# DelightXR and other web VR video in the glasses

Watch 180° and 360° videos from websites in the Luma Ultra without downloading them. Head tracking, projection and
OLED behaviour are those of [VR video](vr-video.md). Status: **offline tests only** (see [Evidence](#evidence)). It has
not run in the glasses, and it has not been tried against a real DelightXR site.

## Findings

### What DelightXR v2.0.57 is

DelightXR is not something installed in the browser. It is a JavaScript player that **websites embed**. A site loads
`cdn.delight-vr.com/latest/dl8-<licence key>.js` and writes custom elements into its pages:

```html
<dl8-video format="STEREO_180_LR" title="…" poster="…">
  <source src="https://cdn…/clip_8k.mp4" type="video/mp4" quality="8K">
  <source src="https://cdn…/clip/master.m3u8" type="application/x-mpegurl">
</dl8-video>
```

From its documentation and changelog:

- **`format` is mandatory** and names the projection exactly: `MONO_360`, `STEREO_360_LR`, `STEREO_360_TB`,
  `MONO_180`, `STEREO_180_LR(_SPHERICAL)`, `STEREO_180_TB(_SPHERICAL)`, flat formats, and custom fisheye formats
  declared with `<meta name="dl8-custom-format">` (base `STEREO_FISHEYE_LR`, `fov` parameter). Nothing has to be guessed.
- **Sources** are plain `<source>` children: progressive MP4/WebM (optionally labelled `quality="4K"` etc.), HLS
  (`.m3u8`) and MPEG-DASH (`.mpd`). `<dl8-live-video>` is the live variant (HLS/DASH). The changelog has no DRM entry
  at all, so DelightXR content is ordinary unencrypted video.
- **Rendering:** WebGL in the page. In a headset browser it enters VR through **WebXR** (since 2.0.6; 2.0.45–2.0.57 is
  mostly visionOS work). On desktops it falls back to an in-page 360° viewer turned by mouse drag. Phones use the
  gyroscope ("magic window", `deviceorientation`). It has a JS API (`play()`, `pause()`, `seek()`, `currentTime`).
- v2.0.57 (2025-09-19) only adds a visionOS zoom option and fixes positioning; nothing relevant to Linux.

### This machine

- **Browser:** Chromium 155.0.8059.84 (Arch package) is the only browser installed. The `google-chrome`, `brave`
  and `vivaldi` config folders hold only `NativeMessagingHosts`, and there is no Firefox. Chromium runs Wayland-native
  with Omarchy's flags and its own unpacked extensions (copy-url, yt-dlp, whatsapp-slim).
- **DelightXR:** not installed anywhere, and there is nothing to install: it arrives with each site. No extension with
  that name is in the Chromium profile (read-only check).
- **XR runtime:** the OpenXR loader (`openxr` package, 1.1.60) is installed, but there is **no runtime** (no Monado,
  no SteamVR, no `active_runtime.json`). XRLinuxDriver (xr-driver-git) provides head tracking only.
- **WebXR on Linux:** stock Chromium has no immersive WebXR on Linux before **M157**, which reached beta and is due
  stable on 2026-11-03, and even then only with `--enable-features=OpenXR`. It needs an OpenXR runtime with
  `XR_KHR_vulkan_enable2` (tested upstream against Monado). M154/M155 contain the backend but nothing reaches it. So
  today "Enter VR" on a DelightXR page cannot reach any headset from Cody's Chromium.

## Options

| | (a) OpenXR runtime → WebXR "Enter VR" | (b) Drive the in-page viewer | (c) Stream URL → our libmpv player (**chosen**) |
| --- | --- | --- | --- |
| How | A Monado driver (or our own runtime) fed by our pose; Chromium ≥ 157 with `--enable-features=OpenXR` renders WebXR frames to it | Feed our head pose into DelightXR's desktop viewer (synthetic mouse drag, or a companion extension faking `deviceorientation`), and watch that browser window on an XR desktop | Read the player's format and stream links on the page; play the stream in the renderer's existing VR video mode |
| Head-motion latency | Monado's compositor timewarps at display rate, but WebXR frames come from the page's JS loop | Worst: page render, then Hyprland capture, then our desktop reprojection, so 2–3 frames behind plus the browser's own pacing | Same as local VR video: per-pixel reprojection every refresh (120 Hz), 0.02 ms/frame |
| Picture quality | Good (eye buffers at the runtime's size) | Poor: a flat browser window (≈ 1920 px wide at best) showing a slice of the sphere, resampled twice | Best: the full 8K source texture sampled directly, stream copied, never re-encoded |
| DRM | Works with Widevine in the page | Works (the browser decodes) | No DRM streams (DelightXR has none) |
| Logins and expiring links | Fine (it is the browser) | Fine | Signed links work, but links that need the browser's cookies do not (see [Limits](#limits)) |
| Effort | Large: install Monado and a newer Chromium with a flag; write a Monado driver for our pose; Monado's compositor would need the glasses output that our renderer owns now | Medium, and fragile against DelightXR updates; input injection on the live desktop is not consented | Small: renderer **unchanged**, ~600 lines of Python, a bookmarklet |
| Safety | New system services and a browser feature flag | Synthetic input into Cody's browser (not granted); or an extension with page access | No browser changes: a bookmark Cody adds himself; no cookies read; the network fetch runs in a separate restricted ffmpeg process |

**Choice: (c).** DelightXR pages carry exactly what our player needs: the projection token and the stream links. (c)
reuses the renderer as it is: same reprojection, latency, OLED-safe card and session model. The renderer gets a pipe
descriptor in place of a file descriptor, so it still opens no paths and makes no network connections, and the pinned
renderer binary does not change. (a) is the long-term route for sites with DRM or WebXR-only content. It needs
Chromium 157+, Monado and display ownership work, so it is listed under [Later](#later). (b) gives the worst picture and
latency and needs input injection, so it was rejected.

## Using it

**Once:** make the "Play in VITURE" bookmark. In this repository:

```sh
python -m tools.delightxr.build | wl-copy
```

In Chromium press Ctrl+Shift+O (bookmark manager), open the ⋮ menu, choose *Add new bookmark*, name it
**Play in VITURE**, and paste the copied `javascript:…` text as its URL. That is the only setup. Delete the bookmark to
remove it. Nothing is installed in Chromium or its profile beyond the bookmark Cody adds himself.

**Each video**, during an XR session:

1. Open the video page in Chromium on one of the XR desktops (or anywhere) and start the player if needed.
2. Click **Play in VITURE** in the bookmarks bar. The page's video pauses and a dim note says it was copied.
3. Press **Ctrl+Alt+U** in the glasses. (Or: panel → VR video → **Paste from browser**.) The video opens around you
   where the page was. It resumes at the page's position if more than 5 s remain.

A bare link works too: copy it and press Ctrl+Alt+U, or type it into the panel's path field and press Play.

- **A stream link** (`.mp4`, `.m3u8`, `.mpd`…) plays directly. The format comes from the link's name and the picture
  shape, as for files.
- **A video page link** (the address bar of a video site, e.g. `https://www.<site>/watch?v=…`) is resolved with
  **yt-dlp**: it finds the page's best direct stream, and the run relays that one. The VR format comes from the
  video's **title and tags** (`360`, `180`/`VR180`, `SBS`/`LR`/`3D`, `TB`/`OU`, `fisheye`…), else from the picture
  shape. **Tab** corrects it. The panel says *detected from title*. The bookmark does the same when a page has no
  stream link of its own (a big site playing from a `blob:`): it copies the page link, and its note says so.

If yt-dlp is not installed, a page link is refused with *that's a web page link, not a video link, and yt-dlp, which
finds the video on a page, is not installed…*. A page where yt-dlp finds no video gets *that's a web page link, not a
video link, and yt-dlp found no video on it: use the "Play in VITURE" bookmark on the page, or copy the video's own
link*.

While it plays, every [VR video key](vr-video.md#using-it) works the same way: Space, Tab, Up/Down, Esc, Ctrl+Alt+Space.
**Left/Right and Shift+Left/Right** seek by restarting the stream at the new position. Presses within 0.4 s are
combined into one restart, which takes about 1–2 s on a good connection (the card reappears, then the picture). The
panel shows *Streaming from <site> (progressive / hls / dash) · position / length*.

If something cannot play, a desktop notification and the panel say why: *the site refused the stream (HTTP 403)…*,
*DRM-protected*, *a flat (non-VR) video*, *no video link on this page*, and so on. The notification appears on the
focused desktop, which is in the glasses. Ctrl+Alt+H's card lists the key as **Ctrl+Alt+O / U — VR video: file /
copied web link** (one row, so the card keeps its 12-row fit).

## Design

```
Chromium page ── "Play in VITURE" bookmarklet: reads <dl8-video> format + <source>s (or a <video>), pauses it
      │           copies {"viture_xr_video":1, page, title, format, fov, start, ua, sources[≤8]} to the clipboard
      v
Ctrl+Alt+U ─ xr_control.py: wl-paste once ─┐       (panel: "Paste from browser" / an https link)
                                           v
             controller (forwards; no consent) ─> run control socket: video_open {"web": …} or {"url": …}
                                           v
 video.VideoControl ─ web_video: validate, choose source, map the DelightXR format, ffprobe the URL (network)
                    ─ StreamRelay: ffmpeg -ss <start> -i URL -c copy -f nut pipe:1 ─> 128 MiB read-ahead ─> pipe
                                           v   (read end, SCM_RIGHTS, the existing `video` open frame)
 renderer (unchanged): VideoPlayer (libmpv, fdclose://N) ─> VideoSphere ─> glasses
```

- **Bookmarklet** (`tools/delightxr/bookmarklet.js`, built by `tools/delightxr/build.py`). Order of sources: the
  DelightXR element's own `<source>`s and `src`, then `<video>` elements inside it (open shadow roots too), then
  manifests (`.m3u8`/`.mpd`) from the page's resource-timing list for players that stream through a `blob:` URL.
  Without a DelightXR element it takes the playing (else the largest visible) `<video>`. Custom fisheye formats are
  resolved from their `<meta>` declaration. It reads no cookies or storage and sends nothing anywhere. It pauses the
  page video so the sound does not play twice. Its note is dim (#a8a8a8 on #141414, nothing above 168/255), never
  moves, and disappears after 4 s, because the page may be on an XR desktop in the glasses.
- **Clipboard**: read once, only when Ctrl+Alt+U or *Paste from browser* is pressed, at most 16 KiB. It never holds
  more than the JSON above.
- **Validation** (`web_video.validate_web` / `validate_url`): http(s) only. It refuses `blob:`, `data:`, `file:`,
  credentials in the URL, control characters and whitespace, and loopback/private/link-local addresses and
  `localhost`. Unknown keys are refused, and strings that become HTTP headers (Referer, User-Agent) must be printable
  ASCII. The control socket checks the shape. The run checks everything again when it opens the video.
- **Source choice**: the player's own sources first, then the highest `quality` label (8K > 5.7K > 4K > 2160p…), then
  progressive files (seek and reconnect best) before HLS, then DASH. For HLS/DASH, ffmpeg's default selection takes
  the highest-resolution variant.
- **Format**: the DelightXR token maps directly (`STEREO_180_LR` → 180° side-by-side, …, `STEREO_FISHEYE_LR` + fov →
  fisheye 180–220°). The panel says *from the web player*. Flat formats are refused. With no token, the existing
  detection (link name, then picture shape) applies.
- **Relay** (`web_video.StreamRelay`): one `ffmpeg` per open or seek, with `-protocol_whitelist
  https,http,tls,tcp,crypto` (no `file:`, so a playlist cannot point at local files), the page as Referer, the
  browser's User-Agent, `-reconnect` for dropped connections, a 15 s read timeout, and `-c copy` (no re-encoding,
  full quality). The output is **NUT**, which carries every codec mpv plays and starts cleanly mid-stream. Matroska
  cannot take ADTS AAC after a seek into an HLS segment. A reader thread fills a 128 MiB read-ahead, about 10 s of
  8K at 100 Mbit/s. A non-blocking writer thread feeds the pipe. A paused video holds the stream back through the pipe,
  and stopping never waits on the renderer. ffmpeg runs in its own process group with a minimal environment and is
  killed on exit, seek, replacement and run end.
- **Position and seeking**: a pipe cannot seek, so the run keeps a position estimate. That is the relay's start
  position plus the time the renderer reported *playing* (at most 1 s counted per poll). A seek restarts the relay
  at the estimate ± the step, clamped to [0, length − 3 s]. Seeking back after *Ended* works the same way. A relay that
  dies with an error after ffmpeg's own reconnects is resumed at the estimate up to twice, then the video closes with
  the reason.
- **Video pages** (`web_video.prepare_target`, `resolve_page`, `pick_formats`): a link whose type and extension are
  not media goes to `yt-dlp --ignore-config --no-plugin-dirs --no-cache-dir --no-cookies --no-cookies-from-browser
  --no-playlist --skip-download --dump-single-json -- <link>`. That means no user config, plugins, cache, cookie files,
  browser cookies or downloads, and a minimal environment (PATH, locale, HOME for its JavaScript runtime). From its
  format list the run takes formats ffmpeg can read directly (http(s), HLS; no DASH fragment lists, no DRM). The
  best is the one that decodes in hardware here at its size, then the most pixels, then frame rate, then bitrate.
  For resolution-limited VR that is the highest-quality one. If it has no audio, the best audio-only format becomes a
  second relay input (`-map 0:v:0 -map 1:a:0`, both seek together). yt-dlp's per-format request headers go to the
  relay with any `Cookie`/`Authorization` dropped. Its stream URL passes the same `validate_url` checks. If yt-dlp
  does not know the page, the link is probed as a stream after all. If ffprobe then finds HTML ("invalid data"), the
  refusal says it is a web page link.
- **Failure reasons**: known ffmpeg/ffprobe/yt-dlp errors map to plain sentences (HTTP 401/403/404/410/429/4xx/5xx,
  DRM, timeouts, DNS, web page). Anything else shows the tool's own last stderr line, with every link,
  `[https @ 0x…]` prefix and `[site] <video id>:` prefix removed. A bare exit code appears only when the tool printed
  nothing.
- **Session and consent**: as for local VR video, a web video is a mode of the running session. It needs no new
  consent, journal entry or recovery step, and the controller only forwards. Compared with a local file it adds one
  outgoing connection to the site of a link Cody chose, made by a child process of the run. The link may carry a
  signed token, so it is never logged or shown: the panel shows only the site's host name.
- **Renderer**: unchanged. Its control frames and the pinned binary are the same as for local files. The help-card
  row is text sent by the run.

## Limits

- **DRM** (Widevine/EME) streams cannot leave the browser. DelightXR has no DRM, but other sites may. The probe reports
  *DRM-protected*.
- **Links that need the browser's login cookies** fail with HTTP 401/403. Most VR sites hand out signed CDN links,
  which work until they expire, but some check a session cookie on every request. The bookmarklet deliberately reads
  no cookies, and nothing reads Chromium's cookie store. Pressing the bookmarklet again gives a fresh link.
- **`blob:` players** with no manifest in the page's resource list (MSE fed by custom JavaScript) have no link to send.
- **Seeking** takes a relay restart (~1–2 s). HLS seeks land on a segment boundary (usually 2–6 s before the target).
  The card's clock counts from the last restart, and its length shows `--:--`, because the stream on the pipe has no
  index. The panel shows the real position and length. Seeking while paused resumes playback.
- **Long pauses**: if the site drops the idle connection, ffmpeg reconnects with a range request. If that fails, the
  run resumes at the estimated position (twice at most).
- **Live streams** (`<dl8-live-video>`, no length) play but do not seek.
- **Video sites through yt-dlp** work only as far as yt-dlp supports the site *without a login*: no cookies are ever
  passed, so members-only, age-gated or private videos are refused with yt-dlp's reason. Sites change often, and an
  old yt-dlp fails first. Keep the package current (`pacman -Syu`).
- **YouTube 360** is usually an equi-angular cubemap or mesh projection. The stream's own metadata says so, and VR
  video refuses it (*cubemap projection is not supported*). Equirectangular uploads play.
- **The format from a title** is a guess: a title with "360" plays as 360°, and so on. Tab fixes a wrong guess.
- **The bookmark** must be clicked on the page each time. It cannot run by itself. A small companion extension
  could add a button to DelightXR players, but that means installing something in Chromium.

## Later

1. **WebXR through OpenXR (option a)** for DRM and WebXR-only sites: Chromium ≥ 157 with `--enable-features=OpenXR`
   (stable from 2026-11-03), Monado ≥ 25.1 as the runtime, and a Monado driver that reads our tracking adapter's pose.
   The hard part is display ownership. Monado's compositor would have to present on the glasses, which our renderer and
   the ownership plugin hold during a session. One approach: run Monado headless (`XR_MND_headless` or its
   swapchain export) and draw its eye images in our renderer like a video texture. That needs Monado installed, a
   newer Chromium, a browser flag, and a new consent scope.
2. A companion extension (opt-in, `--load-extension` like Omarchy's own) that puts a *Play in VITURE* button on
   DelightXR players and sends straight to the controller socket through native messaging. That needs installing a
   native-messaging manifest.
3. Absolute seek and a true clock on the card: a renderer change (`start` in the open frame and mpv
   `rebase-start-time=no`) and a new pinned renderer.

## What needs installing

Nothing beyond what is installed now: Chromium, ffmpeg/ffprobe, mpv/libmpv, wl-clipboard, libnotify and **yt-dlp**
(2026.08.19, `/usr/bin/yt-dlp`, needed only for video *page* links) are all present. Without yt-dlp, stream links
and DelightXR pages still work, and page links are refused with a message naming yt-dlp (`sudo pacman -S yt-dlp`;
nothing here installs it). Cody adds one bookmark himself ([Using it](#using-it)). Going live means only updating the Python in the
controller's worktree when no session runs. The renderer and plugin pins are unchanged.

## Evidence

Offline only: no glasses, no compositor, no real website.

- `tests/native_spike/test_web_video.py`:
  - URL and bookmarklet-object validation, DelightXR format mapping, quality ranking, source choice, the clipboard
    parsers (run and bind helper agree), failure reasons that never contain the link, and the relay command line.
  - The real relay and probe against a local HTTP server with byte ranges (generated H.264/AAC clip, MP4 and HLS):
    every frame copied unchanged into NUT, start positions, a 403 reported as such, and stop not blocked by a paused
    reader.
  - The whole run path (`VideoControl` with the real probe and relay) from the bookmarklet object to the renderer's
    descriptor.
  - `VideoControl` with stand-ins: resume position, combined seeks, paused time not counted, clamping, dropped-link
    resume then give-up, live streams, replacing and closing stop the relay, local files unchanged.
  - The built bookmark run in **headless Chromium 155** on test pages (temporary profile, every host unresolvable):
    DelightXR element with three sources, a custom fisheye format, a plain `<video>`, and a page with no video.
- Page links (`PageFailureTests`, and `RelayTests` with the **installed yt-dlp** against local pages): format choice
  on a YouTube-like list (8K AV1 over 8K H.264, which this decoder cannot do; DASH fragments and DRM skipped; best
  Opus audio), cookie-free headers, the locked-down yt-dlp command, its errors without the link or video id, the
  separate-audio relay, the format from the title (`VR 180 3D SBS` → 180° side-by-side), shape and Tab fallback, and
  the refusals: no video on the page, yt-dlp missing, and a media link that turns out to be HTML. A real local page
  embedding a clip plays through yt-dlp, ffprobe and the relay. A page without video gives the "web page link" reason,
  not an exit code. The bookmark copies the page link in headless Chromium when a page has no stream link.
- `spike/tests/video_render_test.cpp` (`ctest -R video_render`): the **unchanged renderer's VideoPlayer** plays the
  relay's container from a pipe (`ffmpeg -c copy -f nut pipe:1`). On the Ryzen iGPU: `vaapi-copy`, 1024×512, upright,
  pause, held at the end.
- `tests/xr_controller/test_video.py`: the controller forwards `url`/`web` opens while running, refuses bad ones, and
  writes no consent. The panel test drives *Paste from browser* (stand-in wl-paste), typed links and the stream line.
- `python -m tools.native_spike.bind_check`: Ctrl+Alt+U clashes with nothing in Cody's Hyprland config (14 run binds).

First physical checks: a real DelightXR page (format and sources found, signed links accepted), Ctrl+Alt+U while the
browser is on an XR desktop, the notification visible in the glasses, start latency, seek restart time on a real CDN,
an 8K HLS stream's sustained rate, and a long pause.

Sources: [Delight XR changelog](https://delight-vr.com/documentation/changelog/),
[dl8-video documentation](https://delight-vr.com/documentation/dl8-video/),
[compatibility](https://delight-vr.com/documentation/compatibility/),
[Chromium WebXR on Linux (utzcoz/chromium-webxr-linux)](https://github.com/utzcoz/chromium-webxr-linux),
[Chromium issue 506004811](https://issues.chromium.org/issues/506004811).
