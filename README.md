# VITURE workspace feasibility

**The XR workspace app is not implemented.** This repository currently contains a local, read-only Linux/Hyprland diagnostic. It does not create virtual monitors, capture screens, initialize an SDK, track heads, route input, or change display modes. Beast and Luma Ultra support has not been established.

## Per-desktop placement (offline-test-qualified only)

Each XR desktop now has its own placement in the glasses view: left/right and down/up offset, distance (size in space), yaw, pitch, roll and horizontal curvature. Implemented across the renderer (`spike/src/view.cpp` geometry, `gl.cpp` curved/rotated meshes, `placement` control op), supervisor (`tools/native_spike/view.py`), controller (`tools/xr_controller/state.py`) and panel (`PlacementEditor.qml`, in the startup desktop editor and live in the running view). Placements are saved per glasses model in `profiles.json` and can change during a run without new consent; desktop resolution and scale stay fixed while running.

**Qualification: offline tests only.** Covered by CTest (`view`, `ipc`), the Python suites (`tests/native_spike/test_placement.py`, `tests/xr_controller/test_state.py`), `qmltestrunner` and offscreen panel renders. It has **not** been run in the isolated VM and **not** been accepted on physical glasses: the GPU mesh path, curved-desktop readability, comfort and any frame-time cost are unmeasured. The CPU fallback renderer (no GPU composition) ignores placements. Overlapping desktops are drawn back to front by centre distance and are not split where they intersect.

### Installing a build into the live panel (and rolling back)

Only with Cody's approval, with no XR session running (`systemctl --user list-units 'viture-xr-*'` empty, `hyprctl plugin list` shows none):

1. Back up `~/.local/share/viture-workspace/installed.json`, `~/.config/systemd/user/viture-workspace-controller.service` and the target of `~/.config/omarchy/plugins/cody.viture-workspace`.
2. Stage the new `xr-native-spike` with the currently pinned `libxr-ownership.so` (keeps the accepted plugin binary) and run, from the checkout the controller should use: `python3 -m tools.xr_controller install --build <stage>`. This pins both files and points the controller unit at that checkout; it never enables or starts a session.
3. Point the panel symlink at that checkout's `omarchy-plugin/cody.viture-workspace`, then `systemctl --user restart viture-workspace-controller.service`.
4. **Run `omarchy-restart-shell`.** `omarchy-shell shell rescanPlugins` does not reload QML the shell has already loaded, so without a shell restart the panel keeps showing the old UI.
5. Record the pinned sha256s; check the controller's command line names the intended checkout.

Rollback: restore the backed-up `installed.json` and unit file, `systemctl --user daemon-reload`, point the symlink back, restart the controller, then **run `omarchy-restart-shell`** again.

## Run the diagnostic

Use Python 3.10+ from the repository root; the probe uses only the standard library. No dependency installation is required. Existing host tools may be missing: diagnostics preserve uncertainty rather than installing anything.

```sh
python -m tools.xr_probe --help
python -m tools.xr_probe --list-sessions
python -m tools.xr_probe --instance INSTANCE --output-dir docs/feasibility
```

Replace `INSTANCE` with the **literal observed identifier** from `--list-sessions`. Only a session classified `physical` may be selected. Without `--instance`, collection is permitted only when exactly one physical candidate exists and no unknown candidate makes selection ambiguous. Headless-only sessions are never selected. Session listing prints JSON with identifiers and classifications, does not collect USB/DRM/host metadata, and cannot be combined with collection options.

Reports are `host-report.json` (schema version 1) and `host-report.md` (escaped human-readable inventory). The collection timestamp is actual UTC wall-clock time at report construction, not a performance measurement. Reports are sanitized: no window titles, serial-number fields, process/socket/instance identifiers, raw error output, or environment dump is persisted. Listing identifiers to the local terminal is intentional for explicit selection.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Reports collected with successful top-level USB/DRM inventories, or session listing/help succeeded. Missing glasses and unknown optional metadata can still return 0. This is **not** a support or performance verdict. |
| `2` | Invalid/missing arguments; ambiguous, absent, nonphysical, or unmatched session selection; existing report without `--overwrite`; unsafe report target; or existing writer/recovery lock. |
| `3` | Session discovery failed, required top-level inventory failed, host collection raised an error, or output-directory/report writing failed. When an inventory returns a failure status, a sanitized partial report is still written and exit code is 3. Other failures may produce no reports. |

Per-field unknown/missing/error statuses do not necessarily fail the top-level inventory: inspect the report, not just the exit code. Optional architecture/GPU/version probe failures stay unknown and do not by themselves change a successful collection exit code.

### Preserve previous evidence

Existing reports are refused unless explicitly authorized:

```sh
# Review and commit the old evidence in Git before replacement.
python -m tools.xr_probe --instance INSTANCE --output-dir docs/feasibility --overwrite
```

Prefer separate directories for later pair-specific collections so the baseline remains intact. Only perform those collections after the user confirms which pair is connected; the baseline does not establish either pair's identity.

Both new files are completely staged and fsynced before either destination is replaced. Each replacement is atomic; on ordinary write/rename failure the old pair is restored (or newly created partial output removed). A `.xr-probe.lock` directory serializes cooperating CLI writers. Report symlinks and non-regular targets are refused even with `--overwrite`. Use a local filesystem supporting hard links and atomic rename, and a trusted output directory.

**The pair is not a single filesystem transaction.** Readers should wait for the CLI to finish. Power loss, forced termination, external writers ignoring the lock, or a second failure during rollback can leave a mixed pair. The lock/staging directory retains backups when rollback fails; inspect/recover those before removing the lock or running again. Do not blindly delete recovery files. Git remains the durable history, not the temporary rollback mechanism.

## What the evidence means

- USB discovery matches vendor `35ca` without guessing Beast/Luma Ultra from a product ID. Successful empty USB inventory means no matching USB device was observed, not proof that no glasses/display transport exists.
- DRM lists connector status and mode names. Mode names without refresh information do not establish 120 Hz support.
- Hyprland active width/height/refresh is compositor-reported state, **not measured presentation or sustained refresh**. A desktop monitor reporting a refresh rate is not attributed to glasses.
- Available refresh-bearing modes and USB/DRM/output-to-glasses associations are not collected automatically by the current session probe; its report fields remain unknown. Separately collected, user-confirmed pair observations below supply supplemental mode tokens and pair attribution, not a general association algorithm.
- Tracking quality and sustained 120 Hz always remain untested; no SDK or timing measurements occur.
- Host metadata uses only existing allowlisted `uname -m`, filtered `lspci` GPU lines, selected-instance `hyprctl version -j`, and `pkg-config --modversion libdrm`. Hyprland's value is its runtime version; libdrm's is development metadata, not a package-manager release. Mesa, Linux package, and nvidia-utils versions remain unknown rather than guessed from unrelated sources.

The versioned baseline in `docs/feasibility/host-report.{json,md}` was collected on **2026-10-31 at 15:09:31 UTC**. It observed one physical Samsung Odyssey G93SD output at compositor-reported 5120×1440 / 240 Hz, and no matching VITURE USB device. This is host-only evidence, not a Beast or Luma Ultra acceptance result. At baseline collection time, pair-specific enumeration was still pending; that historical snapshot remains unchanged.

**Current stage: milestone A read-only pair enumeration and backend decision are complete; milestone B is a future, unapproved execution plan.** See:

- [Luma Ultra observations and report](docs/feasibility/luma-ultra/): active compositor-reported **1920×1080 at 60 Hz**; a separate explicit-session observation advertised `1920x1200@120.00Hz`, **not 1080p120**. The CLI did not collect those available-mode tokens.
- [Connected Beast observations and report](docs/feasibility/beast-connected/): active compositor-reported **1920×1080 at 120 Hz**, not measured XR presentation cadence. Supplemental advertisements distinguish `120.00Hz` from `119.88Hz`.
- [First, unsuccessful Beast enumeration](docs/feasibility/beast/): retained separately; no matching USB device or second display was observed in that attempt.
- [Backend decision and outstanding gates](docs/feasibility/backend-decision.md) and [future native-spike plan](docs/superpowers/plans/2026-10-31-viture-native-spike.md).

Tracking, sustained presentation/performance and runtime integration remain **untested and unapproved**. No native spike, SDK initialization, input monitoring or device/mode change is authorized by these documents or by passing the diagnostic tests.

## Tests

Canonical full-suite command (root discovery does not find this suite):

```sh
python -m unittest discover -s tests/probe -v
```

CLI-only tests:

```sh
python -m unittest discover -s tests/probe -p 'test_cli.py' -v
python -m compileall -q tools/xr_probe tests/probe
```

Tests inject explicitly synthetic collectors and use temporary directories; their reports are never published as real host evidence. Live reports must be generated through the CLI. No package installation, udev edits, service shutdown, firmware update, display reconfiguration, screen capture, or hardware connection prompt is part of this baseline diagnostic.
