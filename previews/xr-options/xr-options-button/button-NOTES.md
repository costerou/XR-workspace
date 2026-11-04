# B1–B6 options button mockups

Planning only. No production controls, settings behavior, installation, XR access, or runtime changes.

## Recommendation

1. **B3 Labelled Options**: best discoverability, explicit purpose and generous click target. The extra width is worthwhile for occasional configuration in XR. Keep its 16 px label as a candidate pending real angular readability testing.
2. **B1 Gear capsule**: strongest compact alternative. Familiar affordance and clearly separate from the original placement handle. Tooltip should say “Options for XR-2”; menu header distinguishes Selected monitor from Workspace / All monitors.

B2 sliders communicate adjustment but need learning; B4 split silhouette risks mistaken dragging despite separate targets; B5 circle saves space but ellipsis is vague; B6 fits the Connected Tail family but may suggest the existing tail’s all-monitor meaning. B6 is a single options target, not another global 3D switch.

## Source fidelity and limits

Read glass.hpp/glass.cpp, hud.cpp, free_pointer.py, view.py, xr_controller/state.py, and Omarchy RunningEditor/PlacementEditor/VideoControls/Model sources. Drag pill: clamp(.24 × width,64,160), 14 high, 10 below surface. Preserve original 10 px x/y hit padding and gap-to-monitor behavior. Options targets sit beyond it. Original 3D is ABOVE the monitor beside the roll bar, not beside the lower drag pill. Its source 40/28 segments, 2 gap, 4 inner radius, and level-bar scaling are reconstructed. System monospace resolves to installed JetBrains Mono.

The two contexts use 480×270 and 560×315 tile-pixel surfaces, at 1 CSS px per canvas px, not device or angular pixels. Overview neighbors are clipped context silhouettes. Maximized lower controls are explicitly a proposed addition: current pointer/renderer handles are overview-only. Colors approximate a dark M3 tonal seed; they are not a sampled live Omarchy scheme. HTML reconstruction is explicitly labelled; no claim of native screenshot fidelity. Existing offline test binaries were found, but not used to stand in for the current source renderer.

button-overview.png shows closed contexts; button-contact-sheet.png shows open shells and normal/hover/focus/open swatches for all six. button-motion.mp4 shows B1 pointer hover, opening and Escape dismissal without spring/bounce. Local HTML includes live pointer hover, keyboard focus and Escape restoration; outside clicks dismiss. Shell destinations do not apply settings. Press-time pose pinning and single session menu are intended native behavior; the static HTML does not simulate tracking.

## Runtime coverage carried forward

Selected monitor: position left/right and down/up, distance/size, curvature, roll/level, resolution, scale, maximize, 3D enable/strength, ultrawide and remove. Workspace / All monitors: stabilization; prediction; recenter; overview/next/previous; spacing; maximized follow/locked; remembered setup/profile/reset (confirmation and one-step backup); add monitor; global 3D enable, preset, strength and convergence; background/brightness; typing/input, keybind editor and help; send-window commands; physical display privacy; video file/link/clipboard open, playback pause/seek/format/recenter/exit/show-hide and quality. Automatic window memory is retained. Shell groups intentionally do not implement these controls or supersede the sibling inventory.

All post-start configuration belongs inside XR. Startup-only configuration remains before Start. **Safety recommendation: retain external minimal session/status plus emergency Stop/Recover when picture/tracking fails.** Native implementation should hold menu on the selected surface’s press-time pose and reveal below the desktop with view allowance rather than covering content or chasing the head.

## Verification

Chromium at 1440×1000, deviceScaleFactor 1: six articles, no page JS errors, no horizontal page overflow; each B1–B6 overview trigger opened, Escape closed and returned focus. Browser preview click/Escape also exercised. Full-page screenshots visually inspected; corrected menu scope wrapping and lower-control overlap before final export. Native angular readability, controller aiming and tracking require later XR validation after design selection.

## Audit correction · final inventory reconciliation

Supersedes the earlier source-fidelity wording above: **Connected Tail is an illustrative HTML reconstruction, not an unchanged native rendering**. Its source-derived top placement, scaled 40/28 segments, 2 px separation, 4 px inner corners and three 4.2×9 ticks are reconstructed explicitly. Main means selected XR-2; tail means Workspace / All monitors. Both are inert and neutral-state context. No simulated enable toggle can be mistaken for applied SBS. The 148×26 level bar scales by 120/148 at 480 px width and 140/148 at 560 px width, including its 12 px gap. Maximized top AND lower chrome are proposed additions: source only provides overview handles. The lowest-risk implementation returns to anchored overview to open Options, with no automatic maximize on close; this HTML retains the maximal-view study as an explicitly labelled extra-scope alternative.

B1–B6 IDs and separate drag/Options targets remain. B4's bridge and B6's connector are visual only; the pill including its 10 px padding never becomes the Options target. Native projected hit regions remain unproven. Browser rectangles cannot establish curvature/roll/distance/occlusion correctness or native press-latching. B3 remains preferred; B1 is the compact alternative.

Reconciled against the completed final inventory; button-inventory-reference.json records its hash and all identifiers/scopes/lifecycles without modifying sibling inventory. Profiles means automatic remembered per-glasses setup, not a named-profile selector. Video quality explicitly includes texture filter/deband. Window memory remains automatic. External status must be measured; Stop remains reachable externally, Recover is recovery-state-only and retains confirmation/disclosure. A missing fallback can block recovery. Remove evacuates to physical fallback; layout reset differs and both retain confirmations. Global 3D presets/plane are never per-monitor. Physical R1, switching/wait/error/readback are unsimulated native gates.

Browser Tab/Escape and outside-click dismissal are illustrative only: native placement Tab changes monitor, native desktop press passes through and releases menu keys while retaining placement visibility. Native layered Escape, bind-editor ownership, app typing handoff, idle teardown, readback/persistence, tracking, capture loss and SBS retry remain implementation work. No source/live changes were made.

Audit export validation: all 12 overview/maximized trigger click/Escape paths passed in Chromium; all six overview paths restore trigger focus. No JS errors or horizontal overflow at 1440 px. Measured browser gutter beyond pill padding: minimum 2.40625 px (B4), with no options/legend/menu overlap. Final contact sheet visually inspected after shortening the maximal-view caption to avoid its illustrative level bar. Final clip is 3.000 seconds, 1440×1280, H.264. These are browser evidence only, not native safety validation.
