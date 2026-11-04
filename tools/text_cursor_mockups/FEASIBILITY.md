# Can the overview know the pointer is over text?

Investigation only, nothing is built. Hyprland references are to its source at `efb5099` (v0.56.2, the installed build), read
on 2026-11-03. Toolkit references are to the source of the installed versions, except foot (below). No app has been watched
doing any of this on a live desktop: the toolkit column is what the code says, not a test in the glasses.

## Short answer

Yes for almost everything Cody runs, through one small read-only addition to the ownership plugin. Hyprland already knows the
name of the cursor the app under the pointer asked for, including while the overview hides the cursor, but it tells nobody
outside its own process. Overview only: when maximized, the app's real I-beam already shows.

## What Hyprland knows

An app asks for its cursor in one of two ways:

- **By name**, `wp_cursor_shape_device_v1.set_shape`. Hyprland gets `text` (`src/protocols/CursorShape.cpp`,
  `src/helpers/CursorShapes.hpp`) and keeps it (`src/managers/input/InputManager.cpp:67-85`).
- **By picture**, `wl_pointer.set_cursor` with a surface. Hyprland gets pixels and a hotspot, and the name is empty
  (`InputManager.cpp:791-815`). Nothing says it is a text cursor.

The overview hides the cursor with `cursor.invisible`, and that does not lose the name:
`IHyprRenderer::setCursorFromName` stores it in `m_lastCursorData.name` before it looks at `m_cursorHidden`
(`src/render/Renderer.cpp:2863-2912`). `m_lastCursorData.name` is also the better field to read than
`m_cursorSurfaceInfo.name`, which goes stale: over empty desktop or a window's border Hyprland sets `left_ptr` through
`setCursorFromName` and leaves `m_cursorSurfaceInfo.name` alone (`InputManager.cpp:616-618, 657-662`).

## What is not exposed

A search of Hyprland's whole `src/` (799 files) finds no way to read it from outside: no socket2 event names a cursor or a
shape, `hyprctl` has only `cursorpos` and `setcursor` (`src/debug/HyprCtl.cpp:2014, 2038`), and Lua has only `get_cursor_pos`
and `get_monitor_at_cursor` (`src/config/lua/bindings/LuaBindingsQuery.cpp:405-408`).

Routes that were looked at and dropped:

| Route | Why not |
| --- | --- |
| The capture protocol's cursor session (`ext_image_copy_capture_cursor_session_v1`) | It gives a picture, not a name, and hiding the cursor clears that picture (`setCursorHidden` calls `resetCursorImage`, `src/pointer/PointerManager.cpp:241`). It would also widen what the run captures. |
| Hyprland's debug log (`cursorImage request: shape`, `InputManager.cpp:74`) | The log is off here (`debug:disable_logs` is true), so reading it means a live config change for as long as the feature runs, and a log line per shape change. Fine for a one-off probe, not for a feature. |
| Accessibility (AT-SPI) | From general knowledge, not checked here: needs every app's accessibility on, is slow, and says nothing useful about a terminal. |
| Guessing from the picture of a cursor | The pixels are gone once the cursor is hidden, as above. |

## Toolkits

| App or toolkit | Installed | Asks by name? | `text` over text? | Evidence |
| --- | --- | --- | --- | --- |
| GTK 3 | 3.24.52 | Yes, when the compositor offers it | Yes | `gdk/wayland/gdkdevice-wayland.c:427, 509-522` |
| GTK 4 | 4.24.1 | Yes | Yes | `gdk/wayland/gdkdevice-wayland.c:183, 264-282` |
| Qt 6 | 6.12.0 | Yes | Yes, `Qt::IBeamCursor` | `qwaylandinputdevice.cpp:164, 268`, `qwaylandcursor.cpp:248` |
| Chromium | 155.0.8059.84 | Yes, it prefers it | Yes, `kIBeam` | `wayland_window.cc:1295-1298`, `wayland_cursor_shape.cc:85-86` |
| Electron, native Wayland | 41, 43, and the copies inside apps | Same code | Yes | T3 Code, Claude and Hermes run on Wayland now (`hyprctl clients`), and all three binaries contain the protocol |
| foot | 1.28.0 | Yes | Yes, unless the app in it took the mouse | `terminal.c:3635-3640`, read on foot's master; the installed binary contains the protocol |
| XWayland apps (Spotify, Steam here) | Xwayland 24.1.14 | No: the binary has no such protocol | No, plain dot | `/usr/bin/Xwayland` |
| A page with a custom picture cursor (CSS `url()`) | | No | No, plain dot | `wayland_window.cc` falls through to bitmaps |

Alacritty, Ghostty and Kitty are not installed here, and Firefox is not either, so they were not checked. GTK, Qt and Chromium
fall back to a picture on a compositor without the protocol, and Hyprland has it. Every failure above ends in the plain dot,
which is today's behaviour.

## Overview or maximized?

Overview only. The dot exists because the overview hides the real cursor (`overview_cursor.py`). When maximized, in typing mode
and in a VR video the real cursor is shown, and the capture paints it into the picture
(`EXT_IMAGE_COPY_CAPTURE_MANAGER_V1_OPTIONS_PAINT_CURSORS`, `spike/src/wayland.cpp:581-582`), so an app's I-beam already appears
there, in the cursor theme's own look. Matching the grey family when maximized would mean hiding the real cursor there too and
taking on the dot's job in a mode where the plain capture already works. That is a lot of risk for a look.

## Recommended approach

1. **Ownership plugin:** one read-only `hyprctl` command that returns `g_pHyprRenderer->m_lastCursorData.name`, with no hook and
   no listener. A separate small command, not the `xrspike` broker: that path checks the session and writes an envelope for every
   call, and a cursor name needs neither. It runs on Hyprland's main thread, so it needs no lock. A syntax-only compile against the
   installed headers confirms a plugin can read the field (C++23, as the plugin builds; a made-up field fails the same check).
   It has not been linked or loaded.
2. **Supervisor:** in `desktop` mode only, read it about 30 times a second on the sampler thread, and let `_look` (`free_pointer.py`)
   return `text` for `text` and `vertical-text`. Go to the beam at once, and hold it about 100 ms after the name changes, so moving
   across the gaps between lines does not strobe. A failed read means the plain dot. A plugin too old to answer means the plain dot.
3. **Renderer:** a new `PointerLook::text`, its name in `ipc.cpp`, and the shape. An older renderer drops a frame with a look it
   does not know, so the plugin, renderer and Python install together, as they already must.

The plumbing would report every shape, so a hand over links or a crosshair could follow later for little work. Text is the only
thing proposed here.

**Before building, a short probe (needs Cody's OK):** with no XR run going, turn Hyprland's debug log on for a minute, hover
text in Chromium, foot, a GTK app and a Qt app, read the `cursorImage request: shape` lines, and turn the log off. That checks the
toolkit table on the real apps, without a plugin. It is a live config change, so it was not done here.

## Risks

- **The plugin's promise changes.** `spike/ownership/plugin.cpp` opens with "lifecycle-only module. No renderer, capture, input or
  config-file access." Reading a renderer field breaks the letter of that, even though it only reads a string. Cody's decision. It
  also makes a new plugin binary, so its earlier acceptance would need repeating. A second plugin would keep the promise but adds a
  load and unload, and in the runs of 2026-10-31 each of those reloaded the config, so it is the worse trade.
- **Hyprland internals.** `m_lastCursorData` is not plugin API and can move in any update. The plugin is already locked to the exact
  Hyprland commit and rebuilt after every update, so this shows up as a build error, not a silent fault.
- **A thin stem with no anti-aliasing.** With the head tilted, a 2 px stem shows stair-steps (the last frame on each option page), and
  it may crawl as the head moves. A stem of a whole number of px keeps its width while the head translates (measured). How it looks
  in motion can only be judged in the glasses. If it is bad, the fix is an anti-aliased edge for this one shape.
- **Size against text.** Desktops are shown at a third of their size, so a line of text is about 10 px tall and every option spans
  about three lines. Pointing is still exact, since the click lands on the stem's middle, but the cursor is bigger than what it points
  at, by design: the brief keeps it in the dot's size class.
- **Terminals ask for the beam across the whole window,** blank cells included, so in foot the dot would rarely show. That is still
  true to "shows what a press will do": a press there selects. And an app that takes the mouse, such as tmux or vim with mouse mode on,
  gets the plain dot.
- **Late by a moment.** The app decides the shape after it sees the pointer move, then the 30-a-second read adds up to 33 ms. After
  crossing to another window the old shape can last a few ms until the new app answers. None of this has been timed.
- **Unmeasured:** the extra reads alongside the existing 250 a second should be nothing, but that is an estimate.

## Effort by option

Options 1, 3, 4, 5 and 8 are a static shape, the same kind of work as today's resize marks, with option 8 the smallest: one more case
in `direction_marks`. Option 2 adds a smaller disc. Option 6 needs a glow, which the dot's flat path does not have (the mock stacks
flat copies). Option 7 needs its end shape plus a timer and a blend of two rows of points, and its end shape can ship first without
the animation.

## Recommended design

Option 7, Morph Beam, built in two steps: the end shape first, then the animation once it has been judged in the glasses. Its middle
is a thin stem, so the letters on either side stay in view (87% of the dot's cover, 57% of its light), and growing out of the dot ties
it to the dot more closely than any fixed shape can. Option 1, Serif Beam, is the runner-up if the animation is not wanted: it is the
easiest to recognise at real size, but covers more (123%). The pick is Cody's. None of this has been seen in the glasses.
