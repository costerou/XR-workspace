# The wire protocol, self-contained so the daemon's targets can link it. Included from
# spike/driver/CMakeLists.txt.
add_library(xr_luma_protocol STATIC ${CMAKE_CURRENT_LIST_DIR}/src/protocol.cpp)
target_include_directories(xr_luma_protocol PUBLIC ${CMAKE_CURRENT_LIST_DIR}/include)
target_compile_features(xr_luma_protocol PUBLIC cxx_std_20)
target_compile_options(xr_luma_protocol PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_executable(luma_protocol_test ${CMAKE_CURRENT_LIST_DIR}/tests/protocol_test.cpp)
target_link_libraries(luma_protocol_test PRIVATE xr_luma_protocol)
target_compile_options(luma_protocol_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_test(NAME luma_protocol COMMAND luma_protocol_test)
