# The daemon and its binary. Included from spike/driver/CMakeLists.txt, which drv-transport owns.
add_library(xr_luma_daemon STATIC src/daemon.cpp src/wiring.cpp)
target_include_directories(xr_luma_daemon PUBLIC include)
target_compile_features(xr_luma_daemon PUBLIC cxx_std_20)
target_compile_options(xr_luma_daemon PRIVATE -Wall -Wextra -Wpedantic -Werror)
if(TARGET xr_luma_driver)
  target_link_libraries(xr_luma_daemon PUBLIC xr_luma_driver)
endif()

add_executable(xr-luma-driver src/main.cpp)
target_link_libraries(xr-luma-driver PRIVATE xr_luma_daemon)
target_compile_options(xr-luma-driver PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_daemon_test tests/daemon_test.cpp)
target_link_libraries(luma_daemon_test PRIVATE xr_luma_daemon)
target_compile_options(luma_daemon_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_daemon COMMAND luma_daemon_test "$<TARGET_FILE:xr-luma-driver>")
