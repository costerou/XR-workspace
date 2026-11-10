#!/bin/sh
# The driver's wake-ups and CPU on the real glasses (docs/performance.md, "Wake-ups"; rust-rebuild-plan D10).
#
#   spike/driver/tools/wake_check.sh [BUILD_DIR] [SECONDS]
#
# Runs xr-luma-driver on the glasses, lets it settle for 15 s, then counts its context switches (/proc/PID/status) and CPU time
# (/proc/PID/stat) over SECONDS (default 60). Passes at 1,100 switches per second or fewer and 0.3 % of one core or less.
# Everything goes to a fresh folder under ${TMPDIR:-/tmp}: a private feed and its own gyro bias, so the live feed and the real
# bias file are not touched. Run it with XR stopped: only one driver may hold the glasses.
set -eu

build=${1:-build/release}
seconds=${2:-60}
driver=$build/driver/xr-luma-driver
[ -x "$driver" ] || { echo "missing $driver: make -C $build xr-luma-driver" >&2; exit 2; }
if pgrep -x xr-luma-driver >/dev/null || pgrep -x xrDriver >/dev/null; then
    echo "a head-tracking driver is running: stop XR first" >&2
    exit 2
fi
if ! grep -qsx 35ca /sys/bus/usb/devices/*/idVendor; then
    echo "no VITURE device on USB: plug the glasses in" >&2
    exit 2
fi

out=$(mktemp -d "${TMPDIR:-/tmp}/luma-wakes.XXXXXX")
"$driver" --ipc-prefix "$out/ipc_" --state-dir "$out" 2>"$out/driver.log" &
pid=$!
trap 'kill -TERM $pid 2>/dev/null || true' EXIT INT TERM

sample() {  # voluntary involuntary cpu_ticks, for the whole process (it has one thread)
    v=0; n=0
    for task in /proc/$pid/task/*; do
        v=$((v + $(awk '/^voluntary_ctxt_switches/ {print $2}' "$task/status")))
        n=$((n + $(awk '/^nonvoluntary_ctxt_switches/ {print $2}' "$task/status")))
    done
    # utime and stime are fields 14 and 15; the name in field 2 has no spaces here.
    echo "$v $n $(awk '{print $14 + $15}' /proc/$pid/stat)"
}

sleep 15
kill -0 $pid 2>/dev/null || { echo "driver ended early, see $out/driver.log" >&2; exit 1; }
grep -q "luma: calibrated" "$out/driver.log" || { echo "not calibrated after 15 s, see $out/driver.log" >&2; exit 1; }
set -- $(sample); v0=$1 n0=$2 c0=$3
t0=$(date +%s.%N)
sleep "$seconds"
set -- $(sample); v1=$1 n1=$2 c1=$3
t1=$(date +%s.%N)
kill -TERM $pid
wait $pid || true
trap - EXIT INT TERM

hz=$(getconf CLK_TCK)
rate=$(grep "luma: stats" "$out/driver.log" | tail -1 | sed -n 's/.* rate_hz=\([0-9]*\).*/\1/p')
awk -v v="$((v1 - v0))" -v n="$((n1 - n0))" -v c="$((c1 - c0))" -v t0="$t0" -v t1="$t1" -v hz="$hz" -v rate="${rate:-0}" -v out="$out" 'BEGIN {
    s = t1 - t0
    sw = (v + n) / s; cpu = 100 * c / hz / s
    printf "wakes: %.0f s, switches/s %.0f (voluntary %.0f, involuntary %.0f), cpu %.2f %%, samples/s %s\n", s, sw, v / s, n / s, cpu, rate
    printf "json {\"seconds\":%.1f,\"switches_per_s\":%.1f,\"voluntary_per_s\":%.1f,\"involuntary_per_s\":%.1f,\"cpu_percent\":%.3f,\"rate_hz\":%s}\n", s, sw, v / s, n / s, cpu, rate
    ok = sw <= 1100 && cpu <= 0.3
    printf "%s (target: switches/s <= 1100, cpu <= 0.3 %%), logs in %s\n", ok ? "PASS" : "FAIL", out
    exit ok ? 0 : 1
}'
