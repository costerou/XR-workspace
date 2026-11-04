# Our own Luma Ultra driver. The other swarm pieces add their sources here; the
# display module is its own library so it stays optional in the daemon.
add_library(xr_luma_display STATIC src/display.cpp)
target_compile_features(xr_luma_display PUBLIC cxx_std_20)
target_include_directories(xr_luma_display PUBLIC include)
target_compile_options(xr_luma_display PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_display_test tests/display_test.cpp)
target_link_libraries(luma_display_test PRIVATE xr_luma_display)
target_compile_options(luma_display_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_display COMMAND luma_display_test)
