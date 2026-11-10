# Heading-drift recordings (plan D6)

Two real recordings of the Luma Ultra's IMU, lying still on a desk, for learning and testing a gyro bias
model against temperature. Heading drift is judged on these by replay.

| File | What | Must show |
|---|---|---|
| `desk-still.lcap.xz` | 20 minutes still, glasses already warm | Length, no lost samples, no movement. Its temperature span is recorded, whatever it is |
| `warm-up.lcap.xz` | 20 minutes still from a cold plug-in | All of the above, plus a stream that began within 2 minutes of power-up and a rise of at least 5 °C |

Each `.json` beside it holds the file's SHA-256, a summary (samples, lost, temperature at start and end, span,
gyro bias at start and end, how far gravity moved, the worst gyro jolt, the marks) and the problems found, which
must be none. `tests/luma_capture/test_drift.py` (`StoredFixtures`) recomputes the summary from the file and fails
if it differs, if either kind is missing, or if a problem is found. It skips while this folder holds no recordings.

## What is in them

Only the IMU stream on endpoint `0x81` (28-byte packets, layout in `spike/driver/docs/capture-findings.md`) and the
`clock`, `mark`, `usbmon_stats`, `glasses_gone`, `glasses_back` and `stop` notes. No commands, replies, descriptors,
serial, driver or kernel log, host name or URB ids. Host times are the kernel's, as recorded. The format is
`docs/capture-format.md`, xz-compressed: about 10 MiB per 20 minutes instead of about 90.

## Using them

```sh
python3 -m tools.luma_capture drift-summary rust/corpus/drift/warm-up.lcap.xz --kind warm-up   # reads .xz directly
xz -dc rust/corpus/drift/warm-up.lcap.xz > /tmp/warm-up.lcap                               # for the C++ and Rust replays
build/release/driver/luma_replay_report /tmp/warm-up.lcap --every 60
```

## Recording them again

`tools/luma_capture/record_drift.sh desk-still|warm-up` records one with XR off, our own `xr-luma-driver`
streaming into a private pose feed, then writes the fixture and its summary here. The steps, with what each needs,
are in `tools/luma_capture/README.md` under "Drift recordings".
