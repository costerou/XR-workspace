# The edge pan drawn by the real compositor for any layout (README.md). Builds on spike/ without changing it: nothing in the renderer,
# its tests or its install links this, and spike/CMakeLists.txt does not know it exists.
cmake_minimum_required(VERSION 3.25)
project(edge_pan_clips LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(XR_SPIKE "${CMAKE_CURRENT_SOURCE_DIR}/../../spike")
add_subdirectory("${XR_SPIKE}" spike EXCLUDE_FROM_ALL)

find_package(PkgConfig REQUIRED)
pkg_check_modules(CLIPS_GL REQUIRED IMPORTED_TARGET egl glesv2)  # spike's imported targets are private to its directory

add_executable(pan_clip pan_clip.cpp)
target_include_directories(pan_clip PRIVATE "${XR_SPIKE}/src" "${XR_SPIKE}/include" "${XR_SPIKE}/tests")
target_link_libraries(pan_clip PRIVATE xr_presentation PkgConfig::CLIPS_GL)
target_compile_options(pan_clip PRIVATE -Wall -Wextra -Wpedantic -Werror)
