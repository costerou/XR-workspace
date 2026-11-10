#!/bin/sh
# Byte-compare the Rust geometry dumps with the C++ ones.
#   rust/crates/xr-renderer/cpp_parity.sh <C++ build dir>
# The C++ build dir is a CMake build of spike/ with the targets glass_geometry_dump, placement_geometry_dump and
# options_geometry_dump. Exits non-zero on the first difference.
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
