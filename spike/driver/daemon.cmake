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

add_executable(luma_replay_report tools/replay_report.cpp src/predict.cpp)
target_link_libraries(luma_replay_report PRIVATE xr_luma_protocol xr_luma_fusion)
target_compile_options(luma_replay_report PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_real_capture_test tests/real_capture_test.cpp)
target_link_libraries(luma_real_capture_test PRIVATE xr_luma_protocol xr_luma_fusion)
target_compile_options(luma_real_capture_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_real_capture COMMAND luma_real_capture_test "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin"
         "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_upside_down.bin")
add_test(NAME luma_replay_report_smoke COMMAND luma_replay_report "${CMAKE_CURRENT_LIST_DIR}/tests/fixtures/luma_imu_real.bin" --every 2)

add_executable(luma_latency_report tools/latency_report.cpp)
target_include_directories(luma_latency_report PRIVATE include)
target_compile_features(luma_latency_report PRIVATE cxx_std_20)
target_compile_options(luma_latency_report PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_latency_log_test tests/latency_log_test.cpp)
target_include_directories(luma_latency_log_test PRIVATE include)
target_compile_features(luma_latency_log_test PRIVATE cxx_std_20)
target_compile_options(luma_latency_log_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_latency_log COMMAND luma_latency_log_test "$<TARGET_FILE:luma_latency_report>")
