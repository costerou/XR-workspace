# Web VR video in the glasses

Watch 180° and 360° videos from websites in the Luma Ultra without downloading them. Head tracking, projection and
OLED behaviour are those of [VR video](vr-video.md). Status: **offline tests only** (see [Evidence](#evidence)). It has
not run in the glasses.

The run streams the video's own link into the renderer's existing VR video mode: same reprojection, latency, OLED-safe
card and session model. The renderer gets a pipe descriptor in place of a file descriptor, so it still opens no paths
and makes no network connections, and the pinned renderer binary does not change. Sites with DRM or WebXR-only content
need a WebXR/OpenXR route instead (see [Later](#later)).

## Using it

Copy a link and press **Ctrl+Alt+U** in the glasses (or **Paste from browser** in the panel), or type the link into the
panel's path field and press Play.

- **A stream link** (`.mp4`, `.m3u8`, `.mpd`…) plays directly. The format comes from the link's name and the picture
  shape, as for files.
- **A video page link** (the address bar of a video site, e.g. `https://www.<site>/watch?v=…`) is resolved with
  **yt-dlp**: it finds the page's best direct stream, and the run relays that one. The VR format comes from the
  video's **title and tags** (`360`, `180`/`VR180`, `SBS`/`LR`/`3D`, `TB`/`OU`, `fisheye`…), else from the picture
  shape. **Tab** corrects it. The panel says *detected from title*.

If yt-dlp is not installed, a page link is refused with *that's a web page link, not a video link, and yt-dlp, which
finds the video on a page, is not installed…*. A page where yt-dlp finds no video gets *that's a web page link, not a
video link, and yt-dlp found no video on it: copy the video's own link instead*.

While it plays, every [VR video key](vr-video.md#using-it) works the same way: Space, Tab, Up/Down, Esc, Ctrl+Alt+Space.
**Left/Right and Shift+Left/Right** seek by restarting the stream at the new position. Presses within 0.4 s are
combined into one restart, which takes about 1–2 s on a good connection (the card reappears, then the picture). The
panel shows *Streaming from <site> (progressive / hls / dash) · position / length*.

If something cannot play, a desktop notification and the panel say why: *the site refused the stream (HTTP 403)…*,
*DRM-protected*, *a flat (non-VR) video*, and so on. The notification appears on the focused desktop, which is in the
glasses. Ctrl+Alt+H's card lists the key as **Ctrl+Alt+O / U — VR video: file / copied web link** (one row, so the
card keeps its 12-row fit).

## Design

```
Ctrl+Alt+U ─ xr_control.py: wl-paste once ─┐       (panel: "Paste from browser" / an https link)
                                           v
             controller (forwards; no consent) ─> run control socket: video_open {"url": …} (or {"web": …})
                                           v
 video.VideoControl ─ web_video: validate, resolve a page with yt-dlp, choose source, ffprobe the URL (network)
                    ─ StreamRelay: ffmpeg -ss <start> -i URL -c copy -f nut pipe:1 ─> 128 MiB read-ahead ─> pipe
                                           v   (read end, SCM_RIGHTS, the existing `video` open frame)
 renderer (unchanged): VideoPlayer (libmpv, fdclose://N) ─> VideoSphere ─> glasses
```

- **Clipboard**: read once, only when Ctrl+Alt+U or *Paste from browser* is pressed, at most 16 KiB.
- **Requests**: `{"url": …}`, or a `{"web": {"viture_xr_video": 1, page, title, format, fov, start, ua, sources[≤8]}}`
  object for a client that already knows the format and sources. A `{"url"}` is normalised into the same shape.
- **Validation** (`web_video.validate_web` / `validate_url`): http(s) only. It refuses `blob:`, `data:`, `file:`,
  credentials in the URL, control characters and whitespace, and loopback/private/link-local addresses and
  `localhost`. Unknown keys are refused, and strings that become HTTP headers (Referer, User-Agent) must be printable
  ASCII. The control socket checks the shape. The run checks everything again when it opens the video.
- **Source choice**: the player's own sources first, then the highest `quality` label (8K > 5.7K > 4K > 2160p…), then
  progressive files (seek and reconnect best) before HLS, then DASH. For HLS/DASH, ffmpeg's default selection takes
  the highest-resolution variant.
- **Format**: a `format` token in a web request maps directly (`STEREO_180_LR` → 180° side-by-side, …,
  `STEREO_FISHEYE_LR` + fov → fisheye 180–220°). Flat formats are refused. With no token, the existing detection
  (link name, then picture shape) applies.
- **Relay** (`web_video.StreamRelay`): one `ffmpeg` per open or seek, with `-protocol_whitelist
  https,http,tls,tcp,crypto` (no `file:`, so a playlist cannot point at local files), the page as Referer, the
  given User-Agent, `-reconnect` for dropped connections, a 15 s read timeout, and `-c copy` (no re-encoding,
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

- **DRM** (Widevine/EME) streams cannot leave the browser. The probe reports *DRM-protected*.
- **Links that need the browser's login cookies** fail with HTTP 401/403. Signed CDN links work until they expire,
  but some sites check a session cookie on every request. Nothing reads Chromium's cookie store.
- **`blob:` players** (MSE fed by custom JavaScript) have no link to send; copy the page link and let yt-dlp find it.
- **Seeking** takes a relay restart (~1–2 s). HLS seeks land on a segment boundary (usually 2–6 s before the target).
  The card's clock counts from the last restart, and its length shows `--:--`, because the stream on the pipe has no
  index. The panel shows the real position and length. Seeking while paused resumes playback.
- **Long pauses**: if the site drops the idle connection, ffmpeg reconnects with a range request. If that fails, the
  run resumes at the estimated position (twice at most).
- **Live streams** (no length) play but do not seek.
- **Video sites through yt-dlp** work only as far as yt-dlp supports the site *without a login*: no cookies are ever
  passed, so members-only, age-gated or private videos are refused with yt-dlp's reason. Sites change often, and an
  old yt-dlp fails first. Keep the package current (`pacman -Syu`).
- **YouTube 360** is usually an equi-angular cubemap or mesh projection. The stream's own metadata says so, and VR
  video refuses it (*cubemap projection is not supported*). Equirectangular uploads play.
- **The format from a title** is a guess: a title with "360" plays as 360°, and so on. Tab fixes a wrong guess.

## Later

1. **WebXR through OpenXR** for DRM and WebXR-only sites. Stock Chromium has no immersive WebXR on Linux before
   **M157** (stable from 2026-11-03), and then only with `--enable-features=OpenXR` and an OpenXR runtime with
   `XR_KHR_vulkan_enable2` (Monado ≥ 25.1). This machine has the OpenXR loader but no runtime. It would also need a
   Monado driver that reads our tracking adapter's pose. The hard part is display ownership: Monado's compositor would
   have to present on the glasses, which our renderer and the ownership plugin hold during a session. One approach:
   run Monado headless (`XR_MND_headless` or its swapchain export) and draw its eye images in our renderer like a
   video texture. That needs Monado installed, a newer Chromium, a browser flag, and a new consent scope.
2. Absolute seek and a true clock on the card: a renderer change (`start` in the open frame and mpv
   `rebase-start-time=no`) and a new pinned renderer.

## What needs installing

Nothing beyond what is installed now: ffmpeg/ffprobe, mpv/libmpv, wl-clipboard, libnotify and **yt-dlp**
(`/usr/bin/yt-dlp`, needed only for video *page* links). Without yt-dlp, stream links still work, and page links are
refused with a message naming yt-dlp (`sudo pacman -S yt-dlp`; nothing here installs it). Going live means only
updating the Python in the controller's worktree when no session runs. The renderer and plugin pins are unchanged.

## Evidence

Offline only: no glasses, no compositor, no real website.

- `tests/native_spike/test_web_video.py`:
  - URL and web-request validation, format-token mapping, quality ranking, source choice, the clipboard parsers (run
    and bind helper agree), failure reasons that never contain the link, and the relay command line.
  - The real relay and probe against a local HTTP server with byte ranges (generated H.264/AAC clip, MP4 and HLS):
    every frame copied unchanged into NUT, start positions, a 403 reported as such, and stop not blocked by a paused
    reader.
  - The whole run path (`VideoControl` with the real probe and relay) from a web request to the renderer's descriptor.
  - `VideoControl` with stand-ins: resume position, combined seeks, paused time not counted, clamping, dropped-link
    resume then give-up, live streams, replacing and closing stop the relay, local files unchanged.
- Page links (`PageFailureTests`, and `RelayTests` with the **installed yt-dlp** against local pages): format choice
  on a YouTube-like list (8K AV1 over 8K H.264, which this decoder cannot do; DASH fragments and DRM skipped; best
  Opus audio), cookie-free headers, the locked-down yt-dlp command, its errors without the link or video id, the
  separate-audio relay, the format from the title (`VR 180 3D SBS` → 180° side-by-side), shape and Tab fallback, and
  the refusals: no video on the page, yt-dlp missing, and a media link that turns out to be HTML. A real local page
  embedding a clip plays through yt-dlp, ffprobe and the relay. A page without video gives the "web page link" reason,
  not an exit code.
- `spike/tests/video_render_test.cpp` (`ctest -R video_render`): the **unchanged renderer's VideoPlayer** plays the
  relay's container from a pipe (`ffmpeg -c copy -f nut pipe:1`). On the Ryzen iGPU: `vaapi-copy`, 1024×512, upright,
  pause, held at the end.
- `tests/xr_controller/test_video.py`: the controller forwards `url`/`web` opens while running, refuses bad ones, and
  writes no consent. The panel test drives *Paste from browser* (stand-in wl-paste), typed links and the stream line.
- `python -m tools.native_spike.bind_check`: Ctrl+Alt+U clashes with nothing in Cody's Hyprland config (14 run binds).

First physical checks: Ctrl+Alt+U while the browser is on an XR desktop, the notification visible in the glasses,
start latency, seek restart time on a real CDN, an 8K HLS stream's sustained rate, and a long pause.

Sources: [Chromium WebXR on Linux (utzcoz/chromium-webxr-linux)](https://github.com/utzcoz/chromium-webxr-linux),
[Chromium issue 506004811](https://issues.chromium.org/issues/506004811).
