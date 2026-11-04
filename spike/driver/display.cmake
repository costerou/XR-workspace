# Optional one-shot display command (docs/display-control.md). The daemon links
# xr_luma_display; nothing here needs the glasses.
add_library(xr_luma_display STATIC ${CMAKE_CURRENT_LIST_DIR}/src/display.cpp)
target_compile_features(xr_luma_display PUBLIC cxx_std_20)
target_include_directories(xr_luma_display PUBLIC ${CMAKE_CURRENT_LIST_DIR}/include)
target_compile_options(xr_luma_display PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_display_test ${CMAKE_CURRENT_LIST_DIR}/tests/display_test.cpp)
target_link_libraries(luma_display_test PRIVATE xr_luma_display)
target_compile_options(luma_display_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_display COMMAND luma_display_test)
