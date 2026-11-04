# Pose prediction for the Luma driver: included from spike/CMakeLists.txt beside the renderer's own
# predictor, because its tests prove compatibility against that code (xr_predict).
option(XR_ENABLE_LUMA_DRIVER "Build our own Luma Ultra head-tracking driver" ON)
if(XR_ENABLE_LUMA_DRIVER)
  add_library(xr_luma_predict STATIC driver/src/predict.cpp)
  target_compile_features(xr_luma_predict PUBLIC cxx_std_20)
  target_compile_options(xr_luma_predict PRIVATE -Wall -Wextra -Wpedantic -Werror)
  target_include_directories(xr_luma_predict PUBLIC driver/include)
  if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/driver/include/luma/types.hpp")
    target_include_directories(xr_luma_predict PUBLIC driver/tests/stub)  # until the real types.hpp lands
  endif()
  add_executable(luma_predict_test driver/tests/predict_test.cpp)
  target_link_libraries(luma_predict_test PRIVATE xr_luma_predict xr_predict Threads::Threads)
  target_compile_options(luma_predict_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME luma_predict COMMAND luma_predict_test)
endif()
