# Preview-only harness for the options menu and Maximize / Restore restyle mockups (see README.md). Builds on spike/ without
# changing it: nothing in the renderer, its tests or its install links this, and spike/CMakeLists.txt does not know it exists.
cmake_minimum_required(VERSION 3.25)
project(options_restyle_mockups LANGUAGES C CXX)

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
find_package(nlohmann_json 3.12 CONFIG REQUIRED)

add_executable(restyle_preview restyle_preview.cpp)
target_include_directories(restyle_preview PRIVATE "${XR_SPIKE}/src" "${XR_SPIKE}/include")
target_link_libraries(restyle_preview PRIVATE xr_presentation PkgConfig::MOCKUP_GL nlohmann_json::nlohmann_json)
target_compile_options(restyle_preview PRIVATE -Wall -Wextra -Wpedantic -Werror)
