# luma::Fusion and its tests. Included from spike/CMakeLists.txt. Kept in its own file so the
# fusion piece slots into the driver's targets (xr_luma_driver) without touching them.
add_library(xr_luma_fusion STATIC driver/src/fusion.cpp)
target_compile_features(xr_luma_fusion PUBLIC cxx_std_20)
target_include_directories(xr_luma_fusion PUBLIC driver/include)
target_compile_options(xr_luma_fusion PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_executable(fusion_test driver/tests/fusion_test.cpp)
target_link_libraries(fusion_test PRIVATE xr_luma_fusion)
target_compile_options(fusion_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME fusion COMMAND fusion_test --quick)
