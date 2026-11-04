# Edge refinement and strength work

The model output now gets a colour-guided 5x5 joint bilateral refinement before the existing per-desktop motion-aware EMA. It runs on the small model grid, once per inference, outside the pose/render path. Large steps survive colour boundaries; isolated depth noise in flat colour is constrained. This cannot recover fine hair absent from the model grid.

The engine already resets its EMA on scene cuts. That flag now also resets the renderer's guide history, rather than only resetting the convergence anchor. The text protection mask feathers outward with a triangular footprint while retaining full protection on detected text. Border fades multiply independent smooth horizontal/vertical ramps, removing the diagonal crease from taking their minimum. Existing background hole filling remains unchanged and needs validation against the requested edge cases.

The panel has continuous main and selected-desktop strength sliders. The UI range 0–100% maps to the existing internal 0–200 range: Soft 25%, Standard 50% (the unchanged default), Enhanced 75%. Select a desktop in the panel's overview for its slider. Overrides persist by desktop name and travel with the existing live settings message, without restarting the depth engine. Old settings and old wire frames remain valid.

## Validation limits

The offline real-chain proof refused to run because an XR session was active. No session was stopped, and the guard was not bypassed. Therefore there are no new real-chain before/after previews, edge/flicker improvements, or four-desktop performance claims. The requested real-content reproduction and image-space disparity limiting/background-fill improvements remain unfinished. Existing stereo_eval already supplies halo, edge_misalignment and motion-masked flicker metrics; these must be applied to new real-chain recordings once the session is down.

Checks: Release build succeeds. Stereo CPU and GPU mirror/render tests pass on the iGPU after the feather correction. QML: 46 passed; panel pytest: 11 passed; bind_check passes. Added refinement/strength tests: 58 passed, 5 skipped with the depth suite. All Python suite directories were run separately (their test module names collide in one pytest invocation). Native-spike has 23 failures/subfailures, 2413 passes and 19 skips: these include live-driver detection and timing under load. The two immersive fallback/retry failures reproduce on untouched 00c2328 (104 passes, 2 failures). This branch does not meet the requested all-suites-green acceptance condition.
