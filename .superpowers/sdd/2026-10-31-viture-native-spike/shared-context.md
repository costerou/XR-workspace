# Native plan constraints and shared contracts


- Both Beast and Luma Ultra, normally one pair connected at a time (proposed initial scope).
- Three monitors are the initial acceptance configuration, not a fixed product limit.
- Target 120 Hz where the hardware, mode, and transport support it. No unverified claim of flawless performance.
- Head-direction targeting, not eye tracking.
- No capture, typing metadata or head-pose data leaves the machine.
- Never silently edit stock Omarchy files, install system packages, alter udev rules, stop another XR service, change firmware, or restart Hyprland.
- Initial target is this host, not the user's ARM laptop; avoid accidentally building around the bot's headless display.
- No automatic remote fetching, system service installation, root renderer, device grabbing or firmware writes. Do not change the selected out-of-process architecture without Cody's approval.
- This document is a future execution plan, not runtime authorization. The parent owns all named-pair connect/disconnect confirmations using the hardware transition checklist below; hardware may be unplugged now. Offline tasks do not depend on connected glasses.
- Treat SDK terms as a blocking decision, not an installer checkbox to automate. Do not copy vendor headers/binaries into Git. Keep any permitted hardware/SDK measurements local; obtain clarity on third-party benchmark disclosure before sending them to a reviewer/model.
- Do not implement the complete product from this plan. The final gate produces an evidence report and a subsequent production plan, not a daily-use release.

## Review Focus

1. A user changes a workspace or setting after our mutation: recovery preserves that change rather than restoring an entire stale desktop snapshot (Task 2).
2. A capture becomes static, resizes, or disappears while a GPU buffer is sampled: presentation continues with the freshest complete image, and no resource is reused early (Task 4).
3. A key repeat, input hot-unplug or dropped evdev event hides a held drag: head focus fails closed without recording key contents (Task 5).
4. A tracking timestamp resets or polled pose repeats: receipt time must not masquerade as sensor age; stale/unknown tracking cannot drive unsafe focus (Task 6).
5. SDK native stabilization or a mode switch changes the transport: never count a 60/119.88 Hz or duplicate presented sequence as passing a 120 Hz test (Task 7).

## Permission and stop gates

| Gate | Required approval before executing | Stop behavior |
|---|---|---|
| P0 | Parent/user review of this plan and chosen execution method | Only documentation work until accepted |
| P1 | Exact missing build packages, if any | Print package/version requirements; no install, auto-fetch or substitutes |
| P2 | Selected physical Hyprland instance; temporary named headless outputs, test-window placement, output-bound presentation and screen capture of those outputs; explain permission popup | Refusal/missing physical recovery output: no mutations. Do not grant blanket screencopy permissions |
| P3 | Reading explicitly nominated keyboard/pointer event devices for activity only; any temporary ACL, follow-mouse override or shortcut | No privileged fallback. If denied/unavailable, keep head-directed focus disabled and mark safeguard feasibility blocked |
| P4 | Vendor terms, naming/disclosure/privacy resolution, exact SDK package/hash and binary isolation; named pair connected; initialize/start/stop tracking | No linking/loading/SDK calls before clearance; no simultaneous competing driver. SDK failure is not permission to kill a service |
| P5 | Exact glasses mode transition with previous mode and rollback shown; separately authorize stereo/native/bypass changes | Never switch dimension/native mode implicitly. Stop on loss of physical recovery display, unreadable previous state or unverified rollback |

Permissions are scoped to an explicit run, not inferred from a previous run or a connected USB PID. A disconnect/reconnect creates a new tracking epoch and requires device identity revalidation. No production integration begins until every final gate is satisfied or Cody explicitly approves a revised requirement/architecture.

## Parent-owned hardware transition checklist (Tasks 6 and 7)

This is a future runbook requirement, not a request to touch hardware now. Record confirmations and observed transitions separately; enumeration alone is never consent. Refusal, missing acknowledgement, unexpected identity, ambiguous disappearance or failed recovery stops the run; preserve the journal and request human help, never infer consent or continue to another pair.

1. **Name pair and action.** Parent names the current and intended pair (Luma Ultra or Beast), selected physical instance/output and whether this is initial connection, ordinary teardown/pair swap, or a deliberate USB-loss fault injection. Recheck run-scoped approvals and the independent physical recovery display.
2. **Prepare removal.** For ordinary swaps/end of run, stop head focus and tracking, close owned capture/presentation, perform journal recovery and verify window reachability, mode/settings restoration and owned-resource cleanup by readback *before* asking for removal. For deliberate loss while running, explain what remains active and the expected independent recovery, obtain explicit approval for that named-pair fault injection, and verify the supervisor/recovery display are ready; ordinary swap permission does not authorize live loss.
3. **Disconnect checkpoint.** Parent says “Luma Ultra [or Beast]: ready for the approved disconnect; please disconnect that pair and confirm.” Wait for user acknowledgement. Observe disappearance of that pair's USB identity and associated selected glasses output; if only one disappears or identity is uncertain, stop, do not start a new pair/epoch. For fault injection, verify supervisor recovery/restoration and reachable windows by readback **before requesting reattachment**. A failed recovery is a stop, not a reason to reconnect automatically.
4. **Connection checkpoint.** Only after the disconnected state (and recovery where applicable) is verified, ask “Please reconnect Luma Ultra [or Beast] and confirm,” or, for a swap, “Please connect Beast [or Luma Ultra] and confirm.” Wait for explicit confirmation. For an initial run with no prior pair, start here only after verifying the intended disconnected starting state; if a pair is already attached, ask the user to confirm its identity and intended run before proceeding.
5. **Revalidate before resuming.** Re-enumerate actual pair identity, selected physical compositor instance/output, active mode, physical recovery display, device ownership and scoped permissions. Establish a new tracking epoch; invalidate old pose/focus/input reliability state and resynchronize Task 5 coverage before enabling head focus. Reconnection does not authorize replay of a stale mode change. If identity/state differs unexpectedly, stop and seek new approval rather than continuing.
6. **Safe finish.** End *each* run with verified orderly teardown/restoration and an explicit named “Luma Ultra [or Beast] is safe to disconnect; would you like to disconnect it now?” prompt. If accepted, wait for confirmation and verify disappearance as in step 3. If declined, leave the pair attached but inactive, end the run and perform no next-pair transition. Never require it to remain attached or describe unresolved restoration as safe.

Task 6 ordinary sequence is Luma Ultra → verified teardown → confirmed disconnect → observed absence → confirmed Beast connection → revalidated new epoch. Each Task 6/7 reconnect experiment uses the deliberate-loss branch, including its separate approval and recovery-before-reattachment checkpoint.

## Task 3 presentation-only gate (parent ruling)

`present` is an independent 3A acceptance track, not managed `run`. Require the
exact `P2-presentation-only` record (`allow: ["test-pattern"]`, capture/input/sdk/
mode_switch all false), selected instance/output and a different usable physical
recovery display. No output/workspace/settings mutations, journal or rollback.
Keep actual IPC/Wayland peer/session checks and the supervised child watchdog;
close the parent's Wayland FD after spawn and on failure. Renderer binds one
`wl_output` proxy and exits on its removal, never selecting a replacement.
Offline verification does not authorize runtime; parent review/user consent and
named-pair hardware transitions remain pending, glasses kept disconnected.
Three-output lifecycle remains 3B, blocked on atomic ownership-safe removal;
all contracts below remain intact. No cooperative deletion is implemented.

## Task 2 → Task 3 reconciled boundary

- Output intent is exactly `{kind: "output", id: <canonical UUID>, name:
  "XR-SPIKE-<journal-run-id>-<index>", index: <integer 0..2147483647>, before: null}`.
  Index is canonical decimal in the name; bool/float/negative indices are rejected.
  The complete name must satisfy the existing 128-character ASCII token bound.
  Prepare and reload both bind name to the journal run and explicit index. UUID is
  separate ownership identity, not encoded in or inferred from the name. Task 3
  must choose a run ID short enough for all generated names before mutations.
- `RecoveryBackend` (journal.py) is normalized `query(kind)` / `mutate(command)`.
  Task 3 supplies `RecoveryAdapter`; raw `HyprlandClient` is a separate transport.
  Raw query allowlist remains exactly `monitors`, `workspaces`, `clients`,
  `activewindow`, `getoption`, `cursorpos`, with 2-second deadline / 1 MiB cap.
- Adapter mapping: `outputs` ← raw `monitors` plus proven incarnation bindings;
  `workspaces` ← normalized raw workspace records; `settings` ← validated
  `getoption input:follow_mouse`; `session` ← independent connected-instance
  verification, not constructor echo or an invented raw request. Normalized
  `session`/`outputs`/`settings` must never be forwarded as raw requests.
- Keep the report's normalized schemas, mutation strings, repeated session checks,
  migration readback and approved usable physical fallback requirements. UUID
  binding must independently prove the actual output incarnation in that verified
  session. Matching names/prefixes or reusable monitor IDs do not suffice. Lost
  continuity after crash/restart, malformed/incomplete queries, or unavailable
  proof means preserve resources/journal and report blocked recovery; do not
  broaden the raw allowlist to invent proof. Adapter implementation and its fake
  raw-transport integration tests remain Task 3 work, not a live-success claim.

## File map and common contracts

Create under `spike/`, not the future production tree:

- `spike/CMakeLists.txt`, `spike/README.md`, `spike/dependencies.md`: build, explicit local dependencies, commands and permission checklist.
- `spike/include/xr/types.hpp`, `ipc.hpp`, `capture.hpp`, `presentation.hpp`, `tracking.hpp`, `focus.hpp`: internal contracts only, not claimed upstream APIs.
- `spike/src/{ipc,wayland,presentation,capture,gpu,focus,activity,tracking_sdk,pose_clock,metrics,main}.cpp`: corresponding bounded runtime pieces.
- `spike/shaders/{desktop.vert,desktop.frag}`: texture sampling and flat/cylindrical monitor mesh rendering; no web UI renderer.
- `spike/protocols/{wlr-layer-shell-unstable-v1,wlr-screencopy-unstable-v1}.xml`: future pinned upstream copies with notices; `PROVENANCE.md` records URLs/hashes. Generate stubs in build directory. Use installed linux-dmabuf/presentation-time XML capped to negotiated compositor versions.
- `tools/native_spike/{__init__,__main__,ipc,hyprland,journal,supervisor,benchmark}.py`: supervisor/recovery and measurement summarization; no modifications to diagnostic command allowlist.
- `tests/native_spike/test_{ipc,journal,recovery,hyprland,benchmark}.py`: standard-library offline tests with fake IPC, no real display changes.
- `spike/tests/{ipc,layout,presentation,capture,focus,activity,tracking,metrics}_test.cpp`: CTest executables; fixtures explicitly synthetic.
- `docs/feasibility/native-spike/{runbook,results}.md`: later permission record, real results, failures and gate status; no screenshots/key content/raw pose dumps by default.

Modify diagnostics **only in Task 1 of this future plan**: `tools/xr_probe/session.py`, `report.py`, `tests/probe/test_session.py`, `test_report.py`. Preserve schema v1 and historical reports; additive raw-token provenance is permitted, but no fabricated observed capability.

Internal types fixed here (all names in namespace `xr`; no vendor/compositor ABI implied):

- `MonoNs = int64_t`; `Quaternion {double w,x,y,z;}`; `OutputId = std::string` (stable UUID, not array index).
- `PoseSample {Quaternion orientation; uint64_t epoch,sequence; std::optional<uint64_t> device_tick; std::optional<MonoNs> sample_mono_ns; MonoNs received_ns; bool valid;}`. `sample_mono_ns` stays null until a validated clock mapping exists.
- `OutputSpec {OutputId id; std::string name; uint32_t width,height,refresh_millihz; double scale; int32_t x,y;}`. Initial virtual outputs: three 1920x1080, scale 1.0, 120000 mHz; unique nonoverlapping positions calculated from existing output rectangles, never hard-coded over the physical desktop.
- `FrameToken {OutputId output; uint64_t generation,sequence; uint32_t slot; MonoNs ready_received_ns;}` refers to an owned pool slot, not a pointer crossing IPC.
- `Activity {MonoNs received_ns; bool key_activity,pointer_motion,drag_held,reliable;}` carries no key codes, text or window titles.
- `Target {OutputId output; std::optional<std::string> window_address;}`; `FocusDecision {std::optional<Target> target; std::string reason;}`.
- `Status` is `std::variant<std::monostate, Error>`; `Error {std::string code,detail;}`. `Result<T>` is `std::variant<T, Error>`. These are defined in `types.hpp` and reused consistently.

Control wire format: one UTF-8 JSON object per line, maximum 16384 bytes including newline, `schema=1`, monotonically increasing `request_id`, allowlisted operations `status`, `start`, `stop`, `select`, `recover`. Reject duplicate keys, unknown fields/operations, nonfinite numbers and oversized/partial messages; no shell, arbitrary path or raw Hyprland command field. Use `$XDG_RUNTIME_DIR/xr-workspace-spike/<run-id>/` mode 0700, socket/journal mode 0600, same-UID `SO_PEERCRED`, and explicit session/run identifier. A maximum of eight queued control messages; status/metrics never block rendering. Pose worker socket is a separate same-UID channel carrying schema-versioned PoseSample values with the same framing rules, not upstream XRL shared memory.

Build/test commands, always from the repository root:

```sh
python -m unittest discover -s tests/probe -v
python -m unittest discover -s tests/native_spike -v
cmake -S spike -B build/native-spike -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/native-spike
ctest --test-dir build/native-spike --output-on-failure
```

Default build/tests must neither connect to Wayland nor open devices. Hardware-enabled tests are opt-in commands, never CTest defaults. SDK support is OFF unless an approved absolute `XR_SDK_ROOT` with a recorded package hash is supplied. Do not run `ldd` on untrusted SDK binaries; inspect ELF metadata statically before later authorized loading. Recheck all build/runtime versions at execution; mismatch requires requalification, not using private Hyprland headers to force compatibility.

---
