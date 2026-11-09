# Pro 2 high-strength edge refinement

November 8, 2026. The wearer confirmed working depth and L1 return to 2D, then reported artifacts at higher strength on both desktop borders/text and object outlines.

## Rendering change

The model's 420×266 guide is much coarser than each 1920×1080 eye. Interpolating a sharp depth boundary stretches the visible outline, especially after strength amplification. The renderer now reconstructs supported depth edges using nearby source colours, rejects isolated depth islands with a 3×3 median, and filters only this reconstruction correction over time. The existing base depth keeps its timing; large correction changes release history. No frame queue is added.

Text and frame protection are stored separately from depth in the low-resolution RG16F guide. Reconstruction skips protected centres and endpoints; spatial and temporal cleanup also preserve protected values. This matters for small photographs whose texture can trigger the text detector: a protection boundary is not a physical depth edge.

Reconstruction runs once per depth update. Both eyes, hole filling, and the pointer share the resulting surface. The original model-scale hole-search radius and comfort limits remain unchanged. Source-size buffers are owned by the source, released on removal, and recreated on resize without deleting a cached raw upload. Three additional R16F surfaces use about 12.4 MB per 1080p desktop.

## Evidence

The frozen before renderer and final renderer consumed identical generated desktop/thin-line fixtures and a public NASA astronaut image through the real Depth Anything V2 Small ROCm engine. Every scene used a fresh isolated engine to avoid reusing a previous scene's source sequence. No user desktop pixels were captured.

Geometry was native 3840×1080 SBS, 44.5° diagonal FOV, and maximum UI strength (100%, internal 200). Both eyes were scored. Settings and estimated broad-plane disparity were preserved.

Known-answer tests separately measure foreground leaking outside its expected translated silhouette and foreground lost inside it, excluding one pixel of edge antialiasing. At the actual Pro 2 scale:

| Desktop preset, pixels of foreground | Before left / right | After left / right |
|---|---:|---:|
| Excess foreground | 5.60 / 4.53 | 0 / 0 |
| Missing foreground | 5.54 / 4.61 | 1 / 0 |

Video and Games presets also pass the separate leakage and preservation limits in both eyes. Diagonal silhouettes, constant planes, broad gradients, text protection, scene/reset behaviour, and source resize are covered independently.

On the real photograph, large comb-shaped tears around hair, helmet, and shoulder are visibly reduced. Faint contour remnants remain. The general patch-residual metric does **not** improve universally:

| Mean edge residual, RGB fraction | Before left / right | After left / right |
|---|---:|---:|
| Desktop | .001099924 / .001043938 | .001100307 / .001043573 |
| Thin UI | .001028785 / .000997925 | .001029010 / .000997613 |
| Photograph | .007085280 / .006433928 | .007110118 / .006786140 |

Desktop and thin-UI results are effectively unchanged. The photograph's aggregate residual rises about 0.35% left and 5.47% right despite the clearer large silhouette. The estimated interior disparity p95 remains 8 pixels for UI and 28 pixels for the photograph. This is a targeted outline improvement, not a claim that every reconstruction metric improves.

A deterministic ±1 RGB-level noise sequence checks temporal behaviour. The final renderer's 99.9th-percentile source-edge change is 2 RGB levels in both eyes, matching the baseline. Rare larger changes remain more frequent: about .0135% / .0185% of edge pixels exceed 8 levels per frame, versus .0014% / .00036% before. Fine shimmer and missing occluded detail remain limitations of inferred 2D-to-3D depth.

## Performance and verification

RX 7800 XT, native Pro 2 geometry, changing input and real depth updates, 306 measured frames at 60 Hz:

| | Before | After |
|---|---:|---:|
| Total frame mean / p99 | .74 / .88 ms | .86 / 1.07 ms |
| Maximum measured frame | .93 ms | 1.11 ms |
| Failed draws / frames over 16.67 ms | 0 / 0 | 0 / 0 |

The final run delivered about 38 depth maps/s. These are offscreen render timings with GPU completion, not headset latency measurements. The successful production session's journal confirms this machine uses the RX 7800 XT. The 610M iGPU does not meet the same update-frame budget in the worst-case test.

Release build and all 10 focused native tests pass, including CPU/GPU mirrors, unchanged golden images, protected-text and cached-upload resize regressions, and capture/depth integration. Wearer comparison of this refinement remains a separate perceptual check.

The verified renderer was installed in Pro 2 bundle `86b6b59fbad9` and the idle controller was restarted successfully. The installed renderer's SHA256 matches the release build (`65a01317c125e2119e60cb09a67d513a69f0f53bc948638ebc9b5230653170b9`), and the depth-engine launcher is present. Existing bundles remain available. Start a new XR session to use the refinement; saved strength and layout settings were not changed.

Artifacts are under `/home/cody/.cache/xr-pro2-edge-proof/`: `before-report.json`, `afterprotected-report.json`, `comparison-afterprotected.json`, `comparison-afterprotected-photo-{left,right}.png`, `previous-worst-afterprotected-{desktop,thin-ui}-right.png`, `noise-before-report.json`, `noise-afterprotected-report.json`, and `timing-{before,afterprotected}-report.json`. The final benchmark SHA256 is `5b6a995906baa83a4a1a756e0202f1b04c2e1fd3401c2e921829ac7873539e54`.

The cached runners `run_proof.py`, `run_noise_proof.py`, and `run_timing_proof.py` retain the exact inputs and commands. Native test/build logs are `/home/cody/.cache/xr-pro2-edge-native-tests.log` and `/home/cody/.cache/xr-pro2-edge-build.log`.
