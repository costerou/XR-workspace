# The Luma driver's real-time layer (include/luma/rt.hpp): the driver, its benchmark and the renderer's pose reader
# (spike/src/pose.cpp reads the seqlock channel with it) all link this one library. Included from wherever it is first needed.
if(TARGET xr_luma_rt)
  return()
endif()
find_package(Threads REQUIRED)
add_library(xr_luma_rt STATIC ${CMAKE_CURRENT_LIST_DIR}/src/rt.cpp)
target_include_directories(xr_luma_rt PUBLIC ${CMAKE_CURRENT_LIST_DIR}/include)
target_compile_features(xr_luma_rt PUBLIC cxx_std_20)
target_compile_options(xr_luma_rt PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(xr_luma_rt PUBLIC Threads::Threads)
