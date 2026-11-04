# Revised-design acceptance checklist

Status: revised owner exports collected and reviewed. Browser results are in browser-check.json; exact source snapshots are hashed in reconciliation.json. All listed design-fidelity corrections are present in active revised controls or explicitly documented native/gesture/external routes. Catalog presence is not an implementation claim.

| Audit design finding | Required revised evidence |
|---|---|
| Connected Tail fidelity | Split selected-monitor main and all-monitor tail, joined to roll bar above monitor; lower Options beside unchanged drag pill. Verify all three workers. |
| Presets | Actual Desktop / Video / Games values; stabilization Off 0 / Light 25 / Medium 60 / Strong 100; actual filter choices. |
| Placement / strength units | Orbit tenths of degree converted to degrees; curvature degrees, roll degrees, distance layout-plane percent, strength 0–200%, brightness 10–200%; no generic percentage for every field. |
| Scope immutability | Selected 3D exposes monitor enable/override/strength only. Preset/convergence/stabilization/environment remain Workspace. Header must match each displayed control, including C3 quick rows. |
| Speculative controls | No named profile library, Save/Restore, capture-backend choice or fake runtime recovery action. Automatic per-glasses remembering is read-only explanation. |
| Confirmations | Normal Remove explicitly evacuates to physical fallback; Reset has distinct semantics; Stop and recovery state/consent remain separate. |
| Parameter continuity | Adjust value, navigate away/back and reopen; value retained in mock state. Signed/default values and units remain truthful. |
| Catalog coverage | Each final inventory ID has explicit route/disposition, including gestures, modal child editors, startup-only and recovery-only. Presence in a list is not a simulated live edit. |
| Read-only diagnostic state | Label measured status as mock data or unavailable; never editable fake telemetry. |
| View/input | Follow/locked unavailable in overview; maximized access labelled proposed; Escape/focus return; outside-click policy and child-editor routing documented. |
| Spatial stability | Opening-pose anchoring and fit are proposals with native geometry/hit parity gates. No head chasing or bounce. |
| Final evidence | Updated HTML, all requested PNG/contact sheets and MP4, browser test report; manually inspect revised screenshots. |

Native architecture blockers B1–B10 remain future implementation acceptance gates; browser prototype correction cannot claim that native focus, scroll, SBS transitions, crash recovery or XR angular comfort is implemented.
