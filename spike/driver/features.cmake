# Optional device features for xr-luma-driver (docs/features.md). The daemon
# links xr_luma_features; nothing here needs the glasses.
add_library(xr_luma_features STATIC ${CMAKE_CURRENT_LIST_DIR}/src/features.cpp)
target_compile_features(xr_luma_features PUBLIC cxx_std_20)
target_include_directories(xr_luma_features PUBLIC ${CMAKE_CURRENT_LIST_DIR}/include)
target_compile_options(xr_luma_features PRIVATE -Wall -Wextra -Wpedantic -Werror)

add_executable(luma_features_test ${CMAKE_CURRENT_LIST_DIR}/tests/features_test.cpp)
target_link_libraries(luma_features_test PRIVATE xr_luma_features)
target_compile_options(luma_features_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_compile_definitions(luma_features_test PRIVATE
  LUMA_FEATURES_DOC="${CMAKE_CURRENT_LIST_DIR}/docs/features.md")
add_test(NAME luma_features COMMAND luma_features_test)
