#!/bin/sh
# Byte-compare the Rust geometry dumps with the C++ ones.
#   rust/crates/xr-renderer/cpp_parity.sh <C++ build dir>
# The C++ build dir is a CMake build of spike/ with the targets glass_geometry_dump, placement_geometry_dump,
# options_geometry_dump and restore_band_test. Exits non-zero on the first difference.
set -eu
cpp=$(cd "$1" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
cd "$here/../.."
cargo build --locked --release -q -p xr-renderer --bins
rust=$(pwd)/target/release
fixtures=$here/tests/fixtures
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
same() {  # name, then the command line after the binary; stdin from $in
    name=$1; shift
    "$cpp/$name" "$@" < "$in" > "$tmp/c" || { echo "$name $*: C++ exit $?"; exit 1; }
    "$rust/$name" "$@" < "$in" > "$tmp/r" || { echo "$name $*: Rust exit $?"; exit 1; }
    cmp "$tmp/c" "$tmp/r"
    echo "$name $*: identical ($(wc -c < "$tmp/c") bytes)"
}
in=/dev/null
same glass_geometry_dump
same placement_geometry_dump
same options_geometry_dump
in=$fixtures/options_layout.json same options_geometry_dump --layout
in=$fixtures/options_band.json same options_geometry_dump --band
# The restore_band_wire ctest's input: restore_band_test --dump (its pass line goes to stderr) and the Rust twin's.
"$cpp/restore_band_test" --dump > "$tmp/c" 2> /dev/null || { echo "restore_band_test --dump: C++ exit $?"; exit 1; }
"$rust/restore_band_dump" --dump > "$tmp/r" || { echo "restore_band_dump --dump: Rust exit $?"; exit 1; }
cmp "$tmp/c" "$tmp/r"
echo "restore_band_dump --dump: identical ($(wc -c < "$tmp/c") bytes)"
