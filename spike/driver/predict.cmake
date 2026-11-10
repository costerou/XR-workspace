# Pose prediction for the Luma driver: included from spike/CMakeLists.txt beside the renderer's own
# predictor, because its tests prove compatibility against that code (xr_predict). The library is pure code
# (no device, no system call) and is always built: the renderer reads PredictionWire and extrapolates it (D3).
add_library(xr_luma_predict STATIC driver/src/predict.cpp)
target_compile_features(xr_luma_predict PUBLIC cxx_std_20)
target_compile_options(xr_luma_predict PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_include_directories(xr_luma_predict PUBLIC driver/include)
if(XR_ENABLE_LUMA_DRIVER)
  add_executable(luma_predict_test driver/tests/predict_test.cpp)
  target_link_libraries(luma_predict_test PRIVATE xr_luma_predict xr_predict Threads::Threads)
  target_compile_options(luma_predict_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME luma_predict COMMAND luma_predict_test)
endif()
