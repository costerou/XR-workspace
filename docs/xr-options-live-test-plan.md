# Bounded B1+C1 live validation plan (not executed)

The current XR+tracking session at 60 Hz remains untouched. This document authorizes no installation, service operation, compositor change or glasses test. Cody and the parent must separately approve stopping the current session and installing a verified integration before this plan is run.

## Preconditions and bounds

- Integration release gates and all offline suites are green on the exact pushed commit. Verify native app-bound Restore reachability, asynchronous owner readback/failure/rollback and all116 route acceptance before advertising complete migration.
- Cody controls the start/stop decision. Keep the physical fallback display and external status/Stop/recovery shell visible and reachable throughout. Confirm which physical monitor is the fallback before starting.
- Use the existing 60 Hz/tracking configuration for the first trial. Do not change driver, refresh policy, compositor settings, bindings or calibration procedure as part of this UI trial.
- Maximum five minutes of routine UI testing. Stop immediately for missing video, unexpected view jumps, lost input, tracking failure, capture errors or incorrect window routing. No repeated restart/recovery loop. Parent handles recovery using existing state-gated UI; never replace it with output deletion or controller/plugin restart.
- Use one disposable test window with no unsaved data. Do not remove existing monitors or reset the user's remembered layout during this bounded trial. Destructive failure/rollback cases remain offline unless separately approved.

## Routine sequence

1. **Baseline, at most30s.** Confirm visible image and tracking, the actual selected XR-n, output count and physical fallback. Read measured external session health; do not infer success from sound alone. Capture the before image through an already approved capture route.
2. **Overview chrome, at most45s.** Open B1 gear below the chosen monitor, adjacent to its drag pill. Verify level/roll and Connected Tail3D are intact. Check the new overview Maximize button is immediately left of the top level pill and does not start a roll drag. Cancel if drawn and interactive regions disagree.
3. **Maximize/Restore, at most45s.** Maximize that same monitor. Confirm Follow/Locked behavior and placement are preserved. The Restore control is intentionally inset near the top inside the maximized picture so the normal app cursor can reach it. Hover hint must say Restore/Overview. Press/release it once: no app click, no roll, returns to overview with the same XR-n selected. Repeat once in the other existing Follow/Locked mode only if the first transition is clean. No recenter command is part of this step.
4. **C1 navigation and input, at most60s.** Open Options; use pointer and keyboard to change category, page, scope and focus. Scroll only over the menu; confirm focused rows become visible. Click the disposable app and type/scroll: normal application input must resume. Reacquire menu focus and use Escape one layer at a time. Do not add global bindings.
5. **Bounded setting/child checks, at most60s.** Record a reversible setting's initial value, change by one step, observe pending then actual owner-applied value and restore it. Check monitor versus workspace filtering. Open/cancel native file and text pickers without playing media, and open/cancel the existing keybind editor without saving a new chord. Confirm no stuck modal capture. Keep privacy, window movement, SBS and video output-mode transitions out of this first bounded trial unless specifically approved.
6. **End, at most30s.** Restore the recorded setting, close menu and return to the original view/mode/selection. Capture after image and record actual measured health/errors plus exact commit. Cody decides whether to continue the session or use external Stop. Stop must preserve existing evacuation/recovery semantics.

## Separate optional follow-up, requiring new approval

Use the disposable window to validate window-send and return with backend-confirmed target; privacy off/on only while the fallback recovery route is independently reachable; video open/seek/format/quality with a known local sample; 3D enable/disable including physicalR1 requirement and applied state. Test one transition at a time and stop on the first failure. Do not combine this with driver/refresh testing. Monitor add/remove, layout reset, output loss and renderer-fault recovery require a separate explicit test scope; their offline rollback coverage is not permission to fault the live session.

## Record

Exact commit/build; actual refresh/tracking state; fallback; selected XR-n and mode; commands exercised and backend readback; before/after PNG and short native clip if approved; any error and parent recovery action. Never present offline raster evidence as a live glasses recording or scanout proof.
