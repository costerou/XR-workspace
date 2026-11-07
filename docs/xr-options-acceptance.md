# XR options acceptance contract

Review baseline: `origin/master ff7b26a`. Selected corrected B1 gear capsule + C1 category tabs; implementation authorized, installation excluded. This is a contract review, not integrated acceptance. No live XR/device/service/compositor access. Shared handoff: `/home/cody/.hermes/profiles/coder/previews/xr-options/BUILD-ACCEPTANCE.md`.

## Concrete acceptance gates

- [ ] A01 Composition: B1 below each monitor beside existing drag pill; C1 bounded category tabs with explicit workspace/selected XR-n scope on every control. Preserve Connected Tail main and all-monitor tail in existing positions and unchanged actions. No invented profiles, yaw/pitch, or per-monitor stabilization.
- [ ] A02 Safety: external physical-display session/status + Stop always usable without XR rendering/tracking/input. Recover only in recovery-needed state through existing begin/confirm flow and rule disclosure; stale scope rejected. Missing fallback blocks Start or truthfully blocks recovery. Controller/plugin restart never substitutes for evacuation. Fake renderer death, capture/tracking loss and disconnected controller; verify Stop/Recover and DPMS wake via existing owners.
- [ ] A03 Input: native focus order, visible focus, activation, arrows/Shift steps, dropdown selection and Tab/Shift+Tab documented and tested; existing placement Tab changes monitor. App-content click passes through and releases plain modal keys; panel click reacquires. Test R/U/N/Delete/arrows/Tab/Escape typing with no command leak. Bind capture suspends menu; conflicts, reserved Escape, cancel, reset-one/reset-all confirmation, watchdog/triple-Escape and teardown preserved. No global binding without conflict check.
- [ ] A04 Reachability: every live route in ledger available after Start inside XR through C1 or a labelled existing child panel/gesture. Local-file browsing, URL/page link, clipboard, playback/seek/format/projection/stereo and quality filter/deband must have actual XR-safe paths. No desktop editor deletion before equivalent paths pass. Startup-only options stay before Start. Internal plumbing and automatic persistence do not need invented buttons.
- [ ] A05 Scroll/Escape: bounded clip and matching render/hit scroll offsets, wheel captured only over menu, keyboard access to last row and focus kept visible; or explicit paging. Escape cancels innermost capture/picker, then options, never two layers or app leakage; video retains exit semantics. Outside app click keeps established pass-through handoff. Idle close must not interrupt drag/capture. Close/stop/error/output loss/restart releases pointer and modal binds.
- [ ] A06 Geometry: shared native/Python rectangles and projection; verify drawn AND expanded hit regions against gear, pill, top rotation/level, Connected Tail, borders/corners and app content. Half-open boundaries, inert gutter, front-order/occlusion; boundary +/- epsilon tests. Narrow/tall/ultrawide tiles, distance extrema, curvature 0/120, positive/negative roll, moved/resized and overlapping monitors; projection round trips. Press latches surface/target until release: slight gear motion never drags pill, pill drag never opens gear. Slider drag uses press-time surface. Removed XR-n cancels/rebinds safely; selection changes cannot retarget captured edit.
- [ ] A07 Maximize addition (approved): button immediately LEFT of TOP tilt/rotation/level pill on each monitor. Press maximizes that XR-n using existing action/state, subsequent activation restores overview; icon/hint reflects actual state. Test another monitor selected, repeated toggles, stale/removed target, no accidental drag and no drawn/expanded overlap with top pill/Connected Tail at all A06 transforms. Preserve follow/locked semantics, recenter, stored placements and overview anchor; do not invent saved mode preference. Verify native reachability in both states. Options opening from maximized may use corrected overview transition; closing does not silently maximize.
- [ ] A08 State: live edits use existing owner and applied readback, not controller forwarded ACK; show pending/busy/disabled/error/unsupported honestly. Test global stabilization 0/25/100 separately from prediction, background/last/none and disabled brightness; global vs XR-n 3D enable/strength, exceptions and new-monitor inheritance. Strength wire 0..200 converts once to displayed percent. Follow/locked starts follow per run. Placement persists after 500ms idle and flush on close; reload automatic per-model setup/roll, corrupt fallback, no extra authority or Apply/Cancel fiction.
- [ ] A09 Outputs: 1..4 limit, compatible resolution/scale/ultrawide, gapped XR-n identity, add/remove/reset confirmations and busy gating. Broker failure rolls back and resumes guard; removal evacuates via owner, reset has distinct migration semantics. Test bumping neighbors, maximal placement deferral, output/capture disappearance, renderer restart resync without destructive replay; preserve window memory and physical fallback safety.
- [ ] A10 SBS/video: engine-before-SBS startup and renderer-flat-before-off; no consent/no engine/unsupported/error/retry and expected output disappearance. Fake R1 wait up to 45s and mode detection, desired-on/applied-2D distinction, physical return instruction, video stand-down/resume. Preserve bounded retry-backoff and stabilization. External escape survives invisible switching instructions. No hardware test required.
- [ ] A11 Wire: real native decoder tests for malformed/truncated/duplicate/oversized/wrong-type/range/unknown/stale identity frames, empty desktop list, invalid focus/picker/scroll, NaN and units where applicable. Invalid options state rejected without session death or state mutation; valid legacy placement frames retained. Bounded separately typed options snapshot; intents reach validated supervisor workers, not shell/renderer mutation. Do not confuse controller schema/request_id envelope with run/session private frames.
- [ ] A12 Evidence/accessibility: native themed normal/hover/focus/pressed/open/disabled/error states; readable system-font/ASCII fallback, sufficient contrast and actionable hints, keyboard-only access to all tabs/rows/confirmations at native canvas size without clipping. Genuine offline renderer PNGs for overview, maximized transition, top maximize and deepest/last content plus deterministic hit/focus tests. Record command, revision, fixture and capture provenance. Browser PNG/MP4 is design evidence only; px size alone proves no angular comfort. State remaining readability limitations without demanding live glasses testing.

## Evidence and verdict rules

For each new flow supply an actual failing regression before its fix and passing output afterward, including malformed frames and input/hit failures. Run relevant existing native/Python suites for controller, view/menu, pointer/glass geometry, IPC, profiles/desktops, privacy, immersive/SBS, video/quality and keybinds. Offline fixtures/fakes must never invoke installed services or real outputs. Retain logs and native captures in private review artifacts; identify head SHA and test counts. Missing evidence is NOT VERIFIED; reachable label alone is not PASS. No fake native/browser equivalence.

Current source anchors: `view.py:USER_OPS`, `desktop_press`, `panel_keys_on`, `pill_click`, `desktop_action`; `free_pointer.py:hit`, `on_pill`, `glass_to_desktop`; `glass.hpp`/`glass.cpp`; `ipc.cpp:placement_menu`; `state.py:Controller._request`; `profiles.py`; `immersive.py`/`sbs.py`; QML RunningEditor/Launcher/VideoControls/StabilizationControls. UX-AUDIT B1–B10 supplies the source-risk rationale. This branch reviews baseline only; lead requests integrated review after code lands.

Run `python tests/xr_options_acceptance/validate.py` for contract completeness and source-reference existence. It does not test implementation. `--inventory PATH` compares a revised inventory exactly; source-line hashes are provenance, not a requirement to freeze production code.

## Exhaustive route ledger (116 inventory rows)

Scope below records inventory origin; menu commands such as add are contextual routes to workspace actions. Follow/locked affects selected maximized presentation per run despite its workspace settings transport. Apply/flat denote existing live setters, not a new draft/apply UX. Each row requires route, scope, gating, applied result/failure and applicable persistence verification under A01–A12. The approved maximize/restore affordance adds A07 coverage over existing zoom_in/zoom_out, without inventing a 117th inventory feature.

| ID | Existing route / owner contract | Scope | Lifecycle | In-XR destination or exception |
|---|---|---|---|---|
| zoom_in | zoom_in | selected monitor | live | View & Input |
| zoom_out | zoom_out | workspace | live | View & Input |
| prev | prev | selected monitor | live | View & Input |
| next | next | selected monitor | live | View & Input |
| spacing_down | spacing_down | workspace | live | Workspace / All monitors |
| spacing_up | spacing_up | workspace | live | Workspace / All monitors |
| recenter | recenter | workspace | live | View & Input |
| layout_reset | layout_reset | workspace | live | Workspace / All monitors |
| mode_next | mode_next | workspace | live | View & Input |
| prediction | prediction | workspace | live | Workspace / All monitors |
| typing_toggle | typing_toggle | workspace | live | View & Input |
| privacy_display | privacy_display | workspace | live | View & Input |
| environment_toggle | environment_toggle | workspace | live | Workspace / All monitors |
| immersive_toggle | immersive_toggle | workspace | live | Workspace / All monitors |
| menu_toggle | menu_toggle | workspace | live | View & Input |
| video_open | video_open | workspace | live | Video |
| video_paste | video_paste | workspace | live | Video |
| playback_menu_toggle | playback_menu_toggle | workspace | live | Video |
| help | help | workspace | live | Help & Session |
| binds_toggle | binds_toggle | workspace | live | View & Input |
| help_prev | help_prev | workspace | live | Help & Session |
| help_next | help_next | workspace | live | Help & Session |
| send_glasses | send_glasses | selected monitor | live | View & Input |
| send_glasses_1 | send_glasses_1 | selected monitor | live | View & Input |
| send_glasses_2 | send_glasses_2 | selected monitor | live | View & Input |
| send_glasses_3 | send_glasses_3 | selected monitor | live | View & Input |
| send_glasses_4 | send_glasses_4 | selected monitor | live | View & Input |
| send_back | send_back | workspace | live | View & Input |
| menu_field_prev | menu_field_prev | selected monitor | live | Selected monitor · Placement |
| menu_field_next | menu_field_next | selected monitor | live | Selected monitor · Placement |
| menu_dec | menu_dec | selected monitor | live | Selected monitor · Placement |
| menu_inc | menu_inc | selected monitor | live | Selected monitor · Placement |
| menu_dec_big | menu_dec_big | selected monitor | live | Selected monitor · Placement |
| menu_inc_big | menu_inc_big | selected monitor | live | Selected monitor · Placement |
| menu_desktop_next | menu_desktop_next | selected monitor | live | Selected monitor · Placement |
| menu_desktop_prev | menu_desktop_prev | selected monitor | live | Selected monitor · Placement |
| menu_reset | menu_reset | selected monitor | live | Selected monitor · Placement |
| menu_back | menu_back | selected monitor | live | Selected monitor · Placement |
| menu_ultrawide | menu_ultrawide | selected monitor | live | Selected monitor · Placement |
| menu_add | menu_add | selected monitor | live | Selected monitor · Placement |
| menu_delete | menu_delete | selected monitor | live | Selected monitor · Placement |
| video_pause | video_pause | workspace | live | Video |
| video_seek_back | video_seek_back | workspace | live | Video |
| video_seek_fwd | video_seek_fwd | workspace | live | Video |
| video_seek_back_big | video_seek_back_big | workspace | live | Video |
| video_seek_fwd_big | video_seek_fwd_big | workspace | live | Video |
| video_format | video_format | workspace | live | Video |
| video_up | video_up | workspace | live | Video |
| video_down | video_down | workspace | live | Video |
| video_exit | video_exit | workspace | live | Video |
| video_quality | video_quality | workspace | live | Video |
| video_controls | view_op(video_controls) | workspace | live | Video |
| stabilization | stabilization {strength:0..100} | workspace | live | Workspace / All monitors · Display |
| immersive_all | immersive {global,desktops,preset,strength,strengths,convergence} | workspace | live | Workspace / All monitors · 3D |
| immersive_monitor | immersive.desktops[XR-n] | selected monitor | live | Selected monitor · 3D |
| immersive_preset | immersive.preset + convergence | workspace | live | Workspace / All monitors · 3D |
| immersive_strength_presets | immersive.strength=50/100/150 | workspace | live | Workspace / All monitors · 3D |
| immersive_strength | immersive.strength=0..200 | workspace | live | Workspace / All monitors · 3D |
| immersive_monitor_strength | immersive.strengths[XR-n]=0..200 | selected monitor | live | Selected monitor · 3D |
| immersive_plane | immersive.convergence=0..100 | workspace | live | Workspace / All monitors · 3D |
| placement_x | placement.desktops[].x | selected monitor | live | Selected monitor · Placement |
| placement_y | placement.desktops[].y | selected monitor | live | Selected monitor · Placement |
| placement_distance | placement.desktops[].distance | selected monitor | live | Selected monitor · Placement |
| placement_curvature | placement.desktops[].curvature | selected monitor | live | Selected monitor · Placement |
| placement_apply | set_settings {placements} | selected monitor | live | Selected monitor · Placement |
| placement_flat | set_settings {placements} | selected monitor | live | Selected monitor · Placement |
| mode_follow | set_settings {mode:"follow"} | workspace | live | View & Input |
| mode_locked | set_settings {mode:"locked"} | workspace | live | View & Input |
| video_browse | video_open {path} | workspace | live | Video |
| video_play | video_open {path / url / web,format?} | workspace | live | Video |
| video_paste_ui | video_open {url} | workspace | live | Video |
| resolution | desktops RESOLUTION | selected monitor | live | Selected monitor |
| scale | desktops SCALE | selected monitor | live | Selected monitor |
| ultrawide | desktops ULTRAWIDE | selected monitor | live | Selected monitor |
| add_monitor | desktops ADD | workspace | live | Workspace / All monitors |
| remove_monitor | desktops REMOVE | selected monitor | live | Selected monitor |
| background | environment {background,last,brightness} | workspace | live | Workspace / All monitors |
| background_brightness | environment.brightness | workspace | live | Workspace / All monitors |
| roll_ccw | roll {desktop,side} | selected monitor | live | Selected monitor · Placement |
| roll_level | roll {desktop,side} | selected monitor | live | Selected monitor · Placement |
| roll_cw | roll {desktop,side} | selected monitor | live | Selected monitor · Placement |
| drag_move | placement + placement_menu.surface | selected monitor | live | Selected monitor · Placement |
| drag_resize | placement distance | selected monitor | live | Selected monitor · Placement |
| quality_filter | video action=quality filter | workspace | live | Video · Quality |
| quality_deband | video action=quality deband | workspace | live | Video · Quality |
| bind_change | binds_key {mods,code}; renderer binds rows | workspace | live | View & Input · Key binds |
| bind_reset | binds_key {mods,code}; renderer binds rows | workspace | live | View & Input · Key binds |
| bind_reset_all | binds_key {mods,code}; renderer binds rows | workspace | live | View & Input · Key binds |
| bind_cancel | binds_key {mods,code}; renderer binds rows | workspace | live | View & Input · Key binds |
| bind_navigation | binds_key {mods,code}; renderer binds rows | workspace | live | View & Input · Key binds |
| fallback | set_settings {fallback} | workspace | startup-only | Before Start |
| tracking | set_settings {tracking} | workspace | startup-only | Before Start |
| tracking_driver | set_settings {tracking_driver} | workspace | startup-only | Before Start |
| mode_switch | set_settings {mode_switch} | workspace | startup-only | Before Start |
| startup_desktops | set_settings {desktops} | per-monitor | startup-only | Before Start |
| stop | stop | workspace | live | Help & Session; retain external safety/status |
| recover | recover | workspace | recovery-only | Help & Session; retain external safety/status |
| start | start | workspace | startup-only | Help & Session |
| confirm_start | confirm_start | workspace | startup-only | Help & Session |
| confirm_recover | confirm_recover | workspace | recovery-only | Help & Session; retain external safety/status |
| cancel | cancel | workspace | confirmation-only | Help & Session |
| status | status / state event | workspace | live | Help & Session; retain external safety/status |
| controller_restart | systemctl user restart controller | workspace | live | External shell fault/dismissal exception |
| close_external | dismiss() | workspace | live | External shell fault/dismissal exception |
| immersive_connected_tail | immersive_click / immersive_tail_click | selected monitor + workspace | live | Selected monitor · 3D |
| select_pointer | select_desktop | selected monitor | live | View & Input |
| edge_pan | edge_pan + switch_desktop | selected monitor | live | View & Input |
| background_switch | environment_toggle | workspace | live | Workspace / All monitors |
| profile_remember | profiles schema 2: desktops,placements,rolls; previous_setup | per-glasses model | live | Workspace / All monitors |
| plugin_restart | shell restartPlugin | workspace | live | External shell fault/dismissal exception |
| resize_press | resize_press | selected monitor | live | View & Input |
| resize_release | resize_release | selected monitor | live | View & Input |
| resize_cancel | resize_cancel | selected monitor | live | View & Input |
| super_left_press | super_left_press | selected monitor | live | View & Input |
| super_right_press | super_right_press | selected monitor | live | View & Input |
| super_release | super_release | selected monitor | live | View & Input |
