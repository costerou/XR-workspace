# Ownership offset fix — offline, awaiting parent review

## Outcome

Source-supported missing synchronous layout phase fixed. Real module built, production-method regression RED then GREEN, all offline suites passed. **No plugin load, VM boot, host compositor query/mutation, device action, or subagent.** This is not runtime acceptance; the original failed smoke remains the only runtime ownership evidence.

Base: `2827dc2857cc8530db8264187982eaef38e76a17`.

## Observed failure and evidence inspected

Read the original `ownership-isolated-smoke-report.md`, actual serialized requests/replies, pending journal and serial/compositor logs under `/home/cody/.hermes/cache/scratch/xr-ownership-smoke/`.

- Broker sequence was hello/create, first requested `(1280,0)`, observed `(0,0)`, state failed. Exact 1920x1080/120000 mHz, scale 1, transform 0, named workspace 2 matched. No later inspection proves eventual movement.
- Journal retained one pending operation; no fabricated confirmation/recovery.
- Serial logs include deliberate failure traceback, existing EGL fallback errors and shutdown. They do **not** provide a detailed post-create monitor/layout trace; cause is established by pinned source plus offline reproduction, not an invented runtime trace.
- Original serial hash remains `e1cfb890d52cee290f366c4b9f0eb52093fbafa78057c7689e399fe3a048a03d`.

## Source-supported cause: stored offset, missing layout application

Sources fetched read-only from exact Hyprland commit `efb50993780079460b0cbed1363e2166a2de1d9f`, not current main. Copies in `/home/cody/.hermes/cache/scratch/xr-offset-fix/`. Downloaded Monitor.cpp is byte-identical to prior `broker-pinned/Monitor.cpp`.

1. [`src/output/Monitor.cpp`](https://github.com/hyprwm/Hyprland/blob/efb50993780079460b0cbed1363e2166a2de1d9f/src/output/Monitor.cpp): `applyMonitorRule` lines 719–1082 modesets and calls `applyMonitorRuleSoft` at 1069. Soft stores `m_activeMonitorRule` (653) and geometry but does not move the monitor. `explicitPosition` (1249–1253) exposes stored offset; `moveTo` (1719–1737) updates position and floating windows.
2. [`src/state/MonitorLayoutController.cpp`](https://github.com/hyprwm/Hyprland/blob/efb50993780079460b0cbed1363e2166a2de1d9f/src/state/MonitorLayoutController.cpp), 55–82: `arrange()` synchronously invokes position controller, updates XDG outputs, emits layoutChanged and updates XWayland work area.
3. [`src/state/MonitorPositionController.cpp`](https://github.com/hyprwm/Hyprland/blob/efb50993780079460b0cbed1363e2166a2de1d9f/src/state/MonitorPositionController.cpp), 21–38: explicit positions are passed directly to `moveTo`, without normalization/rounding. Auto-position and XWayland handling follow.
4. [`src/config/shared/monitor/MonitorRuleManager.cpp`](https://github.com/hyprwm/Hyprland/blob/efb50993780079460b0cbed1363e2166a2de1d9f/src/config/shared/monitor/MonitorRuleManager.cpp): add schedules reload (37–42); ensureMonitorStatus skips full matches (157–160), returns if no refresh needed (179–180), otherwise separately calls arrange (194). Therefore waiting for deferred reload is **not guaranteed to repair** a manually applied identical active rule. Mode application and layout are separate lifecycle phases. `Monitor::onConnect` also uses synchronous arrange via scope guard (110).

Previous plugin added rule and called applyMonitorRule, then immediately verified position. It omitted the separate layout phase. This explains exact mode/workspace with stale position; it is not evidence that the requested offset was refused.

## Minimal fix and contracts

`plugin.cpp`: include MonitorLayoutController; after successful apply and identity resolution, call the compositor's synchronous `arrange()`, then re-resolve the owned handle before touching workspace state. Existing fresh-workspace check and strict Registry verification still run after callbacks.

- No direct `m_position` assignment, name-based monitor lookup/adoption, rule reapplication loop, sleeps, event-loop pumping or asynchronous wait.
- No pending/inspect protocol redesign required: supported synchronous phase completes before unchanged exact verification. Failed apply/layout/identity/rule/geometry remains failed, never live; journal confirmation requirements unchanged.
- Arrange is compositor-wide: it recomputes existing automatic monitor placement from current active rules and publishes layout notifications, just as normal Hyprland layout does. It does not install or overwrite others' rules. This side effect needs parent runtime review, not a claim of zero desktop effect.
- All geometry/refresh/workspace/rule ownership checks, ABI gate and deferred-reload mismatch latch are unchanged. Further drift yields invalid; no tolerance introduced.

## Regression and RED/GREEN

New `ownership_offset_test.cpp` links real Registry and **compiles actual NativeBackend::configure and ::verified bodies extracted from plugin.cpp at build time**. `extract_ownership_methods.py` performs extraction only, not source-text assertions. This avoids a duplicated implementation or production-only test hook. Offline compositor boundary doubles model pinned apply-without-move and explicit layout application; no compositor is loaded or executed. These tests do not claim actual Hyprland integration.

Before production edit, new test compiled successfully and failed at `r.create("s","r",p)=="live"` (CTest exit 8). Only afterward the six-line production change was made. GREEN covers:

- Exact `(1280,0)`, `(3200,0)` and `(-1920,1080)` placement and named fresh workspace adoption.
- Subsequent inspect and duplicate create without reconfiguration; a one-pixel drift becomes invalid.
- Apply failure does not arrange; suppressed layout never confirms.
- Output destruction during layout callback fails safely.
- Rule replacement during layout callback fails strict verification.

Original full-rule VRR/EOTF, retirement, ownership, Python journal/recovery tests remain passing. Boundary double equality in the new test is limited to fields consumed here; original ownership_native tests retain full rule-field coverage.

## Commands and real results

```sh
cmake -S spike -B build/native-spike-offset-fix -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/native-spike-offset-fix --target ownership_offset_test -j2
ctest --test-dir build/native-spike-offset-fix -R ownership_offset --output-on-failure # RED before fix
cmake --build build/native-spike-offset-fix -j2
ctest --test-dir build/native-spike-offset-fix --output-on-failure
python -m unittest discover -s tests/probe -v
python -m unittest discover -s tests/native_spike -v
git diff --check
```

- Full native build succeeded, including real ELF x86-64 shared module, with -Wall/-Wextra/-Wpedantic/-Werror.
- CTest **11/11 passed**; probe **62 passed**; native Python **90 passed**. Expected refusal diagnostics appear in negative Python tests; no failing tests.
- Logs: scratch `xr-offset-fix/red.log`, `green.log`, `probe-tests.log`, `native-python-tests.log`.
- `nm -D` shows the real module references `State::CMonitorLayoutController::arrange() const`.
- New artifact: `build/native-spike-offset-fix/libxr-ownership.so`.
- SHA-256: **`2be43536ebd358f94851ec927857a7305bbef20a880427fcfef4b19afc168e31`**.
- Original `build/native-spike/libxr-ownership.so` was deliberately not rebuilt; hash still `3c315236b3b80aa6789497b64da79cd2536d4a58beb73f463ab8946ec408ee38`. Parent smoke files and untracked live-evidence files untouched.

Pinned source SHA-256:

| File | SHA-256 |
|---|---|
| Monitor.cpp | `9cf88e154eb5dae676c79d37b5b055ca6134838857cecdbb89a3b747a6821927` |
| MonitorLayoutController.cpp | `0190e7496f1f7b90a4c596ecfe2cb973f65b96d1876a2b3273f360226b41cb0b` |
| MonitorPositionController.cpp | `3b705388fa53aef77c5e8c6f6307c94dab08b5dae9460bab427d648359b5ae49` |
| MonitorRuleManager.cpp | `17d46685c2623657f52e71b22a033c0a798cf3903f7bbeb64ceebce87527f016` |

## Remaining gate

Parent review before any isolated rerun. Three real outputs, later deferred reload inspection, recovery/cleanup, layout notifications and all broader migration/identity-failure acceptance remain unverified at runtime. No physical desktop permission is inferred from offline success.
