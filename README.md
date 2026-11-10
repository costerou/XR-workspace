<p align="center"><img src="assets/logo.svg" width="330" alt="XR Workspace logo"></p>

# XR Workspace

XR Workspace turns VITURE glasses into a multi-monitor desk on [Omarchy](https://omarchy.org) (Hyprland). Put the
glasses on and up to four extra desktops float in front of you. They stay put when you turn your head, and you use them
with your normal keyboard and mouse.

> **Status: early and experimental.** Daily use so far is on the **Luma Ultra**. The **Beast** has only passed display
> tests, and **Pro 2** support is new and lightly tested. Many newer features have passed offline tests but have not been
> tried in the glasses yet. See [what has been tested](docs/technical.md#status).

## Requirements

- Omarchy (Hyprland), with the glasses plugged in over USB-C.
- VITURE Luma Ultra, Beast or Pro 2 glasses.
- XRLinuxDriver (`xr-driver-git`) installed. XR Workspace uses its own head-tracking driver for the Luma Ultra and Pro
  2, and XRLinuxDriver for other models. See [own-drivers-default.md](docs/own-drivers-default.md).
- Build tools: CMake 3.25+, a C++20 compiler, Python 3.10+, and the development files for Wayland, EGL/GLES, GBM,
  libdrm, freetype2, fontconfig, nlohmann-json and Hyprland. On Omarchy:

  ```sh
  sudo pacman -S --needed cmake gcc nlohmann-json wayland wayland-protocols mesa libdrm freetype2 fontconfig
  ```

- Optional: `mpv` for VR video, `yt-dlp` and `ffmpeg` for web videos, `zenity` for the file picker.

## Install

Run from the repository folder:

```sh
# 1. Build
cmake -S spike -B build/release -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/release -j

# 2. Install the build and the background service, then start the service
python -m tools.xr_controller install --build build/release
systemctl --user start viture-workspace-controller.service

# 3. Add the panel to Omarchy
ln -s "$PWD/omarchy-plugin/cody.viture-workspace" ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins

# 4. Optional: app launcher entry and icon
install -Dm644 assets/icon.svg ~/.local/share/icons/hicolor/scalable/apps/viture-workspace.svg
install -Dm644 packaging/viture-workspace.desktop ~/.local/share/applications/
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

The service never starts anything in the glasses on its own. It waits for you to press Start.

**After a Hyprland update**, rebuild and run step 2 again with no session running. The Hyprland plugin must match
the installed Hyprland version.

## Your first session

1. Plug in the glasses and open **XR Workspace** from the app launcher (or add the **XR** widget to your bar).
   If the panel says "controller not running", click **Start / restart controller**.
2. Press **Start XR**. A box lists what the session will do, such as switching the glasses to 120 Hz. Press **Start**.
3. Keep your head still while head tracking calibrates. This takes a few seconds, or up to two minutes on XRLinuxDriver.

Three desktops appear, **XR-1**, **XR-2** and **XR-3**. Your open windows move onto them, one workspace per desktop.
Move the mouse onto a glasses desktop as you would onto another monitor, and open apps there as usual.

Press **Ctrl+Alt+H** in the glasses at any time for the list of keys.

When you are done, press **Stop** in the panel. Every window moves back to your normal monitor and your display settings
are restored.

## Basic keys

These work only while a session runs, so they never get in the way otherwise.

| Keys | What it does |
| --- | --- |
| Ctrl+Alt+H | Show or hide the help card |
| Ctrl+Alt+Down | Overview: see all desktops, fixed in the room |
| Ctrl+Alt+Up | Maximize the selected desktop: it fills your view and follows your head |
| Ctrl+Alt+Left / Right | Select the previous / next desktop |
| Ctrl+Alt+Space | Recenter the desktops in front of you |
| Ctrl+Alt+T | Typing mode: give all these keys back to your apps, until you press it again |
| Ctrl+Alt+1 … 4 | Send the focused window to XR-1 … XR-4 |
| Ctrl+Alt+B | Send the focused window back to your normal monitor |
| Ctrl+Alt+D | Privacy: turn your normal monitor off / on |
| Ctrl+Alt+K | See and change every key |

In the overview, each desktop has handles around it: drag the **pill** under it to move it, drag a **corner** to resize
it, click the **gear** beside the pill for options. The [user guide](docs/user-guide.md) covers the full key list,
placing desktops, VR video, immersive 3D and more.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| "No VITURE glasses detected" | Check the cable and plug the glasses in again. |
| Head tracking did not start | Press Start again. If it fails twice, unplug the glasses for 10 seconds and replug. |
| Start refuses: another tracking driver is running | Close the other XRLinuxDriver instance first. |
| Start says `monitors.lua line N names XR-1` | Delete that line from `~/.config/hypr/monitors.lua`, run `hyprctl reload`, start again. |
| A key does nothing | Keys only work during a session. Check you are not in typing mode (Ctrl+Alt+T). |
| Keys or cursor act oddly after a crash | Press the key once more or click once. |
| The panel looks out of date after an update | Run `omarchy-restart-shell`. |
| It broke after a Hyprland update | Rebuild and reinstall (see [Install](#install)). |

**Recover.** If a session crashed, the panel shows **Recover**. Press it to put your windows and display back. If the
panel says "Needs CLI recovery", see [technical.md](docs/technical.md#cli-recovery).

**Problem reports.** If something breaks during a session, a notification says a report was saved in
`~/.local/state/viture-workspace/incidents/`. Reports stay on your computer and hold no screen content. Logs:
`journalctl --user -u viture-workspace-controller`.

## Privacy and safety

- Nothing starts without you pressing Start, and each approval covers one session.
- Display changes are temporary and undone at Stop. Your saved monitor settings are not edited.
- Only the XR desktops are captured, only to draw them in the glasses. Nothing is recorded or sent anywhere. The head
  tracking driver runs without network access. The only network use is a web video you choose to play.

## Documentation

- [User guide](docs/user-guide.md): every feature and key, for day-to-day use.
- [Technical reference](docs/technical.md): architecture, test evidence, safety design, running the tests.
- [Docs index](docs/README.md): everything else, including VR video, immersive 3D and Pro 2 notes.

## Repository layout

| Path | Contents |
| --- | --- |
| `spike/` | C++ renderer, Hyprland plugin and Luma Ultra driver ([spike/README.md](spike/README.md)) |
| `tools/xr_controller/` | Background service and installer |
| `tools/native_spike/` | Session logic: desktops, keys, placement, video |
| `omarchy-plugin/` | The Omarchy panel and bar widget (QML) |
| `tools/`, `tests/` | Other tools, diagnostics and test suites |
| `docs/` | Documentation and test evidence |
| `packaging/` | systemd unit, desktop entry, udev rules |
