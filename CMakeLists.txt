cmake_minimum_required(VERSION 3.28...3.31)
project(filly VERSION 0.1.0 LANGUAGES CXX)
# filly uses no C++ modules. Without this, CMake 3.28 with Ninja and C++20 needs clang-scan-deps,
# which Ubuntu ships in a separate package (clang-tools).
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)

# The wheel build sets no options, so it builds the Python module only. The MEX gateways are
# opt-in, because they need MATLAB or Octave.
option(FILLY_PYTHON "Build the Python extension module" ON)
option(FILLY_MATLAB "Build the MATLAB MEX gateway in matlab/" OFF)
option(FILLY_OCTAVE "Build the Octave MEX gateway in matlab/ (Linux)" OFF)
if(FILLY_PYTHON)
  find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module OPTIONAL_COMPONENTS Development.SABIModule)
  find_package(nanobind CONFIG REQUIRED)
endif()

set(FILAMENT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.deps" CACHE PATH "Filament 1.77.1 SDK directory")
if(NOT EXISTS "${FILAMENT_ROOT}/include/filament/Engine.h")
  message(FATAL_ERROR "Filament SDK missing. See docs/how-to/build.md for the platform SDK and FILAMENT_ROOT.")
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8 OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(AMD64|amd64|x86_64)$"
    OR NOT (WIN32 OR CMAKE_SYSTEM_NAME STREQUAL "Linux"))
  message(FATAL_ERROR "Supported targets are Windows and Linux x86_64.")
endif()
if(WIN32)
  # Hybrid CRT: static C++ runtime and vcruntime, dynamic UCRT (see the link options below).
  # Python ships no msvcp140.dll, and a host process that loaded an older one first (MATLAB,
  # Qt applications) can crash code built with a newer toolset. This applies to nanobind and
  # libwebp too, because both are built as targets of this project.
  set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
  set(FILAMENT_LIB_DIR "${FILAMENT_ROOT}/lib/x86_64/mt")
else()
  set(FILAMENT_LIB_DIR "${FILAMENT_ROOT}/lib/x86_64")
  # Keep the system GL dispatch library external to manylinux wheels.
  set(OpenGL_GL_PREFERENCE LEGACY)
  find_package(OpenGL REQUIRED)
  find_package(X11 REQUIRED)
  find_package(Threads REQUIRED)
endif()
set(FILAMENT_LIBRARIES gltfio_core filament-iblprefilter filament backend filabridge filaflat
  geometry ibl utils bluegl smol-v stb dracodec meshoptimizer
  mikktspace uberzlib zstd basis_transcoder ktxreader image abseil)
if(WIN32)
  list(APPEND FILAMENT_LIBRARIES bluevk)
endif()
foreach(name IN LISTS FILAMENT_LIBRARIES)
  add_library(filament_sdk_${name} STATIC IMPORTED)
  set_target_properties(filament_sdk_${name} PROPERTIES
    IMPORTED_LOCATION "${FILAMENT_LIB_DIR}/${CMAKE_STATIC_LIBRARY_PREFIX}${name}${CMAKE_STATIC_LIBRARY_SUFFIX}")
  list(APPEND FILAMENT_LINK_TARGETS filament_sdk_${name})
endforeach()

include(native/libwebp.cmake)

# The output-pass materials and the material archive are embedded as C arrays.
enable_language(C)
set(FILLY_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
include(native/materials/output.cmake)

# glTF materials come from filly's precompiled material archive (native/materials), built here
# with the SDK's matc and uberz. The module has no material compiler. See
# docs/explanation/material-precompilation.md.
include(native/materials/materials.cmake)

add_library(filly_core STATIC native/renderer.cpp native/output_pass.cpp native/gl_interop.cpp native/archive_materials.cpp
  native/gltf_prepare.cpp native/json.cpp native/shapes.cpp native/webp_provider.cpp native/egl_platform.cpp
  native/vendor/meshoptimizer/vertexcodec.cpp native/vendor/meshoptimizer/indexcodec.cpp
  native/vendor/meshoptimizer/vertexfilter.cpp)
target_compile_features(filly_core PUBLIC cxx_std_20)
target_link_libraries(filly_core PRIVATE filly_output_materials filly_material_archive)
set_target_properties(filly_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(filly_core PUBLIC native PRIVATE "${FILAMENT_ROOT}/include" "${libwebp_SOURCE_DIR}/src")
target_link_libraries(filly_core PRIVATE webpdecoder)
if(MSVC)
  target_compile_options(filly_core PRIVATE /Zc:__cplusplus /bigobj /EHsc)
  target_link_libraries(filly_core PRIVATE ${FILAMENT_LINK_TARGETS} opengl32 gdi32 user32 shlwapi ws2_32)
else()
  # Filament's static archives contain circular references.
  target_link_libraries(filly_core PRIVATE -Wl,--start-group ${FILAMENT_LINK_TARGETS} -Wl,--end-group
    OpenGL::GL X11::X11 Threads::Threads ${CMAKE_DL_LIBS})
  target_compile_options(filly_core PRIVATE -fvisibility=hidden)
endif()

# The licenses of the code that the native modules link, for every package that ships one.
function(filly_install_licenses destination)
  install(FILES "${FILAMENT_ROOT}/LICENSE" DESTINATION ${destination} RENAME Filament.txt ${ARGN})
  install(FILES "${PROJECT_SOURCE_DIR}/native/vendor/meshoptimizer/LICENSE.md" DESTINATION ${destination}
          RENAME meshoptimizer.txt ${ARGN})
  install(FILES "${PROJECT_SOURCE_DIR}/native/vendor/cgltf/LICENSE" DESTINATION ${destination} RENAME cgltf.txt ${ARGN})
  install(FILES "${libwebp_SOURCE_DIR}/COPYING" DESTINATION ${destination} RENAME libwebp.txt ${ARGN})
  install(FILES "${libwebp_SOURCE_DIR}/PATENTS" DESTINATION ${destination} RENAME libwebp-PATENTS.txt ${ARGN})
endfunction()

if(FILLY_PYTHON)
  nanobind_add_module(_native STABLE_ABI NOSTRIP native/bindings.cpp)
  target_link_libraries(_native PRIVATE filly_core)
  if(MSVC)
    # The UCRT is part of Windows 10 and later, and one shared copy keeps a single heap and
    # locale state per process. The static runtime objects request libucrt.lib by default.
    target_link_options(_native PRIVATE /NODEFAULTLIB:libucrt.lib /DEFAULTLIB:ucrt.lib)
  else()
    # Filament marks its public API visible, and the C++ template instantiations are visible
    # regardless of -fvisibility. Exported, they could bind to another copy of Filament, libwebp,
    # or a different libstdc++ build in the process, so only the module entry point is exported.
    set(FILLY_EXPORTS "${CMAKE_CURRENT_BINARY_DIR}/_native.map")
    file(WRITE "${FILLY_EXPORTS}" "{\n  global: PyInit__native;\n  local: *;\n};\n")
    target_link_options(_native PRIVATE "LINKER:--version-script=${FILLY_EXPORTS}")
    set_property(TARGET _native APPEND PROPERTY LINK_DEPENDS "${FILLY_EXPORTS}")
  endif()
  install(TARGETS _native LIBRARY DESTINATION filly RUNTIME DESTINATION filly)
  filly_install_licenses(filly/licenses)
endif()

if(FILLY_MATLAB OR FILLY_OCTAVE)
  add_subdirectory(matlab)
endif()
