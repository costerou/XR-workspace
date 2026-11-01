# Task 13: first Luma panel session blocked

Source `f17f4c0f1fab1c1203178bc3e562aa6309675af0`; functional VM evidence
`1480e9f` tested source `822495c`. This is a failed physical startup attempt,
not successful physical acceptance. [Recorded evidence](physical-panel-acceptance.json).

Cody explicitly approved the first Luma normal Start/control/Stop session.
Fresh preflight verified Luma Ultra on DP-2 and Samsung fallback DP-1, no loaded
plugin or competing driver, valid external-only tracking configuration, and no
unresolved journal after the coordinator's intact archival of historical fol2.
Installed renderer/plugin hashes matched VM evidence and were checked again before
confirmation and after teardown. The panel symlink/widget was installed/enabled;
a foreground controller and network-isolated tracking driver were started.
Tracking calibrated successfully. No persistent service was installed or enabled.

The user made two panel-confirmed attempts:
`cf0b020b5f430d892688488a905ca627` and
`45a4da524331c896f97b9100ab0e738f`. Both consented to three 1920×1080@1 desktops,
tracking, and the temporary Luma 1920×1200@120 mode switch. Both runner results
failed in phase `mode` with `session mode (1920, 1200, 120) not observed on readback`.
Both units exited status 2. Neither launched the renderer or reached managed
output creation. Cody reported: “The XR view did not appear”.

The physical mode was not observed by the runner's verification. The implementation
reads back immediately after `hl.monitor`; a timing issue is possible but unproven.
No extra live mode experiment or retry was performed by the agent. The controller
returned to idle without surfacing this runner error in its status reason; this
also needs coordinator investigation before the next physical attempt.

After the report, the session's driver was stopped and foreground controller
terminated. Readback matches the display baseline: DP-1 5120×1440@240, scale 1.25,
position 0,0; DP-2 1920×1080@60, scale 1.25, position 4096,0. Plugin list is empty,
neither attempt created a managed journal (historical journals remain untouched), both installed hashes are unchanged and match both
consents. The archive was not changed. Panel/pinned installation remains.

No presentation rate, missed count, capture path or visual/control/comfort
acceptance exists for these attempts. No 120-FPS claim follows. Luma acceptance,
crash/Recover, Beast and mixed/ultrawide measurements remain incomplete. Further
physical sessions require Cody's explicit approval; this record grants none.

## Approved normal retry: Errno 11

Cody's “go” directly answered the request to explicitly approve the same Luma
normal-session retry. Source `8538468` includes bounded mode readback and the
coordinator's `5580a58` runner-error propagation. Fresh preflight and pins passed;
tracking calibrated in its network namespace. Existing installed binaries were
reused unchanged. No persistent service was installed or enabled.

Panel-confirmed run `06422ac48c7cdd7272b533f4d7508559` failed in phase `mode`:
`[Errno 11] Resource temporarily unavailable; unload: [Errno 11] Resource temporarily unavailable`.
Renderer return code is null: no renderer was launched and no run journal was
created. Cody reported “Errno 11”. The controller now correctly exposed the runner
error in its status reason. Read-only observer samples are preserved in the JSON.
No measured presentation statistics or visual acceptance exists for this run.

Automatic unload failed and the broker remained loaded. With the run's consent
plugin pin reverified, only `xr-ownership` loaded, no managed journal and physical
DP-1/DP-2 only, the agent explicitly unloaded that exact pinned plugin once as
normal session cleanup. Readback confirmed empty. The session-owned driver,
foreground controller and read-only observer were then stopped. Final display
readback matches the original baseline and both installed hashes are unchanged.
No other records were archived or changed.

This is a new IPC/OS error, not evidence that the display mode was unavailable.
The saved result has no syscall/traceback, so which IPC operation returned EAGAIN
is not established. No additional live experiment or retry was performed. Further
offline diagnosis and separately approved physical verification remain necessary.
