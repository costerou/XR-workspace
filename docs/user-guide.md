# XR Workspace user guide

The full how-to for a running session. Start with the [README](../README.md) for installing and a first session.

> **Test status.** The basics (desktops, head tracking, switching, spacing, recenter, the help card, Stop and Recover)
> have been used in the glasses on a Luma Ultra. Most of what is described below the
> [key list](#keys) has passed offline tests only and has not been tried in the glasses yet. The exact evidence for each
> feature is in [technical.md](technical.md#status).

## Contents

- [The two views: overview and maximized](#the-two-views-overview-and-maximized)
- [Keys](#keys)
- [The options menu in the glasses](#the-options-menu-in-the-glasses)
- [Using the mouse in the overview](#using-the-mouse-in-the-overview)
- [Placing desktops](#placing-desktops)
- [Adding, removing and resizing desktops](#adding-removing-and-resizing-desktops)
- [Your layout and windows come back](#your-layout-and-windows-come-back)
- [Moving windows between desktops](#moving-windows-between-desktops)
- [Typing mode](#typing-mode)
- [Changing keys](#changing-keys)
- [Background, privacy and stabilization](#background-privacy-and-stabilization)
- [VR video](#vr-video)
- [Immersive 3D](#immersive-3d)
- [When something goes wrong](#when-something-goes-wrong)

## The two views: overview and maximized

- **Overview** (Ctrl+Alt+Down). All desktops are visible and stay fixed in the room. Turn your head to look at
  another one. Every session starts here. Each desktop has a small pill under it and a level bar above it.
- **Maximized** (Ctrl+Alt+Up). The selected desktop fills your view. By default it **follows** your head: it eases
  back in front of you when you turn. Press **Ctrl+Alt+V** to **lock** it to your view instead, like a screen fixed to
  your face. Your choice applies to every desktop you maximize until you press Ctrl+Alt+V again. Each new session
  starts with follow.

Going back to the overview puts every desktop exactly where it was. Ctrl+Alt+Space recenters the desktops in front of
you in either view.

## Keys

All keys work only while a session runs, and you can change them all (see [Changing keys](#changing-keys)).

| Keys | What it does |
| --- | --- |
| Ctrl+Alt+H | Help card on / off. Ctrl+Alt+PgUp / PgDn turn its pages |
| Ctrl+Alt+K | Key bind panel: see and change every key |
| Ctrl+Alt+T | Typing mode on / off |
| Ctrl+Alt+Up / Down | Maximize the selected desktop / back to the overview |
| Ctrl+Alt+Left / Right | Select the previous / next desktop |
| Ctrl+Alt+V | While maximized: follow your head or lock to your view |
| Ctrl+Alt+Space | Recenter |
| Ctrl+Alt+R, twice | Reset to three flat 1920×1080 desktops and save that as your layout |
| Ctrl+Alt+[ / ] | Less / more space between desktops |
| Ctrl+Alt+L | Open or close the selected desktop's placement pill |
| Ctrl+Alt+P | Head-motion prediction on / off (on by default; it cuts lag) |
| Ctrl+Alt+G | Send the focused window to the selected desktop |
| Ctrl+Alt+1 … 4 | Send the focused window to XR-1 … XR-4 |
| Ctrl+Alt+B | Send the focused window back to your normal monitor |
| Ctrl+Alt+D | Privacy: turn your normal monitor off / on |
| Ctrl+Alt+E | Background off / back to the last one you used |
| Ctrl+Alt+I | Immersive 3D on / off for every desktop |
| Ctrl+Alt+O | Play a VR video file |
| Ctrl+Alt+U | Play the video link you copied |
| Ctrl+Alt+C | During a VR video: hide / show the controls card |

## The options menu in the glasses

During a session the desktop panel only shows **Stop** and a few live numbers. Everything else is set in the glasses.
In the overview, click the small **gear** to the right of a desktop's pill. The options menu opens under that
desktop, with tabs for 3D, stabilization, layout, view, video, input, key binds, windows, privacy, help and the
session. Placement, resolution, scale and background stay in the desktop's own
[placement panel](#placing-desktops).

The settings a session *starts* with (each desktop's resolution and scale, head tracking, the Luma Ultra's 120 Hz
switch, the monitor windows return to) are under **Session setup** in the panel, before you press Start. They are saved as the default; the
confirm page's switches can turn tracking or the 120 Hz switch off for one session only.

## Using the mouse in the overview

In the overview the mouse is drawn as a small grey dot. Over a desktop, the real pointer sits right under it, so you can
click, scroll, drag and type into windows as on any monitor. Move the dot onto another desktop and that desktop becomes
the selected one, with your keyboard on it.

The dot changes shape to show what a press will do: an I-beam over text, two arrows on a resize handle, four arrows on a
pill, circling arrows on a level bar.

Only the handles around a desktop do XR things. Everything on the desktop itself belongs to its windows:

- **The pill** under a desktop. Drag it to move the desktop. Click it to open the placement panel.
- **The level bar** above a desktop. Click its left or right part to roll the desktop 2° (or straight to level), or
  drag it to roll freely. Click the middle to level it.
- **Corner caps and edges.** Drag them to make the desktop look bigger or smaller (half to ten times its size). This
  changes its size in the glasses, not its resolution.
- **The gear** next to the pill opens the [options menu](#the-options-menu-in-the-glasses).

**Pushing past an edge.** Keep pushing the mouse past a desktop's edge toward a neighbour and the view turns with your
hand to that desktop. Pull back or pause for half a second to cancel. It only switches if a desktop lies roughly that
way (within about 45°), so nothing behind you is picked by mistake. In Hyprland's scrolling layout, pushing first
scrolls the hidden columns into view.

The dot, pills and handles hide after 30 seconds without mouse movement, and they are off in typing mode and during a
video.

## Placing desktops

Each desktop has four settings: **left/right** and **down/up** (in degrees around you; 180° is directly behind you,
±90° is straight overhead or underfoot), **distance** (how big it looks) and **curvature**. Every desktop always
faces you and stays upright, wherever you put it.

There are three ways to place a desktop:

1. **Drag its pill** in the overview. Drag past the edge of your view to carry it behind you or overhead. To pick a far
   desktop up again, look at it and push the mouse out past the edge of the view on its side until its pill lights up.
2. **The placement panel.** Click a pill, or press Ctrl+Alt+L, and it opens into four sliders. Changes apply at once and
   are saved; there is no Save button. Click outside it or press Esc to close it.
3. **The keyboard**, while a placement panel is open:

| Keys (panel open) | What it does |
| --- | --- |
| Tab / Shift+Tab | Next / previous desktop's panel |
| Up / Down | Previous / next setting |
| Left / Right | Change it a little (Shift: a bigger step) |
| R | Put this desktop back flat in its slot |
| U | Ultrawide on / off |
| N | Add a desktop |
| Shift+Delete, twice | Remove this desktop |
| Esc or Ctrl+Alt+L | Close the panel |

These keys only take over while a panel is open. Clicking into a window hands them back to that window.

**Desktops never overlap.** Moving or resizing a desktop pushes any desktop in its way aside, and that one stays where it
was pushed. Adding, removing, widening or respacing desktops also pushes overlapping ones apart.

## Adding, removing and resizing desktops

You can have 1 to 4 desktops. In a desktop's placement panel:

- **Add new** makes another desktop the same size (up to XR-4).
- **Remove** (press twice) moves that desktop's windows to your normal monitor.
- **Ultrawide** switches the desktop between 16:9 and 21:9.
- **Resolution** offers common 16:9, 16:10 and ultrawide sizes from 1280×720 to 5120×1440. **Scale** goes from 100% to
  300%. Both apply at once, without restarting.

Use these instead of Omarchy's own monitor scale keys (Super+/ and Super+Alt+/). Those save the change into
`~/.config/hypr/monitors.lua`, which breaks an XR desktop. If it happens, see
[Troubleshooting](../README.md#troubleshooting).

## Your layout and windows come back

Each session starts the way the last one ended: the same desktops, resolutions, scales, positions, sizes, curves and
rolls. The layout is saved per glasses model in `~/.config/viture-workspace/profiles.json`. If it can't be used, the
session starts with three flat desktops and the reason is logged (`journalctl --user -u viture-workspace-controller`).

When a session stops, XR Workspace also remembers where each window was. The next session puts every window that is
still open back in place. Nothing is reopened for you. This memory lives in
`~/.config/viture-workspace/window-memory.json`, readable only by you. It contains window titles in plain text.

**Ctrl+Alt+R, twice within three seconds,** resets to three flat 1920×1080 desktops at 100%. The first press shows
"Press Ctrl+Alt+R again to reset all monitors". Windows on a removed
desktop move to XR-3. The layout you had is kept once as `previous_setup` in `profiles.json`.

## Moving windows between desktops

- **Super + left drag** a window onto another desktop, as between monitors. **Super + right drag** resizes it.
- **Ctrl+Alt+G** sends the focused window to the selected desktop; **Ctrl+Alt+1 … 4** to a numbered one;
  **Ctrl+Alt+B** back to your normal monitor.

Pinned windows can't be sent. Unpin them first.

## Typing mode

**Ctrl+Alt+T** hands every XR key back to your apps, so the arrows, Space, Tab, Esc and other apps' Ctrl+Alt shortcuts
work normally. Open cards and panels close, and the dot and pills hide. Ctrl+Alt+T is the one key that still works:
press it again to go back. Super drags work in both modes.

## Changing keys

Press **Ctrl+Alt+K**. Pick a key with **Up / Down**, press **Enter**, then press the new keys (Ctrl, Alt or Super plus a
key; Shift may be added). The change works at once and is saved for later sessions. **R** resets the picked key,
**Shift+R** twice resets them all, **Esc** closes the panel.

Keys already used by another XR key or by a Hyprland / Omarchy bind are refused and shown in red. If the panel ever
seems stuck holding the keyboard, press **Esc three times**. Keys are stored in
`~/.config/viture-workspace/keybinds.json`.

## Background, privacy and stabilization

- **Background.** By default there is nothing behind your desktops. A desktop's placement panel has a **Background**
  choice: **Grid** or **Smoke**, with a brightness slider, in your theme's colour. **Ctrl+Alt+E** switches it off and
  back on. It is saved per pair of glasses.
- **Privacy.** **Ctrl+Alt+D** turns your normal monitor off so only the glasses show anything. Press it again, or stop
  the session, to turn it back on.
- **Stabilization** steadies small head movements but lets deliberate turns through. Set its strength (0–100%) on the
  options menu's Stabilization tab. See [stabilization.md](stabilization.md).

## VR video

Watch 180° and 360° videos, including 3D ones, around you. Your desktops come back when you press Esc. Needs `mpv`;
web links also need `yt-dlp` and `ffmpeg`.

- **A file:** press **Ctrl+Alt+O** and pick it.
- **A web video:** copy its link or the page address, then press **Ctrl+Alt+U**.

| Keys (video playing) | What it does |
| --- | --- |
| Space | Play / pause |
| Left / Right | Back / forward 10 s (Shift: 60 s) |
| Tab | Try the next video format if the picture looks wrong |
| Up / Down | Show / hide the controls |
| Q | Quality menu: texture filter, deband, background |
| Ctrl+Alt+Space | Recenter the video |
| Esc | Back to your desktops |

Copy-protected videos and links that need a login won't play. Details: [vr-video.md](vr-video.md),
[web-video.md](web-video.md).

## Immersive 3D

Shows any desktop in stereo 3D, using an AI depth model on its normal 2D picture. Click the **3D** button beside a
desktop's level bar, or press **Ctrl+Alt+I** for all desktops, and hold R1 on the glasses when asked. It needs extra
setup: see [immersive-3d.md](immersive-3d.md).

## When something goes wrong

XR Workspace watches itself during a session. If the renderer crashes, the picture or frame rate drops, the USB link
drops, head tracking is lost or Hyprland crashes, you get a notification: **"XR Workspace hit a problem: … A report was
saved."** It never stops or restarts anything by itself.

If the glasses' USB drops but the picture stays, the view holds still and shows **Reconnecting tracking…**. After 60
seconds without tracking the session stops normally and your windows come back.

Reports are saved in `~/.local/state/viture-workspace/incidents/` as a readable `.md` and a detailed `.json`. They
contain logs and timings only: no screen content, window titles, clipboard text or video links, and anything that looks
like a password is removed. To hand reports to another tool, make `~/.config/viture-workspace/on-incident` executable;
it is run with each report's path. More in [technical.md](technical.md#incident-reports).

**Screen burn-in.** The glasses use OLED screens. Cards, pills and the dot are dimmed and hide after 30 seconds without
input. Don't leave a bright video paused for a long time.
