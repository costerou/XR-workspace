# Preview-only harness for the 3D button mockups (see README.md). Builds on spike/ without changing it: nothing in the
# renderer, its tests or its install links this, and spike/CMakeLists.txt does not know it exists.
cmake_minimum_required(VERSION 3.25)
project(button_3d_mockups LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(XR_SPIKE "${CMAKE_CURRENT_SOURCE_DIR}/../../spike")
add_subdirectory("${XR_SPIKE}" spike EXCLUDE_FROM_ALL)

find_package(PkgConfig REQUIRED)
pkg_check_modules(MOCKUP_GL REQUIRED IMPORTED_TARGET egl glesv2)  # spike's imported targets are private to its directory

add_executable(button_preview button_preview.cpp)
target_include_directories(button_preview PRIVATE "${XR_SPIKE}/src" "${XR_SPIKE}/include")
target_link_libraries(button_preview PRIVATE xr_presentation PkgConfig::MOCKUP_GL)
target_compile_options(button_preview PRIVATE -Wall -Wextra -Wpedantic -Werror)
