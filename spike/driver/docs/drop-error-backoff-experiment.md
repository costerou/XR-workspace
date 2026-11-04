# Short error-session cooldown experiment

Branch: `drop-error-backoff`. Base: `11a514659c0bde3d6ee1f8faf485f568a8601094`.
Worktree: `/home/cody/.t3/worktrees/viture-workspace/t3-6ab574b4`.

This implements a bounded mitigation for the supplied November 4 incident, not a claim that
retries caused the hub loss or that the underlying fault is fixed. No installed-driver hash or
raw incident evidence was independently analyzed here; that investigation belongs to the parent.
No live validation, installation, merge, or PR was performed. All USB interactions in the tests
use FakeUsbfs and a temporary fake sysfs/dev tree. Hidraw tests use a pty; daemon tests use fake
links or replay with temporary IPC paths. No live XR/tracking/unit/Hyprland control, physical
USB/hidraw access, sysfs writes, or sudo was used.

## Actual test-first evidence

Before any production edits, added
`test_short_error_sessions_keep_backoff_through_transport_link` to the existing transport suite.
It feeds a captured valid IMU packet to 0x81, then EPROTO to 0x85, with eight reads per endpoint.
The test calls the real TransportLink close after each gone result, matching daemon go_down().

Configured and built offline:

```sh
cmake -S spike -B /tmp/drop-error-backoff-build -G Ninja \
  -DXR_ENABLE_PRESENTATION=OFF -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build /tmp/drop-error-backoff-build --target luma_transport_test -j 4
/tmp/drop-error-backoff-build/driver/luma_transport_test
```

**RED:** executable exited 1; 30 failed checks, all in the new regression. Representative actual
output before the production change (line numbers from that first test version):

```text
FAIL .../transport_test.cpp:1754: !link.wait_for_device(hold - 15ms)
FAIL .../transport_test.cpp:1755: fake->opens() == opens
FAIL .../transport_test.cpp:1756: fake->claims() == claims
FAIL .../transport_test.cpp:1757: fake->submitted_endpoints().size() == submits
FAIL .../transport_test.cpp:1758: fake->inflight() == 0
FAIL test_short_error_sessions_keep_backoff_through_transport_link
30 failed checks
```

Full original output: `/tmp/drop-error-backoff-red.log` (local, untracked). This was an assertion
failure after a successful build, not a compilation failure. The payload had erased history,
allowing open/claim/queue before the expected progressively longer holds elapsed.

**GREEN:** after the transport change, the same executable exited 0 with `0 failed checks`
(`/tmp/drop-error-backoff-green.log`). Added a second invocation with `healthy_after = 60ms` to
exercise the configurable interval, then rebuilt and ran the complete configured CTest suite:

```sh
cmake --build /tmp/drop-error-backoff-build -j 4
ctest --test-dir /tmp/drop-error-backoff-build --output-on-failure
```

**Final suite result:** 17/17 passed, zero failures, 42.86 s; `luma_transport` took 14.51 s
and reported both new cases `ok` and `0 failed checks`. Full local output:
`/tmp/drop-error-backoff-ctest.log`. `git diff --check` also passed.

The regression checks literal scaled holds of 40, 80, 160, 320, 320, 320 ms (base 20 ms), no
open/claim/submit during each hold, and finite reopening afterward. It also checks that two
packets separated by more than the healthy interval do not qualify, continuous traffic clears
the penalty, EPIPE still permits same-device retry, interrupt wakes a cooldown immediately,
and observed disappearance clears the penalty for the new enumeration. Every ioctl observed
by this fake is checked against the existing allowlist. Existing tests also cover stalled EPIPE
retries, first-open recovery, timeout/interrupt behavior, forbidden ioctls, and daemon wrapper
silent-close behavior. Tests use real monotonic time with scaled delays, so extreme scheduler
stalls can cause timing failures; they are not live USB timing measurements.

## Review scope and risks

Production change: one config duration, sustained receipt tracking, a saturating failure counter,
and corrected EPIPE diagnostic wording. Existing settle/rescan/poll and teardown paths remain
in use. No endpoint removed; no reset, clear-halt, configuration, or identity/quarantine mechanism
added. Default short-session delays now reach 32 seconds, potentially delaying recovery after
transient errors. Two seconds of payload traffic clears history only if receipt gaps stay at or
below 100 ms. This is payload-level evidence, not semantic IMU validity. Scheduler delays can
keep the penalty active longer.

The daemon's read-error close is a no-op in TransportLink, preserving this history. Watchdog
silence still closes an up transport and resets it; command/write failure paths that close an
up-marked wrapper can also reset it. Those are not solved here. Disappearance must be observed
by a scan to clear history; an enumeration change entirely between scans is not specially detected.
Fresh first opens remain immediate, and observed rearrival retains the normal base settle.
See [transport.md](transport.md) for the exact behavior and limitations.
