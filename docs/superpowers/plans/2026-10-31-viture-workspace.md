# Viture Workspace Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an executable, read-only host/device diagnostic and an evidence-backed integration decision for the approved native XR workspace, without changing the user's running desktop.

**Architecture:** Keep discovery separate from the XR engine. Gather explicit user-session, compositor, GPU and USB/display evidence into a versioned report; classify unsupported, untested and verified claims separately. Choose the production tracking/rendering path only after this evidence is reviewed.

**Tech Stack:** Python 3 standard library and unittest for diagnostics; subprocess argv without shell, sysfs, Hyprland JSON IPC via hyprctl. C++/CMake, Wayland/EGL and PipeWire are available candidates for later native work, not yet selected production dependencies.

**Spec:** `docs/superpowers/specs/2026-10-31-viture-workspace-design.md`

## Global Constraints
- Local home: `/home/cody/Projects/viture-workspace`. No GitHub account, remote, publication, or cloud service required.
- Both Beast and Luma Ultra, normally one pair connected at a time (proposed initial scope).
- Three monitors are the initial acceptance configuration, not a fixed product limit.
- Target 120 Hz where the hardware, mode, and transport support it. No unverified claim of flawless performance.
- Head-direction targeting, not eye tracking.
- Initial target is this host, not the user's ARM laptop; avoid accidentally building around the bot's headless display.
- Never silently edit stock Omarchy files, install system packages, alter udev rules, stop another XR service, change firmware, or restart Hyprland.
- No capture, typing metadata or head-pose data leaves the machine.
- This milestone is discovery only. Runtime input, output creation, SDK initialization, display mode switching, screen capture and installation require a later execution plan and appropriate authorization.

## Scope and staged delivery
The approved design spans independently testable subsystems. This is the complete executable plan for milestone A, not a claim that the whole application can be built from a read-only probe. Later milestones receive exact implementation plans after their dependencies have evidence:

| Milestone | Working deliverable | Design coverage / gate |
| --- | --- | --- |
| A — this plan | Diagnostic CLI, tested parsers, real host report, compatibility decision | physical session, USB models, output modes, GPU ownership, available protocols/dependencies, driver/license investigation |
| B | Native single-output capture/presentation and pose integration spike with real timing | input remains on desktop, fresh pose with static capture, real 120 Hz mode, GPU import/synchronization, tracking axes/drift on each pair; safe teardown |
| C | Deterministic workspace geometry/focus/follow core with replay tests | 0 DoF/Anchor/Follow, yaw/pitch/roll, raycasting curved monitors, dwell, mouse intent, typing/drag locks, centering latch, mode transitions, recenter |
| D | Transactional virtual-output/workspace management plus recovery supervisor | independent desktops, creation/removal, migration, rollback, cursor routing, actual compositor focus behavior, disconnect/crash recovery |
| E | Omarchy plugin and native layout editor wired to engine | approved mockup controls, size/scaling/resolution, presets, curvature, per-device profiles, safe IPC, validated shortcuts |
| F | Tested daily-use build and local packaging | ten-minute three-1080p performance runs, at least 99% expected 120 Hz presentation intervals without a missed refresh, separate higher-resolution runs, both-pair comfort feedback, fault injection, regression suite |

B is a hard gate before promising a renderer architecture; C can be developed with replay evidence but is not hardware acceptance. D must prove recovery before sustained native sessions. E does not turn simulation into hardware support. F requires actual glasses and user comfort feedback. Any gate failure is reported with evidence and alternatives rather than hidden downgrade.

## Review Focus
1. Multiple compositor sessions or headless output only: never pick the bot desktop silently; Task 2 tests ambiguity and explicit selection.
2. Disconnected glasses or unreadable USB/display metadata: report absent/permission-denied/unknown, not unsupported or supported; Tasks 1 and 3 test this.
3. Advertised 120 Hz but no selected 120 Hz mode or no present-time evidence: distinguish advertised, active and sustained; Task 3 tests non-equivalence.
4. Hanging/malformed subprocess output: bound runtime, cap retained output, preserve failure category and do not crash a whole report; Task 1 tests these cases.
5. Sensitive session metadata in reports: avoid window titles, key data, serial numbers and full environment dumps; Task 3 tests that prohibited fields never appear.

## File structure and shared schema
- `tools/xr_probe/__init__.py`: package marker only.
- `tools/xr_probe/model.py`: immutable dataclasses `ProbeResult`, `SessionCandidate`, `HostReport` and serialization helpers.
- `tools/xr_probe/commands.py`: bounded read-only subprocess runner and command allowlist.
- `tools/xr_probe/session.py`: enumerate/validate Hyprland candidates; never automatically guess between candidates.
- `tools/xr_probe/hardware.py`: sysfs USB/DRM discovery and sanitized compositor output normalization.
- `tools/xr_probe/report.py`: JSON schema v1 and human-readable capability report.
- `tools/xr_probe/__main__.py`: CLI entry point; `python -m tools.xr_probe`.
- `tests/probe/test_commands.py`, `test_session.py`, `test_hardware.py`, `test_report.py`, `test_cli.py`: standard-library tests, explicitly labeled synthetic fixtures in tests only.
- `docs/feasibility/host-report.json`, `host-report.md`: real observed local evidence, never fabricated fixtures.
- `docs/feasibility/backend-decision.md`: versioned source references, licenses, evidence matrix and next-milestone decision.
- `README.md`: diagnostic usage and honest current status.

`ProbeResult` fields: `status: Literal['ok','missing','permission_denied','timeout','error','truncated']`, `data: object | None`, `detail: str`. Never overload a command error as an empty successful result.
`SessionCandidate` fields: `instance: str`, `pid: int`, `wayland_socket: str | None`, `outputs: list[dict]`, `classification: Literal['physical','headless_only','unknown']`.
`HostReport` fields: `schema_version: int` (1), `collected_at: str` (UTC ISO timestamp), `host: dict`, `session: dict`, `usb: ProbeResult`, `drm: ProbeResult`, `capabilities: dict[str, dict]`.
Capability entries have `state: Literal['observed','advertised','untested','blocked','absent']`, `evidence: list[str]`, `reason: str`. Sustained 120 Hz and tracking quality are always `untested` in milestone A.

### Task 1: Bounded, read-only probe foundation

**Files:** Create `tools/xr_probe/__init__.py`, `model.py`, `commands.py`; test `tests/probe/test_commands.py`.

**Interfaces:**
- Produces `run_probe(argv: list[str], *, timeout_s: float = 5.0, max_bytes: int = 65536) -> ProbeResult`.
- Produces model types described above. Command policy permits only required read operations: `hyprctl instances -j`, explicit-instance `version -j`, `monitors all -j`, `systeminfo`; `uname -m`, `lspci`, and `pkg-config --modversion` with named dependencies. Omit systeminfo if it contains unnecessary sensitive data; version/monitor reads are sufficient. Reject all dispatch/keyword/reload/plugin/output mutation requests and arbitrary executable paths.

- [ ] Write tests asserting `run_probe(['hyprctl','reload']).status == 'error'` without invoking subprocess; missing command maps to `missing`; timeout maps to `timeout`; oversized stdout maps to `truncated`; stderr is bounded; fixtures never execute supplied shell metacharacters. Test a mocked nonzero exit does not return `ok`.
- [ ] Run `python -m unittest discover -s tests/probe -p 'test_commands.py' -v`; confirm failure from missing implementation.
- [ ] Implement models and command runner. Spawn no shell. Read subprocess pipes incrementally with bounded buffering; terminate and reap timed-out/oversized children. Allow an internal injectable runner for tests, not a CLI flag executing arbitrary commands.
- [ ] Rerun the same tests; require all PASS and no leftover child processes.
- [ ] Commit only foundation files and tests with `feat: add bounded read-only XR probe primitives`.

### Task 2: Explicit physical-session discovery

**Files:** Create `session.py`; test `tests/probe/test_session.py`.

**Interfaces:**
- Consumes `run_probe` and `SessionCandidate`.
- Produces `discover_sessions(run: Callable = run_probe) -> ProbeResult` containing candidate records.
- Produces `select_session(candidates: list[SessionCandidate], requested: str | None) -> ProbeResult` containing exactly one selected candidate or a clear blocked/error result. Default permits only one unambiguous physical candidate; selection of a headless-only candidate must fail with explanation.

- [ ] Write `test_ambiguous_physical_sessions_require_selection`: two physical candidates and no request returns `error`; no candidate is silently chosen. Write `test_headless_is_not_physical`, `test_missing_requested_instance`, `test_single_physical_selected`, `test_malformed_instances_json` and `test_output_probe_permission_denied`. Explicit-instance commands must contain exactly the selected identifier, with no ambient instance fallback.
- [ ] Run `python -m unittest discover -s tests/probe -p 'test_session.py' -v`; observe expected failures.
- [ ] Implement enumeration using installed Hyprland JSON shapes, validating types and missing fields. Use output descriptions/connector evidence to classify headless-only vs physical; mark uncertain cases unknown. No reading broad process environments or compositor window lists. Report session ambiguity to user if unavoidable.
- [ ] Run Task 1 and Task 2 tests with `python -m unittest discover -s tests/probe -v`; require PASS.
- [ ] Commit with `feat: identify explicit physical Hyprland session safely`.

### Task 3: Hardware inventory and truthful capability reporting

**Files:** Create `hardware.py`, `report.py`; test `test_hardware.py`, `test_report.py`.

**Interfaces:**
- Produces `probe_usb(sysfs_root: Path = Path('/sys/bus/usb/devices')) -> ProbeResult` with vendor/product IDs, product/model text and authorization status where readable; no serial numbers.
- Produces `probe_drm(sysfs_root: Path = Path('/sys/class/drm')) -> ProbeResult` with connector names/status, mode names and GPU device links. Plain sysfs mode strings without refresh metadata do not establish 120 Hz.
- Produces `build_report(session: ProbeResult, usb: ProbeResult, drm: ProbeResult, host: dict, *, collected_at: str) -> HostReport`.
- Produces `render_markdown(report: HostReport) -> str` and `to_json(report: HostReport) -> str`.

- [ ] Write tests using temporary synthetic sysfs trees: absent VITURE device returns `ok` with no matches; unreadable files remain explicit unknown/permission errors; malformed vendor IDs do not match `35ca`; multiple interfaces deduplicate by physical device path. Do not identify Beast/Luma Ultra from unverified product-ID mappings.
- [ ] Write report tests asserting advertised `120` capability never marks sustained presentation or tracking verified; active mode and available modes have distinct fields; empty EDID/mode data stays unknown; serialized JSON contains neither supplied `serial` nor `window_title` nor environment secrets. Verify escaped/sanitized text cannot add report headings or terminal control sequences.
- [ ] Run `python -m unittest discover -s tests/probe -v`; verify the new tests fail before implementation.
- [ ] Implement these interfaces; include source field paths in evidence strings and per-field uncertainty. Store only necessary sanitized host architecture/GPU/package versions. Do not read captured screen content or keyboard streams. Use actual current UTC collection time rather than a baked-in timestamp.
- [ ] Rerun all tests and require PASS.
- [ ] Commit with `feat: report XR capabilities without inferring hardware support`.

### Task 4: Executable diagnostic and real host evidence

**Files:** Create `__main__.py`, `tests/probe/test_cli.py`, `README.md`, real reports under `docs/feasibility/`.

**Interfaces:**
- CLI: `python -m tools.xr_probe --list-sessions`; or `python -m tools.xr_probe [--instance INSTANCE] --output-dir PATH`.
- Exit codes: 0 report collected; 2 physical-session ambiguity/invalid arguments; 3 host collection failure. Missing glasses may still return 0 with explicit absent/untested capabilities, never a support claim.
- CLI refuses overwriting existing reports unless `--overwrite` is supplied. Write complete reports via temporary file and atomic replacement; keep prior evidence versioned through Git.

- [ ] Write CLI tests for ambiguous sessions, bad output directory, existing report protection, missing glasses report, valid JSON schema version 1 and nonzero failure exit. Inject collectors in tests so synthetic fixtures are never published as real host evidence.
- [ ] Run `python -m unittest discover -s tests/probe -p 'test_cli.py' -v`; observe expected failures.
- [ ] Implement CLI entry point and usage/status documentation. Explicitly state the app is not yet implemented, reports are read-only, and current probes cannot validate tracking or sustained refresh.
- [ ] Run all tests and `python -m tools.xr_probe --help`; require PASS and documented flags.
- [ ] Run `python -m tools.xr_probe --list-sessions` on the live host. Select only verified physical user-session evidence; if ambiguous ask Cody instead of guessing.
- [ ] Run `python -m tools.xr_probe --instance <observed-instance> --output-dir docs/feasibility` using the literal observed identifier. Verify output files parse and findings correspond to raw read-only observations. If glasses are absent, request one pair at a time for a later fresh probe; retain honest partial evidence now.
- [ ] Commit diagnostic and sanitized evidence with `feat: expose XR diagnostic CLI and record real host evidence`.

### Task 5: Tracking/rendering decision and milestone-B plan

**Files:** Create `docs/feasibility/backend-decision.md` and `docs/superpowers/plans/<actual-date>-viture-native-spike.md`.

**Interfaces:** Consumes real Task 4 reports and approved design; produces a decision table with candidate/version, supported host architecture, source/license, interface and timestamp semantics, device coverage, outstanding blockers, and evidence status. No runnable SDK or renderer code in this task.

- [ ] Read actual installed Hyprland protocol/API documentation or matching source for virtual outputs, capture, input activity and focus. Record exact version and concrete APIs; do not treat generic Wayland documentation as proof of implementation support.
- [ ] Inspect candidate tracking SDK/driver source or public headers and licenses without executing downloaded code. Prefer authoritative device documentation over search snippets. Record licensing and whether reuse entails linking, IPC, redistribution or explicit user acceptance.
- [ ] Compare direct vendor SDK against XRLinuxDriver adapter and choose a provisional backend based on both-pair support, licensing, timestamp quality and integration risk. Mark untested Beast claims explicitly.
- [ ] Write the renderer feasibility decision: capture mechanism, independent-output creation, persistent XR presentation without input theft, expected GPU path, available synchronization, and fallback costs. List each unanswered fact as a test in milestone B, not as a fabricated success.
- [ ] Produce the next exact spike plan using `superpowers:writing-plans`, with selected language/API versions, files, interfaces, tests, real hardware measurements, changes requiring permission, crash rollback and stop conditions. Ensure production integration cannot begin until the spike proves the approved requirements feasible. If architecture must change, ask the user first.
- [ ] Review milestone-A claims against command evidence, run all diagnostic tests, verify `git diff --check`, and commit with `docs: record XR backend decision and native spike plan`.

## Self-review / coverage
The complete product requirements map to milestones A–F above; this plan intentionally does not pretend to specify a production renderer before feasibility. Every task here has an independent artifact and verification gate. ProbeResult/SessionCandidate/HostReport names and capabilities states are shared consistently. All five Review Focus risks have owning tests. No code is allowed to create virtual outputs, change a mode or capture content in this milestone. Milestone-B execution still requires plan review; diagnostics cannot constitute full application completion.

## Execution handoff
Recommended: subagent-driven, with one implementer and independent review per task. Session selection and capability truthfulness are safety boundaries worth reviewing early; tasks consume small explicit interfaces. Parent handles device-connect prompts and any authorization questions. Native execution is also possible, with a fresh final reviewer. Ask Cody to review this staged plan and choose execution approach before implementation.
