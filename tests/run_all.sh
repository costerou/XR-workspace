#!/usr/bin/env bash
# One command for every offline test suite: the C++ ctest suite, each Python suite, the panel's QML tests
# and the Rust workspace. Nothing here opens a device, installs anything or talks to a running session.
#
#   tests/run_all.sh                 # everything
#   tests/run_all.sh rust python     # only the named stages: ctest, python, qml, rust
#
# BUILD_DIR (default build/release) is configured with the documented release flags if it has no cache yet,
# then rebuilt. Every stage runs even when an earlier one fails; the exit status is 1 if any failed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2
BUILD_DIR="${BUILD_DIR:-build/release}"
JOBS="${JOBS:-$(nproc)}"
# The suites put Unix sockets under TMPDIR; a long per-session TMPDIR overflows sun_path (108 bytes).
export TMPDIR=/tmp

STAGES=("$@")
[ ${#STAGES[@]} -eq 0 ] && STAGES=(ctest python qml rust)
for s in "${STAGES[@]}"; do
  case "$s" in ctest|python|qml|rust) ;; *) echo "unknown stage: $s (ctest, python, qml, rust)" >&2; exit 2 ;; esac
done

FAILED=()
RESULTS=()
step() {  # step NAME CMD...: run one suite, record its result
  local name="$1"; shift
  echo
  echo "=== $name"
  if "$@"; then RESULTS+=("PASS  $name"); else RESULTS+=("FAIL  $name"); FAILED+=("$name"); fi
}

native_build() {
  if [ ! -f "$BUILD_DIR/CMakeCache.txt" ]; then
    cmake -S spike -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release \
      -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF -DXR_ENABLE_OWNERSHIP_PLUGIN=ON || return 1
  fi
  cmake --build "$BUILD_DIR" -j "$JOBS"
}

run_ctest() {
  # The capture_e2e tests time real frames against a loopback compositor, so they run one at a time.
  ctest --test-dir "$BUILD_DIR" --output-on-failure -j "$JOBS" -E '^capture_e2e' || return 1
  ctest --test-dir "$BUILD_DIR" --output-on-failure -j 1 -R '^capture_e2e'
}

# Each directory runs on its own: several suites reuse module names, and the executable ctest scripts
# under spike/ would be collected by a repository-wide discovery (docs/stabilization.md). pytest, numpy and
# Pillow come from uv's cache (fetched once) in an environment of /usr/bin/python3, the interpreter that
# runs the stack, since it has no pytest or Pillow of its own.
PY_SUITES=(tests/*/ omarchy-plugin/cody.viture-workspace/tests spike/driver/research spike/driver/docs/tools)
run_pytest() {
  uv run --no-project --python /usr/bin/python3 --with pytest --with numpy --with pillow python -m pytest "$1" -q -p no:cacheprovider
}

# The native checks the options suites call, from the build (tools/xr_options_evidence/README.md).
native_report() {
  mkdir -p "$BUILD_DIR/qa" &&
  python3 tools/xr_options_evidence/build_native_report.py --build "$BUILD_DIR" --output "$BUILD_DIR/qa/native_report"
}
python_env() {
  local build="$ROOT/$BUILD_DIR"
  export CONTROL_WIRE_CHECK="${CONTROL_WIRE_CHECK:-$build/control_wire_check}"
  export XR_RESTORE_GEOMETRY_BINARY="${XR_RESTORE_GEOMETRY_BINARY:-$build/restore_band_test}"
  export XR_NATIVE_REPORT_BINARY="${XR_NATIVE_REPORT_BINARY:-$build/qa/native_report}"
}

run_qml() {
  QT_QPA_PLATFORM=offscreen QT_QUICK_CONTROLS_STYLE=Basic /usr/lib/qt6/bin/qmltestrunner \
    -input omarchy-plugin/cody.viture-workspace/tests -import omarchy-plugin/cody.viture-workspace/tests/imports
}

run_rust() {
  ( cd rust && cargo build --locked && cargo test --locked ) || return 1
  if command -v cargo-deny >/dev/null; then
    ( cd rust && cargo deny check licenses bans sources )
  else
    echo "cargo-deny not installed: licence check skipped"
  fi
}

for s in "${STAGES[@]}"; do
  case "$s" in
    ctest)
      step "native build ($BUILD_DIR)" native_build
      step ctest run_ctest ;;
    python)
      python_env
      [ -x "$XR_NATIVE_REPORT_BINARY" ] || step "native report build" native_report
      for d in "${PY_SUITES[@]}"; do
        case "$d" in
          # Its own runner puts it in fresh user, IPC and network namespaces.
          tests/pro2_runtime_qa/) step "pro2_runtime_qa (run.py)" python3 tests/pro2_runtime_qa/run.py ;;
          *) step "pytest ${d%/}" run_pytest "${d%/}" ;;
        esac
      done ;;
    qml)
      step "qml tests" run_qml ;;
    rust)
      step "cargo build + test (rust/)" run_rust ;;
  esac
done

echo
echo "=== summary"
printf '%s\n' "${RESULTS[@]}"
[ ${#FAILED[@]} -eq 0 ]
