# Isolated-mode implementation report — architecture decision

## Outcome

**NOT IMPLEMENTED; runtime remains blocked.** Taking the user's explicit instruction to return a decision if the runtime-proof architecture has substantive unresolved risk. No production or test source was changed. Normal guards are unchanged. No new RED/GREEN tests or isolated-mode success are claimed.

Read the approved native plan, topology report, consent/CLI, session/peer validation, managed orchestration, spawned guard, journal, Python broker and native wire/backend contracts at starting HEAD `a9f27a5a055a675d8375c0a82d76d8f5f1535dd2`. Loaded the requested integration, brainstorming, planning and TDD skills, plus completion verification. The authorized scope is recorded in `isolated-mode-scope-amendment.md` beside this report.

## Concrete proof gap

1. `tools/native_spike/supervisor.py:129-142` currently authorizes the pair from verified session plus monitor state and a physical connector regex. `hyprland.py:160-181` validates monitor data but establishes no kernel DRM object binding. Replacing the regex with a virtio sysfs lookup would not prove that the selected compositor output object is that connector; a different backend/object with the same name is a counterexample that the new tests must reject.
2. `spike/ownership/registry.cpp:25-31` allows only `fallback_name` in `hello`. `plugin.cpp:243-257` retains only the fallback and returns an opaque fallback token plus `qualified`. There is no retained target handle, DRM device major/minor or connector ID in this protocol. `inspect` concerns managed headless UUIDs, not the selected target/fallback pair. A live sysfs inventory and a verified socket peer cannot fill this missing object-to-connector association.
3. `plugin.cpp:250` wraps the fallback with `physical=true`; `usable()` at lines 162-164 requires that flag plus DRM backend and display state. This legacy implementation uses “physical” for DRM fallback eligibility and would label a Virtual connector internally physical. The new requirement explicitly forbids that conflation. Merely labeling the CLI report “VM” would not fix the native contract.
4. `journal.py:81-88` accepts exactly instance/output/recovery_output/epoch/fallback as saved broker scope. The new mode/boot/runtime/device proof needs explicit durable authority, with cross-mode rejection on restart, rather than a second loose consent check. `managed.py:65-76` also reaches broker recovery after failures, so proof enforcement must cover recovery, not only initial launch and the copied guard.

These are missing contract guarantees, not evidence that a safe implementation is impossible. They make a Python-only bypass around the reviewed module an inadequate bounded implementation. No such bypass was added.

## Recommended decision / implementation order

Proceed native-proof-first, with an additive versioned isolated pair contract and a replacement module requiring independent review. Keep normal schema-1 behavior and physical guards unchanged. First settle and test:

- Retained target and fallback object lifetimes, distinct connector/device identities, virtual-DRM classification separate from `physical`, and fail-closed observations after callbacks/replacement.
- Actual guest evidence collection and trust boundary. Kernel boot identity, positive virtualization evidence and virtio sysfs topology must agree with the native object observations. The isolated QEMU launch envelope (no host device/socket passthrough, network or shares) still needs independent host-side verification: guest-local metadata does not attest all host QEMU arguments. This is not protection against a malicious hypervisor/root fabricating kernel evidence.
- Explicit consent and durable mode/proof authority on launch, spawn and recovery, including mode mismatch refusal before broker mutations; no new hello when restoring saved authority.

There is a promising source-supported native route: installed Aquamarine 0.15.1 exposes public `CDRMOutput::getConnectorID()` (`/usr/include/aquamarine/backend/DRM.hpp:255`) and backend `drmFD()` (`backend/Backend.hpp:95`). The connector member itself is private (`DRM.hpp:273`). A reviewed implementation could combine the retained output's public connector ID with `fstat` on its backend DRM FD and independently verified guest sysfs topology. These headers alone do **not** prove ABI linkage, behavior, lifetime guarantees or runtime connector association. No private-member workaround, device open or runtime probe was attempted.

The already reviewed module SHA256 `2be43536ebd358f94851ec927857a7305bbef20a880427fcfef4b19afc168e31` lacks this new contract. Do not treat its prior review as review of a future replacement. Parent should resolve this native boundary before continuing implementation; no renewed approval for the already approved overall VM-mode intent is implied by this report.

## Actual offline verification

Commands executed from `/home/cody/Projects/viture-workspace/.worktrees/native-spike`:

```sh
python -m unittest discover -s tests/native_spike -v
python -m unittest discover -s tests/probe -v
cmake -S spike -B build/native-spike-isolated-decision \
  -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF \
  -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build/native-spike-isolated-decision -j2
ctest --test-dir build/native-spike-isolated-decision --output-on-failure
```

Results: **105 native Python tests passed**, **62 probe tests passed**, Release configure/build including the opt-in module succeeded, **11/11 CTests passed**. Python negative cases printed expected `blocked:` diagnostics while passing. No test failures occurred. These are unchanged-baseline checks, not implementation acceptance. The new build directory does not overwrite `build/native-spike-offset-fix` or the launcher's `build/native-spike` executable. No module was loaded.

## Next VM test, only after implementation and independent review

Use a new private clone of the preserved single-head fixture, not its existing image/evidence. First qualify two actual virtio scanouts with `max_outputs=2` **and both per-output dimensions**, as documented in `managed-guest-topology.md`. Keep the disconnected non-GL envelope and automatic kms_swrast/llvmpipe path. Read back both kernel connectors, actual native object/device associations, compositor enabled/DPMS/nonmirror state and real guest/session/IPC/Wayland peers. Require healthy rendering on each before managed mutation.

Then issue distinct guest-boot/session/runtime/target/fallback/run consent for the explicit isolated mode; execute the real managed CLI/supervisor, verify the journal and exact broker epoch/handles, normal teardown and separately consented renderer/controller failure recovery. Verify owned surfaces/processes/resources disappear and unrelated display/workspace state remains. Physical instance `efb50993780079460b0cbed1363e2166a2de1d9f_1793311125_1142154332` remains forbidden. No VM boot is authorized by this report. Even a passing trial proves virtual launcher/lifecycle behavior only, never physical glasses, comfort, tracking or physical display recovery.

## Artifacts / restrictions

Created this report and `isolated-mode-scope-amendment.md`; generated only the separate ignored build directory. Existing parent live-evidence files were untouched. No VM boot, host compositor connection, plugin load, host graphics/VT/input/render-node action, network request, installation or subagent dispatch occurred. Independent review remains pending. The requested mode, its RED/GREEN tests and runtime qualification remain outstanding, intentionally rather than weakened.
