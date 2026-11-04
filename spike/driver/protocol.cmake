# The wire protocol, self-contained so the daemon's targets can link it.
# Included from spike/CMakeLists.txt; paths are relative to spike/.
option(XR_ENABLE_LUMA_DRIVER "Build our own Luma Ultra head-tracking driver" ON)
if(XR_ENABLE_LUMA_DRIVER)
  add_library(xr_luma_protocol STATIC driver/src/protocol.cpp)
  target_include_directories(xr_luma_protocol PUBLIC driver/include)
  target_compile_features(xr_luma_protocol PUBLIC cxx_std_20)
  target_compile_options(xr_luma_protocol PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_executable(luma_protocol_test driver/tests/protocol_test.cpp)
  target_link_libraries(luma_protocol_test PRIVATE xr_luma_protocol)
  target_compile_options(luma_protocol_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME luma_protocol COMMAND luma_protocol_test)
endif()
