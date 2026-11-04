<p align="center"><img src="assets/logo.svg" width="330" alt="XR Workspace logo"></p>

# XR Workspace

XR Workspace turns your VITURE glasses into a big multi-screen desk on Omarchy. Put the glasses on and you see
three extra desktops floating in front of you. They stay where you put them when you turn your head, and you can use
them with your normal keyboard and mouse.

It works with the VITURE **Luma Ultra** (tested in the glasses) and **Beast** (only partly tested so far).

## What you need

- A computer running [Omarchy](https://omarchy.org) (it uses the Hyprland window manager).
- VITURE Luma Ultra or Beast glasses, plugged in.
- XRLinuxDriver (`xr-driver-git`). It reads the glasses' motion sensors so the desktops can react to your head.
- To build it: CMake, a C++ compiler, and Python 3.10 or newer.
- Optional, for videos: `mpv` (playing), `ffmpeg` (web videos), `zenity` (the file picker), `wl-clipboard` and Chromium
  (the browser button).

## Getting started

### 1. Install

Run these from this folder:

```sh
# Build the program that draws in the glasses
cmake -S spike -B build/release -DCMAKE_BUILD_TYPE=Release \
  -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/release -j

# Install it and the background helper, then start the helper
python -m tools.xr_controller install --build build/release
systemctl --user start viture-workspace-controller.service

# Add the panel to Omarchy
ln -s "$PWD/omarchy-plugin/cody.viture-workspace" ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins

# Optional: app icon and a launcher entry
install -Dm644 assets/icon.svg ~/.local/share/icons/hicolor/scalable/apps/viture-workspace.svg
install -Dm644 packaging/viture-workspace.desktop ~/.local/share/applications/
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

The background helper never starts anything in the glasses by itself. It waits for you to press Start.

### 2. Open the panel

Plug in the glasses, then open **XR Workspace** from the app launcher. You can also add the small **XR** widget to
your bar and click it.

If the panel says "controller not running", click **Start / restart controller**.

### 3. Start a session

1. Press the big **Start XR** button.
2. A box shows what the session will do, such as switching the glasses to 120 Hz for this session. Press **Start**.
3. Keep the glasses still while head tracking calibrates. This can take up to two minutes.

Your desktops now appear in the glasses, set up the way you left them last session (see
[Your layout comes back](#placing-desktops)). The very first time there are three, called XR-1, XR-2 and XR-3, flat in a
row.

The windows on your normal monitor move into the glasses as the session starts, one workspace per desktop: your first
workspace with windows goes to XR-1, the next to XR-2, and so on. If you have more workspaces with windows than
desktops, the rest go to the last desktop, each still its own workspace. Scratchpad workspaces, pinned windows and the
XR Workspace panel stay on your monitor. Stop brings everything back.

**Before a session** the panel shows little more than the Start button. Everything else (where each desktop sits,
ultrawide, adding or removing desktops, view mode, spacing, cursor follow, pose prediction, videos) is set in the
glasses during the session. Press Ctrl+Alt+H there for the keys.

Two things can show up next to Start:

- **Return windows to.** If more than one other monitor is connected and none of them is your saved choice, pick the
  one your windows go back to when the session stops. Start waits until you do.
- **Startup options** (click to open). These are the few settings a session starts with: each desktop's resolution
  and scale (click a desktop; both can also be changed in the glasses, from the desktop's pill), head tracking, the
  Luma Ultra's 120 Hz switch, and which monitor windows return to. You rarely need them.

## Everyday use

Move your mouse onto a glasses desktop the same way you would move it onto another monitor. Open apps there as usual.

Press **Ctrl+Alt+H** at any time for a help card in the glasses. The keys below only work while a session is running.
When the session ends they go away, so they never get in the way of normal typing.

The help card groups its keys by what they do. If they don't all fit the glasses' screen at the card's usual text
size, it splits them into pages and shows "1 / 3" between two arrows at the bottom. Turn pages with **Ctrl+Alt+PgUp /
PgDn**, or in the overview move the mouse onto an arrow or the page number and click (the grey dot works on the card,
which sits on top of the desktops). The card opens on page 1 every time.

### Key cheat sheet

| Keys | What it does |
| --- | --- |
| Ctrl+Alt+H | Show or hide the help card |
| Ctrl+Alt+K | Show or hide the key bind panel: change any key on this list, and see conflicts (see below) |
| Ctrl+Alt+PgUp / PgDn | Previous / next page of the help card, when it has more than one |
| Ctrl+Alt+T | Typing mode on or off: hands every key below back to your apps (see below) |
| Ctrl+Alt+Up | Maximize the selected desktop: it fills your view and follows your head |
| Ctrl+Alt+Down | Back to the overview of all desktops, fixed in the room. The mouse can then move anywhere around them, and each desktop has a small glass pill under it |
| Ctrl+Alt+Left / Right | Select the previous / next desktop |
| Ctrl+Alt+[ / ] | Less / more space between desktops |
| Ctrl+Alt+Space | Recenter: bring the desktops back in front of you |
| Ctrl+Alt+V | While a desktop is maximized: switch it between following your head and locked to your view (see below) |
| Ctrl+Alt+P | Turn head-motion prediction on or off (it cuts lag; on by default) |
| Ctrl+Alt+E | The background behind your desktops: off, and back to the last one you used (see below) |
| Ctrl+Alt+I | Immersive 3D for every desktop, on or off (not working yet: see docs/technical.md, "Immersive 3D") |
| Ctrl+Alt+L | Open or close the placement pill of the selected desktop (goes back to the overview first) |
| Ctrl+Alt+R, twice | Reset every desktop to three flat 1920×1080 desktops at 100%, and save that as your layout (see [Placing desktops](#placing-desktops)) |
| Ctrl+Alt+G | Send the window you're using to the selected glasses desktop |
| Ctrl+Alt+1 / 2 / 3 / 4 | Send the window you're using to XR-1 / XR-2 / XR-3 / XR-4 |
| Ctrl+Alt+B | Send the window back from the glasses to your normal monitor |
| Ctrl+Alt+O | Play a VR video file |
| Ctrl+Alt+U | Play the video or video page link you copied |

**Typing mode.** Press **Ctrl+Alt+T** when you want to type or use an app's own shortcuts while the session runs.
All of the keys in this list stop working until you press Ctrl+Alt+T again, so the arrow keys, Space, Tab, Esc and
other apps' Ctrl+Alt shortcuts do their normal thing. Any open help card or placement pill closes, and a playing video
gives its keys back too. In the overview the mouse stays a normal cursor (no grey dot) and the pills are hidden until
you leave typing mode. Ctrl+Alt+T is the one key that keeps working. Press it again to go back to normal XR control.
Super + mouse drags still work in typing mode: dragging a window across desktops brings the dot back until you let go.
A small, dim note in the glasses says which mode you're in and fades out after two seconds. Each session starts in XR
control. The panel's buttons work in both modes.

**Changing keys** (new, not yet tried in the glasses). Press **Ctrl+Alt+K** for the key bind panel: every key on
this list with what it does. While it is open the keyboard drives the panel only. **Up / Down** pick a key, **Enter**
changes it: press the new keys (Ctrl, Alt or Super with a letter, digit, F-key, arrow or punctuation; Shift may join
them) and they work at once, no restart, and are saved for every later session. **Esc** cancels a change. **R** puts
the picked key back to its default, **Shift+R** twice puts every key back. **Esc** or **Ctrl+Alt+K** closes the panel,
and it closes by itself after 30 seconds without a key. Keys that another XR key or a Hyprland / Omarchy bind already
uses are refused: the row turns red and says what has them, and a notification says it too. When a session starts,
the panel also checks your saved keys against Hyprland's: a key that clashes with a Hyprland bind is left off (red in
the panel, with a notification), because taking it away again at the end would also take away Hyprland's. If anything
goes wrong while the panel has the keyboard, **Esc three times** always gives it back. Your keys live in
`~/.config/viture-workspace/keybinds.json`; the help card shows them as you set them.

**Overview and Maximized** (new, not yet tried in the glasses). The way the desktops move with your head goes with
what you are looking at, as on the INAIR Pod:

- **The overview** shows all your desktops, and they always stay fixed in the room (**anchor**). Turn your head to
  look at a different one. Every session starts here.
- **Maximized:** in the overview, move the mouse onto a desktop (or pick it with Ctrl+Alt+Left / Right), then press
  **Ctrl+Alt+Up** (or the panel's **Maximize**). It fills your view and **follows** your head: it eases back in front
  of you when you turn. However you rolled it in the overview, it sits level and square to the glasses while
  maximized, locked as well. Clicking in a desktop never maximizes it. Press **Ctrl+Alt+V** to **lock** it to your view
  instead, like a normal screen glued to your face, and again to go back to follow. Your choice sticks: every desktop
  you maximize after that uses it, until you press Ctrl+Alt+V again. Each new XR start begins with follow.
- **Back to the overview** (Ctrl+Alt+Down, Ctrl+Alt+L or a pill): the desktops are fixed in the room again, exactly
  where they were before you maximized, each with the roll it had, wherever you turned meanwhile. Ctrl+Alt+Space
  (recenter) while maximized brings the overview back centred on the desktop you had maximized.

Ctrl+Alt+V in the overview changes nothing; a small notification says why. The little card in the glasses names the
mode when the session starts and when Ctrl+Alt+V changes it, not on every maximize.

**The background** (new, not yet tried in the glasses). By default there is nothing behind your desktops: black, which you
see through. Open any desktop's pill (Ctrl+Alt+L) and the **Background** dropdown offers **None**, **Grid** or **Smoke**. Grid is
the TRON grid: a black sky, a glowing grid floor running off to a bright horizon, a ring of mountain outlines in the distance,
and light trails racing along the grid lines. Smoke is slow wisps of fog drifting round you, thickest about the horizon and
thinning out overhead, with clear black air between them. Either one fades in behind your desktops and stays put in the room whatever they do, so you can
look all the way round, up and down, also while a desktop is maximized or locked to your view.

- **Ctrl+Alt+E**, or the switch beside the dropdown, turns it off and back on. It goes between None and the last background
  you used.
- **Brightness**, the slider under the dropdown, goes from 10% to 200%; 100% is the normal look. Black stays black at any
  setting and nothing gets brighter than the glasses' safe limit. With the pill open, Up / Down reach it too and Left / Right
  change it.
- **The colour is your theme's**, the same colour as the desktops' borders and glow. A theme change recolours it at once.
- **Behind a VR video** it stays where it is: you see it round a 180° or fisheye video. A 360° video covers all of it. The
  video's quality menu (Q) has a Background row too.

Your desktops, pills and the grey dot sit on top of it exactly as before. Your choice is remembered per pair of glasses and
comes back at your next XR start.

**Sending windows.** Ctrl+Alt+G sends a window to the desktop you have maximized, or the one highlighted in the
overview. The number keys work on any keyboard layout. A pinned window can't be sent; unpin it first. If nothing can be
sent, a small notification tells you why.

## Placing desktops

You can move, curve and resize each desktop in space, and put it anywhere around you: beside you, behind you,
above or below. Your layout is saved for your glasses model and comes back next session. There are three ways to do
it: the panel, the glass pill under each desktop in the glasses, and the mouse in the overview.

**Your layout comes back** (new, not yet tried in the glasses). Each XR session starts the way the last one ended: the
same number of desktops, each with its resolution and scale, where it sat round you, its size, its curve and its roll.
The layout is saved for your glasses model every time you change it and once more when the session stops. If nothing is
saved yet, or the saved layout can't be used (a damaged file, a value out of range, or desktops the system refuses to
create), the session starts with three flat desktops instead, and the reason is written to the log (`journalctl --user
-u viture-workspace-controller`). Follow or locked for a maximized desktop is not part of the layout: every session
still starts in follow.

**Your windows come back too** (new, not yet tried in the glasses). When a session stops, XR Workspace remembers where each
window was: which desktop, which workspace, its order among the tiled windows (including scrolling columns and their widths),
its position and size if it was floating, and whether it was fullscreen. The next session puts every window that is still
open back in that place, once your layout is back. Nothing is ever reopened for you: a window you closed stays closed, though
another open window of the same app can take its place. Windows you opened since go where they always go. A window you moved
to DP-2 or another normal monitor stays there, through Stop and the next session; only the monitor windows return to is
brought in. If a window's desktop no longer exists, it goes to the last desktop instead, like XR-4 to XR-3 on a reset.
Nothing to press. It is saved for your glasses model in `~/.config/viture-workspace/window-memory.json`, which holds window
titles in plain text, is readable only by you, and stays up to date as you move windows, Super drags included. A window is recognised by its identity while Hyprland keeps
running, and after a Hyprland restart by its app and title, so two identical terminals may swap places.

**Start over: Ctrl+Alt+R** (new, not yet tried in the glasses). Puts every desktop back to the default: three flat
1920×1080 desktops at 100%, level, in a row, with the default space between them. It asks first: the first press shows
a small notification, "Press Ctrl+Alt+R again to reset all monitors", and a second press within three seconds resets. If
you don't press it again, nothing happens. An open pill closes and a maximized desktop goes back to the overview first.

The whole reset glides. The desktops move back into the row in about half a second while a fourth desktop drops away
below your view, then the row settles along the same smooth curve as the desktops change size and resolution, and a
desktop that was missing grows out of its place. The windows on a fourth desktop move to XR-3, each workspace still its
own, so XR-3 may show one of them afterwards, with its own windows one workspace away. Anything that can't move goes to
your normal monitor, as with the pill's Remove.

Your saved layout is replaced by the default at the same time, so the next session starts that way too. The layout you
had is kept as a backup in `~/.config/viture-workspace/profiles.json` (`previous_setup`), one step back: the next reset
replaces it. There is no key to bring it back yet. A reset keeps the memory of your windows: windows on a fourth desktop stay on XR-3, and
if you add that desktop back with a pill's **Add new** during the session they return to it. A small notification confirms the reset. Ctrl+Alt+R does nothing while
a VR video plays or another desktop change is still running (a notification says so), and in typing mode. The help card
lists it on the recenter row, as Ctrl+Alt+Space / R.

**In the panel.** During a session, use the **Place** section. Each desktop has
four settings: left/right, down/up, distance (how big it looks) and curvature.

Left/right and down/up move the desktop round you, in degrees. Left/right goes all the way round: 180° (or −180°) is
directly behind you. Down/up goes from −90° (underfoot) to 90° (straight overhead). 0, 0 is the desktop's usual place
in the row. The **Place** section sums a moved desktop up in one line, for example "moved 12.5° right, 3° up". Its
small preview shows a desktop that is beside or behind you at the end of the row.

**Every desktop faces you** (new, not yet tried in the glasses). Each desktop is always turned square to you and kept
upright: in its place in the row, while you drag it, and wherever you leave it. So the two side desktops of the row
now turn a little toward you instead of sitting flat beside the middle one. Overhead or underfoot, a desktop faces
straight down or up at you. There is no setting to turn or tilt a desktop any more: the yaw, pitch and roll settings
are gone from the panel, from the glass pill and from saved layouts.

**Older saved layouts.** Until 2 November 2026 left/right and down/up were a share of the desktop's own size, and each
desktop also had yaw, pitch and roll. A layout saved before then still loads. Its left/right and down/up now read as
at most 10°, so a moved desktop comes back about where it was. Its yaw, pitch and roll are dropped, and its distance
and curvature are kept. Update the program that draws in the glasses, the background helper and the panel together
(see [docs/technical.md](docs/technical.md#build-and-install)): the way they talk about placements changed, so an older
copy of any one of them can't take the new placements. Going back to an older background helper alone resets the
placements this version saved to flat.

**Desktops bump each other out of the way** (new, not yet tried in the glasses). However you move or resize a desktop
in the overview (dragging its pill or a corner, or the sliders and keys of its pill), it never goes through another one. Any desktop in its way is pushed aside, just far enough, and slides along with it; if that one
runs into a third, the third is pushed too. A pushed desktop stays where it was pushed: moving the first one back
doesn't pull it back, and it is saved with your layout like any other move. What counts is how they look from where you sit, curve and size included: a small desktop far
away still bumps a big one in front of it if it would show through it, and two desktops that don't cover each other as
you see them never bump. A desktop is never pushed past straight overhead or underfoot; when there is no room left to
push (say against the top of the room, or round you, desktops all the way round), the one you move stops where it still
fits, and when you drag at an angle it slides along what can't give way. A key press or a click on a slider that bumps
desktops glides them there together in a quarter of a second; one that bumps nothing moves as it always did, and while
you drag they move with your hand. Only the handles round a desktop do this (its pill, its resize corners and edges, its
pill's sliders and keys); pressing and dragging on a desktop's content goes to the app there and never moves, resizes or
bumps anything. Super + drag is your window manager's as always: it moves or resizes a window, never a desktop, and
bumps nothing. Press Super while you are dragging a desktop and the drag stops right there.

Desktops get close before they bump. Side by side they stop about 2 px apart, and the highlight round the selected
desktop goes under its neighbour instead of over it, so there it shows as a thin line. Above and below, a desktop keeps
only the room its pill and its level bar really take: right above each other the two still fit in the gap and you can
use both, and anywhere else the desktops come just as close as side by side. What bumps is each desktop as you see it,
turned the way its level bar turned it, so two levelled desktops meet along their whole edge. Everything on a desktop
grows with it, so on a desktop made bigger these gaps look bigger too, and positions go in steps of a tenth of a degree,
which can add a few pixels. Two desktops raised above your eyes (or lowered below them) that you haven't levelled lean
toward each other, because each one faces you: they meet at their top corners and the gap opens downward. Click the
middle of each one's level bar and they stand upright. Turning or levelling a desktop never moves another one, even if
a corner swings over it. Right next to a neighbour that side has no room left for its resize handle, so use another
edge or a corner; a click on the neighbour's picture is always the neighbour's.

The panel's **Place** section is the exception: Apply puts a desktop exactly where you set it, even over another one.
While a desktop is maximized, a change there waits: a small notification says so, the panel keeps showing it, and it
applies when you go back to the overview. If you stop XR first it is saved for the next start.

When the layout itself changes, desktops that overlap afterwards are pushed apart by themselves, gliding the same way:
when a session starts with a saved layout that overlaps, when you change the spacing, and when you add, remove or widen
a desktop. The selected desktop stays where it is, and the others move the shortest way to a clear spot. That includes an
overlap you made with Apply, the next time the layout changes.

**With the placement pills in the glasses** (new, not yet tried in the glasses). In the overview
(**Ctrl+Alt+Down**) each desktop has a small pill in your theme's colour just under it. Click a pill with the grey dot (see the
next part) and it grows smoothly into that desktop's own placement panel: four sliders, one for each setting
(Left/right, Down/up, Distance, Curvature), each with its value.
Or press **Ctrl+Alt+L** to open the pill of the selected desktop. If a desktop is maximized, Ctrl+Alt+L goes back to
the overview first so you can see it. Only one pill is open at a time: opening another one closes the first. Changes apply straight away,
and there's no Save button.

With a pill open:

- **Click on a slider** and it jumps to that spot. Hold the button and drag along it to change it live; the desktop
  follows, while the pill stays put under the dot until you let go. Letting go saves it.
- **Drag the top strip** of the open pill to move the desktop, the same as dragging the closed pill.
- **Change the desktop's resolution or scale** with the two boxes under the sliders (new, not yet tried in the glasses).
  Click **Resolution** for a list of common sizes in three columns: 16:9 (1280x720 up to 3840x2160), 16:10 (1680x1050,
  1920x1200, 2560x1600) and ultrawide (2560x1080, 3440x1440, 3840x1080, 5120x1440). The current one is highlighted. Pick
  one and that desktop changes size on the spot, without restarting XR or touching your other monitors: its windows stay
  on it and it stays where it is in the glasses. Within one shape (16:9 to 16:9) it also keeps its size in the glasses;
  only how sharp it is and how much fits on it change. A new shape smoothly makes it wider or narrower where it is, and
  the desktops beside it slide along (the Ultrawide button does the same). When a desktop gets smaller, floating windows
  that would stick out are pulled back inside it, and one too big for it shrinks to fit. It keeps its scale if that still
  fits the new size, else it takes the nearest one that does. Click **Scale** for 100% to 300%; steps that would not give
  whole pixels at this resolution are greyed out. The next session starts with both.
- **Close it:** click the top strip, click anywhere outside the open pill, or press **Esc**. A click on a window goes
  to that window and closes the pill too, so what you type next goes to the window, not to the pill's keys.

| Keys (pill open) | What it does |
| --- | --- |
| Tab / Shift+Tab | Open the next / previous desktop's pill instead |
| Up / Down | Previous / next setting |
| Left / Right | Change the setting a little (hold Shift for a bigger step) |
| R | Reset this desktop to flat |
| U | Ultrawide on or off for this desktop |
| N | Add a new desktop |
| Shift+Delete | Remove this desktop (press twice) |
| Esc | Close the pill |
| Ctrl+Alt+L | Close the pill |

For left/right one press is 0.2° and Shift+Left / Shift+Right is 15°. It never stops: past 180° it carries on round to
the other side. For down/up one press is 0.2° and Shift is 10°, and it stops at 90° up or down. The pill shows whole
degrees, so a single small step may not change the number there; the panel's **Place** section shows the tenths. The
keys and sliders bump other desktops out of the way like a drag does (above), and R walks the desktop back to its slot,
pushing aside whatever is there now.

**While a pill is open** (new, not yet tried in the glasses) your desktops ease up a little, so the whole panel fits in view
with your head level, key hints included, and ease back down when it closes. A background stays where it is. **The open pill
follows the selected desktop:** select another desktop in the overview (Ctrl+Alt+Left / Right, the dot moving onto it, or a click
on it) and its pill opens as the old one closes. If no pill is open, selecting a desktop opens nothing. A click into a window keeps
the pill open, but its keys (arrows, Tab, R, U, N, Shift+Delete, Esc) go back to the window so you can type. Click the panel to
get them back.

These keys only take over while a pill is open. After it closes, the arrows, Tab, R and Esc type normally again.
Maximizing a desktop (Ctrl+Alt+Up) closes an open pill, and so does starting a video.

**With the mouse in the overview** (new, not yet tried in the glasses). Press **Ctrl+Alt+Down** to see all desktops.
In the overview your mouse is no longer stuck inside the desktops, and every desktop works like a normal screen:

- **The dot is your mouse.** Only a small grey dot is drawn, and over a desktop the real mouse pointer is right under
  it. Hover, click, right-click, scroll, drag and resize windows there and type into them, as on any monitor.
- **Push the dot past the edge of a desktop** and it keeps going into the space around it, to the pills, corners and
  the other desktops. Move it onto another desktop (changed 3 November 2026, not yet tried in the glasses) and the
  mouse works there at once: that desktop becomes the selected one (it gets the highlight), your keyboard goes to it,
  and its windows take every click, drag, right-click, scroll and key, wherever it is placed, however it is curved,
  rolled or sized, and whatever its scale.
- **The dot shows what a press will do** (new, not yet tried in the glasses). It's one cursor everywhere, and its look
  changes with what's under it: a plain dot over windows and empty space, a thin I-beam over text, two small arrows on a
  desktop's edge or corner, pointing the way it resizes, four arrows on a pill (move), and two arrows chasing round the
  dot on the level bar. The look stays through a drag until you let go.
- **Over text the dot turns into an I-beam** (new, not yet tried in the glasses, and it needs the new ownership plugin).
  Wherever an app asks for a text cursor, the dot becomes a thin grey beam with a small serif at each end, in the dot's
  grey and with its black ring, and clicks land on the beam's middle, exactly where the dot's centre was. It stays about
  a tenth of a second after the app stops asking, so it doesn't flicker as you cross the gaps between lines, and the
  arrows on an edge, corner, pill or level bar always win over it. This is the overview only: maximized, in typing mode
  and during a video you see the app's own cursor, as before. It works for apps that ask for their cursor by name, which
  GTK, Qt, Chromium and Electron apps and foot do. XWayland apps such as Spotify and Steam, and web pages with their own
  picture cursor, send a picture with no name, so they keep the dot. Terminals ask for the beam over the whole window,
  blank cells too, and an app that takes the mouse, such as tmux with mouse mode on, keeps the dot.
- **Move a desktop:** put the dot on the pill under a desktop. The pill lights up. Hold the left button and drag; the
  desktop follows like a window by its title bar, moving round you and facing you all the way. It snaps back into its
  slot if you drop it within half a degree of it. It never goes through another desktop as you see it: it bumps it
  out of the way (see [Desktops bump each other out of the way](#placing-desktops); side by side they stop about 2 px
  apart, and above and below only the room the pill and the level bar take is kept), and the bumped one stays where it
  ends up. However fast you move, it
  never jumps through one. To pass a neighbour without moving it, lift it clear of the row first, or carry it round
  behind you. A click that doesn't move opens the pill instead of moving anything.
- **Resize a desktop:** every corner has a small cap in your theme's colour wrapped round it. Put the dot on a cap, or
  just outside an edge, until it shows its two arrows (the cap under the dot lights up and grows along the edges).
  The handles are outside the desktop, so a window's corner, like its close
  button, stays the window's. Pushing the mouse against a desktop's edge from inside lands on that handle. Drag outward
  to make it bigger and inward to make it smaller. It can go from half to ten times its normal size (far bigger than your
  field of view; past the edge of the view the drag speeds up), and it keeps its shape. Its pill stays the same size and stays just below the desktop's border.
  Growing bumps the desktops around it out of the way, on every side; shrinking leaves them where they are.
- **Level or roll a desktop** (new 3 November 2026, not yet tried in the glasses): every desktop in the overview has a
  level bar, a capsule above its top edge in your theme's colour, like the pill below it. It has three parts, and only
  the one under the dot lights up. The left part turns the desktop counter-clockwise and the right part clockwise: click
  one and it turns that way, straight to level if level lies that way (say you recentred with your head tipped to one
  side; judged against gravity in anchor mode), else 2° further. Or hold either one and drag the dot round the desktop
  to roll it either way, up to 90°; it rests at level as you pass it. The middle part levels the desktop in one click and
  leaves it where it is. The bar moves, grows, shrinks and turns with the desktop. A desktop's picture, and any desktop
  covering a bar, keeps its clicks. Press R in its open pill to put the desktop back flat in its slot and level. The roll
  is saved with the rest of your layout. It belongs to the overview: a maximized desktop always sits level, and gets its
  roll back when you return.
- **Only the handles do XR things** (changed 3 November 2026, not yet tried in the glasses): the pill under a desktop
  (and its panel when open), the level bar above it, and the resize handles just outside its edges and corners. All of
  them are outside the desktop itself; everything on the desktop is its windows'. Clicking a window
  never maximizes the desktop. To maximize, press **Ctrl+Alt+Up** (or the panel's **Maximize**): the desktop under the
  dot grows where it is to fill your view, then follows your head.
- **Drag a window to another desktop with Super** (new, not yet tried in the glasses): hold **Super** and drag it with the
  left button, as on any monitor. Past the edge of its desktop the dot carries on through the space between desktops, and
  when it reaches another desktop the window arrives under it, wherever that desktop is placed. A tiled window fits in
  where you drop it. Let go with the dot on a desktop and the window lands on that desktop, whole and under the dot, even
  if you were holding a floating window by its far end. Let go between desktops and it stays where it was. While you
  drag, the pills, level bars and resize handles do nothing, and nothing animates. **Super and the right button**
  resizes the window as usual, and the mouse stays on that desktop until you let go. If the dot is off the desktops, Super
  and a click do nothing. All of this works in typing mode too.
- **Moving a window to another desktop by holding it** without Super is switched off for now, so a long press or a slow
  drag in an app stays the app's. **Ctrl+Alt+1–4** and **Ctrl+Alt+G** still send the window you're using.
- **A held button stays on its desktop.** While you hold a mouse button (dragging inside an app, selecting text) the
  mouse stays on that desktop until you let go, even at its edge. The selected desktop is the one under the dot;
  Ctrl+Alt+Left / Right still pick one too.
- **Push the mouse past an edge to switch** (new, not yet tried in the glasses): maximized, keep pushing the mouse past
  an edge and the view follows your hand to the desktop placed that way. The picture zooms out only as far as it has to,
  a dot takes over from the mouse pointer, and the view turns with your hand at the speed the mouse moved on the desktop, so
  it goes as fast as you push. Pull back before you get there and the view goes back to the desktop you were on. Stop
  for a moment (about half a second) and it does the same. Once the dot reaches the other desktop it settles in smoothly:
  the desktop comes in front of you (follow) or stays locked to your view, and the mouse is at the matching height
  on the edge you came in by. This only happens for a push that really goes that way: the other desktop's middle
  must be within about 45° of the direction you are pushing, measured from the spot on the edge where the mouse is and from
  where the desktops really are. If none is, the mouse just stops at the edge and nothing moves, so a desktop off to
  the side or behind you is never picked by mistake, and a desktop off at an angle can be in reach from one end of an edge
  and not the other. Far round you, it goes the way you pushed. A short push does nothing, so a quick touch (to reach a bar
  at the top, say) doesn't switch. Not in typing mode or during a VR video. Every other switch is instant, with no
  zoom or turn: Ctrl+Alt+Left / Right, the panel, Ctrl+Alt+G sends and anything in the overview.
- **Scrolling layout: the columns come first** (new, not yet tried in the glasses): maximized on a workspace in
  Hyprland's scrolling layout, pushing the mouse against the left or right edge scrolls the windows waiting past it
  into view first, one column at a time, as on your normal monitor. The mouse stays at the edge meanwhile. Once the
  last column is in, it waits a moment (about half a second), and only then does pushing on, or pushing again, switch
  to the desktop placed that way. Other layouts, fullscreen windows and the top and bottom edges work as before. The
  scrolling also works in typing mode (which never switches).

If your mouse is on your normal monitor when you press Ctrl+Alt+Down, it jumps into the middle of the selected desktop.
Maximizing (Ctrl+Alt+Up) gives the mouse back as normal. The dot, pills, level bars and corner caps are off in typing mode (Ctrl+Alt+T)
and while a video plays, and they hide after 30 seconds without moving the mouse (move it to bring them back). An open
pill stays until you close it or until 30 seconds pass without input.

Resizing changes how big a desktop looks, not its resolution. To change its resolution (how sharp it is and how much
fits on it), use the **Resolution** box in the desktop's pill (see [Placing desktops](#placing-desktops)).

**Desktops all around you** (new, not yet tried in the glasses). Carrying a desktop round you works in the overview,
where the desktops always stay fixed in the room (anchor, see above):

- **Carry a desktop round you.** Drag its pill as above. Inside the view the pill stays under the dot. Keep dragging
  past the edge of the view and the desktop speeds up. The dot can go about half a view past each edge. At its far left
  or right end the desktop is directly behind you, and at its top or bottom end it is straight overhead or underfoot.
  It faces you the whole way.
- **Pick it up again.** Turn round to look at the desktop and move the mouse out past the edge of the view on the
  desktop's side, the way you dragged it there, until its pill lights up. The dot itself stays in front of you, so you
  won't see it. Then drag as usual. This works for pills only: a far desktop's corners and the desktop itself only
  react under the dot.
- **Maximized.** Maximizing a desktop brings it in front of you, wherever you put it: select it with Ctrl+Alt+Left /
  Right and press Ctrl+Alt+Up, even when it is behind you. Back in the overview every desktop is where you left it.
- **Out of the mouse's reach.** For a desktop far round you, the mouse can only reach its pill, not its corners, its
  open pill panel or the desktop itself. Use the keys: Ctrl+Alt+Left / Right selects it, Ctrl+Alt+L opens its pill,
  the arrows change it and R puts it back flat in its slot and level.

All of this is checked by offline tests only. It has not been tried in an isolated VM or in the glasses.

## VR video

During a session you can watch 360° and 180° videos, including 3D ones, all around you. The desktops come back when you
press Esc. This is new and has not been tried in the glasses yet.

**A video file:** press **Ctrl+Alt+O** and pick a file. Or use the panel's **VR video** section and click
**Browse…**.

**A video on a website:** copy the video's link, or the page's address, then press **Ctrl+Alt+U** (or click **Paste
from browser** in the panel). The video opens around you. Page links are looked up with yt-dlp.

While a video plays:

| Keys | What it does |
| --- | --- |
| Space | Play / pause |
| Left / Right | Back / forward 10 seconds (hold Shift for 60 seconds) |
| Tab | Try the next video format, if the picture looks wrong |
| Up / Down | Show or hide the controls |
| Q | Open or close the quality menu (see below) |
| Ctrl+Alt+Space | Recenter the video |
| Esc | Back to your desktops |

**Quality menu:** press **Q** while a video plays. The controls card shows the video's quality settings. Up and Down
pick a setting, Left and Right change it, and Q or Esc closes the menu. Changes show at once and are remembered for
your next videos, per pair of glasses. There are two video settings, plus a [Background](#everyday-use) row. **Texture filter** is *Catmull-Rom* (the default, crisp), *Sharp*
(crisper, with a little ringing), *Bilinear* (softer) or *Nearest pixels* (hard pixel edges). **Deband** is *Off* (the default) or *On*; it smooths the stepped bands you can see in skies and gradients.

Videos with copy protection, or links that need you to be logged in, won't play. The panel or a notification will tell
you why. See [docs/vr-video.md](docs/vr-video.md) and [docs/web-video.md](docs/web-video.md) for formats and limits.

## Stopping, recovering and troubleshooting

**Stop.** Press **Stop** in the panel. Every window on the glasses desktops moves back to your normal monitor, and your
display goes back to how it was.

**Recover.** If a session crashed or the computer lost track of it, the panel shows **Recover**. Press it to clean up
and put your windows and display back. If Recover can't finish, the panel says why, naming the desktop or window in the
way. If the panel says "Needs CLI recovery", see [docs/technical.md](docs/technical.md#cli-recovery).

**Use the glass pill's Scale on XR desktops, not Omarchy's.** Omarchy's monitor scale (the Monitor panel, Super+/ and
Super+Alt+/) changes whichever monitor has focus, and it saves the change. On an XR desktop that breaks the desktop and
adds a line naming it to `~/.config/hypr/monitors.lua`. If that happens, stop and press Recover if the panel offers it: its
confirm box lists the line, and Recover takes it out and keeps a copy of the file next to it. If the panel doesn't offer
Recover, the session has already cleaned up, and a notification tells you which line to delete.

**Common problems:**

- **"No VITURE glasses detected."** Check the cable and plug the glasses in again.
- **Starting takes a long time.** Head tracking is calibrating. Keep the glasses still for up to two minutes.
- **Start refuses because another tracking driver is running.** Close the other XRLinuxDriver first.
- **A key does nothing.** The keys only work during a session. The arrows, Tab, R and Esc only act on a pill while
  one is open, and most desktop keys do nothing while a video plays.
- **Keys or the cursor act strangely after a crash.** Press the key once more, or click once. That clears what the
  crashed session left behind.
- **Start says `monitors.lua line 9 names XR-1`.** Delete that line from `~/.config/hypr/monitors.lua`, run
  `hyprctl reload`, and start again. Hyprland won't let an XR desktop be created while a rule uses its name.
- **The panel looks out of date after an update.** Run `omarchy-restart-shell`.
- **It stopped working after a Hyprland update.** Rebuild and run the install step again with no session running.

**About ghost images on the glasses screens.** The glasses use tiny OLED screens. Bright text that stays still for a
long time can leave a faint ghost image that fades after a while. To prevent that, the help card, the open placement
pill and the video controls are dimmed, and they close by themselves after 30 seconds without input. The overview's
dot, glass pills, level bars and corner caps are dim and hide after 30 seconds without mouse movement. Don't leave a bright
video paused for a long time either.

### When something goes wrong

XR Workspace keeps an eye on itself while a session runs. If something breaks, you get a desktop notification saying
**"XR Workspace hit a problem: ... A report was saved."**, and the panel shows the last problem and when it happened.
Nothing is needed from you: the session is left as it is, and it never stops or restarts anything by itself.

It notices when:

- the program drawing in the glasses crashes or stops unexpectedly
- the picture stops reaching the glasses, or the frame rate collapses
- the glasses' USB connection drops
- head tracking is lost
- Hyprland crashes, or the XR plugin stops working
- a session dies or stops answering, or Recover doesn't finish

Pressing Stop, using Recover, restarting the panel and unplugging the glasses after you stop are all normal, so none of
them make a report. A USB drop the session survives does make one, so you can see how often it happens, but the same
problem is reported at most once every ten minutes.

Each report is a pair of files in `~/.local/state/viture-workspace/incidents/`: a short `.md` you can read and a `.json`
with the full detail. It says what happened and when, and what the likely cause was. Reports stay on your computer. They
hold logs and timing numbers, with no screen content, window titles, clipboard text or video links, and anything that
looks like a password or key is removed. If `~/.config/viture-workspace/on-incident` exists and can be run, XR Workspace
runs it with the report's path, so another tool can pick the report up. There's more in
[docs/technical.md](docs/technical.md#incident-reports).

## FAQ

**Does it change my normal monitor setup?**
No. Any display change is temporary and is undone when you stop. Your saved monitor settings are never edited.

**Does it record or send my screen anywhere?**
No. It only reads the three glasses desktops, and only to draw them in the glasses. The picture stays in memory and is
never saved or sent anywhere. The head tracking driver runs with no network access. The only thing that goes online is
a web video you choose to play.

**Can I use more or fewer than three desktops?**
Yes, from 1 to 4, in the glasses (new, not yet tried in the glasses). Open a desktop's glass pill: under its sliders are
**Ultrawide** (switches that desktop between 16:9 and 21:9 at the same height), **Add new** (a new desktop the size of
that one, named with the lowest free number up to XR-4) and **Remove** (press it twice; that desktop's windows move to
your normal monitor, as Stop would move them). The keys U, N and Shift+Delete do the same while the pill is open. The next
session starts with the same desktops; Ctrl+Alt+R goes back to three. Above those buttons, **Resolution** sets that
desktop's resolution (16:9, 16:10 and ultrawide sizes, from 1280x720 to 5120x1440) and **Scale** its scale, from 100% to
300% (steps that would not give whole pixels are greyed out). Both apply at once, without restarting XR, and the next
session keeps them (new, not yet tried in the glasses). Any other size can be set under **Startup options** in the panel
before starting.

**Which glasses are tested?**
The Luma Ultra has been used in real sessions. On the Beast only basic display tests have run. See
[what has been tested](docs/technical.md#status).

**Will it start a session by itself?**
No. A session only starts when you press Start, and that approval covers only that one session.

## More detail

- [docs/technical.md](docs/technical.md): test evidence, how it is built, the safety design, the full key rules, running
  the tests, and the host diagnostic tool.
- [docs/vr-video.md](docs/vr-video.md) and [docs/web-video.md](docs/web-video.md): how VR video and web video work.
- [docs/placement-menu-test-plan.md](docs/placement-menu-test-plan.md): step-by-step checks to try in the glasses.
