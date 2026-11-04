# Design integration delivery

Revised comparison gallery integrated and verified for visual selection. Planning/mockups only. Open index.html directly; no server or network dependency. Assets are local snapshots in ignored assets/, with embedded worker HTML for click-through. Rebuild with `python build.py`, then `node check.mjs`, then `python contact.py` from this directory. Build reads the shared preview root and worker-owned worktree exports. Restore sibling exports to that root when reproducing elsewhere. Own source only is committed; sibling originals remain on their own branches.

## Choice
Pairing A **B3 + C2 + F1**, combination 22 (audit preference): labelled Options, grouped drill-in, anchored complete inspector. This follows the audit's lower-risk scope/navigation advice and full worker's unobscured-desktop recommendation. Alternative **B1 + C3 + F3**, combination 9: quieter entry and frequent quick controls, conditional on immutable Workspace scope and corrected field semantics. C2 reduces simultaneous choices but adds navigation. C3 puts frequent edits closer but needs immutable global scope on quick controls. Present both; Cody chooses before production implementation.

## Six-thread collection
| Owner | Files/reports read | Delivery |
|---|---|---|
| Inventory | INVENTORY.md, inventory.json | 116 rows, complete source references; pushed 71bed2c |
| Button | button-demo.html, button-NOTES.md, PNG contact/overview, MP4 | B1–B6 present; revised export pushed 181495a |
| Compact | compact-demo.html, compact-rationale.md, validation/categories, PNG contact/states, MP4 | C1–C3 present; revised export pushed 2b449ce |
| Full | full-demo.html, full-notes.md, full-control-homes.json, 9 state PNGs, contact sheet, MP4 | F1–F3 and 116 identifier homes present; revised export pushed; see snapshot hashes and full owner report |
| Audit | UX-AUDIT.md and browser evidence narrative | Feasibility, source/control, input/anchor and safety findings present; pushed 4a179d7 |
| Integration | index.html, contact-sheet.png, browser-check.json, IMPLEMENTATION-PLAN.md | 54 combinations, embedded real worker prototypes, two pairings, original/proposed running-state comparison, inventory contract table |

No missing HTML/PNG/MP4 deliverable remains among the three design workers. Their exports are HTML reconstructions, not native renderer screenshots. Offline native rendering was considered: existing glass tooling does not implement these new layouts, so no native screenshot is claimed.

## Verification
Local Chromium headless, disabled GPU, file URL, no installed dependencies. Fresh browser-check.json records 15 selection/open/dismiss checks, 54/54 scene-bound checks, all 3 embedded worker prototypes loaded without horizontal overflow at desktop width, actual CDP pointer opening + Escape focus return, no page exceptions, no outer page horizontal overflow at 1440 and 390. Contact sheet combines actual gallery and sibling browser captures; visually inspected. Browser bounds are not proof of native XR angular legibility or transformed hit testing. Sibling full layouts are desktop review canvases; mobile suitability of every internal scene was not claimed.

## Revised audit reconciliation

Supersedes the earlier draft review. Read revised button-NOTES.md, compact-rationale.md, full-notes.md, owner validation JSONs and updated HTML/PNG/MP4 exports. All 116 inventory rows have a compact C1/C2/C3 path and full home/disposition, with exact scope/lifecycle/source preserved in reconciliation.json and gallery contract disclosures. Compact coverage hash matches final inventory. Full runtime catalogs and primary controls remain distinct; seven startup-only and five external recovery/shell-maintenance rows retain external dispositions rather than becoming runtime editors. External shell maintenance is not an additional session configuration authority.

Independent browser checks: 348 compact row/variant visits, full 348 route/disposition checks (312 browser category visits plus 36 external disposition checks), seven compact fidelity checks and eight full fidelity checks pass. Verified actual source presets, signed parameter persistence, global quick-row labels, selected 3D excluding global preset/plane, Remove physical-fallback copy, read-only diagnostics, video filter/deband, and Workspace per-run follow/locked disabled in overview. Split Connected Tail above monitor is reconstructed and explicitly illustrative. Existing drag/Options targets remain separate. No named Save/Restore or speculative capture-backend setting remains in active revised controls. Scope gates and lifecycle exceptions are inspectable. The audit's design-fidelity issues are corrected for this visual comparison; its native architecture gates remain open by design.

## Choice tradeoffs and future boundaries

Audit prefers B3+C2: labelled discovery, bounded group list and Back; it takes more navigation. Initial C3 direction puts frequent controls closer and now labels their immutable Workspace scope; it uses more simultaneous controls. Both remain selectable; no choice is forced. F1 keeps the desktop clear; F3 gives detail space at the cost of narrower category labels. Cody chooses before build.

Native input/focus/scroll and layered Escape, transformed anchors/hit testing, SBS desired/applied/R1/failure flow, XR-safe file picker, child bind editor, asynchronous backend readback, busy/video gates, persistence and emergency recovery are planned contracts, not implemented by these DOM prototypes. Retain external emergency Stop/Recover with state/consent guards and truthful failure. Generic controller geometry/video settings are not the live native authority. No production code, controller, live XR, services, installation, merge or master change performed. See IMPLEMENTATION-PLAN.md for ownership after Cody chooses.
