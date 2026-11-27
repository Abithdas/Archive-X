cmake_minimum_required(VERSION 3.20)
project(axplayer CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Which engine is built as axplayer.dll (the same C functions, see include/axplayer.h):
#   lav  (default)  LAV Filters + DirectShow (src/axplayer.cpp)
#   mpv             libmpv (src/axmpv.cpp); the "lav_dir" given to ax_create is then the folder with libmpv-2.dll
set(AX_ENGINE "lav" CACHE STRING "Native video engine: lav or mpv")
set_property(CACHE AX_ENGINE PROPERTY STRINGS lav mpv)

if(AX_ENGINE STREQUAL "mpv")
  # Only mpv's client.h (ISC licence) is needed to build: libmpv itself is loaded from its folder at run time.
  add_library(axplayer SHARED src/axmpv.cpp)
  target_compile_definitions(axplayer PRIVATE AXPLAYER_EXPORTS UNICODE _UNICODE)
  target_include_directories(axplayer PUBLIC include PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/mpv)
  target_link_libraries(axplayer PRIVATE user32 gdi32)
  set_target_properties(axplayer PROPERTIES OUTPUT_NAME axmpv)   # axmpv.dll, so both engines can sit side by side in the installer
else()
  # The folder named "include" of the LAV Filters source (ILAVVideoSettings and friends); packaging/scripts/fetch-lav.sh --headers puts it in
  # native/axplayer/third_party/lavfilters.
  set(LAV_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/lavfilters/include" CACHE PATH "LAV Filters include folder")

  add_library(axplayer SHARED src/axplayer.cpp)
  target_compile_definitions(axplayer PRIVATE AXPLAYER_EXPORTS UNICODE _UNICODE)
  target_include_directories(axplayer PUBLIC include PRIVATE ${LAV_INCLUDE})
  target_link_libraries(axplayer PRIVATE strmiids uuid ole32 oleaut32 mfuuid mfplat user32 gdi32)
endif()
if(MSVC)
  target_compile_options(axplayer PRIVATE /W3 /EHsc)
endif()

add_executable(axplayer_test test/axplayer_test.cpp)
target_compile_definitions(axplayer_test PRIVATE UNICODE _UNICODE)
target_link_libraries(axplayer_test PRIVATE axplayer)
