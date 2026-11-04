# RQ7 canonical identity and packaged subprocess acceptance

The parent's second authorized test started the SDK and loaded the plugin, then failed before managed output creation with `explicit Pro2 supervised test scope required`. The parent verified clean backout, no XR picture, no remaining outputs/plugin/USB owner, and unchanged 1080p120 readback. The integrator did not run hardware or read private parent SDK logs.

The exact packaged shell reproduced the failure in a child process. Its `runpy(..., run_name='__main__')` entry created a different TestScope class from the canonical import used by managed.py. BundleClient had the same unsafe class origin for spawned-process serialization. Functional fix 189fc93 moves TestScope, ScopedGuard and BundleClient into tools.pro2_runtime.scope and imports canonical main from the shell entry. Strict type validation and normal production Pro2 guards remain intact.

`tests/pro2_cli` executes the actual packaged entry without Python source overlays or substitutions for execute, client construction, managed startup, journal, view controls or renderer launch. It exercises real BundleClient transport and verified Unix peer, managed output transactions, bind generation, a separately spawned guard, and a CPU renderer endpoint consuming native Publisher/PoseFeed/PoseState. Only external endpoints are simulated: the C SDK, compositor wire server and renderer child. No compositor Lua, GPU rendering or physical picture is proved.

Isolation includes private user/IPC/network namespaces, private home/config/state/runtime, fake display endpoints, a nonexistent D-Bus address, a notification stub and device/library/socket audit checks. The production artifact contains neither the synthetic bridge nor a vendor SDK.

The four-case matrix covers:

- Successful managed startup, native pose consumption and complete teardown.
- Renderer exit 9 with a false overall result and complete recovery.
- Partial output creation failure retaining plugin authority, followed by a second actual packaged `--recover` invocation without any additional SDK calls and with confirmed plugin unload.
- SDK open failure before plugin load or journal creation.

Fresh source checks: 42 independent QA cases, 10 runtime/pipeline cases, 534 controller regressions, 8 normal guard cases, and 2474 native Python cases (one explicit skip; 10882 subtests). Logs are retained alongside this report. Independent QA also ran the genuine four-case matrix, including from a fresh archive extraction. Final release acceptance repeats source/hash/canonical-entry checks and this matrix against the frozen replacement bundle.

These are offline orchestration results. The old 92f01a7 and 910cabc releases are superseded for launch. Only the parent may authorize another physical attempt after reviewing the replacement artifact, final-path preflight and full-entrypoint evidence. Installation, real compositor semantics, optics and physical XR acceptance remain unproved.
