# The daemon and its binary. Included from spike/driver/CMakeLists.txt.
add_library(xr_luma_daemon STATIC src/daemon.cpp src/wiring.cpp)
target_include_directories(xr_luma_daemon PUBLIC include)
target_compile_features(xr_luma_daemon PUBLIC cxx_std_20)
target_compile_options(xr_luma_daemon PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(xr_luma_daemon PUBLIC xr_luma_driver xr_luma_protocol xr_luma_fusion xr_luma_rt xr_luma_features)

add_executable(luma_daemon_test tests/daemon_test.cpp)
target_link_libraries(luma_daemon_test PRIVATE xr_luma_daemon)
target_compile_options(luma_daemon_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_daemon COMMAND luma_daemon_test "$<TARGET_FILE:xr-luma-driver>")

# D3: the gyro's motion on PredictionWire, checked against the real recording through the real daemon.
add_executable(luma_prediction_wire_test tests/prediction_wire_test.cpp)
target_link_libraries(luma_prediction_wire_test PRIVATE xr_luma_daemon)
target_compile_options(luma_prediction_wire_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_prediction_wire COMMAND luma_prediction_wire_test "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin")

add_executable(luma_replay_report tools/replay_report.cpp src/predict.cpp)
target_link_libraries(luma_replay_report PRIVATE xr_luma_protocol xr_luma_fusion)
target_compile_options(luma_replay_report PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_real_capture_test tests/real_capture_test.cpp)
target_link_libraries(luma_real_capture_test PRIVATE xr_luma_protocol xr_luma_fusion)
target_compile_options(luma_real_capture_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_real_capture COMMAND luma_real_capture_test "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin"
         "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_upside_down.bin")
add_test(NAME luma_replay_report_smoke COMMAND luma_replay_report "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin" --every 2)
# The prediction replay bench (rust-rebuild-plan D3): RMS and p95 per horizon for the old and the new predictor, every
# horizon scored ("n 0 " would mean one scored nothing).
add_test(NAME luma_prediction_bench COMMAND luma_replay_report "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin"
         --every 100 --predict-ms 10,17,25)
set_tests_properties(luma_prediction_bench PROPERTIES
    PASS_REGULAR_EXPRESSION "old vs new[^\n]*\n +10 ms +old rms +[0-9.]+ p95 +[0-9.]+ +new rms +[0-9.]+ p95 +[0-9.]+[^\n]*\n +17 ms [^\n]*\n +25 ms +old rms"
    FAIL_REGULAR_EXPRESSION "n 0 ")

# The golden files the Rust fusion port is held to bit for bit (rust/corpus/luma-fusion/, rust/README.md).
# Fails when fusion.cpp's output moves; rewrite them on purpose with `luma_fusion_golden write`.
add_executable(luma_fusion_golden tools/fusion_golden.cpp)
target_link_libraries(luma_fusion_golden PRIVATE xr_luma_protocol xr_luma_fusion)
target_compile_options(luma_fusion_golden PRIVATE -Wall -Wextra -Wpedantic -Werror)
set(LUMA_FUSION_CORPUS "${CMAKE_CURRENT_LIST_DIR}/../../rust/corpus/luma-fusion")
set(LUMA_FIXTURES "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures")
add_test(NAME luma_fusion_golden_real COMMAND luma_fusion_golden check plain "${LUMA_FIXTURES}/luma_imu_real.bin" "${LUMA_FUSION_CORPUS}/real.bin")
add_test(NAME luma_fusion_golden_upside_down COMMAND luma_fusion_golden check plain "${LUMA_FIXTURES}/luma_imu_upside_down.bin" "${LUMA_FUSION_CORPUS}/upside_down.bin")
add_test(NAME luma_fusion_golden_real_faults COMMAND luma_fusion_golden check faults "${LUMA_FIXTURES}/luma_imu_real.bin" "${LUMA_FUSION_CORPUS}/real_faults.bin")

# D11: Mahony against an error-state Kalman filter, replay only (spike/driver/docs/fusion-eskf-review.md).
add_executable(luma_fusion_review tools/fusion_review.cpp)
target_include_directories(luma_fusion_review PRIVATE tests)
target_link_libraries(luma_fusion_review PRIVATE xr_luma_protocol xr_luma_fusion)
target_compile_options(luma_fusion_review PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_fusion_review COMMAND luma_fusion_review "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin"
         "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_upside_down.bin" --quick)

add_executable(luma_latency_report tools/latency_report.cpp)
target_include_directories(luma_latency_report PRIVATE include)
target_compile_features(luma_latency_report PRIVATE cxx_std_20)
target_compile_options(luma_latency_report PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_latency_log_test tests/latency_log_test.cpp)
target_include_directories(luma_latency_log_test PRIVATE include)
target_compile_features(luma_latency_log_test PRIVATE cxx_std_20)
target_compile_options(luma_latency_log_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_latency_log COMMAND luma_latency_log_test "$<TARGET_FILE:luma_latency_report>")

# D3, the renderer's half: the renderer's own feed reader and predictor (xr_predict) on the same replay, wire against q0/q1.
if(TARGET xr_predict)
  add_executable(luma_renderer_prediction_test tests/renderer_prediction_test.cpp)
  target_link_libraries(luma_renderer_prediction_test PRIVATE xr_luma_daemon xr_predict)
  target_compile_options(luma_renderer_prediction_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME luma_renderer_prediction COMMAND luma_renderer_prediction_test "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin")
endif()

# The display vsync bench (rust-rebuild-plan D5): a recording with known scan-outs through the real daemon, scored the way
# the renderer scores against wp_presentation. Two seeds. Then the shipped binary on such a recording with --read-events.
if(TARGET xr_predict)
  add_executable(luma_vsync_bench tools/vsync_bench.cpp)
  target_link_libraries(luma_vsync_bench PRIVATE xr_luma_daemon xr_predict)
  target_compile_options(luma_vsync_bench PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME luma_vsync_bench COMMAND luma_vsync_bench --seconds 60 --seed 1 --quiet)
  add_test(NAME luma_vsync_bench_seed2 COMMAND luma_vsync_bench --seconds 30 --seed 2 --quiet)
  set_tests_properties(luma_vsync_bench luma_vsync_bench_seed2 PROPERTIES PASS_REGULAR_EXPRESSION "vsync bench: PASS")
  add_test(NAME luma_vsync_replay COMMAND sh -c "d=$(mktemp -d) || exit 1; \"$1\" --seconds 20 --quiet --write \"$d/v.lcap\" >/dev/null && \"$2\" --replay \"$d/v.lcap\" --ipc-prefix \"$d/feed_\" --read-events; r=$?; rm -rf \"$d\"; exit $r"
           sh "$<TARGET_FILE:luma_vsync_bench>" "$<TARGET_FILE:xr-luma-driver>")
  set_tests_properties(luma_vsync_replay PROPERTIES PASS_REGULAR_EXPRESSION "luma: vsync events=18[0-9][0-9] locked=1 hz=120.00 resets=1"
                       FAIL_REGULAR_EXPRESSION "error|bad_packets=[1-9]")
endif()
