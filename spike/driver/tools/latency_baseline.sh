#!/bin/sh
# The real-glasses USB latency baseline (docs/performance.md, "Real glasses: USB latency"; rust-rebuild-plan D1).
#
#   spike/driver/tools/latency_baseline.sh [BUILD_DIR] [SECONDS]
#
# Runs xr-luma-driver on the glasses twice, SECONDS each (default 120): first with the machine idle, then with one busy loop
# per CPU. Each run logs every IMU packet's URB completion time and device time, and luma_latency_report summarises it.
# Everything goes to a fresh folder under ${TMPDIR:-/tmp}. The driver writes a private feed and keeps its gyro bias in that
# folder too, so the live feed and the real bias file are not touched.
#
# Run it with XR stopped: it refuses while any head-tracking driver is up, because only one may hold the glasses.
set -eu

build=${1:-build/release}
seconds=${2:-120}
driver=$build/driver/xr-luma-driver
report=$build/driver/luma_latency_report
for b in "$driver" "$report"; do
    [ -x "$b" ] || { echo "missing $b: make -C $build xr-luma-driver luma_latency_report" >&2; exit 2; }
done
if pgrep -x xr-luma-driver >/dev/null || pgrep -x xrDriver >/dev/null; then
    echo "a head-tracking driver is running: stop XR first" >&2
    exit 2
fi
if ! grep -qsx 35ca /sys/bus/usb/devices/*/idVendor; then
    echo "no VITURE device on USB: plug the glasses in" >&2
    exit 2
fi

out=$(mktemp -d "${TMPDIR:-/tmp}/luma-latency.XXXXXX")
busy=""
cleanup() { [ -z "$busy" ] || kill $busy 2>/dev/null || true; }
trap cleanup EXIT INT TERM

run() {  # label
    echo "== $1: ${seconds} s" >&2
    # SIGINT is the driver's clean stop: it flushes the log. 124 is timeout's own exit for "it ran the whole time".
    status=0
    timeout --signal=INT --kill-after=10 "$seconds" \
        "$driver" --ipc-prefix "$out/ipc_" --state-dir "$out" --latency-log "$out/$1.lat" 2>"$out/$1.driver.log" || status=$?
    [ "$status" -eq 0 ] || [ "$status" -eq 124 ] || { echo "driver exited $status, see $out/$1.driver.log" >&2; exit 1; }
    grep -m1 "luma: scheduling" "$out/$1.driver.log" >&2 || true
    "$report" "$out/$1.lat" --label "$1" >"$out/$1.report" || { cat "$out/$1.report"; echo "no report for $1" >&2; exit 1; }
    cat "$out/$1.report"
}

run idle
cpus=$(nproc)
i=0
while [ "$i" -lt "$cpus" ]; do
    sh -c 'while :; do :; done' &
    busy="$busy $!"
    i=$((i + 1))
done
run loaded
cleanup
busy=""
echo "== results in $out" >&2
