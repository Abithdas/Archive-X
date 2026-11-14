cmake_minimum_required(VERSION 3.20)
project(axplayer CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The folder named "include" of the LAV Filters source (ILAVVideoSettings and friends); desktop/scripts/fetch-lav.sh --headers puts it in
# desktop/axplayer/third_party/lavfilters.
set(LAV_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/lavfilters/include" CACHE PATH "LAV Filters include folder")

add_library(axplayer SHARED src/axplayer.cpp)
target_compile_definitions(axplayer PRIVATE AXPLAYER_EXPORTS UNICODE _UNICODE)
target_include_directories(axplayer PUBLIC include PRIVATE ${LAV_INCLUDE})
target_link_libraries(axplayer PRIVATE strmiids uuid ole32 oleaut32 mfuuid mfplat user32 gdi32)
if(MSVC)
  target_compile_options(axplayer PRIVATE /W3 /EHsc)
endif()

add_executable(axplayer_test test/axplayer_test.cpp)
target_compile_definitions(axplayer_test PRIVATE UNICODE _UNICODE)
target_link_libraries(axplayer_test PRIVATE axplayer)
