# 3D edges and live strength — completed offline proof

The reproduced faults were stretched/doubled object-outline texture (particularly grass beside game buildings), foreground colour leaking into background holes, and a hard text-protection boundary. The desktop-border minimum also introduced a diagonal corner crease. These changes fix those stages at Standard strength; this comparison does not turn the strength down.

## Changes

- Colour-guided filtering on the model grid snaps blurred depth boundaries toward real picture edges. It runs before letterbox restoration, so black bars keep a uniform plane.
- A symmetric depth-step limiter caps normalized adjacent-map steps at 0.3, without moving constant planes or broad gradients. GPU interpolation further spreads these steps over picture pixels; comfort ceilings remain enforced.
- Hole fill searches for the farther surface, rejects nearer vertical taps, and uses a smooth 0–1 pixel disparity-transition band. In the known-answer foreground/background case, foreground leakage fell from 0.50 to 0.00 (RGB fraction). CPU and iGPU shader mirrors agree.
- The static depth EMA time constant is 240 ms, with existing motion/depth-change release and scene-cut reset. Scene cuts also reset the renderer's history. The static-noise regression now requires fivefold noise reduction; moving-window and scene-cut tests still pass.
- Text retains its full protected core, with feathering outside it. Horizontal and vertical border ramps multiply smoothly.
- Continuous 0–100% main and selected-desktop sliders persist and apply live, including a completely still desktop: the renderer immediately rebuilds its guide from an owned cached depth upload. It does not restart the engine or refresh a stale map's age. Standard is 50% on the new scale, preserving the old default; Soft is 25%, Enhanced 75%.
- Fixed Python failures: an engine-failure/repeated-request race; startup namespace readiness; failure notification after an unsuccessful restart; fake-driver tests reading host processes; and a CPU performance assertion counting OS descheduling. Running services, drivers and engines were not changed.

## Real content and method

Before is origin/master 00c2328, with its original renderer and original engine in an archive. After is this branch. Both use the real Depth Anything V2 Small ROCm engine from ~/.cache/viture-depth, a private memfd, DepthLink and the real headless GlCompositor at 3840x1200. No live capture, display, controller, glasses or running engine is contacted. All GPU processes use nice 19.

Fixtures: repo-generated desktop text with a film image; NASA's public-domain astronaut portrait with hair (scikit-image fixture); generated thin-line UI; Tears of Steel frames (Blender Foundation, CC BY 3.0); and the cached 0 A.D. screenshot/pan (CC BY-SA 3.0, sources in docs/3d-comfort.md). A supplementary portrait sequence adds deterministic ±1 RGB-level dither to stress nearly still depth.

`tools.stereo_eval.metrics.edge_artifact` measures 5x5 RGB patch residuals near source edges, allowing horizontal stereo displacement. `temporal_flicker` masks motion using the nearest known source frame and subtracts source RGB change. Both have known-answer tests. Existing disparity flicker/jitter are also scored from the recorded SBS frames. These are engineering proxies, not a glasses comfort rating; block matching has roughly 3 arcminute quantization.

## Edge measurements

Mean edge residual, RGB fraction of full scale; lower is better. Before/after use identical input pictures and settings.

| Scene | Before | After | Reduction |
|---|---:|---:|---:|
| text | 0.0009265 | 0.0006372 | 31.2% |
| hair | 0.0050018 | 0.0048849 | 2.3% |
| thin-ui | 0.0008600 | 0.0007443 | 13.4% |
| video | 0.0031119 | 0.0030906 | 0.7% |
| game | 0.0096304 | 0.0046642 | 51.6% |

The estimated near/far disparity ranges are unchanged on every still; border violations remain zero. The gain is not explained by reducing Standard strength globally. Fine structure absent from the model's small depth map cannot be recovered perfectly.

| Sequence | Disparity flicker before → after, arcmin/frame | Jitter before → after, arcmin |
|---|---:|---:|
| motion-video | 1.26633 → 1.24604 | 2.39196 → 2.34089 |
| motion-game | 0.06331 → 0.05076 | 1.76204 → 1.36003 |
| noise-hair | 0.08046 → 0.08672 | 0.16032 → 0.17284 |

Still scenes have zero measured flicker before and after. The added 1-level dither portrait is slightly worse on the recorded-frame proxy (about 0.006 arcmin/frame difference, below the matcher's per-pixel resolution); it is retained in the report rather than hidden. The supplementary RGB metric and full p95 values are in edges-comparison.json. No claim is made that every noisy source or occlusion can be perfectly reconstructed.

## Performance

RX 7800 XT, 60 Hz, scrolling text on every desktop, 600 measured frames per count after a 3 s settle. Frame time includes glFinish. All runs had zero draw failures and zero frames above 16.67 ms. Maps/s is per desktop (aggregate / desktop count).

| Desktops | Before p50 / p99 ms | After p50 / p99 ms | Maps/s before → after |
|---|---:|---:|---:|
| 1 | 0.75 / 0.92 | 0.76 / 1.05 | 36.8 → 35.3 |
| 2 | 0.63 / 0.92 | 0.67 / 1.61 | 29.9 → 30.2 |
| 3 | 0.63 / 1.03 | 0.74 / 1.32 | 26.1 → 23.1 |
| 4 | 0.68 / 1.32 | 0.74 / 1.35 | 24.5 → 21.4 |

Depth throughput costs up to about 13% in this test. Four desktops still receive over 21 maps/s each while the renderer stays comfortably within 60 Hz. Pose sampling, prediction, presentation and queues are unchanged; extra inference conditioning is asynchronous and never delays head tracking waiting for a map. This is an offline rendering proof, not a scanout or headset latency measurement.

## Tests

All suites pass. Optional tests remain explicitly skipped; the real ROCm model tests were also enabled separately.

| Python suite | Passed | Skipped |
|---|---:|---:|
| Depth engine | 58 | 5 |
| Capture | 234 | 0 |
| Luma probe | 135 | 0 |
| Luma soak | 128 | 0 |
| Desktop / native spike | 2429 | 19 |
| Probe | 62 | 0 |
| Stereo evaluation | 51 | 0 |
| Controller | 523 | 0 |
| Panel | 11 | 0 |

Total: 3,631 Python tests passed, 24 optional skips, plus 11,012 passing subtests. Opt-in real-model GPU tests: 3 passed. CTest on the Radeon 610M iGPU: 101/101 passed, including the CPU/GPU mirrors and still-desktop live-strength regression. Standalone QML: 46 passed; bind_check: no clashes (252 desktop chords, 22 run binds). The panel's 11 pytest tests are included above. Release build and git diff --check pass.

## Artifacts and reproduction

All artifacts are under `/home/cody/.hermes/profiles/coder/previews/3d/`:

- `edges-before-<scene>-sbs.png` and `edges-after-<scene>-sbs.png`: actual renderer output.
- `edges-<stage>-<scene>-worst-{sbs,anaglyph,eye-difference}-crop.png`: identical baseline-selected edge rectangles in both stages.
- `edges-before-after-crops.png`: labelled comparison sheet; `edges-before-after.mp4`: labelled comparison clip with synchronized scene order.
- `edges-<stage>-clip.mp4`: original full SBS clips; `edges-metrics.png`: summary chart.
- `edges-comparison.json`, `edges-before-report.json`, `edges-after-report.json`: full numbers and crop coordinates. `edges-*-work` retains every input and recorded frame.
- `edges-python-tests.log`, `edges-ctest.log`, `edges-qml.log`, `edges-bind-check.log`, `edges-real-model-tests.log`: test evidence.

From the repo root, after building the before/after `stereo_chain_bench` binaries:

```sh
nice -n 19 python -m tools.immersive_proof.edges --stage before --engine-root BASELINE_ARCHIVE --bench BASELINE_BINARY --photo PORTRAIT --out OUTPUT
nice -n 19 python -m tools.immersive_proof.edges --stage after --bench AFTER_BINARY --photo PORTRAIT --out OUTPUT
# Older baseline recordings without the dither case can add it with --only noise-hair.
python -m tools.immersive_proof.edges_compare OUTPUT
```

Nothing installed, merged or changed in the running XR setup. No subagents were used.
