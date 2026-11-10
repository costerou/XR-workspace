# Soak reports

Stored soak reports of the Luma Ultra, for the Rust rebuild's drop gate (`docs/rust-rebuild-plan.md`, D13): the Rust
driver must show no more USB drops per hour than the C++ driver over equal hours. This folder holds the C++ "before".

## Results

| Date | Driver | Hours | Drops | Drops per hour | Valid | Report |
|---|---|---|---|---|---|---|
| 2026-11-01 to 03 | XRLinuxDriver, mixed use, kernel log only | 72.1 (25.5 with glasses on) | 54 | 0.75 over the boot, up to 3.2 in use | replay: can count, not prove | `tests/luma_soak/fixtures/kernel_boot_2026-11-01_03.log` |
| _pending_ | C++ `xr-luma-driver` | 3 | | | | `cpp-baseline-<stamp>.report.json` |

Fill a row from the summary line the script prints. A drop is the thing counted, so a run with drops is still a valid
baseline. "Valid" means every other condition of `luma_soak gate` held: at least 3 hours, glasses on USB at both ends,
`xr-luma-driver` running and the pose feed advancing for 95% of the run, the kernel log readable at the end, no suspend.

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

## What the report holds about the C++ driver

The driver writes `luma: <word> key=value ... at=<epoch s>` lines to `~/.local/state/viture-workspace/luma-driver.log`
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
