# Our own Luma Ultra head-tracking driver (transport -> protocol -> fusion -> publish). libc and
# pthreads only, no VITURE SDK. Tests never open a real device.
find_package(Threads REQUIRED)

include(rt.cmake)
add_library(xr_luma_driver STATIC src/transport.cpp src/publish.cpp src/predict.cpp src/vsync.cpp)
target_compile_features(xr_luma_driver PUBLIC cxx_std_20)
target_include_directories(xr_luma_driver PUBLIC include)
target_compile_options(xr_luma_driver PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(xr_luma_driver PUBLIC Threads::Threads xr_luma_rt)

add_executable(luma_transport_test tests/transport_test.cpp)
target_compile_options(luma_transport_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(luma_transport_test PRIVATE xr_luma_daemon)
# The ioctl trace the Rust driver is held to as well.
target_compile_definitions(luma_transport_test PRIVATE
    LUMA_TRANSPORT_TRACE="${CMAKE_CURRENT_SOURCE_DIR}/../../rust/corpus/luma/transport_trace.txt")
add_test(NAME luma_transport COMMAND luma_transport_test)
set_tests_properties(luma_transport PROPERTIES TIMEOUT 60)

# The real renderer sources, compiled in so that the contract test cannot pass against a copy of the reader.
add_executable(publish_test tests/publish_test.cpp ../src/pose.cpp ../src/predict.cpp)
target_include_directories(publish_test PRIVATE ../include ../tests)
target_link_libraries(publish_test PRIVATE xr_luma_driver)
add_test(NAME luma_publish COMMAND publish_test)
# D2's gate (pose age p99 <= 1.5 ms at 1 kHz, zero reader waits, mutex fallback): the renderer's PoseFeed against the
# Publisher. The smoke run is 2 s a scenario; run it bare for the 10 s numbers.
add_executable(luma_pose_channel_bench bench/pose_channel_bench.cpp ../src/pose.cpp ../src/predict.cpp)
target_include_directories(luma_pose_channel_bench PRIVATE ../include)
target_link_libraries(luma_pose_channel_bench PRIVATE xr_luma_driver)
add_test(NAME luma_pose_channel_bench_smoke COMMAND luma_pose_channel_bench --quick)
set_tests_properties(luma_pose_channel_bench_smoke PROPERTIES TIMEOUT 60)

add_executable(luma_vsync_test tests/vsync_test.cpp)
target_compile_options(luma_vsync_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(luma_vsync_test PRIVATE xr_luma_driver)
add_test(NAME luma_vsync COMMAND luma_vsync_test)

# Each other piece declares its own targets and tests in its own file, so pieces can land in any
# order. Add one line per piece.
include(protocol.cmake)
include(cmake/fusion.cmake)
add_subdirectory(bench)
include(daemon.cmake)
include(display.cmake)
include(features.cmake)

# The binary, defined here where contract_test looks for it. xr_luma_daemon is in daemon.cmake.
add_executable(xr-luma-driver src/main.cpp)
target_link_libraries(xr-luma-driver PRIVATE xr_luma_daemon)
target_compile_options(xr-luma-driver PRIVATE -Wall -Wextra -Wpedantic -Werror)

# drv-review-qa's shape check. Everything is merged, so a missing header now fails instead of skipping.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/contract_test.cpp")
  add_executable(luma_contract_test tests/contract_test.cpp)
  target_compile_options(luma_contract_test PRIVATE -Wall -Wextra -Wpedantic)
  target_link_libraries(luma_contract_test PRIVATE xr_luma_driver)
  target_compile_definitions(luma_contract_test PRIVATE XR_ENABLE_LUMA_DRIVER=1 LUMA_CONTRACT_REQUIRED=1)
  target_include_directories(luma_contract_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../include")
  add_test(NAME luma_contract COMMAND luma_contract_test "${CMAKE_CURRENT_SOURCE_DIR}")
  set_tests_properties(luma_contract PROPERTIES SKIP_RETURN_CODE 77)
endif()
