cmake_minimum_required(VERSION 3.28...3.31)
project(filly VERSION 0.1.0 LANGUAGES CXX)

find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module OPTIONAL_COMPONENTS Development.SABIModule)
find_package(nanobind CONFIG REQUIRED)

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
set(FILAMENT_LIBRARIES gltfio gltfio_core filamat shaders uberarchive filament-iblprefilter filament backend filabridge filaflat
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

# glTF materials: "runtime" compiles each material configuration with filamat while loading;
# "archive" uses filly's precompiled material archive (native/materials), built here with the
# SDK's matc and uberz. See docs/explanation/material-precompilation.md.
set(FILLY_MATERIALS runtime CACHE STRING "glTF material path: runtime or archive")
set_property(CACHE FILLY_MATERIALS PROPERTY STRINGS runtime archive)
if(FILLY_MATERIALS STREQUAL "archive")
  include(native/materials/materials.cmake)
  set(FILLY_MATERIAL_SOURCES native/archive_materials.cpp)
elseif(FILLY_MATERIALS STREQUAL "runtime")
  set(FILLY_MATERIAL_SOURCES native/materials.cpp native/surface_material.cpp)
else()
  message(FATAL_ERROR "FILLY_MATERIALS must be runtime or archive, not '${FILLY_MATERIALS}'")
endif()
message(STATUS "filly glTF materials: ${FILLY_MATERIALS}")

add_library(filly_core STATIC native/renderer.cpp native/output_pass.cpp native/gl_interop.cpp ${FILLY_MATERIAL_SOURCES}
  native/gltf_prepare.cpp native/json.cpp native/shapes.cpp native/webp_provider.cpp native/egl_platform.cpp
  native/vendor/meshoptimizer/vertexcodec.cpp native/vendor/meshoptimizer/indexcodec.cpp
  native/vendor/meshoptimizer/vertexfilter.cpp)
target_compile_features(filly_core PUBLIC cxx_std_20)
target_link_libraries(filly_core PRIVATE filly_output_materials)
if(FILLY_MATERIALS STREQUAL "archive")
  target_compile_definitions(filly_core PUBLIC FILLY_MATERIALS_ARCHIVE=1)
  target_link_libraries(filly_core PRIVATE filly_material_archive)
endif()
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
install(FILES "${FILAMENT_ROOT}/LICENSE" DESTINATION filly/licenses RENAME Filament.txt)
install(FILES native/vendor/meshoptimizer/LICENSE.md DESTINATION filly/licenses RENAME meshoptimizer.txt)
install(FILES native/vendor/cgltf/LICENSE DESTINATION filly/licenses RENAME cgltf.txt)
install(FILES "${libwebp_SOURCE_DIR}/COPYING" DESTINATION filly/licenses RENAME libwebp.txt)
install(FILES "${libwebp_SOURCE_DIR}/PATENTS" DESTINATION filly/licenses RENAME libwebp-PATENTS.txt)
