# F1–F3 roomier menu studies

Planning only. HTML reconstructions, not native renderer screenshots. No production code changed.

Recommend F1: it leaves the selected desktop unobscured and has stable category labels. F2 offers a tall reading surface beside the desktop, at the cost of covering a neighbor. F3 saves navigation width but requires more careful label reading. Each keeps the existing drag pill separate from Options and Connected Tail 3D. The pill remains visually slender; its existing hit geometry is not redefined here. New controls use 44px targets; detail labels are 18px JetBrains Mono. Text angular size and OLED contrast need real glasses validation after selection.

The panel freezes at its opening pose and sits on a flat zero-disparity surface. No animated following. Overview and maximized examples are proposed placements, not claims that native maximized menu access already exists. Production should clamp the surface into view and explicitly recenter it, rather than tie it to continuous head motion. The simulated dot indicates pointer position; hover highlights actual controls. Escape and close return focus to Options; Tab and native range arrows work. Transitions use a simple 200ms fade/translation, no bounce.

## Source reconciliation

Read glass.hpp/glass.cpp, free_pointer.py/view.py, HUD renderer, controller server and QML RunningEditor, PlacementEditor, VideoControls, StabilizationControls, Model.js. Existing native menus are overview-only, with placement surface frozen during slider drags. Material tonal capsules, M3 surface/outline hierarchy and JetBrains Mono informed these studies.

Reconciled every inventory.json identifier into full-control-homes.json; no sibling inventory file modified. Runtime category catalogs in the demo expose the complete reconciled list, including keyboard/gesture operations. Startup-only rows stay Before Start; recovery-only rows stay external and have Session help. Exact commands and values in primary controls are illustrative; catalog labels preserve the inventory vocabulary. Save/restore profile buttons are design proposals; current persistent per-model behavior must be retained. Do not interpret mock preset labels or slider ranges as implementation specifications.

Global controls have Workspace / All monitors scope. Placement is fixed to Selected monitor · XR-2. 3D permits global strength or a selected strength override; presets and convergence stay global. Session status remains read-only. Catalog scope labels are authoritative for mixed command categories.

Safety recommendation: retain a minimal external session/status and emergency Stop/Recover escape hatch when picture or tracking fails. Simplify the running desktop app only after XR controls are implemented and verified. Before-start setup remains external. This phase removes nothing.

Validation: browser click tests for all three compositions, categories, scope selection, strength adjustment, scroll, Escape and reopening. Nine full-size views exported for overview, maximized, closed/hover. Native live preview was not used because this work does not access XR, controllers or glasses; no suitable offline full-menu renderer exists for these new compositions.
