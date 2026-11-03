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
- **Startup options** (click to open). These are the few settings that can't change once a session runs: each
  desktop's resolution and scale (click a desktop), head tracking, the Luma Ultra's 120 Hz switch, and which monitor
  windows return to. You rarely need them.

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
| Ctrl+Alt+PgUp / PgDn | Previous / next page of the help card, when it has more than one |
| Ctrl+Alt+T | Typing mode on or off: hands every key below back to your apps (see below) |
| Ctrl+Alt+Up | Maximize the selected desktop: it fills your view and follows your head |
| Ctrl+Alt+Down | Back to the overview of all desktops, fixed in the room. The mouse can then move anywhere around them, and each desktop has a small glass pill under it |
| Ctrl+Alt+Left / Right | Select the previous / next desktop |
| Ctrl+Alt+[ / ] | Less / more space between desktops |
| Ctrl+Alt+Space | Recenter: bring the desktops back in front of you |
| Ctrl+Alt+V | While a desktop is maximized: switch it between following your head and locked to your view (see below) |
| Ctrl+Alt+P | Turn head-motion prediction on or off (it cuts lag; on by default) |
| Ctrl+Alt+L | Open or close the placement pill of the selected desktop (goes back to the overview first) |
| Ctrl+Alt+R | Reset every desktop to three flat 1920×1080 desktops at 100%, and save that as your layout (see [Placing desktops](#placing-desktops)) |
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
A small, dim note in the glasses says which mode you're in and fades out after two seconds. Each session starts in XR
control. The panel's buttons work in both modes.

**Overview and Maximized** (new, not yet tried in the glasses). The way the desktops move with your head goes with
what you are looking at, as on the INAIR Pod:

- **The overview** shows all your desktops, and they always stay fixed in the room (**anchor**). Turn your head to
  look at a different one. Every session starts here.
- **Maximized:** click a desktop in the overview (or select it and press Ctrl+Alt+Up) and it fills your view and
  **follows** your head: it eases back in front of you when you turn. Press **Ctrl+Alt+V** to **lock** it to your view
  instead, like a normal screen glued to your face, and again to go back to follow. Your choice sticks: every desktop
  you maximize after that uses it, until you press Ctrl+Alt+V again. Each new XR start begins with follow.
- **Back to the overview** (Ctrl+Alt+Down, Ctrl+Alt+L or a pill): the desktops are fixed in the room again, exactly
  where they were before you maximized, wherever you turned meanwhile. Ctrl+Alt+Space (recenter) while maximized
  brings the overview back centred on the desktop you had maximized.

Ctrl+Alt+V in the overview changes nothing; a small notification says why. The little card in the glasses names the
mode when the session starts and when Ctrl+Alt+V changes it, not on every maximize.

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

**Start over: Ctrl+Alt+R** (new, not yet tried in the glasses). Puts every desktop back to the default: three flat
1920×1080 desktops at 100%, level, in a row. An open pill closes and a maximized desktop goes back to the overview first.
The desktops glide back into the row in about half a second, then a fourth desktop is removed (its windows go to your
normal monitor, as with the pill's Remove), missing ones are added, and every resolution and scale goes back to
1920×1080 at 100%. Your saved layout is replaced by the default at the same time, so the next session starts that way
too. A small notification confirms it. Ctrl+Alt+R does nothing while a VR video plays or another desktop change is still
running (a notification says so), and in typing mode. The help card lists it on the recenter row, as Ctrl+Alt+Space / R.

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
- **Close it:** click the top strip, click anywhere outside the open pill, or press **Esc**. A click inside a desktop
  with the normal cursor goes to the window there and leaves the pill open.

| Keys (pill open) | What it does |
| --- | --- |
| Tab / Shift+Tab | Open the next / previous desktop's pill instead |
| Up / Down | Previous / next setting |
| Left / Right | Change the setting a little (hold Shift for a bigger step) |
| R | Reset this desktop to flat |
| U | Ultrawide on or off for this desktop |
| N | Add a new desktop |
| Delete | Remove this desktop (press twice) |
| Esc | Close the pill |
| Ctrl+Alt+L | Close the pill |

For left/right one press is 0.2° and Shift+Left / Shift+Right is 15°. It never stops: past 180° it carries on round to
the other side. For down/up one press is 0.2° and Shift is 10°, and it stops at 90° up or down. The pill shows whole
degrees, so a single small step may not change the number there; the panel's **Place** section shows the tenths. The
keys and sliders don't stop at other desktops, so they can push one desktop over another. Only dragging the pill (below)
keeps them apart.

These keys only take over while a pill is open. After it closes, the arrows, Tab, R and Esc type normally again.
Maximizing a desktop (Ctrl+Alt+Up) closes an open pill, and so does starting a video.

**With the mouse in the overview** (new, not yet tried in the glasses). Press **Ctrl+Alt+Down** to see all desktops.
In the overview your mouse is no longer stuck inside the desktops:

- **The dot is your mouse.** Only a small grey dot is drawn, and over the selected desktop the real mouse pointer is
  right under it. Hover, right-click, scroll, drag and resize windows there as on any monitor.
- **Push the dot past the edge of the selected desktop** and it keeps going into the space around it, to the pills,
  corners and the other desktops. Over another desktop it is only a pointer for clicking: the mouse never works there,
  and the desktop isn't selected until you click it.
- **Move a desktop:** put the dot on the pill under a desktop. The pill lights up. Hold the left button and drag; the
  desktop follows like a window by its title bar, moving round you and facing you all the way. It snaps back into its
  slot if you drop it within half a degree of it. It never overlaps another desktop as you see it: it stops just
  before touching one (a small gap is kept, and the strip with its pill counts as part of the desktop), and if you
  keep pushing at an angle it slides along that desktop's edge. However fast you move, it never jumps through one. To
  get past a neighbour, lift it clear of the row first, or carry it round behind you. A desktop that already overlaps
  another (set with the sliders or keys) can still be dragged, as long as it doesn't overlap that one more. A click
  that doesn't move opens the pill instead of moving anything.
- **Resize a desktop:** put the dot on a corner of a desktop until a small grey bracket appears. Drag outward to make
  it bigger and inward to make it smaller. It can go from half to ten times its normal size (far bigger than your
  field of view; past the edge of the view the drag speeds up), and it keeps its shape. Its pill stays the same size.
  Resizing doesn't stop at other desktops.
- **Level or roll a desktop:** every desktop in the overview has two small curved arrows in your theme's colour above
  its top corners: counter-clockwise on the left, clockwise on the right. Click one and the desktop turns that way:
  straight to level if level lies that way (say you recentred with your head tipped to one side; judged against
  gravity in anchor mode), else 2° further. Or hold either arrow and drag the dot round the desktop to roll it either
  way, up to 90°; it rests at level as you pass it. The roll is saved with the rest of your layout.
- **Maximize a desktop:** click it with the dot. The window under the dot gets the click and the desktop is maximized,
  as Ctrl+Alt+Up does: it grows where it is to fill your view, then follows your head. It gets the focus and the
  normal cursor appears where you clicked. A press that drags (moving a window, selecting text) doesn't maximize.
- **Move a window to another desktop** (new, not yet tried in the glasses): press on the window and hold the button for
  a moment, then drag. A small outline of the window follows the dot and the desktop under it gets a coloured frame.
  Let go over that desktop and the window moves there, onto the workspace it shows. Nothing is maximized and the
  selected desktop stays the same. Let go anywhere else (its own desktop, a pill, empty space) and nothing moves. On a
  desktop other than the selected one you can also just start dragging. On the selected desktop, moving before you've
  held still drags inside the window as usual. A quick click still maximizes. Pinned windows can't be moved this way.
- **The mouse stays on its desktop.** It only ever works the selected desktop. In the overview, moving the dot over
  another desktop does nothing until you click it: that click (or Ctrl+Alt+Left / Right) is how you switch there.
- **Push the mouse past an edge to switch** (new, not yet tried in the glasses): maximized, keep pushing the mouse past
  an edge of the desktop and the view switches to the desktop placed that way round you, as Ctrl+Alt+Left / Right do:
  straight to it, with no trip through the overview, and it comes in front of you (follow) or stays locked to your view.
  It goes by where the desktops are placed, so pushing up reaches a desktop you put
  above, and a desktop off at an angle is reached from the side it is nearest. The mouse comes in at the matching edge
  of the new desktop. If no desktop is placed that way, the mouse just stops at the edge. At an edge with a desktop
  beyond it the mouse bounces back a few pixels each time it touches, so a quick touch (to reach a bar at the top, say)
  doesn't switch. Not in typing mode or during a VR video.

If your mouse is on your normal monitor when you press Ctrl+Alt+Down, it jumps into the middle of the selected desktop.
Maximizing (Ctrl+Alt+Up) gives the mouse back as normal. The dot, pills and bracket are off in typing mode (Ctrl+Alt+T)
and while a video plays, and they hide after 30 seconds without moving the mouse (move it to bring them back). An open
pill stays until you close it or until 30 seconds pass without input.

Resizing changes how big a desktop looks, not its resolution. To change resolution or sharpness, stop the session and
edit the desktop under **Startup options** in the panel.

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
  the arrows change it and R puts it back flat in its slot.

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
your next videos, per pair of glasses. There are two settings. **Texture filter** is *Catmull-Rom* (the default, crisp), *Sharp*
(crisper, with a little ringing), *Bilinear* (softer) or *Nearest pixels* (hard pixel edges). **Deband** is *Off* (the default) or *On*; it smooths the stepped bands you can see in skies and gradients.

Videos with copy protection, or links that need you to be logged in, won't play. The panel or a notification will tell
you why. See [docs/vr-video.md](docs/vr-video.md) and [docs/web-video.md](docs/web-video.md) for formats and limits.

## Stopping, recovering and troubleshooting

**Stop.** Press **Stop** in the panel. Every window on the glasses desktops moves back to your normal monitor, and your
display goes back to how it was.

**Recover.** If a session crashed or the computer lost track of it, the panel shows **Recover**. Press it to clean up
and put your windows and display back. If the panel says "Needs CLI recovery", see
[docs/technical.md](docs/technical.md#cli-recovery).

**Common problems:**

- **"No VITURE glasses detected."** Check the cable and plug the glasses in again.
- **Starting takes a long time.** Head tracking is calibrating. Keep the glasses still for up to two minutes.
- **Start refuses because another tracking driver is running.** Close the other XRLinuxDriver first.
- **A key does nothing.** The keys only work during a session. The arrows, Tab, R and Esc only act on a pill while
  one is open, and most desktop keys do nothing while a video plays.
- **Keys or the cursor act strangely after a crash.** Press the key once more, or click once. That clears what the
  crashed session left behind.
- **The panel looks out of date after an update.** Run `omarchy-restart-shell`.
- **It stopped working after a Hyprland update.** Rebuild and run the install step again with no session running.

**About ghost images on the glasses screens.** The glasses use tiny OLED screens. Bright text that stays still for a
long time can leave a faint ghost image that fades after a while. To prevent that, the help card, the open placement
pill and the video controls are dimmed, and they close by themselves after 30 seconds without input. The overview's
dot, glass pills and corner bracket are dim and hide after 30 seconds without mouse movement. Don't leave a bright
video paused for a long time either.

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
your normal monitor, as Stop would move them). The keys U, N and Delete do the same while the pill is open. The next
session starts with the same desktops; Ctrl+Alt+R goes back to three. Above those buttons, **Scale** sets that desktop's scale: click the box and pick
a step from 100% to 300% (steps that would not give whole pixels are greyed out). It applies at once and the next
session keeps it (new, not yet tried in the glasses). Other resolutions are set under **Startup options** in the panel
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
