# Soak reports

Stored soak reports of the Luma Ultra, for the Rust rebuild's drop gate (`docs/rust-rebuild-plan.md`, D13): the Rust
driver must show no more USB drops than the C++ driver over equal hours. This folder holds the C++ "before" and the Rust
"after"; `tools/luma_soak/soak.sh rust` compares them and prints the gate's result.

## Results

| Date | Driver | Hours | Drops | Drops per hour | Valid | Report |
|---|---|---|---|---|---|---|
| 2026-11-01 to 03 | XRLinuxDriver, mixed use, kernel log only | 72.1 (25.5 with glasses on) | 54 | 0.75 over the boot, up to 3.2 in use | replay: can count, not prove | `tests/luma_soak/fixtures/kernel_boot_2026-11-01_03.log` |
| _pending_ | C++ `xr-luma-driver` | 3 | | | | `cpp-baseline-<stamp>.report.json` |
| _pending_ | Rust `xr-luma-driver` | 3 | | | | `rust-soak-<stamp>.report.json` |

Fill a row from the summary line the script prints. A drop is the thing counted, so a run with drops is still a valid
baseline. "Valid" means every other condition of `luma_soak gate` held: at least 3 hours, glasses on USB at both ends,
`xr-luma-driver` running and the pose feed advancing for 95% of the run, the kernel log readable at the end, no suspend,
and only the named build running. Both builds are called `xr-luma-driver`, so the soak records the binary each driver
process runs (`/proc/PID/exe`, `coverage.driver_exes`). A path with a `rust` folder in it is the Rust build
(`bin/<hash>/rust/xr-luma-driver`, `rust/target/...`); anything else is the C++ one. A Rust soak with no binary on record
is not valid. A C++ one is, because sessions recorded before 2026-11-10 hold no binary and could only run the C++ driver.

## Running the C++ baseline

Read-only. It starts, stops and sends nothing; it reads the kernel log, sysfs, `/proc`, the driver's log and the pose
feed. It needs no root.

1. Check no XRLinuxDriver override is set; `ls ~/.config/viture-workspace/tracking-override.json` must say no such
   file. Without it a Luma session runs the installed C++ `xr-luma-driver`.
2. Start a normal Luma Ultra session from the panel and put the glasses on. Wait for tracking.
3. In a terminal, from the repo root:

       tools/luma_soak/cpp_baseline.sh

   It watches for 3 hours (10,800 s). Use the machine as usual. Keep it awake: a suspend makes the run not valid.
   Do not stop the session, unplug the glasses or switch to the Pro 2 during the run.
4. At the end it prints one line, for example
   `cpp baseline: VALID: 2 drop(s) in 10800.4 s, 0.67 per hour; report docs/soak/cpp-baseline-20261111-200000.report.json`,
   and stores `.report.json` and `.report.txt` here, with the host name replaced by `host`.

Exit 0 is a valid measurement. Exit 2 is not; the reasons are printed under the summary line and in the report.

If the run is cut short (Ctrl-C, a crash, a reboot), the session file in `~/.local/state/luma_soak/` still holds every
record. Judge and store it with `tools/luma_soak/cpp_baseline.sh --session ~/.local/state/luma_soak/cpp-baseline-<stamp>.jsonl`.
It is still judged against the full 3 hours: a shorter run is stored but marked not valid. One unbroken run is the
baseline; shorter runs are not added together.

## Running the Rust soak, and the gate

The same soak with the Rust driver pinned, then the comparison. Run it only after a valid C++ baseline is stored here.

1. Build and pin the Rust driver: `cargo build --release -p xr-luma-driver` in `rust/`, then
   `python3 -m tools.xr_controller install --build <build dir> --rust-driver rust/target/release/xr-luma-driver`.
   `tracking-override` must not be set (step 1 above); `luma-cpp` would run the C++ driver.
2. Start a Luma Ultra session and wait for tracking, as for the baseline. The panel names the driver in use.
3. From the repo root:

       tools/luma_soak/soak.sh rust

   It watches for 3 hours, stores `rust-soak-<stamp>.report.json` and `.report.txt` here, and prints
   `rust soak: VALID: ...` as the baseline does. Then it compares the report with the newest `cpp-baseline-*.report.json`
   here (or `LUMA_SOAK_BASELINE=FILE`) and prints `drop gate: PASS`, `FAIL` or `INCONCLUSIVE` with one line why.
4. Back to the C++ driver at any time: `python3 -m tools.xr_controller tracking-override set luma-cpp`.

| Exit | Gate | Meaning |
|---|---|---|
| 0 | PASS | Both soaks valid, and the Rust drops are no more than the C++ drops scaled to the Rust hours |
| 1 | FAIL | Both valid, and the Rust driver dropped more |
| 2 | INCONCLUSIVE | No stored baseline, either soak not valid, the wrong build in either, or the Rust soak under 95% of the baseline's hours |

The same comparison from two stored reports: `python3 -m tools.luma_soak compare --baseline FILE --candidate FILE`.
Only a PASS lets the Rust driver become the default (plan phase 2). At about 3 drops per hour one 3-hour pair is noisy
(plan, Risks): a FAIL by one or two drops is worth a second pair of runs before the port is blamed.

## What the report holds about the driver

Both drivers write the same `luma: <word> key=value ... at=<epoch s>` lines to `~/.local/state/viture-workspace/luma-driver.log`
(`tools/native_spike/tracking.py` sends its stderr there). The soak reads them:

| Driver line | Read as |
|---|---|
| `luma: stale ms=N` | a sample gap of N ms, a candidate cause in the 5 s before a drop |
| `luma: disconnected reason=R` | the driver losing the glasses (`driver_events.disconnects`) |
| `luma: retry attempt=N reason=R` | a reconnect attempt |
| `luma: reconnected gap_ms=N` | a reconnect; `driver_events.reconnect_gap_ms` gives the average and worst, the baseline for D8 |
| `connected`, `calibrating`, `calibrated`, `waiting`, `fresh`, `stopped` | state |

The `Back on USB` figure is the kernel's time to bring the glasses back; `reconnect_gap_ms` is the driver's own gap,
kernel time plus its timers.
