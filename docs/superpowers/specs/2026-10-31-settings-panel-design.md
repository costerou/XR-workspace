# VITURE Workspace settings panel: design

Date: 2026-10-31. Status: draft for Cody's review.
Parent design: `docs/superpowers/specs/2026-10-31-viture-workspace-design.md` ("Settings/UI" and "Approved visual direction").
Mockups (schematic, not production): `/home/cody/Projects/viture-workspace/.superpowers/brainstorm/1815073-1793494707/content/` — `launcher-flow.html`, `launcher-desktops.html`, `running-editor.html`.

## Intent

Cody wants his own full XR editor as an Omarchy shell plugin, not a third-party one (he tried `afruth.omarchy-xr` "XR Monitor Studio" and didn't like it; it is not a code source, its licence is custom). Today every setting is a Ctrl+Alt keybind during a run, and starting a run means hand-writing a consent file and running the CLI. The panel should make starting, stopping and configuring a session an obvious, ordinary desktop action, without weakening any safety guarantee the native spike already proved.

## Goals

1. A Quickshell panel plugin plus bar widget for the Omarchy shell.
2. A **Start XR session** button when glasses are present, a **Stop** button while running, a **Recover** button when a previous run left resources behind.
3. A start confirmation whose click is the one-run consent (replaces hand-written consent files for panel-started runs).
4. Before starting: per-desktop shape (16:9 / 21:9 / 32:9 / custom), resolution, and scale (100/125/150/175/200%).
5. While running: the controls that already work (prev/next, zoom, recenter, Anchor/Follow/Locked, spacing, cursor follow, pose prediction) plus measured status.
6. Settings saved per glasses model (Beast, Luma Ultra).
7. The editor only ever shows controls that really work. Future native features (per-desktop position, curvature, add/remove beyond three, live resize) appear greyed out and labelled "coming" until they land.

## Non-goals (this milestone)

- Changing resolution/scale of a desktop while a session runs. These are fixed at start; changing them means stop and start.
- More or fewer than three desktops; per-desktop position, size in space, curvature, rotation.
- Eye tracking, input injection, general desktop capture, SDK licence acceptance — all still unauthorised.

## Anti-goals (failure even if it "works")

- Any control in the panel that does nothing, or that looks applied when the native side ignored or altered it.
- A session that dies or strands XR outputs because the Omarchy shell restarted (theme switch, config reload, shell crash).
- Safety logic (consent, ownership, recovery) living in QML where it can't be tested offline.
- Showing a mode label (e.g. "120 Hz") as if it were measured performance.
- Starting anything that touches the glasses without the confirmation click.

## Constraints

- Safety rules from the parent design and handoff stay: intent journaled before mutation; readback before confirmation; handle/epoch-addressed ownership, never name-only; recovery preserves ambiguous resources; windows return to the fallback output on stop or failure.
- The Omarchy shell is one long-lived Quickshell process (`/usr/share/omarchy/shell`) with a manifest-based plugin system (`~/.config/omarchy/plugins/<id>/manifest.json`, kinds `panel`, `bar-widget`). It restarts on some reloads, so **no run may be a child of it**.
- Capture remains limited to the run's own XR-n outputs, in memory.
- Tracking remains XRLinuxDriver with network blocked.
- Parallel Claude sessions edit `feat/native-spike`; stage only named files, never revert others' work.
- Tests ≠ VM trials ≠ physical acceptance; each claim names which.

## Approaches considered

1. **Background controller service + thin panel (chosen).** A user-level systemd service in Python, built on the existing `tools/native_spike` code, owns detection, settings, consent, and run lifecycle. Panel and bar widget are clients over one local socket. Safety logic stays in tested Python; costs one more installed component.
2. **Panel launches runs directly** via `systemd-run --user` and reads files/the run's control socket. No new service, but consent, detection and crash handling split across QML, shell glue and Python — hardest to test and puts safety in the UI. Rejected.
3. **Run inside the shell process.** Dropped: a shell restart would kill the run and strand outputs.

Cody picked 1.

## Design

### Components

- **Controller** (`tools/xr_controller/` or similar, Python, stdlib-first like the rest): a `systemd --user` service. Survives shell restarts. One run at a time.
- **Panel plugin** `cody.viture-workspace` (QML): `kinds: ["panel", "bar-widget"]`, source kept in this repo, installed into `~/.config/omarchy/plugins/` (symlink or copy is a builder choice). Follows the Omarchy theme like other shell plugins.
- **Native changes** to ownership plugin, Python planner/verifier, and renderer for per-desktop geometry (below).

### Controller responsibilities

- **Glasses detection:** watch USB/DRM through the existing read-only `xr_probe` primitives (exact VITURE vendor id `35ca`; never infer a model from product id alone beyond what the probe already classifies). Report model and connector, or "none".
- **State machine:** `no-glasses` → `idle` → `confirming` → `starting` → `running` → `stopping` → `idle`; plus `recovery-needed` (a canonical journal under `$XDG_RUNTIME_DIR/xr-workspace-spike/<run>/` is incomplete) and `failed` (with the blocked reason shown verbatim).
- **Consent:** on the panel's confirm, write a fresh run id and a private (0600, single-link) consent record whose scope exactly matches what the confirmation listed, then invoke the existing managed run path. The record is created only in response to that confirmation message; the controller never starts a run on its own (not on plug-in, not on login, not on restart of the controller).
- **Lifecycle steps the operator previously did by hand:** load the ownership plugin into Hyprland before the run and unload it after teardown; for the Luma, set DP-2 to 1920×1200@120 session-only (`hyprctl eval hl.monitor(...)`, never a saved config edit) when that box is ticked. Both are listed on the confirmation. Plugin load/unload reloads config (DP-2 returns to 1080@60), so ordering matters — builder to sequence and verify by readback.
- **Stop:** normal managed teardown; windows return to the fallback; plugin unloaded; state back to `idle`.
- **Recover:** the existing `recover_managed` path with the saved run's consent scope.
- **Settings:** owns the saved settings (below) and forwards live view ops to the running supervisor's existing control socket.
- **Status:** forwards measured figures from the run. Today the renderer reports counts at the end of a run; a periodic status message from renderer → supervisor → controller is new work. Only measured values are shown; anything not measured is omitted, not estimated.

### Confirmation (consent) contents

Required to start: presenting on the detected glasses/output; fallback output windows return to; creating the XR desktops and capturing only those, in memory; loading the ownership plugin for this session. Optional (can be unticked): head tracking via XRLinuxDriver with network blocked; the Luma 120 Hz session-only mode switch (for the Beast, which already runs 120 Hz natively, this line reports the mode and switches nothing). The confirmation also lists each desktop's resolution and scale.

### Panel ↔ controller protocol

One Unix socket in `$XDG_RUNTIME_DIR`, newline-delimited JSON, schema-versioned, like the existing control socket. Panel sends requests (`status`, `get_settings`, `set_settings`, `start` with the confirmed scope, `stop`, `recover`, `view_op`); controller pushes state changes so the panel and bar widget update without polling. Peer credentials checked (same uid). The panel builds its live controls from a capability list the controller reports, so new supervisor toggles (pose prediction just appeared this way) show up without panel changes.

### Panel UI (Cody approved the mockups)

Launcher-then-editor layout (option B).

- **Bar widget:** "XR" — grey idle/none, theme accent running, amber recovery-needed. Click opens the panel.
- **No glasses:** message, no Start.
- **Idle:** detected glasses + output, Start button, one-line summary of startup settings, and a desktop preview (real proportions). Clicking a desktop opens its settings: shape dropdown, resolution menu grouped by shape with a custom width × height entry, scale buttons. Startup view: mode, spacing, cursor follow, tracking.
- **Recovery needed:** Recover button replaces Start.
- **Running:** status line (glasses, output, live mode, uptime), Stop; preview with the in-view desktop highlighted, click to bring into view; prev/next/zoom/recenter; mode, spacing, cursor follow, pose prediction; measured stats; per-desktop geometry shown read-only; greyed "coming" rows.
- Ctrl+Alt keybinds keep working; panel and keybinds stay in sync through the supervisor's state.

### Per-desktop geometry (native work)

Resolution presets: 16:9 — 1920×1080, 2560×1440; 21:9 — 2560×1080, 3440×1440; 32:9 — 3840×1080, 5120×1440; custom width × height (builder sets bounds). Scales: 1.0, 1.25, 1.5, 1.75, 2.0.

Today 1920×1080 at scale 1 is hardcoded in: `spike/ownership/plugin.cpp` (`configure` rule and `verified` check), `tools/native_spike/hyprland.py` (`plan_outputs` fixed 1920 spacing, `verify_output`), `tools/native_spike/ownership.py` spec checks, and the renderer layout (`spike/src/wayland.cpp` sizes all tiles from the first capture). Each must take per-desktop width/height/scale from the journaled spec:

- Broker creates/verifies the exact requested mode and scale; exact readback still required. The config-reload re-apply fix must restore each desktop's own rule.
- Planner places desktops by their logical widths (pixel width / scale), not a fixed 1920.
- Renderer lays out mixed-size tiles at their true proportions in all view modes (overview, zoom, anchor/follow/locked, spacing).
- **Scale validity:** Hyprland silently adjusts scales that don't produce a clean logical size. The panel greys out scales the controller reports as not exact for that resolution (e.g. 175% at 3840×1080), with a one-line reason; the controller re-validates on start and the broker still checks exact readback.
- **Performance:** large layouts (5120×1440 is ~3.5× a 1080p desktop) are unmeasured at 120 Hz; the panel shows a warning, and no 120 Hz claim is made until measured on the glasses.

### Saved settings

Extend `$XDG_CONFIG_HOME/viture-workspace/layout.json` (or a sibling file) to hold, per glasses model: the three desktops' resolution and scale, plus the existing mode/spacing/cursor follow/prediction and the tracking/120 Hz defaults. Schema-versioned, atomic writes, each field validated independently with fallback to defaults (as `load_settings` does now). Existing files keep loading.

## Decisions settled after builder review (Claude's calls, for Cody to check)

1. **Model detection.** The USB product string the device reports, exact match against the recorded evidence: `VITURE Beast XR Glasses` (product id 1201) → Beast; `VITURE Luma Ultra XR GLASSES` (1104) → Luma Ultra (`docs/feasibility/beast-connected`, `luma-ultra` host reports on master). Product string *and* id must both match; the `VITURE Microphone` (1102) interface is ignored. Anything else from vendor `35ca` is "Unknown VITURE": Start is still offered with generic saved settings, and the 120 Hz line only reports (no mode switch). The presentation output is the DRM connector whose EDID model matches the device (`VITURE Beast`, or `VITURE` for the Luma); if that pairing is not unambiguous, Start is blocked with the reason shown. A model is never inferred from product id alone.
2. **Run process lifetime.** Each run gets its own `systemd-run --user` transient unit, not a child of the controller, so restarting or upgrading the controller does not end a run. On start-up the controller rediscovers a live run (unit alive and its control socket answering for the journaled run id) and shows `running`; a journal with no live unit shows `recovery-needed`.
3. **Recover.** Needs its own confirmation click listing what it will do: move windows to the fallback, remove the listed XR outputs, unload the plugin. Consent records the controller writes are kept in a private controller state dir (0700) under `$XDG_STATE_HOME`, so they outlive controller restarts. The panel offers Recover only for runs whose consent it wrote. For other journals (CLI runs) it shows a read-only "needs CLI recovery" notice with the journal path, and never rebuilds a scope.
4. **Consent schema.** Panel runs use a new gate (`P2-panel`) with its own validator and exact fields for the optional lines (`tracking`, `mode_switch` as the exact session-only mode, or `false`), the plugin load, and the per-desktop geometry. The existing CLI `P2` validator and its `mode_switch: false` rule are unchanged. Tests assert that the written record is byte-for-byte derived from the confirmed scope and that each gate refuses the other's records.
5. **Plugin and renderer binaries.** The controller never loads from the worktree build dir, because parallel sessions rebuild it. An explicit install step copies the ownership `.so` and renderer into a versioned install dir and records their sha256. The controller re-checks both hashes right before load/launch and puts them in the run record. If any ownership plugin is already loaded, Start is blocked ("ownership plugin already loaded; unload it or recover first"). The controller does not reuse or unload a plugin it didn't load.
6. **Fallback output.** Auto-picked when exactly one other enabled, DPMS-on, non-mirrored physical output exists (today the Samsung Odyssey G93SD). With several, the idle screen shows a selector and saves the choice per model. With none, Start is blocked. The confirmation names the fallback either way.

## Verification expectations

- Offline tests: controller state machine, consent record matches confirmation exactly, no start without confirm, cross-scope misuse refused, protocol validation, settings migration, scale-validity table, planner layout for mixed sizes, renderer layout for mixed sizes.
- VM: mixed-geometry outputs created and verified by readback, reload survival with mixed rules, full start/stop/recover through the controller, shell restart mid-run does not affect the run.
- Physical (each needs Cody's go-ahead per session): panel-driven start/stop/recover on the Luma and the Beast; measured presentation rate for at least one mixed/ultrawide layout.

## Left to the builder

Controller package name and module layout; socket path and message names; how the plugin is installed into `~/.config/omarchy/plugins`; QML component structure and styling details; custom-resolution bounds; status update cadence; whether settings move to a new file; test tooling. Do not change the approved behaviour above to suit a library without asking.
