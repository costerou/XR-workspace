#!/bin/sh
# The 3-hour baseline soak of the shipped C++ xr-luma-driver (docs/rust-rebuild-plan.md D13, "before"). Kept as the name
# docs/soak/README.md gives; tools/luma_soak/soak.sh does the work, and `soak.sh rust` is the "after".
#
#   tools/luma_soak/cpp_baseline.sh                  watch for 3 h, then store the report in docs/soak/
#   tools/luma_soak/cpp_baseline.sh --session FILE   judge and store a session already recorded (cut short or killed)
exec "$(dirname "$0")/soak.sh" cpp "$@"
