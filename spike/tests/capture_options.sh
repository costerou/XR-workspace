#!/usr/bin/env bash
# Run only the offline CPU raster harness, then encode its pixels. No live XR.
set -euo pipefail
build_dir=${1:?usage: capture_options.sh BUILD_DIR OUTPUT_DIR}
output_dir=${2:?usage: capture_options.sh BUILD_DIR OUTPUT_DIR}
mkdir -p "$output_dir"
"$build_dir/options_capture" "$output_dir"
"$build_dir/options_geometry_dump" > "$output_dir/native-options-geometry.json"
for state in 0 1 2 3; do
 ffmpeg -y -i "$output_dir/native-options-$state.ppm" "$output_dir/native-options-$state.png" >/dev/null 2>&1
done
ffmpeg -y -framerate 1 -i "$output_dir/native-options-%d.ppm" -c:v libx264 -pix_fmt yuv420p -r 24 "$output_dir/native-options.mp4" >/dev/null 2>&1
