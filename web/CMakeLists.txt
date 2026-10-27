# filly's web module: the bindings in this folder over filly_core, compiled with Emscripten for
# WebGL2 into filly-core.js and filly-core.wasm, which filly.js wraps. Configure the repository
# root with Emscripten's toolchain file; web/tools/build_filly_web.sh does that. The core's
# CMakeLists.txt has the web inputs and options (FILAMENT_ROOT, FILLY_WEB_SIMD).

add_executable(filly_web bindings.cpp)
set_target_properties(filly_web PROPERTIES OUTPUT_NAME filly-core SUFFIX ".js")
target_link_libraries(filly_web PRIVATE filly_core)
target_link_options(filly_web PRIVATE -lembind
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
  -sFULL_ES3=0
  # Records the GL state that the core leaves, for the hand-back to the page.
  "--js-library=${CMAKE_CURRENT_SOURCE_DIR}/gl_tracking.js")
set_target_properties(filly_web PROPERTIES LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/gl_tracking.js")
