#!/bin/sh
# The 3-hour soak of one build of xr-luma-driver (docs/rust-rebuild-plan.md D13): `cpp` is the "before", `rust` the "after".
# Read-only, like luma_soak itself: it starts, stops and sends nothing. Start a Luma session on that build first.
#
#   tools/luma_soak/soak.sh cpp|rust                  watch for 3 h, then store the report in docs/soak/
#   tools/luma_soak/soak.sh cpp|rust --session FILE   judge and store a session already recorded (cut short or killed)
#
# Any further arguments go to `luma_soak gate`. The soak counts only the named build: the driver's binary path tells them
# apart (the Rust pin sits in bin/<hash>/rust/), so a session that ran the other one is not valid.
# cpp exit: 0 a valid measurement, whatever the drop count (a drop is the thing counted, not a failure); 2 the soak proved
# nothing (too short, glasses or driver absent, wrong build, feed frozen, kernel log unreadable, suspend).
# rust exit: the drop gate, `luma_soak compare` against the C++ baseline: 0 no more drops over equal hours, 1 more drops,
# 2 either soak proved nothing or there is no baseline yet. LUMA_SOAK_BASELINE names the baseline report; default the
# newest docs/soak/cpp-baseline-*.report.json.
# LUMA_SOAK_SECONDS (default 10800) and LUMA_SOAK_OUT (default docs/soak) change the length and where it is stored.
set -u
cd "$(dirname "$0")/../.." || exit 2
build=${1:-}
case $build in
    cpp) prefix=cpp-baseline label='cpp baseline' ;;
    rust) prefix=rust-soak label='rust soak' ;;
    *) echo 'usage: tools/luma_soak/soak.sh cpp|rust [--session FILE] [gate options]' >&2; exit 2 ;;
esac
shift
seconds=${LUMA_SOAK_SECONDS:-10800}
out=${LUMA_SOAK_OUT:-docs/soak}
state=${XDG_STATE_HOME:-$HOME/.local/state}
py=${PYTHON:-/usr/bin/python3}
# Any number of drops still makes a valid measurement; every other condition of the gate must hold.
judge="--min-duration $seconds --budget 1000000 --driver-build $build"

if [ "${1:-}" = "--session" ]; then
    session=${2:?--session needs a file}
    shift 2
else
    mkdir -p "$state/luma_soak"
    session=$state/luma_soak/$prefix-$(date +%Y%m%d-%H%M%S).jsonl
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
"$py" - "$out/$name.report.json" "$label" <<'PY'
import json, sys
try:
    report = json.load(open(sys.argv[1]))
except ValueError:
    print(f'{sys.argv[2]}: NOT VALID: the soak could not be judged; see {sys.argv[1]}')
    sys.exit(2)
valid = report['verdict']['exit'] == 0
print(f"{sys.argv[2]}: {'VALID' if valid else 'NOT VALID'}: {report['drops']} drop(s) in {report['duration_s']} s, "
      f"{report['drops_per_hour']} per hour; report {sys.argv[1]}")
for reason in report['verdict']['reasons']:
    print(f'  {reason}')
sys.exit(0 if valid else 2)
PY
valid=$?
[ "$build" = cpp ] && exit "$valid"

# The drop gate. It runs on a soak that is not valid too, so the reasons are printed, and then says INCONCLUSIVE.
baseline=${LUMA_SOAK_BASELINE:-$(ls -1 "$out"/cpp-baseline-*.report.json 2>/dev/null | sort | tail -n 1)}
if [ -z "$baseline" ] || [ ! -f "$baseline" ]; then
    echo "drop gate: INCONCLUSIVE"
    echo "  no C++ baseline report in $out: run tools/luma_soak/soak.sh cpp first, or set LUMA_SOAK_BASELINE"
    exit 2
fi
echo "baseline: $baseline"
"$py" -m tools.luma_soak compare --baseline "$baseline" --candidate "$out/$name.report.json"
