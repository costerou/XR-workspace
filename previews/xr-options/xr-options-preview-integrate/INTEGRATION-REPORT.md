# Design integration delivery

Combined gallery completed, planning/mockups only. Open index.html directly; no server or network dependency. Assets are local snapshots in ignored assets/, with embedded worker HTML for click-through. Rebuild with `python build.py`, then `node check.mjs`, then `python contact.py` from this directory. Build reads the shared preview root and worker-owned worktree exports. Restore sibling exports to that root when reproducing elsewhere. Own source only is committed; sibling originals remain on their own branches.

## Choice
Recommend **B3 + C2 + F1**, combination 22: labelled Options, grouped drill-in, anchored complete inspector. This follows the audit's lower-risk scope/navigation advice and full worker's unobscured-desktop recommendation. Alternative **B1 + C3 + F3**, combination 9: quieter entry and frequent quick controls, conditional on immutable Workspace scope and corrected field semantics. Cody chooses before production implementation.

## Six-thread collection
| Owner | Files/reports read | Delivery |
|---|---|---|
| Inventory | INVENTORY.md, inventory.json | 116 rows, complete source references; pushed 71bed2c |
| Button | button-demo.html, button-NOTES.md, PNG contact/overview, MP4 | B1–B6 present; pushed 8a0ff43 |
| Compact | compact-demo.html, compact-rationale.md, validation/categories, PNG contact/states, MP4 | C1–C3 present; pushed 13a230c |
| Full | full-demo.html, full-notes.md, full-control-homes.json, 9 state PNGs, contact sheet, MP4 | F1–F3 and 116 identifier homes present; pushed e17e3c7 |
| Audit | UX-AUDIT.md and browser evidence narrative | Feasibility, source/control, input/anchor and safety findings present; pushed 4a179d7 |
| Integration | index.html, contact-sheet.png, browser-check.json, IMPLEMENTATION-PLAN.md | 54 combinations, embedded real worker prototypes, two pairings, original/proposed running-state comparison, inventory contract table |

No missing HTML/PNG/MP4 deliverable remains among the three design workers. Their exports are HTML reconstructions, not native renderer screenshots. Offline native rendering was considered: existing glass tooling does not implement these new layouts, so no native screenshot is claimed.

## Verification
Local Chromium headless, disabled GPU, file URL, no installed dependencies. Fresh browser-check.json records 15 selection/open/dismiss checks, 54/54 scene-bound checks, all 3 embedded worker prototypes loaded without horizontal overflow at desktop width, actual CDP pointer opening + Escape focus return, no page exceptions, no outer page horizontal overflow at 1440 and 390. Contact sheet combines actual gallery and sibling browser captures; visually inspected. Browser bounds are not proof of native XR angular legibility or transformed hit testing. Sibling full layouts are desktop review canvases; mobile suitability of every internal scene was not claimed.

## Parent follow-up before production interaction approval
Audit findings remain intentionally visible. Correct compact presets/stabilization/filter options, physical-fallback Remove wording, speculative startup selector, immutable per-control scope, parameter units/ranges and navigation value retention. Full notes acknowledge illustrative values and speculative Save/Restore; its later version filters selected 3D to strength and adds the inventory catalog, but exact primary-control fidelity still needs re-review. Compact/full simplified 3D context must use actual split Connected Tail above the monitor, or be explicitly treated as illustrative; the combined scene now shows the split above and Options below beside the pill. Do not treat catalog presence as implemented controls.

Keep external emergency Stop/Recover with controller state/consent guards and truthful failure. Generic controller geometry/video settings are not the live native routing authority. Preserve native child editors, capability/busy state, supervised input and delayed persistence. Native maximized access, scroll/focus, flat opening-pose surfaces, resolution constraints and crash recovery require implementation contracts and later tests. See IMPLEMENTATION-PLAN.md for file ownership and order after Cody chooses.

No production code, controller, live XR, services, installation, merge or master change performed.
