# filly's renderer: the C++ facade in renderer.h over Filament, as the static library filly_core,
# which every frontend links. Desktop builds (Windows, Linux, and macOS) use the GL interop in
# gl_interop.cpp; Emscripten builds use WebGL2 through gl_interop_web.cpp.
#
# Inputs:
#   FILAMENT_ROOT         Filament 1.77.1 SDK: the desktop SDK (docs/how-to/build.md), or the
#                         WebAssembly SDK from web/tools/build_filament_web.sh
#   FILLY_MATERIAL_TOOLS  matc and uberz of Filament 1.77.1 for the build machine
#                         (materials/output.cmake)
set(FILLY_CORE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")

if(EMSCRIPTEN)
  set(FILAMENT_ROOT "" CACHE PATH "Filament 1.77.1 WebAssembly SDK directory")
  if(NOT EXISTS "${FILAMENT_ROOT}/include/filament/Engine.h")
    message(FATAL_ERROR "Set FILAMENT_ROOT to the WebAssembly SDK from web/tools/build_filament_web.sh")
  endif()
  set(FILAMENT_LIB_DIR "${FILAMENT_ROOT}/lib/x86_64" CACHE PATH "Static libraries of the WebAssembly SDK")
  set(FILAMENT_LIBRARIES gltfio_core filament-iblprefilter filament backend filabridge filaflat geometry
    ibl-lite ktxreader image basis_transcoder stb dracodec meshoptimizer mikktspace uberzlib zstd utils abseil)

  # GLSL ES 3.00 for WebGL2.
  set(FILLY_OUTPUT_MATC_FLAGS -a opengl -p mobile CACHE STRING "matc flags for the output-pass materials")
  set(FILLY_MATC_FLAGS -a opengl -p mobile -V stereo,ssr,vsm CACHE STRING "matc flags for the archive")
  # Off by default: a module with SIMD instructions does not load at all in browsers without
  # WebAssembly SIMD (before Chrome 91, Firefox 89, and Safari 16.4). It applies to filly's own
  # code, the meshoptimizer decoders, and libwebp; the Filament SDK keeps the flags it was built with.
  option(FILLY_WEB_SIMD "Compile filly's code and libwebp for WebAssembly SIMD" OFF)
  set(WEBP_ENABLE_SIMD ${FILLY_WEB_SIMD} CACHE BOOL "" FORCE)

  # Native exceptions: filly reports errors by throwing, and the JavaScript glue reads their
  # messages. Filament's libraries have no handlers but propagate them. -fno-rtti matches them:
  # filly subclasses their polymorphic types.
  set(FILLY_WEB_FLAGS -fwasm-exceptions -fno-rtti -DEMSCRIPTEN_HAS_UNBOUND_TYPE_NAMES=0)
  # For example -fsanitize=address -g for a debugging build.
  set(FILLY_WEB_EXTRA_FLAGS "" CACHE STRING "Extra compile and link flags")
  separate_arguments(_extra UNIX_COMMAND "${FILLY_WEB_EXTRA_FLAGS}")
  list(APPEND FILLY_WEB_FLAGS ${_extra})
  if(FILLY_WEB_SIMD)
    list(APPEND FILLY_WEB_FLAGS -msimd128)
  endif()
else()
  # tools/fetch_filament.py extracts the Windows and macOS release archives to these folders.
  if(APPLE)
    set(_sdk "${PROJECT_SOURCE_DIR}/.deps/filament-mac")
  else()
    set(_sdk "${PROJECT_SOURCE_DIR}/.deps")
  endif()
  set(FILAMENT_ROOT "${_sdk}" CACHE PATH "Filament 1.77.1 SDK directory")
  if(NOT EXISTS "${FILAMENT_ROOT}/include/filament/Engine.h")
    message(FATAL_ERROR "Filament SDK missing. See docs/how-to/build.md for the platform SDK and FILAMENT_ROOT.")
  endif()
  # The macOS release archive has arm64 libraries only. CMAKE_OSX_ARCHITECTURES overrides the
  # processor name for a cross build, so it is checked too.
  if(APPLE)
    set(_supported FALSE)
    if(CMAKE_OSX_ARCHITECTURES)
      if(CMAKE_OSX_ARCHITECTURES STREQUAL "arm64")
        set(_supported TRUE)
      endif()
    elseif(CMAKE_SYSTEM_PROCESSOR STREQUAL "arm64")
      set(_supported TRUE)
    endif()
  elseif(CMAKE_SIZEOF_VOID_P EQUAL 8 AND CMAKE_SYSTEM_PROCESSOR MATCHES "^(AMD64|amd64|x86_64)$"
      AND (WIN32 OR CMAKE_SYSTEM_NAME STREQUAL "Linux"))
    set(_supported TRUE)
  else()
    set(_supported FALSE)
  endif()
  if(NOT _supported)
    message(FATAL_ERROR "Supported targets are Windows and Linux x86_64, and macOS arm64.")
  endif()
  if(WIN32)
    set(FILAMENT_LIB_DIR "${FILAMENT_ROOT}/lib/x86_64/mt")
  elseif(APPLE)
    set(FILAMENT_LIB_DIR "${FILAMENT_ROOT}/lib/arm64")
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
  if(WIN32 OR APPLE)
    list(APPEND FILAMENT_LIBRARIES bluevk)
  endif()
endif()
foreach(name IN LISTS FILAMENT_LIBRARIES)
  add_library(filament_sdk_${name} STATIC IMPORTED)
  set_target_properties(filament_sdk_${name} PROPERTIES
    IMPORTED_LOCATION "${FILAMENT_LIB_DIR}/${CMAKE_STATIC_LIBRARY_PREFIX}${name}${CMAKE_STATIC_LIBRARY_SUFFIX}")
  list(APPEND FILAMENT_LINK_TARGETS filament_sdk_${name})
endforeach()

include(libwebp.cmake)

# The output-pass materials and the material archive are embedded as C arrays.
include(materials/output.cmake)

# glTF materials come from filly's precompiled material archive (materials/), built here with the
# SDK's matc and uberz. The module has no material compiler. See
# docs/explanation/material-precompilation.md.
include(materials/materials.cmake)

add_library(filly_core STATIC renderer.cpp output_pass.cpp archive_materials.cpp gltf_prepare.cpp json.cpp
  shapes.cpp webp_provider.cpp
  vendor/meshoptimizer/vertexcodec.cpp vendor/meshoptimizer/indexcodec.cpp vendor/meshoptimizer/vertexfilter.cpp)
target_compile_features(filly_core PUBLIC cxx_std_20)
target_include_directories(filly_core PUBLIC "${FILLY_CORE_DIR}"
  PRIVATE "${FILAMENT_ROOT}/include" "${libwebp_SOURCE_DIR}/src")
target_link_libraries(filly_core PRIVATE filly_output_materials filly_material_archive webpdecoder)
if(EMSCRIPTEN)
  target_sources(filly_core PRIVATE gl_interop_web.cpp)
  # libwebp.cmake turns position-independent code on for this directory. The module is one
  # statically linked executable, and -fPIC would only add GOT indirection to the core.
  set_target_properties(filly_core PROPERTIES POSITION_INDEPENDENT_CODE OFF)
  # PUBLIC: the bindings and the final link need the same exception and RTTI model.
  target_compile_options(filly_core PUBLIC ${FILLY_WEB_FLAGS})
  target_link_options(filly_core INTERFACE ${FILLY_WEB_FLAGS})
  target_link_libraries(filly_core PRIVATE ${FILAMENT_LINK_TARGETS})
else()
  target_sources(filly_core PRIVATE gl_interop.cpp egl_platform.cpp)
  set_target_properties(filly_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
  if(MSVC)
    target_compile_options(filly_core PRIVATE /Zc:__cplusplus /bigobj /EHsc)
    target_link_libraries(filly_core PRIVATE ${FILAMENT_LINK_TARGETS} opengl32 gdi32 user32 shlwapi ws2_32)
  elseif(APPLE)
    target_sources(filly_core PRIVATE cgl_platform.cpp)
    # Apple's linker searches static archives again until no symbol is left undefined, so the
    # circular references need no group. Filament's backend archive also holds the Metal,
    # Vulkan, and Cocoa platforms, which its platform factory references.
    target_link_libraries(filly_core PRIVATE ${FILAMENT_LINK_TARGETS}
      "-framework OpenGL" "-framework AppKit" "-framework Foundation" "-framework CoreFoundation"
      "-framework CoreVideo" "-framework Metal" "-framework QuartzCore"
      # Copy targets (CopyTarget in renderer.h) cross between contexts through an IOSurface.
      "-framework IOSurface")
    # The release SDK is built without RTTI, so classes that derive from Filament's polymorphic
    # types cannot refer to their type information. The core uses no RTTI (see the web build);
    # the bindings keep it for nanobind.
    target_compile_options(filly_core PRIVATE -fvisibility=hidden -fno-rtti)
  else()
    # Filament's static archives contain circular references.
    target_link_libraries(filly_core PRIVATE -Wl,--start-group ${FILAMENT_LINK_TARGETS} -Wl,--end-group
      OpenGL::GL X11::X11 Threads::Threads ${CMAKE_DL_LIBS})
    target_compile_options(filly_core PRIVATE -fvisibility=hidden)
  endif()
endif()

# The licenses of the code that the core links, for every package that ships it. They are copied
# at configure time because libwebp's source folder is known only in this directory's scope.
set(FILLY_LICENSE_DIR "${PROJECT_BINARY_DIR}/licenses" CACHE INTERNAL "")
configure_file("${FILAMENT_ROOT}/LICENSE" "${FILLY_LICENSE_DIR}/Filament.txt" COPYONLY)
configure_file(vendor/meshoptimizer/LICENSE.md "${FILLY_LICENSE_DIR}/meshoptimizer.txt" COPYONLY)
configure_file(vendor/cgltf/LICENSE "${FILLY_LICENSE_DIR}/cgltf.txt" COPYONLY)
configure_file("${libwebp_SOURCE_DIR}/COPYING" "${FILLY_LICENSE_DIR}/libwebp.txt" COPYONLY)
configure_file("${libwebp_SOURCE_DIR}/PATENTS" "${FILLY_LICENSE_DIR}/libwebp-PATENTS.txt" COPYONLY)
function(filly_install_licenses destination)
  install(DIRECTORY "${FILLY_LICENSE_DIR}/" DESTINATION ${destination} ${ARGN})
endfunction()
