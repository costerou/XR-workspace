# Our own Luma Ultra head-tracking driver (transport -> protocol -> fusion -> publish). libc and
# pthreads only, no VITURE SDK. Tests never open a real device.
find_package(Threads REQUIRED)

add_library(xr_luma_driver STATIC src/transport.cpp)
target_compile_features(xr_luma_driver PUBLIC cxx_std_20)
target_include_directories(xr_luma_driver PUBLIC include)
target_compile_options(xr_luma_driver PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(xr_luma_driver PUBLIC Threads::Threads)

add_executable(luma_transport_test tests/transport_test.cpp)
target_compile_options(luma_transport_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(luma_transport_test PRIVATE xr_luma_driver)
add_test(NAME luma_transport COMMAND luma_transport_test)
set_tests_properties(luma_transport PROPERTIES TIMEOUT 60)

# Each other piece declares its own targets and tests in its own file, so pieces can land in any
# order. Add one line per piece. protocol.cmake is not here: its paths are relative to spike/.
include(daemon.cmake OPTIONAL)
include(display.cmake OPTIONAL)
include(features.cmake OPTIONAL)
include(cmake/fusion.cmake OPTIONAL)
