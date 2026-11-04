# B1 + C1 integration status

This branch is **not install-ready and the full migration is not complete**. Implementation, independent acceptance material and all five implementation branches are integrated. No master update, install, live XR control, service action, driver change or compositor hook was performed. The installed renderer was not modified.

## Required decision and remaining release gates

1. **Maximized Restore is not reachable with the approved location and current pointer ownership.** Its native icon and transformed hit mesh exist, and region-gated acquisition/release uses the existing input owner. However, the icon is above the captured desktop and the real app cursor is confined inside that desktop. `test_actual_above_desktop_mesh_cannot_be_acquired_from_app_pixels` proves the limitation. Synthetic reachable-mesh tests are not native reachability evidence. Choose an explicit top-edge excursion pointer, or a reachable overlay inside the maximized picture. Neither change has been silently substituted. The existing overview Maximize action works; Restore by existing keyboard behavior remains unchanged.
2. **Complete asynchronous action readback is unfinished.** Renderer-owned setting frames now require a real renderer receipt plus owner value equality. Empty action returns do not count as applied. Actions without observable completion remain pending and eventually show a readback timeout. Window-send/privacy, asynchronous video/3D transitions, output transaction failure and rollback need complete action-specific success/error readback in the menu. A renderer receipt is not proof of SBS hardware state, video playback success or window evacuation.
3. **The 116-row ledger is a route ledger, not 116 accepted native flows.** Every row has an implementation or retained-owner route in `docs/xr-options-build-coverage.json` and `tests/native_spike/options_adapter_coverage.json`; per-row full native interaction/readback/recovery acceptance remains pending. Do not treat a schema/ledger integrity pass as functional completion.
4. **External simplification remains gated off.** `options_runtime_complete` is deliberately not advertised. Existing runtime editors remain available until equivalence is verified; the minimal external status/Stop/recovery surface and capability gating are implemented and tested.
5. **Evidence is offline native raster, not projected GPU or glasses interaction.** The opening/navigation clip uses real supervisor snapshots decoded by the C++ decoder and drawn by production CPU raster functions. Its empty monitor content is synthetic. It does not establish transformed GPU pointer reachability, angular readability or native Restore operation. Text input uses compositor keyboard descriptions and libxkbcommon; complete IME and changing multi-layout modifier-state integration is not established.

## Implemented integration

- Display-only bounded `options_menu` protocol, strict native decoder, malformed-payload isolation, stable XR-n identity and namespace checking.
- Native B1 gear, category-tab C1 panel, clipped scrolling, scope filtering, signed backend ranges and units, native top Maximize/Restore icon states and hover hints. Existing roll/level and Connected Tail positions/actions are retained.
- Supervisor pointer, keyboard, child, capture, idle and lifecycle state; typing handoff; layered Escape; stop/restart/output-loss cleanup. Opening Options while maximized explicitly returns to overview without automatically maximizing on close.
- Runtime adapters use existing ViewControl, output manager, video, keybind, privacy and send owners. Startup-only settings stay outside runtime options. Internal paged file picker and URL/folder text editing are implemented; existing native placement/help/video/keybind editors are reused.
- Distinct Remove, layout-reset and Stop confirmations. Remove keeps the minimum-one guard and the existing physical fallback evacuation owner. Stop follows the existing interrupt/recovery unwind.
- Native child surfaces replace the parent surface; live settings retain existing persistence owners.

## Integration commits

All work is based on ff7b26a and authored/committed as costerou <257170158+costerou@users.noreply.github.com>.

| Ownership | Integrated branch head |
| --- | --- |
| Native chrome/decoder/geometry | 943314e |
| Supervisor/input/Restore region routing | a9a704a |
| Runtime adapters/child editors | a2a75f0 |
| External app/status/recovery | 3eb65fb |
| Integration regression QA | 3c6da77 |
| Independent acceptance sibling | 02401a0 |

Lead integration commits include e564af1 (shared contract), 36de513 (approved Maximize addition), c8544b8 (private transport), a6f33dd (keyboard/wheel ownership), 44fc8e5 (text/geometry), e4fb75f (receipt gate), and 71f832e (child/confirmation/fit integration). The following review-fix commit contains the final keyboard/track/folder corrections.

Independent correctness review found three P1 defects: keyboard access to scope/categories, slider track mismatch, and unconnected folder-path editing. All three now have failing-before-fix regressions and passing targeted tests. UTF-8 filename byte bounds also have a failing-before-fix regression. There is no claim that this review accepted the whole migration.

## Verification

Build: fresh `build/integration-release`, Release, `-DXR_ENABLE_OWNERSHIP_PLUGIN=ON`; subsequent source changes rebuilt successfully. No install target ran.

| Check | Result | Log in preview build directory |
| --- | --- | --- |
| Full native CTest after review fixes | 105/105 passed, 137.64s | review-final-ctest.log |
| Native Python + integration + acceptance | 2582 passed, 1 skipped, 10875 subtests, 128.38s | review-final-python.log |
| Latest focused review/UTF-8/picker/slider/readback/adapter/fuzz checks | 29 passed | review-final-targeted.log |
| XR controller | 530 passed | final-xr_controller.log |
| Depth engine | 60 passed, 3 skipped | final-depth_engine.log |
| Luma capture | 234 passed | final-luma_capture.log |
| Luma probe | 135 passed | final-luma_probe.log |
| Luma soak | 128 passed | final-luma_soak.log |
| Probe | 62 passed | final-probe.log |
| Stereo evaluation | 51 passed | final-stereo_eval.log |
| Optional real stabilization recordings | 3 skipped: no supplied offline recordings | final-stabilization.log |
| QML/panel wrapper suites | 11 passed, including real offscreen Qt cases | final-qml.log |
| Isolated bind_check | config ok, no clash in throwaway fixture | final-bind-check.log |
| Seeded actual C++ wire fuzz | 200 valid accepted, 200 corruptions refused | review-final-targeted.log |

The full native suite includes pose/calibration/restart, stabilization, 3D, backoff, capture and iGPU tests. Recording-only skips are not hardware signoff. No new global binding was added. Bind verification uses a private fixture; live user configuration was neither changed nor certified conflict-free.

New regressions cover malformed frames, stale revision/run identity, bounded decoder roundtrips, scope, keyboard header focus/scroll, signed slider endpoints and stationary knob values, file/URL text, Unicode labels, child replacement, empty-action non-completion, renderer receipts, cancellation, geometry and Restore boundaries. Earlier failures and interrupted test logs are retained rather than relabelled as passes.

## Offline native evidence

Directory: `/home/cody/.hermes/profiles/coder/previews/xr-options/build/`.

- `native-before.png`, `native-after.png`: production native CPU raster composed with an explicitly synthetic empty monitor.
- `native-navigation.mp4`: 13 real supervisor snapshots, native decoder and production raster; opening, category/scope navigation, pending setting, URL child, cancellation and close.
- `native-options-0.png`, `native-options-1.png`, `native-options.mp4`: original native raster/atlas study with both Maximize/Restore icon states; not proof both actions are reachable.
- `navigation.wire`: exact snapshot frames. `native-evidence-manifest.json`: artifact hashes/provenance.

Reproduce with `python -m tools.xr_options_evidence.navigation`, `build/integration-release/options_sequence OUTPUT_DIR < navigation.wire`, then the recorded ffmpeg encode commands. This uses no browser reconstruction, compositor, devices or installed session.

## Live-safety incident check

Read-only source/FD inspection confirmed benchmark `DP-2` and `XR-1..3` names belong to the in-process fake Wayland server. `loopback_compositor.cpp` creates private socketpairs, passes its Wayland endpoint as FD 11, and the renderer sets `WAYLAND_SOCKET=11` before connecting. Tests do not connect to real Hyprland or mutate real outputs.

Earlier GPU tests did open renderD128, sharing the live GPU. This does not establish the cause of Cody's no-video incident. The lead stopped only CTest PID 2728740 and its enumerated test descendants; native worker also stopped its own tests. Live renderer PID 2709787 was excluded and untouched. Parent owns live recovery.

The harness now supports explicit render-node selection and prefers the iGPU. Final GPU runs used `DRI_PRIME=pci-0000_12_00_0! XR_BENCH_RENDER_NODE=/dev/dri/renderD129 XR_DEPTH_TEST_NODE=/dev/dri/renderD129`. `igpu-fd-proof.json` records a benchmark renderer opening only renderD129. Native raster evidence has no EGL/device connection.

This report is a blocked integration handoff, not permission or a recommendation to install.
