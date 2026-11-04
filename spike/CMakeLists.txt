cmake_minimum_required(VERSION 3.25)
project(xr_native_spike VERSION 0.1.0 LANGUAGES C CXX)

# Faster local builds: use ccache and mold (or lld) when installed. Explicit
# -DCMAKE_<LANG>_COMPILER_LAUNCHER / -DCMAKE_LINKER_TYPE always win. Pick the
# generator with -G Ninja (CMake cannot choose it from here).
option(XR_FAST_BUILD_TOOLS "Auto-use ccache and mold/lld when found" ON)
if(XR_FAST_BUILD_TOOLS)
  # Per-user installs (mise shims, ~/.local/bin) count even when the shell
  # running cmake does not have them on PATH.
  set(XR_TOOL_HINTS "$ENV{HOME}/.local/share/mise/shims" "$ENV{HOME}/.local/bin")
  find_program(XR_CCACHE ccache HINTS ${XR_TOOL_HINTS})
  if(XR_CCACHE)
    foreach(lang C CXX)
      if(NOT CMAKE_${lang}_COMPILER_LAUNCHER)
        set(CMAKE_${lang}_COMPILER_LAUNCHER "${XR_CCACHE}")
      endif()
    endforeach()
  endif()
  find_program(XR_LD_MOLD ld.mold HINTS ${XR_TOOL_HINTS})
  if(NOT CMAKE_LINKER_TYPE)
    find_program(XR_LLD ld.lld)
    if(XR_LD_MOLD)
      set(CMAKE_LINKER_TYPE MOLD)
    elseif(XR_LLD)
      set(CMAKE_LINKER_TYPE LLD)
    endif()
  endif()
  # -fuse-ld=mold looks up ld.mold on the build-time PATH; point the compiler
  # driver at the directory we found so builds work from any shell.
  if(CMAKE_LINKER_TYPE STREQUAL "MOLD" AND XR_LD_MOLD)
    get_filename_component(XR_LD_MOLD_DIR "${XR_LD_MOLD}" DIRECTORY)
    if(NOT XR_LD_MOLD_DIR STREQUAL "/usr/bin")
      # Linker-flag variables (not add_link_options) so try_compile checks
      # such as FindThreads link the same way.
      foreach(kind EXE SHARED MODULE)
        set(CMAKE_${kind}_LINKER_FLAGS "${CMAKE_${kind}_LINKER_FLAGS} -B${XR_LD_MOLD_DIR}")
      endforeach()
    endif()
  endif()
  message(STATUS "Fast build tools: launcher='${CMAKE_CXX_COMPILER_LAUNCHER}' linker='${CMAKE_LINKER_TYPE}'")
endif()

option(XR_ENABLE_HARDWARE "Enable later opt-in hardware components" OFF)
option(XR_ENABLE_SDK "Enable later approved SDK worker" OFF)
option(XR_ENABLE_IPC "Build offline native IPC parser and tests" ON)
option(XR_ENABLE_PRESENTATION "Build native test-pattern renderer; tests do not connect" ON)
option(XR_ENABLE_VIDEO "VR video playback in the renderer via local libmpv (docs/vr-video.md); stubbed if libmpv is missing" ON)
option(XR_ENABLE_OWNERSHIP_PLUGIN "Build opt-in ABI-pinned ownership module (never auto-load)" OFF)
if(XR_ENABLE_OWNERSHIP_PLUGIN)
  find_package(nlohmann_json 3.12 CONFIG REQUIRED)
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(HYPRLAND REQUIRED IMPORTED_TARGET hyprland)
  pkg_check_modules(AQUAMARINE REQUIRED IMPORTED_TARGET aquamarine)
  add_library(xr_ownership STATIC ownership/registry.cpp)
  set_target_properties(xr_ownership PROPERTIES POSITION_INDEPENDENT_CODE ON)
  target_compile_features(xr_ownership PUBLIC cxx_std_23)
  target_include_directories(xr_ownership PUBLIC ownership)
  target_link_libraries(xr_ownership PUBLIC nlohmann_json::nlohmann_json)
  target_compile_options(xr_ownership PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_library(xr-ownership MODULE ownership/plugin.cpp)
  target_compile_features(xr-ownership PRIVATE cxx_std_23)
  target_compile_options(xr-ownership PRIVATE -Wall -Wextra -Wpedantic -Werror)
  target_link_libraries(xr-ownership PRIVATE xr_ownership PkgConfig::HYPRLAND PkgConfig::AQUAMARINE)
  add_executable(ownership_test tests/ownership_test.cpp)
  set_target_properties(xr_ownership xr-ownership ownership_test PROPERTIES CXX_EXTENSIONS OFF)
  target_compile_options(ownership_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  target_link_libraries(ownership_test PRIVATE xr_ownership)
  add_test(NAME ownership COMMAND ownership_test)
  add_executable(ownership_pair_test tests/ownership_pair_test.cpp)
  target_link_libraries(ownership_pair_test PRIVATE xr_ownership)
  target_compile_options(ownership_pair_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME ownership_pair COMMAND ownership_pair_test)
  add_executable(ownership_native_test tests/ownership_native_test.cpp)
  target_compile_features(ownership_native_test PRIVATE cxx_std_23)
  target_include_directories(ownership_native_test PRIVATE ownership)
  target_link_libraries(ownership_native_test PRIVATE xr_ownership PkgConfig::HYPRLAND PkgConfig::AQUAMARINE)
  target_compile_options(ownership_native_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(NAME ownership_pair_wire COMMAND "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/ownership_pair_wire_test.py" "$<TARGET_FILE:ownership_test>")
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/ownership_methods.inc"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/extract_ownership_methods.py"
      "${CMAKE_CURRENT_SOURCE_DIR}/ownership/plugin.cpp" "${CMAKE_CURRENT_BINARY_DIR}/ownership_methods.inc" configure verified cleanup
    DEPENDS ownership/plugin.cpp tests/extract_ownership_methods.py VERBATIM)
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/ownership_pair_methods.inc" "${CMAKE_CURRENT_BINARY_DIR}/ownership_pair_broker.inc"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/extract_pair_methods.py"
      "${CMAKE_CURRENT_SOURCE_DIR}/ownership/plugin.cpp" "${CMAKE_CURRENT_BINARY_DIR}/ownership_pair_methods.inc"
    DEPENDS ownership/plugin.cpp tests/extract_pair_methods.py VERBATIM)
  add_executable(ownership_pair_boundary_test tests/ownership_pair_boundary_test.cpp
    "${CMAKE_CURRENT_BINARY_DIR}/ownership_pair_methods.inc" "${CMAKE_CURRENT_BINARY_DIR}/ownership_pair_broker.inc")
  target_include_directories(ownership_pair_boundary_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
  target_link_libraries(ownership_pair_boundary_test PRIVATE xr_ownership)
  target_compile_options(ownership_pair_boundary_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME ownership_pair_boundary COMMAND ownership_pair_boundary_test)
  add_executable(ownership_offset_test tests/ownership_offset_test.cpp "${CMAKE_CURRENT_BINARY_DIR}/ownership_methods.inc")
  target_include_directories(ownership_offset_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
  target_link_libraries(ownership_offset_test PRIVATE xr_ownership)
  target_compile_options(ownership_offset_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME ownership_offset COMMAND ownership_offset_test)
  # Config reloads (Omarchy's workspace layout toggle is one) keep the run's monitor rules: the plugin's
  # keptThroughReload against the pinned reload sequence; nothing is modeset.
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/ownership_reload_methods.inc"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/extract_ownership_methods.py"
      "${CMAKE_CURRENT_SOURCE_DIR}/ownership/plugin.cpp" "${CMAKE_CURRENT_BINARY_DIR}/ownership_reload_methods.inc" keptThroughReload
    DEPENDS ownership/plugin.cpp tests/extract_ownership_methods.py VERBATIM)
  add_executable(ownership_reload_test tests/ownership_reload_test.cpp "${CMAKE_CURRENT_BINARY_DIR}/ownership_reload_methods.inc")
  target_compile_features(ownership_reload_test PRIVATE cxx_std_23)
  target_include_directories(ownership_reload_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
  target_link_libraries(ownership_reload_test PRIVATE xr_ownership)
  target_compile_options(ownership_reload_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME ownership_reload COMMAND ownership_reload_test)
  foreach(CASE default retirement vrr eotf)
    add_test(NAME ownership_native_${CASE} COMMAND ownership_native_test ${CASE})
  endforeach()
endif()

if(XR_ENABLE_PRESENTATION AND NOT XR_ENABLE_IPC)
  message(FATAL_ERROR "Presentation needs the approved IPC decoder; use -DXR_ENABLE_PRESENTATION=OFF for layout-only")
endif()

# Component-scoped: the offline layout contract uses only the standard library.
# No downloads, implicit fallback JSON implementation, or runtime device probes.
if(XR_ENABLE_IPC)
  find_package(nlohmann_json 3.12 CONFIG QUIET)
  if(NOT nlohmann_json_FOUND)
    message(FATAL_ERROR "XR_ENABLE_IPC requires local nlohmann_json >=3.12 (nlohmann_jsonConfig.cmake). Request package approval; no auto-fetch. Layout-only build: -DXR_ENABLE_IPC=OFF")
  endif()

endif()
if(XR_ENABLE_HARDWARE)
  message(FATAL_ERROR "Hardware components are not implemented in Task 1. Use -DXR_ENABLE_HARDWARE=OFF; see dependencies.md and obtain run-scoped permission for later tasks")
endif()
if(XR_ENABLE_SDK)
  message(FATAL_ERROR "SDK worker is not implemented/authorized in Task 1. Use -DXR_ENABLE_SDK=OFF; later work requires approved absolute XR_SDK_ROOT and recorded package hash")
endif()

add_library(xr_types INTERFACE)
target_include_directories(xr_types INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_compile_features(xr_types INTERFACE cxx_std_20)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()
add_executable(layout_test tests/layout_test.cpp)
target_link_libraries(layout_test PRIVATE xr_types)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(layout_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME layout COMMAND layout_test)

if(XR_ENABLE_IPC)
  add_library(xr_ipc STATIC src/ipc.cpp src/video_ipc.cpp)
  target_link_libraries(xr_ipc PUBLIC xr_types PRIVATE nlohmann_json::nlohmann_json)
  add_executable(ipc_test tests/ipc_test.cpp)
  target_link_libraries(ipc_test PRIVATE xr_ipc)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(xr_ipc PRIVATE -Wall -Wextra -Wpedantic -Werror)
    target_compile_options(ipc_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME ipc COMMAND ipc_test)
  # Placement-menu contract (shared fixture with tests/native_spike/test_placement_menu.py); exit 77 = renderer lacks the op.
  add_executable(placement_menu_ipc_test tests/placement_menu_ipc_test.cpp)
  target_link_libraries(placement_menu_ipc_test PRIVATE xr_ipc nlohmann_json::nlohmann_json)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(placement_menu_ipc_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME placement_menu_ipc COMMAND placement_menu_ipc_test
    "${CMAKE_CURRENT_SOURCE_DIR}/../tests/native_spike/fixtures/placement_menu_payloads.json")
  set_tests_properties(placement_menu_ipc PROPERTIES SKIP_RETURN_CODE 77)
endif()

if(XR_ENABLE_PRESENTATION)
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(WAYLAND REQUIRED IMPORTED_TARGET wayland-client>=1.22)
  pkg_check_modules(PROTOCOLS REQUIRED wayland-protocols)
  pkg_get_variable(WAYLAND_PROTOCOLS_DIR wayland-protocols pkgdatadir)
  find_program(WAYLAND_SCANNER wayland-scanner REQUIRED)
  set(GENERATED "${CMAKE_CURRENT_BINARY_DIR}/generated")
  file(MAKE_DIRECTORY "${GENERATED}")
  set(LAYER_XML "${CMAKE_CURRENT_SOURCE_DIR}/protocols/wlr-layer-shell-unstable-v1.xml")
  set(XDG_XML "${WAYLAND_PROTOCOLS_DIR}/stable/xdg-shell/xdg-shell.xml")
  foreach(PROTOCOL wlr-layer-shell-unstable-v1 xdg-shell presentation-time ext-foreign-toplevel-list-v1 ext-image-capture-source-v1 ext-image-copy-capture-v1 linux-dmabuf-v1 fractional-scale-v1 viewporter)
    if(PROTOCOL STREQUAL "xdg-shell")
      set(XML "${XDG_XML}")
    elseif(PROTOCOL MATCHES "^ext-(image-capture-source|image-copy-capture|foreign-toplevel-list)-v1$")  # toplevel: interface symbol only, never bound
      string(REGEX REPLACE "-v1$" "" CAPTURE_DIR "${PROTOCOL}")
      set(XML "${WAYLAND_PROTOCOLS_DIR}/staging/${CAPTURE_DIR}/${PROTOCOL}.xml")
      if(NOT EXISTS "${XML}")
        message(FATAL_ERROR "Managed-output capture requires local wayland-protocols ${XML}; no auto-fetch")
      endif()
    elseif(PROTOCOL STREQUAL "linux-dmabuf-v1")
      set(XML "${WAYLAND_PROTOCOLS_DIR}/stable/linux-dmabuf/linux-dmabuf-v1.xml")
      if(NOT EXISTS "${XML}")
        message(FATAL_ERROR "Zero-copy capture requires local wayland-protocols ${XML}; no auto-fetch")
      endif()
    elseif(PROTOCOL STREQUAL "fractional-scale-v1" OR PROTOCOL STREQUAL "viewporter")  # native-resolution rendering
      if(PROTOCOL STREQUAL "viewporter")
        set(XML "${WAYLAND_PROTOCOLS_DIR}/stable/viewporter/viewporter.xml")
      else()
        set(XML "${WAYLAND_PROTOCOLS_DIR}/staging/fractional-scale/fractional-scale-v1.xml")
      endif()
      if(NOT EXISTS "${XML}")
        message(FATAL_ERROR "Native-resolution rendering requires local wayland-protocols ${XML}; no auto-fetch")
      endif()
    elseif(PROTOCOL STREQUAL "presentation-time")
      set(XML "${WAYLAND_PROTOCOLS_DIR}/stable/presentation-time/presentation-time.xml")
      if(NOT EXISTS "${XML}")
        message(FATAL_ERROR "Presentation timing requires local wayland-protocols stable/presentation-time/presentation-time.xml; no auto-fetch")
      endif()
    else()
      set(XML "${LAYER_XML}")
    endif()
    add_custom_command(OUTPUT "${GENERATED}/${PROTOCOL}-client-protocol.h"
      COMMAND "${WAYLAND_SCANNER}" client-header "${XML}" "${GENERATED}/${PROTOCOL}-client-protocol.h"
      DEPENDS "${XML}" VERBATIM)
    add_custom_command(OUTPUT "${GENERATED}/${PROTOCOL}-protocol.c"
      COMMAND "${WAYLAND_SCANNER}" private-code "${XML}" "${GENERATED}/${PROTOCOL}-protocol.c"
      DEPENDS "${XML}" VERBATIM)
    list(APPEND PROTOCOL_SOURCES "${GENERATED}/${PROTOCOL}-protocol.c" "${GENERATED}/${PROTOCOL}-client-protocol.h")
    set(XR_PROTOCOL_XML_${PROTOCOL} "${XML}")  # the loopback compositor's server headers (bench/)
  endforeach()
  add_library(xr_metrics STATIC src/metrics.cpp)
  target_link_libraries(xr_metrics PUBLIC xr_types PRIVATE nlohmann_json::nlohmann_json)
  add_executable(metrics_test tests/metrics_test.cpp)
  target_link_libraries(metrics_test PRIVATE xr_metrics)
  add_test(NAME metrics COMMAND metrics_test)
  find_package(Threads REQUIRED)
  add_library(xr_pose STATIC src/pose.cpp)
  target_link_libraries(xr_pose PUBLIC xr_types Threads::Threads)
  add_executable(pose_test tests/pose_test.cpp)
  target_link_libraries(pose_test PRIVATE xr_pose)
  add_test(NAME pose COMMAND pose_test)
  add_library(xr_predict STATIC src/predict.cpp)
  target_link_libraries(xr_predict PUBLIC xr_pose)
  add_executable(predict_test tests/predict_test.cpp)
  target_link_libraries(predict_test PRIVATE xr_predict)
  add_test(NAME predict COMMAND predict_test)
  # Head-tracking benchmark (offline): per-frame CPU, allocations, lock stalls, prediction error/jitter.
  add_executable(tracking_bench tests/tracking_bench.cpp)
  target_link_libraries(tracking_bench PRIVATE xr_predict xr_metrics)
  target_compile_options(tracking_bench PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME tracking_bench_smoke COMMAND tracking_bench --quick)
  add_library(xr_view STATIC src/view.cpp)
  target_link_libraries(xr_view PUBLIC xr_types)
  add_executable(view_test tests/view_test.cpp)
  target_link_libraries(view_test PRIVATE xr_view)
  add_test(NAME view COMMAND view_test)
  # Follow / locked centre the focused desktop (placement maths and head view together).
  add_executable(focus_center_test tests/focus_center_test.cpp)
  target_link_libraries(focus_center_test PRIVATE xr_view xr_pose)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(focus_center_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME focus_center COMMAND focus_center_test)
  if(XR_ENABLE_IPC)  # overview corner-resize handle: control frame + geometry (tools/native_spike/resize.py)
    add_executable(resize_handle_test tests/resize_handle_test.cpp)
    target_link_libraries(resize_handle_test PRIVATE xr_view xr_ipc xr_hud)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      target_compile_options(resize_handle_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
    endif()
    add_test(NAME resize_handle COMMAND resize_handle_test)
    # Overview free pointer: control frame, geometry status line, dot (shared fixture with tests/native_spike/test_free_pointer.py)
    add_executable(free_pointer_test tests/free_pointer_test.cpp)
    target_link_libraries(free_pointer_test PRIVATE xr_view xr_ipc nlohmann_json::nlohmann_json)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      target_compile_options(free_pointer_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
    endif()
    add_test(NAME free_pointer COMMAND free_pointer_test
      "${CMAKE_CURRENT_SOURCE_DIR}/../tests/native_spike/fixtures/overview_pointer_payloads.json"
      "${CMAKE_CURRENT_SOURCE_DIR}/../tests/native_spike/fixtures/serif_beam_shape.json")
  endif()
  # Pop-up text: installed system font via fontconfig + FreeType (local libraries, no downloads;
  # approved 2026-10-31). Missing font at runtime -> built-in pixel font, reported.
  pkg_check_modules(FONT REQUIRED IMPORTED_TARGET freetype2 fontconfig)
  # video.cpp: VR video maths + controls card state; material*.cpp: the Material Design 3 palette, tokens and drawing.
  # binds_panel.cpp: the key bind panel's card (Ctrl+Alt+K, tools/native_spike/keybinds.py).
  add_library(xr_hud STATIC src/hud.cpp src/hud_font.cpp src/video.cpp src/glass.cpp src/glass_theme.cpp src/material.cpp
              src/material_draw.cpp src/environment.cpp src/binds_panel.cpp)
  target_link_libraries(xr_hud PUBLIC xr_types xr_pose xr_view PRIVATE PkgConfig::FONT)
  add_executable(hud_test tests/hud_test.cpp)
  target_link_libraries(hud_test PRIVATE xr_hud)
  add_test(NAME hud COMMAND hud_test)
  # HudFont's glyph / advance / metrics caches never change what is drawn or measured; UTF-8 decoding matches the reference.
  add_executable(hud_font_test tests/hud_font_test.cpp)
  target_link_libraries(hud_font_test PRIVATE xr_hud)
  target_compile_options(hud_font_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME hud_font COMMAND hud_font_test)
  set_tests_properties(hud_font PROPERTIES SKIP_RETURN_CODE 77)
  # Help card fit (Ctrl+Alt+H): sized from the output so it never clips, at every scale up to 2.
  add_executable(help_card_test tests/help_card_test.cpp)
  target_link_libraries(help_card_test PRIVATE xr_hud)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(help_card_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME help_card COMMAND help_card_test)
  # Key bind panel card (binds_panel.hpp): fit at every scale, scrolling, the row states' M3 roles, the OLED cap, the fade.
  add_executable(binds_panel_test tests/binds_panel_test.cpp)
  target_link_libraries(binds_panel_test PRIVATE xr_hud)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(binds_panel_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME binds_panel COMMAND binds_panel_test)
  # Glass pills (glass.hpp): geometry, look, panel content, animation.
  add_executable(glass_test tests/glass_test.cpp)
  target_link_libraries(glass_test PRIVATE xr_hud)
  add_test(NAME glass COMMAND glass_test)
  # The pills' theme colour (glass_theme.hpp): every Omarchy theme's hue, one brightness, the OLED cap, live recolouring.
  add_executable(glass_theme_test tests/glass_theme_test.cpp)
  target_link_libraries(glass_theme_test PRIVATE xr_hud)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(glass_theme_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME glass_theme COMMAND glass_theme_test)
  # Material Design 3 (material.hpp): the tonal palette from every Omarchy theme colour, the OLED cap, tokens, icons.
  add_executable(material_test tests/material_test.cpp)
  target_link_libraries(material_test PRIVATE xr_hud)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(material_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
  add_test(NAME material COMMAND material_test)
  # Roll-level icons: the levelling roll, the tilt from level, the icon and its arrow, the `roll` frame.
  if(TARGET xr_ipc)
    add_executable(roll_level_test tests/roll_level_test.cpp)
    target_link_libraries(roll_level_test PRIVATE xr_hud xr_ipc)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      target_compile_options(roll_level_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
    endif()
    add_test(NAME roll_level COMMAND roll_level_test)
    # What level is measured against: the pose feed is relative to the driver's reference, not to gravity.
    add_executable(level_frame_test tests/level_frame_test.cpp)
    target_link_libraries(level_frame_test PRIVATE xr_hud xr_ipc)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
      target_compile_options(level_frame_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
    endif()
    add_test(NAME level_frame COMMAND level_frame_test)
  endif()
  # The glass pill geometry, renderer side to supervisor side (tools/native_spike/free_pointer.py mirrors it).
  add_executable(glass_geometry_dump tests/glass_geometry_dump.cpp)
  target_link_libraries(glass_geometry_dump PRIVATE xr_hud)
  # The orbit placement (view.cpp place_point on wire units), renderer side to supervisor side (tools/native_spike/orbit.py mirrors it).
  add_executable(placement_geometry_dump tests/placement_geometry_dump.cpp)
  target_link_libraries(placement_geometry_dump PRIVATE xr_hud)
  find_package(Python3 COMPONENTS Interpreter)
  if(Python3_Interpreter_FOUND)
    add_test(NAME glass_contract COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/glass_contract_test.py"
      "$<TARGET_FILE:glass_geometry_dump>" "${CMAKE_CURRENT_SOURCE_DIR}/..")
    add_test(NAME placement_contract COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/placement_contract_test.py"
      "$<TARGET_FILE:placement_geometry_dump>" "${CMAKE_CURRENT_SOURCE_DIR}/..")
    if(TARGET level_frame_test)  # the level bar pressed with the tilts level_frame_test reports
      add_test(NAME level_contract COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/level_contract_test.py"
        "$<TARGET_FILE:level_frame_test>" "${CMAKE_CURRENT_SOURCE_DIR}/..")
    endif()
  endif()
  add_library(xr_capture STATIC src/capture.cpp)
  target_link_libraries(xr_capture PUBLIC xr_types)
  add_executable(capture_test tests/capture_test.cpp)
  target_link_libraries(capture_test PRIVATE xr_capture)
  add_test(NAME capture COMMAND capture_test)
  pkg_check_modules(GPU REQUIRED IMPORTED_TARGET egl glesv2 wayland-egl gbm libdrm)
  add_library(xr_presentation STATIC src/wayland.cpp src/presentation.cpp src/gl.cpp src/hud_gl.cpp src/dmabuf.cpp
    src/video_gl.cpp src/video_player.cpp src/video_mode.cpp src/environment_gl.cpp ${PROTOCOL_SOURCES})
  target_include_directories(xr_presentation PRIVATE "${GENERATED}")
  target_link_libraries(xr_presentation PUBLIC xr_types xr_metrics xr_capture xr_view xr_pose xr_predict xr_hud PRIVATE PkgConfig::WAYLAND PkgConfig::GPU)
  add_executable(xr-native-spike src/main.cpp)
  target_link_libraries(xr-native-spike PRIVATE xr_presentation xr_ipc)
  add_executable(presentation_test tests/presentation_test.cpp)
  target_link_libraries(presentation_test PRIVATE xr_presentation)
  # A closed help card leaves nothing in the frame (real GlHud on surfaceless EGL; skipped without it).
  add_executable(overlay_residue_test tests/overlay_residue_test.cpp src/hud_gl.cpp)
  target_include_directories(overlay_residue_test PRIVATE src)
  target_link_libraries(overlay_residue_test PRIVATE xr_hud PkgConfig::GPU)
  add_test(NAME overlay_residue COMMAND overlay_residue_test)
  set_tests_properties(overlay_residue PROPERTIES SKIP_RETURN_CODE 77)
  # The glass shader matches its CPU mirror and the OLED cap on a real GLES2 context (surfaceless EGL; skipped without it).
  add_executable(glass_render_test tests/glass_render_test.cpp)
  target_link_libraries(glass_render_test PRIVATE xr_hud PkgConfig::GPU)
  target_compile_options(glass_render_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME glass_render COMMAND glass_render_test)
  set_tests_properties(glass_render PROPERTIES SKIP_RETURN_CODE 77)
  # GlCompositor frames on surfaceless EGL (create_offscreen): a long-lived compositor draws every scenario frame exactly
  # like a fresh one (ctest); --bench and --hashes time and fingerprint the draw path. gl.cpp and hud_gl.cpp are compiled
  # into it a second time with their GL calls counted (tests/gl_call_counts.hpp). Skipped without EGL.
  add_executable(gl_bench tests/gl_bench.cpp tests/gl_bench_gl.cpp tests/gl_bench_hud_gl.cpp tests/gl_bench_environment_gl.cpp)
  target_include_directories(gl_bench PRIVATE src tests)
  target_link_libraries(gl_bench PRIVATE xr_hud xr_view xr_pose PkgConfig::GPU PkgConfig::WAYLAND)
  target_compile_options(gl_bench PRIVATE -Wall -Wextra -Wpedantic -Werror -Wno-subobject-linkage)  # gl.cpp included as a header
  add_test(NAME gl_frame COMMAND gl_bench)
  set_tests_properties(gl_frame PROPERTIES SKIP_RETURN_CODE 77)
  # The background (xr/environment.hpp): its table, colour, fade, the levelled world-locked view and its control frames.
  if(TARGET xr_ipc)
    add_executable(environment_test tests/environment_test.cpp)
    target_link_libraries(environment_test PRIVATE xr_hud xr_ipc nlohmann_json::nlohmann_json)
    target_compile_options(environment_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
    add_test(NAME environment COMMAND environment_test "${CMAKE_CURRENT_SOURCE_DIR}/../tests/native_spike/fixtures/environment_payloads.json")
  endif()
  # ... and drawn behind the desktops on surfaceless EGL (exit 77 without it). `environment_render_test DIR` also writes
  # the offscreen preview PNGs there (zlib); ctest writes them into the build directory's environment-previews/.
  find_package(ZLIB)
  add_executable(environment_render_test tests/environment_render_test.cpp)
  target_include_directories(environment_render_test PRIVATE src)
  target_link_libraries(environment_render_test PRIVATE xr_presentation PkgConfig::GPU)
  if(ZLIB_FOUND)
    target_compile_definitions(environment_render_test PRIVATE XR_HAVE_ZLIB)
    target_link_libraries(environment_render_test PRIVATE ZLIB::ZLIB)
  endif()
  target_compile_options(environment_render_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME environment_render COMMAND environment_render_test "${CMAKE_CURRENT_BINARY_DIR}/environment-previews")
  set_tests_properties(environment_render PROPERTIES SKIP_RETURN_CODE 77)
  # The pill stays a fixed gap clear of the selected desktop's border at every size, in drawn pixels (surfaceless EGL;
  # skipped without it).
  add_executable(pill_border_render_test tests/pill_border_render_test.cpp)
  target_include_directories(pill_border_render_test PRIVATE src)
  target_link_libraries(pill_border_render_test PRIVATE xr_presentation PkgConfig::GPU)
  target_compile_options(pill_border_render_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME pill_border_render COMMAND pill_border_render_test)
  set_tests_properties(pill_border_render PROPERTIES SKIP_RETURN_CODE 77)
  foreach(TARGET xr_presentation xr-native-spike presentation_test xr_metrics metrics_test xr_capture capture_test xr_view view_test xr_pose pose_test xr_predict predict_test xr_hud hud_test glass_test glass_geometry_dump placement_geometry_dump)
    target_compile_options(${TARGET} PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endforeach()
  add_test(NAME presentation COMMAND presentation_test)
  add_test(NAME native_help COMMAND xr-native-spike --help)
  # VR video (docs/vr-video.md): decode through local libmpv (no downloads); without it the player is a reported stub.
  set(XR_VIDEO_MPV OFF)
  if(XR_ENABLE_VIDEO)
    pkg_check_modules(MPV IMPORTED_TARGET mpv>=0.38)
    if(MPV_FOUND)
      set(XR_VIDEO_MPV ON)
      target_compile_definitions(xr_presentation PRIVATE XR_HAVE_MPV)
      target_link_libraries(xr_presentation PRIVATE PkgConfig::MPV)
    else()
      message(WARNING "libmpv not found: the renderer builds without VR video playback")
    endif()
  endif()
  add_executable(video_test tests/video_test.cpp)
  target_link_libraries(video_test PRIVATE xr_hud xr_ipc nlohmann_json::nlohmann_json)
  target_compile_options(video_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME video COMMAND video_test "${CMAKE_CURRENT_SOURCE_DIR}/../tests/native_spike/fixtures/video_payloads.json")
  # Projection pass and controls card on surfaceless EGL, and (with libmpv and ffmpeg) real decoding of generated clips.
  find_program(XR_FFMPEG ffmpeg)
  add_executable(video_render_test tests/video_render_test.cpp src/video_gl.cpp src/video_player.cpp src/video_mode.cpp src/hud_gl.cpp)
  target_include_directories(video_render_test PRIVATE src)
  target_link_libraries(video_render_test PRIVATE xr_hud PkgConfig::GPU)
  target_compile_options(video_render_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  if(XR_VIDEO_MPV)
    target_compile_definitions(video_render_test PRIVATE XR_HAVE_MPV)
    target_link_libraries(video_render_test PRIVATE PkgConfig::MPV)
  endif()
  add_test(NAME video_render COMMAND video_render_test "${XR_FFMPEG}" "${CMAKE_CURRENT_BINARY_DIR}/video-samples")
  set_tests_properties(video_render PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 240)
  # Capture pipeline bench (bench/): capture_bench times the CPU pixel paths against their previous versions;
  # capture_e2e_bench runs the real renderer against an in-process loopback Wayland compositor (frame callbacks,
  # presentation feedback, ext-image-copy-capture with SHM or dmabuf targets) and measures frame delivery.
  add_executable(capture_bench bench/capture_bench.cpp)
  target_link_libraries(capture_bench PRIVATE xr_capture xr_presentation)
  target_compile_options(capture_bench PRIVATE -Wall -Wextra -Wpedantic -Werror)
  pkg_check_modules(WAYLAND_SERVER IMPORTED_TARGET wayland-server>=1.22)
  pkg_check_modules(BENCH_GPU IMPORTED_TARGET egl glesv2 gbm)
  if(WAYLAND_SERVER_FOUND AND BENCH_GPU_FOUND)
    set(SERVER_HEADERS "")
    set(SERVER_CODE "${GENERATED}/xdg-shell-protocol.c" "${GENERATED}/ext-foreign-toplevel-list-v1-protocol.c")
    foreach(PROTOCOL wlr-layer-shell-unstable-v1 presentation-time ext-image-capture-source-v1 ext-image-copy-capture-v1 linux-dmabuf-v1
                     fractional-scale-v1 viewporter)
      add_custom_command(OUTPUT "${GENERATED}/${PROTOCOL}-server-protocol.h"
        COMMAND "${WAYLAND_SCANNER}" server-header "${XR_PROTOCOL_XML_${PROTOCOL}}" "${GENERATED}/${PROTOCOL}-server-protocol.h"
        DEPENDS "${XR_PROTOCOL_XML_${PROTOCOL}}" VERBATIM)
      list(APPEND SERVER_HEADERS "${GENERATED}/${PROTOCOL}-server-protocol.h")
      list(APPEND SERVER_CODE "${GENERATED}/${PROTOCOL}-protocol.c")
    endforeach()
    add_executable(capture_e2e_bench bench/loopback_compositor.cpp ${SERVER_HEADERS} ${SERVER_CODE})
    target_include_directories(capture_e2e_bench PRIVATE "${GENERATED}")
    target_link_libraries(capture_e2e_bench PRIVATE xr_capture nlohmann_json::nlohmann_json PkgConfig::WAYLAND_SERVER PkgConfig::BENCH_GPU)
    target_compile_options(capture_e2e_bench PRIVATE -Wall -Wextra -Werror)
    add_library(xr_malloc_count SHARED bench/malloc_count.c)
    # Short end-to-end smoke per path: frames flow, captured frames reach the screen, the renderer exits cleanly.
    foreach(MODE cpu pattern gl-shm gl-dmabuf)
      add_test(NAME capture_e2e_${MODE} COMMAND capture_e2e_bench --check --mode ${MODE} --seconds 1.5 --warmup 1
        --renderer "$<TARGET_FILE:xr-native-spike>")
      set_tests_properties(capture_e2e_${MODE} PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    endforeach()
    # The glasses at Hyprland scale 1.25: the renderer draws at the native pixels (wp_fractional_scale_v1 + wp_viewporter)
    # and the captured desktops land where the logical layout puts them.
    foreach(MODE cpu gl-shm gl-dmabuf)
      add_test(NAME capture_e2e_scaled_${MODE} COMMAND capture_e2e_bench --check --mode ${MODE} --seconds 1.5 --warmup 1
        --fractional-scale 150 --renderer "$<TARGET_FILE:xr-native-spike>")
      set_tests_properties(capture_e2e_scaled_${MODE} PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    endforeach()
    # Every desktop of a full run maximizes: the renderer gets the supervisor's frames for XR-N and XR-N's own frames fill the
    # view (the stamp in the middle of the committed buffer says which desktop it came from).
    foreach(N RANGE 1 4)
      add_test(NAME capture_e2e_maximize_${N}_gl-dmabuf COMMAND capture_e2e_bench --check --mode gl-dmabuf --seconds 1.5 --warmup 1
        --outputs 4 --maximize ${N} --renderer "$<TARGET_FILE:xr-native-spike>")
      set_tests_properties(capture_e2e_maximize_${N}_gl-dmabuf PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    endforeach()
    # The hand's pan through the real renderer: from desktop 1 to desktop 3 by `edge_pan` frames and the landing, read off the view.
    add_test(NAME capture_e2e_edge_pan_gl-dmabuf COMMAND capture_e2e_bench --check --mode gl-dmabuf --seconds 3.5 --warmup 1
      --outputs 4 --maximize 3 --edge-pan --renderer "$<TARGET_FILE:xr-native-spike>")
    set_tests_properties(capture_e2e_edge_pan_gl-dmabuf PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    add_test(NAME capture_e2e_maximize_4_gl-shm COMMAND capture_e2e_bench --check --mode gl-shm --seconds 1.5 --warmup 1
      --outputs 4 --maximize 4 --renderer "$<TARGET_FILE:xr-native-spike>")
    set_tests_properties(capture_e2e_maximize_4_gl-shm PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    # The dot and the Serif Beam (`overview_pointer`'s look): every flip lands in the first draw after it, read back from the
    # pixels, so nothing between the control socket and the frame holds a change back.
    add_test(NAME capture_e2e_pointer_look_gl-shm COMMAND capture_e2e_bench --check --mode gl-shm --seconds 2.5 --warmup 1
      --outputs 1 --capture-hz 30 --pointer-look --renderer "$<TARGET_FILE:xr-native-spike>")
    set_tests_properties(capture_e2e_pointer_look_gl-shm PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    # Constraint churn as a Hyprland layout change or config reload sends it: no desktop goes blank, constraints that come
    # back re-create nothing, and a real size change keeps the last frame up until the new buffers deliver one.
    foreach(MODE gl-shm gl-dmabuf)  # the background through the real renderer: its frame, the fade, the desktops still shown
      add_test(NAME capture_e2e_environment_${MODE} COMMAND capture_e2e_bench --check --mode ${MODE} --seconds 1.5 --warmup 1
        --environment grid --renderer "$<TARGET_FILE:xr-native-spike>")
      set_tests_properties(capture_e2e_environment_${MODE} PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
      add_test(NAME capture_e2e_environment_smoke_${MODE} COMMAND capture_e2e_bench --check --mode ${MODE} --seconds 1.5 --warmup 1
        --environment smoke --renderer "$<TARGET_FILE:xr-native-spike>")
      set_tests_properties(capture_e2e_environment_smoke_${MODE} PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    endforeach()
    # An open placement panel through the real renderer: its frame, the view shifted so all of it shows, frames still flowing.
    # A tracked run's status says whether tracking works (here it cannot: no driver), which the incident monitor reads.
    add_test(NAME capture_e2e_tracked COMMAND capture_e2e_bench --check --mode gl-dmabuf --seconds 1.5 --warmup 1 --tracking
      --renderer "$<TARGET_FILE:xr-native-spike>")
    set_tests_properties(capture_e2e_tracked PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    add_test(NAME capture_e2e_open_panel COMMAND capture_e2e_bench --check --mode gl-dmabuf --seconds 1.5 --warmup 1 --outputs 1
      --environment grid --open-panel --renderer "$<TARGET_FILE:xr-native-spike>")
    set_tests_properties(capture_e2e_open_panel PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    foreach(CHURN bounce resize)
      foreach(MODE cpu gl-shm gl-dmabuf)
        add_test(NAME capture_e2e_churn_${CHURN}_${MODE} COMMAND capture_e2e_bench --check --mode ${MODE} --seconds 2 --warmup 1
          --constraint-churn ${CHURN} --renderer "$<TARGET_FILE:xr-native-spike>")
        set_tests_properties(capture_e2e_churn_${CHURN}_${MODE} PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
      endforeach()
    endforeach()
  endif()
  # Playback timing harness (docs/vr-video.md#performance): run by hand, never by ctest (slow, GPU and load dependent).
  add_executable(video_bench tests/video_bench.cpp src/video_gl.cpp src/video_player.cpp)
  target_include_directories(video_bench PRIVATE src)
  target_link_libraries(video_bench PRIVATE xr_hud PkgConfig::GPU nlohmann_json::nlohmann_json)
  target_compile_options(video_bench PRIVATE -Wall -Wextra -Wpedantic -Werror)
  if(XR_VIDEO_MPV)
    target_compile_definitions(video_bench PRIVATE XR_HAVE_MPV)
    target_link_libraries(video_bench PRIVATE PkgConfig::MPV)
  endif()
endif()

# Our own Luma Ultra driver: the wire protocol (spike/driver/include/luma/protocol.hpp).
include(driver/protocol.cmake)
