cmake_minimum_required(VERSION 3.28...3.31)
project(filly LANGUAGES C CXX)
# filly uses no C++ modules. Without this, CMake 3.28 with Ninja and C++20 needs clang-scan-deps,
# which Ubuntu ships in a separate package (clang-tools).
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)

# The shared renderer is in core/; each frontend is a folder that links it. The wheel build sets
# no options, so it builds the Python module only. The MEX gateways are opt-in, because they need
# MATLAB or Octave. An Emscripten configuration builds the web module only.
if(EMSCRIPTEN)
  set(_desktop OFF)
else()
  set(_desktop ON)
endif()
option(FILLY_PYTHON "Build the Python extension module in python/" ${_desktop})
option(FILLY_MATLAB "Build the MATLAB MEX gateway in matlab/" OFF)
option(FILLY_OCTAVE "Build the Octave MEX gateway in matlab/ (Linux, macOS)" OFF)
option(FILLY_WEB "Build the WebAssembly module in web/ (Emscripten only)" ${EMSCRIPTEN})
if(EMSCRIPTEN AND (FILLY_PYTHON OR FILLY_MATLAB OR FILLY_OCTAVE))
  message(FATAL_ERROR "An Emscripten build makes the web module only. See web/tools/build_filly_web.sh.")
endif()
if(FILLY_WEB AND NOT EMSCRIPTEN)
  message(FATAL_ERROR "FILLY_WEB needs Emscripten's toolchain file. See web/tools/build_filly_web.sh.")
endif()

if(WIN32)
  # Hybrid CRT: static C++ runtime and vcruntime, dynamic UCRT (see the frontends' link options).
  # Python ships no msvcp140.dll, and a host process that loaded an older one first (MATLAB,
  # Qt applications) can crash code built with a newer toolset. This applies to nanobind and
  # libwebp too, so it is set here, for every target of the project.
  set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
endif()

add_subdirectory(core)
if(FILLY_PYTHON)
  add_subdirectory(python)
endif()
if(FILLY_MATLAB OR FILLY_OCTAVE)
  add_subdirectory(matlab)
endif()
if(FILLY_WEB)
  add_subdirectory(web)
endif()
