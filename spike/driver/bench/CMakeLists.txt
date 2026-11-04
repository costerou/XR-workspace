# Real-time layer of the Luma driver, its tests and its latency benchmark (docs/performance.md). No hardware: the benchmark
# feeds the pipeline from a fake 1 kHz transport, and nothing here opens a USB or HID device.
find_package(Threads REQUIRED)
set(LUMA_RT_WARNINGS -Wall -Wextra -Wpedantic -Werror)

add_library(xr_luma_rt STATIC ../src/rt.cpp)
target_include_directories(xr_luma_rt PUBLIC ../include)
target_compile_features(xr_luma_rt PUBLIC cxx_std_20)
target_compile_options(xr_luma_rt PRIVATE ${LUMA_RT_WARNINGS})
target_link_libraries(xr_luma_rt PUBLIC Threads::Threads)

add_executable(luma_rt_test rt_test.cpp)
target_link_libraries(luma_rt_test PRIVATE xr_luma_rt)
target_compile_options(luma_rt_test PRIVATE ${LUMA_RT_WARNINGS} -O2)
add_test(NAME luma_rt COMMAND luma_rt_test)
set_tests_properties(luma_rt PROPERTIES TIMEOUT 60)

add_executable(luma_rt_bench rt_bench.cpp)
target_link_libraries(luma_rt_bench PRIVATE xr_luma_rt)
target_compile_options(luma_rt_bench PRIVATE ${LUMA_RT_WARNINGS} -O2)
# Likewise real fusion, compiled here so the benchmark needs no other target. Until it is merged: stub_fusion.hpp.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../src/fusion.cpp")
  target_sources(luma_rt_bench PRIVATE ../src/fusion.cpp)
  target_compile_definitions(luma_rt_bench PRIVATE LUMA_BENCH_REAL_FUSION)
endif()
# Smoke only: asserts no sample lost, no allocation, nothing about timing, which a busy machine would fail. Run --check by hand.
add_test(NAME luma_rt_bench_smoke COMMAND luma_rt_bench --quick)
set_tests_properties(luma_rt_bench_smoke PROPERTIES TIMEOUT 30)
