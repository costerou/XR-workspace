#!/usr/bin/env bash
# Records one heading-drift fixture (plan D6) with our own C++ driver streaming the IMU and the glasses on the desk.
#
#   tools/luma_capture/record_drift.sh desk-still|warm-up [BUS]
#
# Run from the repository root, with XR off and usbmon readable (python3 -m tools.luma_capture check).
# warm-up: the glasses must be UNPLUGGED when this starts, and cold (unplugged for at least an hour). It waits for
#          them to be plugged in, then starts the driver at once.
# desk-still: the glasses plugged in and warm (after at least 20 minutes of use, or right after a warm-up).
# Either way: lay them flat on the desk, arms open, and touch neither them nor the desk until it says done.
#
# It starts only the recorder and one xr-luma-driver of its own, in its own user and network namespace, writing its
# pose feed to the run folder, never the live one. It stops both itself. It never starts, stops or touches XR.
set -euo pipefail

kind=${1:-}
bus=${2:-5}
case "$kind" in desk-still|warm-up) ;; *) echo "usage: $0 desk-still|warm-up [BUS]" >&2; exit 2 ;; esac
[[ -f tools/luma_capture/drift.py ]] || { echo 'run this from the repository root' >&2; exit 2; }

bin=${LUMA_DRIVER:-$PWD/build/release/driver/xr-luma-driver}
seconds=${DRIFT_SECONDS:-1290}     # 20 minutes, plus the driver's start and a margin
run="$HOME/.local/state/viture-workspace/luma-drift/$(date +%F-%H%M%S)-$kind"
out="rust/corpus/drift/$kind.lcap.xz"

[[ -x "$bin" ]] || { echo "no driver at $bin: build it (docs/technical.md) or set LUMA_DRIVER" >&2; exit 1; }
if pgrep -x xrDriver >/dev/null || pgrep -x xr-luma-driver >/dev/null; then
    echo 'a tracking driver is running: turn XR off first. This script does not stop it.' >&2; exit 1
fi
python3 -m tools.luma_capture check --bus "$bus" >/dev/null || { python3 -m tools.luma_capture check --bus "$bus"; exit 1; }
glasses() { python3 -c 'import sys; from tools.luma_capture.usbmon import find_glasses; sys.exit(0 if find_glasses() else 1)'; }
if [[ $kind == warm-up ]] && glasses; then
    echo 'warm-up needs the glasses unplugged (and cold) when it starts. Unplug them, wait an hour, run again.' >&2; exit 1
fi
if [[ $kind == desk-still ]] && ! glasses; then
    echo 'desk-still needs the glasses plugged in and warm.' >&2; exit 1
fi

mkdir -p "$run"
echo "Run folder: $run"
python3 -m tools.luma_capture record --bus "$bus" --out "$run/capture" --segment 300 --keep-segments 20 \
    --max-gib 4 --duration "$seconds" > "$run/recorder.log" 2>&1 &
recorder=$!
driver=
stop() {
    [[ -n $driver ]] && kill -TERM "$driver" 2>/dev/null || true
    kill -TERM "$recorder" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap 'echo; echo stopped early: the recording is not a fixture; stop; exit 130' INT TERM
sleep 2
kill -0 "$recorder" 2>/dev/null || { cat "$run/recorder.log"; echo 'the recorder did not start' >&2; exit 1; }

if [[ $kind == warm-up ]]; then
    echo 'Plug the cold glasses in now, lay them flat on the desk, and let go.'
    until glasses; do sleep 0.2; done
    sleep 2     # enumeration
fi
unshare --user --map-current-user --net -- "$bin" --ipc-prefix "$run/feed_" 2>> "$run/driver.log" &
driver=$!
python3 -m tools.luma_capture mark "$kind: on the desk, driver started" --out "$run/capture"
echo "Recording $kind for $seconds s. Do not touch the glasses or the desk. Ctrl+C abandons it."
sleep 10
if ! kill -0 "$driver" 2>/dev/null; then
    tail -n 20 "$run/driver.log"; echo 'the driver stopped: no fixture' >&2; stop; exit 1
fi
python3 -m tools.luma_capture drift-summary "$run/capture" 2>&1 | head -3 || true
wait "$recorder" || true
kill -TERM "$driver" 2>/dev/null || true
wait "$driver" 2>/dev/null || true
trap - INT TERM
echo 'Recording done. Making the fixture (about a minute):'
python3 -m tools.luma_capture drift-fixture "$run/capture" --kind "$kind" -o "$out"
