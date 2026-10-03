# libwebp decodes EXT_texture_webp images; the Filament SDK is built without it. The release
# archive is pinned by SHA-256 (signed by the WebP release key 6B0E6B70976DE303EDF2F601F9C3D6BDB8232B5D).
# Offline builds can set FETCHCONTENT_SOURCE_DIR_LIBWEBP to an extracted copy of the same release.
include(FetchContent)
set(FILLY_LIBWEBP_URL "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.5.0.tar.gz"
  CACHE STRING "libwebp 1.5.0 release archive")
FetchContent_Declare(libwebp
  URL "${FILLY_LIBWEBP_URL}"
  URL_HASH SHA256=7d6fab70cf844bf6769077bd5d7a74893f8ffd4dfb42861745750c63c2a5c92c
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  EXCLUDE_FROM_ALL)
set(WEBP_LINK_STATIC ON CACHE BOOL "" FORCE)
set(WEBP_BUILD_ANIM_UTILS OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_CWEBP OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_DWEBP OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_GIF2WEBP OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_IMG2WEBP OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_VWEBP OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_WEBPINFO OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_LIBWEBPMUX OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_WEBPMUX OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_EXTRAS OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_WEBP_JS OFF CACHE BOOL "" FORCE)
set(WEBP_BUILD_FUZZTEST OFF CACHE BOOL "" FORCE)
# The core links it into a shared module; libwebp's own CMake predates CMP0091.
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_POLICY_DEFAULT_CMP0091 NEW)
set(BUILD_SHARED_LIBS OFF)
if(NOT WIN32)
  # libwebp marks its API visibility("default"). The module's version script already keeps it
  # unexported; hidden visibility also lets the compiler bind calls directly, without the PLT.
  set(CMAKE_C_VISIBILITY_PRESET hidden)
  add_compile_definitions(WEBP_EXTERN=extern)
endif()
FetchContent_MakeAvailable(libwebp)
