Task 12: isolated VM qualification of the combined settings panel, prediction and keyboard help build.

Tested source: `822495c296a3825b768347a988b920b760da35fc`, descended from the coordinator’s combined `4235a46`. The evidence and complete binary hashes are in [isolated-vm-panel-live.json](isolated-vm-panel-live.json). Renderer SHA256: `62989b6eb6bc85cb121ef2d87afa120ef68ab7b8683be0dc9547dae8f487ee21`; plugin: `a68bd142a95a601e43d9fb3c6a4c68956b401a6a7a0db0c5b196a9785b587ae5`.

The new explicit `python -m tools.xr_controller.isolated serve --vm-consent <file>` path requires an existing private, one-run `VM-isolated` consent plus the normal panel confirmation. The former fixes the guest boot, verified session, runtime, virtual connector pair and run ID; the latter fixes geometry and pins. It reuses the existing kernel/virtio proof, schema-2 broker and isolated recovery. Normal physical validation and normal controller commands are unchanged. Runs use real, independent `systemd-run --user` units in the guest.

| VM scenario | Observed result |
|---|---|
| Mixed geometry | 1920×1080@1, 3840×1080@1, 3440×1440@1.25; exact x=2560/4480/8320, y=0, 120000 mHz. Renderer captured all three at their own sizes, with zero capture failures. |
| Presets/scales | All 20 preset × valid-scale combinations passed exact broker readback, three outputs per case. No scale-rule changes required. |
| Reload | Mixed rules survived both broker-only reload and reload during a controller-owned capture run. Renderer PID survived. |
| Start/stop | Begin alone created no renderer/plugin. Confirmation started one unit; Stop returned to idle, removed owned outputs and unloaded the plugin. |
| Renderer crash | SIGKILL produced renderer return code -9 and automatic complete teardown. Fixed the isolated runner to return failed unit status rather than success. |
| Recovery | SIGKILL of the independent runner left `recovery-needed`; explicit Begin/Confirm Recover completed using the saved isolated journal, then unloaded the plugin. |
| Restarts | Killing/reopening the actual QML Panel in an offscreen guest Quickshell process preserved the run. Killing/restarting the controller rediscovered the same renderer PID and active unit. |
| Help/prediction | H/P binds were present. Help requests reached the native renderer: one show, one hide, ending hidden. Prediction started on, toggled off/on, saved on and remained on in later runs; native report recorded 628 predicted frames from the synthetic pose feed. |
| Plugin wire | Production client used `/plugin load <pinned path>`, `j/plugin list`, `/plugin unload <pinned path>`; readback confirmed each transition. |

Fresh checks: 216 native Python tests, 61 controller Python tests, and 19/19 CTests passed. Boundary tests, crash exit-status regression and Help diagnostic-counter tests were observed failing before their implementations. Review was self-review only, as agents/threads were prohibited.

Both final trials used the approved disposable q35/KVM, 2-vCPU, 4-GiB, non-GL virtio-GPU envelope: no network, shares, passthrough or host display sockets; snapshot images and outer timeout. Sampled host FD audits and empty survivor lists are archived. Guest-only systemd/QML dependencies were copied into the disposable image. No host services, configuration, compositor or glasses were changed.

An earlier artificial sweep kept one broker loaded across many runs and exhausted guest RAM after mixed plus 18 scale cases. The broker intentionally retains output identity objects; their precise contribution was not measured. The final full sweep followed production unload/load boundaries and passed without OOM. Sustained single-broker resource stress remains unqualified.

This is functional VM evidence, not physical acceptance or a performance benchmark. Rendering used llvmpipe, SHM and a fake pose feed. A 120000 mHz mode is not measured 120 FPS. Panel lifecycle was exercised offscreen; H/P operations and confirmation messages used the real socket without keyboard/input injection. Earlier separate physical prediction evidence does not qualify this combined build. Task 13 remains unperformed and requires separate authorization.
