# Selected B1 + C1 · implementation handoff

Status: Cody selected B1 compact gear capsule + C1 category tabs. Build and acceptance started separately; this document is a preview-stage handoff reference. This branch must not implement or duplicate that build.

## Recorded decision
B1 + C1 is selected. F variants remain reference-only. Carry monitor anchoring/maximum-panel bounds, scope labels, shortcut entry, and recovery wording into the separate build. Validate readable angular size on hardware in a separately authorized session. Browser pixels cannot establish XR comfort.

## Parallel ownership and contracts
1. **Native rendering owner** — spike/include/xr/glass.hpp, spike/src/glass.cpp, HUD/menu rendering. Own panel layout and states only; publish geometry constants and a CPU/offline preview. Preserve Connected Tail 3D and placement pill. Options below monitor beside pill, with a separate non-overlapping hit zone. No menu drag-to-move; placement header remains the drag surface.
2. **Pointer/input owner** — tools/native_spike/free_pointer.py. Consume renderer geometry, implement options hit zones, hover/focus, pointer capture, Escape/back and focus return. Never reinterpret an options press as a pill drag; hold selection stable while a panel is open. Coordinate shared view.py changes through runtime owner.
3. **Runtime/state owner** — tools/native_spike/view.py, ipc payload declarations, controller routing. Own one canonical capabilities/state model. Every command identifies scope (workspace or stable XR monitor ID), expected current session, bounds and busy state. Existing command handlers remain authoritative. Publish a versioned payload schema before renderer and input work begins; synchronize C++/Python validation and geometry contract tests.
4. **Desktop shell owner** — omarchy-plugin/cody.viture-workspace/RunningEditor.qml and related UI only. Reduce running view to status, session failures, Stop and Recover, after parity is proved. Preserve pre-start configuration. Recover must work without a visible XR menu, restore physical fallback and expose failures; retain existing controller safety guards.
5. **Coverage/QA owner** — migration matrix + offline fixtures/tests. Map every inventory ID to exact runtime entry and command, scope, availability, persisted state, confirmation, keyboard path and emergency alternative. Enumerate unsupported rows explicitly rather than hide them. Test overview/maximized, missing monitor, busy/rejected operation, typing/video mode, resolution/scale constraints, destructive confirmations, reconnect, focus and Escape.

## Integration order
Freeze contracts → add geometry + state skeleton → input/render in parallel against fixtures → runtime capability coverage → parity review → simplify desktop running UI → offline verification → Cody review → separately authorized live XR validation/install.

## Acceptance
All inventory controls retained. Global settings visibly Workspace / All monitors; contextual controls name selected monitor. No moving head-locked target, desktop occlusion bounded, panel opens in usable view and stays anchored. Preserve menu state on ordinary hover, dismiss on Escape with focus restoration, no bounce. 3D and pill remain reachable without overlap. Emergency Stop/Recover available externally. Exercise existing glass geometry, pointer, view wiring, IPC, shell tests and source-backed inventory checklist. No production merge or install in this phase.

## Source audit integration constraints
- Generic controller settings reject live desktop geometry; use native DesktopManager live paths for resolution/scale/add/remove.
- Generic video-quality settings are not forwarded live; retain native quality menu routing.
- 3D state must distinguish requested, switching, applied and failed; retained preference can coexist with 2D fallback. Preserve glasses R1 instructions.
- Normal Remove migrates windows to physical fallback; Reset uses different migration semantics. Do not conflate them.
- Native opening today unmaximizes. Keeping a menu in maximized follow/locked is a deliberate new behavior requiring a bounded press-time anchor and independent input eligibility.
- Supervisor owns modal binds, input handoff, delayed save and press-time slider geometry. Desktop clicks must continue handing typing back to applications.
