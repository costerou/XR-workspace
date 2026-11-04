# luma::Fusion and its tests. Included from spike/driver/CMakeLists.txt.
add_library(xr_luma_fusion STATIC ${CMAKE_CURRENT_LIST_DIR}/../src/fusion.cpp)
target_compile_features(xr_luma_fusion PUBLIC cxx_std_20)
target_include_directories(xr_luma_fusion PUBLIC ${CMAKE_CURRENT_LIST_DIR}/../include)
target_compile_options(xr_luma_fusion PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_executable(fusion_test ${CMAKE_CURRENT_LIST_DIR}/../tests/fusion_test.cpp)
target_link_libraries(fusion_test PRIVATE xr_luma_fusion)
target_compile_options(fusion_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME fusion COMMAND fusion_test --quick)
