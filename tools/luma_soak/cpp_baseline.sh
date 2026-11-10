#!/bin/sh
# The 3-hour baseline soak of the shipped C++ xr-luma-driver (docs/rust-rebuild-plan.md D13, "before").
# Read-only, like luma_soak itself: it starts, stops and sends nothing. Start a Luma session first.
#
#   tools/luma_soak/cpp_baseline.sh                  watch for 3 h, then store the report in docs/soak/
#   tools/luma_soak/cpp_baseline.sh --session FILE   judge and store a session already recorded (cut short or killed)
#
# Any further arguments go to `luma_soak gate`. Exit 0: a valid measurement, whatever the drop count, because a drop is
# the thing being counted, not a failure. Exit 2: the soak proved nothing (too short, glasses or driver absent, feed
# frozen, kernel log unreadable, suspend); the stored report says why.
# LUMA_SOAK_SECONDS (default 10800) and LUMA_SOAK_OUT (default docs/soak) change the length and where it is stored.
set -u
cd "$(dirname "$0")/../.." || exit 2
seconds=${LUMA_SOAK_SECONDS:-10800}
out=${LUMA_SOAK_OUT:-docs/soak}
state=${XDG_STATE_HOME:-$HOME/.local/state}
py=${PYTHON:-/usr/bin/python3}
# Any number of drops still makes a valid measurement; every other condition of the gate must hold.
judge="--min-duration $seconds --budget 1000000"

if [ "${1:-}" = "--session" ]; then
    session=${2:?--session needs a file}
    shift 2
else
    mkdir -p "$state/luma_soak"
    session=$state/luma_soak/cpp-baseline-$(date +%Y%m%d-%H%M%S).jsonl
    trap ':' INT TERM   # Ctrl-C stops the watch (the monitor records why), and the report is still stored
    # shellcheck disable=SC2086
    "$py" -m tools.luma_soak gate --duration "$seconds" --session "$session" --driver-name xr-luma-driver \
        --driver-log "$state/viture-workspace/luma-driver.log" $judge "$@" > /dev/null
    trap - INT TERM
fi

mkdir -p "$out"
name=$(basename "$session" .jsonl)
scrub() { host=$(cat /proc/sys/kernel/hostname 2>/dev/null); if [ -n "$host" ]; then sed "s/$host/host/g"; else cat; fi; }
# shellcheck disable=SC2086
"$py" -m tools.luma_soak gate --session "$session" $judge --json "$@" | scrub > "$out/$name.report.json"
# shellcheck disable=SC2086
"$py" -m tools.luma_soak gate --session "$session" $judge "$@" 2>/dev/null | scrub > "$out/$name.report.txt" || true
"$py" - "$out/$name.report.json" <<'EOF'
import json, sys
try:
    report = json.load(open(sys.argv[1]))
except ValueError:
    print(f'cpp baseline: NOT VALID: the soak could not be judged; see {sys.argv[1]}')
    sys.exit(2)
valid = report['verdict']['exit'] == 0
print(f"cpp baseline: {'VALID' if valid else 'NOT VALID'}: {report['drops']} drop(s) in {report['duration_s']} s, "
      f"{report['drops_per_hour']} per hour; report {sys.argv[1]}")
for reason in report['verdict']['reasons']:
    print(f'  {reason}')
sys.exit(0 if valid else 2)
EOF
