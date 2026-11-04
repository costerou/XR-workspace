# Our own head-tracking driver for the VITURE Luma Ultra. libc and pthreads only; no VITURE SDK.
find_package(Threads REQUIRED)
add_library(xr_luma_driver STATIC src/publish.cpp)
target_include_directories(xr_luma_driver PUBLIC include)
target_compile_features(xr_luma_driver PUBLIC cxx_std_20)
target_compile_options(xr_luma_driver PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(xr_luma_driver PUBLIC Threads::Threads)

# The real renderer sources, compiled in so that the contract test cannot pass against a copy of the reader.
add_executable(publish_test tests/publish_test.cpp ../src/pose.cpp ../src/predict.cpp)
target_include_directories(publish_test PRIVATE ../include ../tests)
target_link_libraries(publish_test PRIVATE xr_luma_driver)
add_test(NAME luma_publish COMMAND publish_test)
