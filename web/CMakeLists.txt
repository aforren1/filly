# filly's web build: the core in native/ and the bindings in this folder, compiled with
# Emscripten for WebGL2 into filly-core.mjs and filly-core.wasm, which web/filly.mjs wraps.
# tools/build_filly_web.sh runs it.
#
# Inputs:
#   FILAMENT_ROOT         Filament 1.77.1 WebAssembly SDK (tools/build_filament_web.sh)
#   FILLY_MATERIAL_TOOLS  matc and uberz of Filament 1.77.1 for the build machine
#
# The web build always uses the precompiled material archive: Filament's WebAssembly build has
# no material compiler. The materials are compiled for GLSL ES 3.00 (matc -p mobile).
cmake_minimum_required(VERSION 3.28...3.31)
project(filly_web VERSION 0.1.0 LANGUAGES C CXX)

if(NOT EMSCRIPTEN)
  message(FATAL_ERROR "Configure web/ with Emscripten's toolchain file. See tools/build_filly_web.sh.")
endif()
get_filename_component(FILLY_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
set(FILAMENT_ROOT "" CACHE PATH "Filament 1.77.1 WebAssembly SDK directory")
if(NOT EXISTS "${FILAMENT_ROOT}/include/filament/Engine.h")
  message(FATAL_ERROR "Set FILAMENT_ROOT to the WebAssembly SDK from tools/build_filament_web.sh")
endif()
set(FILAMENT_LIB_DIR "${FILAMENT_ROOT}/lib/x86_64" CACHE PATH "Static libraries of the WebAssembly SDK")

set(FILLY_OUTPUT_MATC_FLAGS -a opengl -p mobile CACHE STRING "matc flags for the output-pass materials")
set(FILLY_MATC_FLAGS -a opengl -p mobile -V stereo,ssr,vsm CACHE STRING "matc flags for the archive")
include("${FILLY_SOURCE_DIR}/native/libwebp.cmake")
include("${FILLY_SOURCE_DIR}/native/materials/output.cmake")
include("${FILLY_SOURCE_DIR}/native/materials/materials.cmake")

set(FILAMENT_LIBRARIES gltfio_core filament-iblprefilter filament backend filabridge filaflat geometry
  ibl-lite ktxreader image basis_transcoder stb dracodec meshoptimizer mikktspace uberzlib zstd utils abseil)
foreach(name IN LISTS FILAMENT_LIBRARIES)
  add_library(filament_sdk_${name} STATIC IMPORTED)
  set_target_properties(filament_sdk_${name} PROPERTIES IMPORTED_LOCATION "${FILAMENT_LIB_DIR}/lib${name}.a")
  list(APPEND FILAMENT_LINK_TARGETS filament_sdk_${name})
endforeach()

# Native exceptions: filly reports errors by throwing, and the JavaScript glue reads their
# messages. Filament's libraries have no handlers but propagate them. -fno-rtti matches them:
# filly subclasses their polymorphic types.
set(FILLY_WEB_FLAGS -fwasm-exceptions -fno-rtti -DEMSCRIPTEN_HAS_UNBOUND_TYPE_NAMES=0)
# For example -fsanitize=address -g for a debugging build.
set(FILLY_WEB_EXTRA_FLAGS "" CACHE STRING "Extra compile and link flags")
separate_arguments(_extra UNIX_COMMAND "${FILLY_WEB_EXTRA_FLAGS}")
list(APPEND FILLY_WEB_FLAGS ${_extra})

add_executable(filly_web
  "${FILLY_SOURCE_DIR}/web/bindings.cpp"
  "${FILLY_SOURCE_DIR}/native/renderer.cpp" "${FILLY_SOURCE_DIR}/native/output_pass.cpp"
  "${FILLY_SOURCE_DIR}/native/gl_interop_web.cpp" "${FILLY_SOURCE_DIR}/native/archive_materials.cpp"
  "${FILLY_SOURCE_DIR}/native/gltf_prepare.cpp" "${FILLY_SOURCE_DIR}/native/json.cpp" "${FILLY_SOURCE_DIR}/native/shapes.cpp"
  "${FILLY_SOURCE_DIR}/native/webp_provider.cpp"
  "${FILLY_SOURCE_DIR}/native/vendor/meshoptimizer/vertexcodec.cpp"
  "${FILLY_SOURCE_DIR}/native/vendor/meshoptimizer/indexcodec.cpp"
  "${FILLY_SOURCE_DIR}/native/vendor/meshoptimizer/vertexfilter.cpp")
set_target_properties(filly_web PROPERTIES OUTPUT_NAME filly-core SUFFIX ".mjs")
target_compile_features(filly_web PRIVATE cxx_std_20)
target_compile_definitions(filly_web PRIVATE FILLY_MATERIALS_ARCHIVE=1)
target_compile_options(filly_web PRIVATE ${FILLY_WEB_FLAGS})
target_include_directories(filly_web PRIVATE "${FILLY_SOURCE_DIR}/native" "${FILAMENT_ROOT}/include"
  "${libwebp_SOURCE_DIR}/src")
target_link_libraries(filly_web PRIVATE filly_output_materials filly_material_archive webpdecoder
  ${FILAMENT_LINK_TARGETS})
target_link_options(filly_web PRIVATE ${FILLY_WEB_FLAGS} -lembind
  -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createFillyModule -sENVIRONMENT=web
  -sMIN_WEBGL_VERSION=2 -sMAX_WEBGL_VERSION=2
  # filly's JSON parser recurses up to depth 256; the default 64 KiB stack is too small.
  -sSTACK_SIZE=1048576 -sALLOW_MEMORY_GROWTH=1 -sMAXIMUM_MEMORY=2GB
  # The glue registers the page's context and textures (GL), writes files that glTF and
  # environment loads read by path (FS), and reads the messages of C++ exceptions.
  -sFORCE_FILESYSTEM=1
  -sEXPORTED_RUNTIME_METHODS=GL,FS,getExceptionMessage,decrementExceptionRefcount
  # GL calls go straight to the context: no client-side array or buffer-mapping emulation,
  # whose binding caches would go stale when the host page calls WebGL itself.
  -sFULL_ES3=0)
