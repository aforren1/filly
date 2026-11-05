# The MEX gateways for MATLAB (FILLY_MATLAB) and Octave (FILLY_OCTAVE), from one source file.
# See matlab/PLAN.md and docs/how-to/matlab.md.

# The files go next to the +filly package, so that one folder on the path holds the whole
# frontend. MATLAB loads filly_mex.mexa64 or .mexw64 and Octave loads filly_mex.mex, so builds
# for both can share the folder.
set(FILLY_MEX_OUTPUT_DIR "${CMAKE_CURRENT_SOURCE_DIR}" CACHE PATH "Directory for the built MEX files")

function(_filly_mex_output target)
  foreach(config IN ITEMS "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(${target} PROPERTIES LIBRARY_OUTPUT_DIRECTORY${config} "${FILLY_MEX_OUTPUT_DIR}")
  endforeach()
endfunction()

# filly.version reports FILLY_VERSION, the version of the git tags.
include("${PROJECT_SOURCE_DIR}/core/version.cmake")

# One toolbox package per MEX file, which users unzip and add to the path with SetupFilly:
#   cmake --install <build> --component <matlab|octave> --prefix <folder>
function(_filly_package target component)
  target_compile_definitions(${target} PRIVATE FILLY_VERSION="${FILLY_VERSION}")
  set(_where COMPONENT ${component} EXCLUDE_FROM_ALL)
  install(TARGETS ${target} LIBRARY DESTINATION . ${_where} RUNTIME DESTINATION . ${_where})
  install(DIRECTORY +filly DESTINATION . ${_where})
  install(FILES SetupFilly.m README.md "${PROJECT_SOURCE_DIR}/LICENSE" DESTINATION . ${_where})
  install(DIRECTORY "${PROJECT_SOURCE_DIR}/samples/" DESTINATION samples ${_where})
  filly_install_licenses(licenses ${_where})
endfunction()

# The packages have the sample models next to +filly. The build copies them there too, so that
# filly.samples.horse() also works in a build's output folder.
file(COPY "${PROJECT_SOURCE_DIR}/samples/" DESTINATION "${FILLY_MEX_OUTPUT_DIR}/samples")

# As for the Python module: the static C++ runtime avoids the older msvcp140.dll that MATLAB
# loads and the MinGW libstdc++ that Octave loads, and one shared UCRT keeps one heap per
# process.
function(_filly_msvc_mex target)
  target_compile_options(${target} PRIVATE /Zc:__cplusplus /EHsc)
  target_link_options(${target} PRIVATE /NODEFAULTLIB:libucrt.lib /DEFAULTLIB:ucrt.lib)
endfunction()

if(FILLY_MATLAB)
  find_package(Matlab REQUIRED COMPONENTS MX_LIBRARY)
  if(APPLE)
    # The gateway uses the C MEX API only. With the C++ API, matlab_add_mex also links the
    # MATLAB Engine and Data Array libraries and exports the C++ entry points, which this file
    # does not define; Apple's linker then fails, although MATLAB's own mex links the file.
    set(Matlab_HAS_CPP_API 0)
  endif()
  # On Linux and macOS, matlab_add_mex also applies MATLAB's export list, so the file exports
  # only mexFunction and the API version symbols.
  # CMAKE_DL_LIBS: dlsym and dladdr, for the check of which library provides Mesa's _glapi
  # symbols. Through LINK_TO, because FindMatlab links with the plain target_link_libraries
  # signature, which a keyword call on the same target cannot mix with.
  matlab_add_mex(NAME filly_mex MODULE SRC filly_mex.cpp LINK_TO filly_core ${CMAKE_DL_LIBS} R2018a)
  target_compile_features(filly_mex PRIVATE cxx_std_20)
  _filly_mex_output(filly_mex)
  _filly_package(filly_mex matlab)
  if(MSVC)
    _filly_msvc_mex(filly_mex)
  elseif(NOT APPLE)
    # MATLAB puts its own libstdc++ and libgcc_s first in the search path, and they are older
    # than the ones that the core is built with. Static copies make the file independent of
    # them; the export map keeps their symbols out of the process. On macOS, MATLAB and the
    # core use the system's libc++.
    target_link_options(filly_mex PRIVATE -static-libstdc++ -static-libgcc)
  endif()
endif()

if(FILLY_OCTAVE)
  # A plain module with mkoctfile's include directories, not a custom command that runs
  # mkoctfile --mex: mkoctfile cannot take filly_core's link libraries from CMake. mkoctfile
  # --mex links no Octave library either; the MEX API resolves against the running Octave.
  # The release build sets FILLY_OCTAVE_INCLUDE_DIRS instead: it compiles in the manylinux
  # container, which has no Octave, against headers copied from the oldest supported Octave.
  set(FILLY_OCTAVE_INCLUDE_DIRS "" CACHE STRING "Octave include directories; empty asks mkoctfile")
  set(_includes "${FILLY_OCTAVE_INCLUDE_DIRS}")
  if(NOT _includes)
    find_program(FILLY_MKOCTFILE NAMES mkoctfile REQUIRED)
    execute_process(COMMAND "${FILLY_MKOCTFILE}" -p INCFLAGS OUTPUT_VARIABLE _flags
      OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
    # On Windows, mkoctfile quotes each path, which has spaces ("C:\Program Files\GNU Octave").
    if(WIN32)
      separate_arguments(_flags WINDOWS_COMMAND "${_flags}")
    else()
      separate_arguments(_flags UNIX_COMMAND "${_flags}")
    endif()
    foreach(_flag IN LISTS _flags)
      if(_flag MATCHES "^-I(.+)$")
        file(TO_CMAKE_PATH "${CMAKE_MATCH_1}" _dir)
        # On Windows, one directory holds MinGW's C library, whose headers would replace MSVC's.
        if(NOT (WIN32 AND EXISTS "${_dir}/stddef.h"))
          list(APPEND _includes "${_dir}")
        endif()
      endif()
    endforeach()
    if(NOT _includes)
      message(FATAL_ERROR "${FILLY_MKOCTFILE} -p INCFLAGS returned no include directory")
    endif()
  endif()
  # Octave 10 moved the MEX API into liboctmex, with an ABI that differs from earlier releases,
  # so each side needs its own build. mkoctfile --mex links liboctmex and adds the symbol
  # __octave_mex_soversion__ (liboctmex's soname version); Octave 11 refuses a file without it.
  set(_octave_major 0)
  foreach(_dir IN LISTS _includes)
    if(EXISTS "${_dir}/version.h")
      file(STRINGS "${_dir}/version.h" _line REGEX "define OCTAVE_MAJOR_VERSION ")
      string(REGEX REPLACE ".*OCTAVE_MAJOR_VERSION ([0-9]+).*" "\\1" _octave_major "${_line}")
      break()
    endif()
  endforeach()
  set(_exports "mexFunction;")
  set(_octmex "")
  # Windows: the DLL that exports the MEX API. A DLL import names its DLL, so the file loads only
  # in Octave releases with that DLL name.
  set(_api_dll "")
  if(_octave_major GREATER_EQUAL 10 OR WIN32)
    set(FILLY_OCTAVE_LIBRARY_DIR "" CACHE PATH
      "Folder of Octave's liboctmex, or on Windows of its DLLs; empty asks mkoctfile")
    set(_libdir "${FILLY_OCTAVE_LIBRARY_DIR}")
    if(NOT _libdir)
      find_program(FILLY_MKOCTFILE NAMES mkoctfile REQUIRED)
      # Windows keeps the DLLs in BINDIR; OCTLIBDIR has only MinGW import libraries.
      if(WIN32)
        set(_variable BINDIR)
      else()
        set(_variable OCTLIBDIR)
      endif()
      execute_process(COMMAND "${FILLY_MKOCTFILE}" -p ${_variable} OUTPUT_VARIABLE _libdir
        OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
      file(TO_CMAKE_PATH "${_libdir}" _libdir)
    endif()
  endif()
  if(_octave_major GREATER_EQUAL 10)
    # liboctmex.so.1 on Linux, liboctmex.1.dylib on macOS, liboctmex-1.dll on Windows.
    if(WIN32)
      set(_pattern "liboctmex-*.dll")
      set(_regex "^liboctmex-([0-9]+)\\.dll$")
    elseif(APPLE)
      set(_pattern "liboctmex.*.dylib")
      set(_regex "^liboctmex\\.([0-9]+)\\.dylib$")
    else()
      set(_pattern "liboctmex.so.*")
      set(_regex "^liboctmex\\.so\\.([0-9]+)$")
    endif()
    file(GLOB _candidates RELATIVE "${_libdir}" "${_libdir}/${_pattern}")
    foreach(_name IN LISTS _candidates)
      if(_name MATCHES "${_regex}")
        set(_octmex "${_libdir}/${_name}")
        set(_soversion "${CMAKE_MATCH_1}")
      endif()
    endforeach()
    if(NOT _octmex)
      message(FATAL_ERROR "No ${_pattern} in ${_libdir}, which Octave ${_octave_major} MEX files link")
    endif()
    string(APPEND _exports " __octave_mex_soversion__;")
    set(_api_dll "${_octmex}")
  elseif(WIN32)
    # Before Octave 10, liboctinterp, the interpreter's DLL, exports the MEX API. Its name has
    # the library version (liboctinterp-10.dll in Octave 7.3), so one build serves one release
    # series; Psychtoolbox for Windows supports only Octave 7.3.
    file(GLOB _candidates RELATIVE "${_libdir}" "${_libdir}/liboctinterp-*.dll")
    foreach(_name IN LISTS _candidates)
      if(_name MATCHES "^liboctinterp-[0-9]+\\.dll$")
        set(_api_dll "${_libdir}/${_name}")
      endif()
    endforeach()
    if(NOT _api_dll)
      message(FATAL_ERROR "No liboctinterp-*.dll in ${_libdir}, which Octave ${_octave_major} MEX files link")
    endif()
  endif()
  add_library(filly_octave_mex MODULE filly_mex.cpp)
  if(WIN32)
    # Octave for Windows is built with MinGW, and its import libraries are in MinGW's format.
    # The MEX API has C linkage (mexproto.h), so an import library made from the DLL's export
    # table lets MSVC code call it; no C++ type crosses the boundary.
    get_filename_component(_tools "${CMAKE_LINKER}" DIRECTORY)
    find_program(FILLY_DUMPBIN NAMES dumpbin HINTS "${_tools}" REQUIRED)
    execute_process(COMMAND "${FILLY_DUMPBIN}" /nologo /exports "${_api_dll}" OUTPUT_VARIABLE _dump
      COMMAND_ERROR_IS_FATAL ANY)
    get_filename_component(_dll "${_api_dll}" NAME)
    set(_def "LIBRARY ${_dll}\nEXPORTS\n")
    string(REPLACE "\r" "" _dump "${_dump}")
    string(REPLACE "\n" ";" _lines "${_dump}")
    foreach(_line IN LISTS _lines)
      # "    ordinal hint RVA      name"
      if(_line MATCHES "^ +[0-9]+ +[0-9A-F]+ +[0-9A-F]+ +((mex|mx)[A-Za-z0-9_]+)$")
        string(APPEND _def "  ${CMAKE_MATCH_1}\n")
      endif()
    endforeach()
    if(NOT _def MATCHES "mexFunctionName")
      message(FATAL_ERROR "No MEX API in the exports of ${_api_dll}")
    endif()
    set(_import "${CMAKE_CURRENT_BINARY_DIR}/octave_mex_api.lib")
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/octave_mex_api.def" "${_def}")
    execute_process(COMMAND "${CMAKE_AR}" /nologo /machine:x64
      "/def:${CMAKE_CURRENT_BINARY_DIR}/octave_mex_api.def" "/out:${_import}" COMMAND_ERROR_IS_FATAL ANY)
    target_link_libraries(filly_octave_mex PRIVATE "${_import}")
  endif()
  if(_octmex)
    # On macOS, a linked liboctmex is recorded by its full path, which contains the Octave
    # version, so the file would load only in the Octave that it was built with. octave-cli and
    # octave-gui link liboctmex themselves, so the bundle can resolve the MEX API at load time.
    if(NOT WIN32 AND NOT APPLE)
      target_link_libraries(filly_octave_mex PRIVATE "${_octmex}")
    endif()
    target_compile_definitions(filly_octave_mex PRIVATE FILLY_OCTAVE_MEX_SOVERSION=${_soversion})
  endif()
  set_target_properties(filly_octave_mex PROPERTIES PREFIX "" OUTPUT_NAME filly_mex SUFFIX ".mex")
  _filly_mex_output(filly_octave_mex)
  _filly_package(filly_octave_mex octave)
  target_compile_features(filly_octave_mex PRIVATE cxx_std_20)
  target_include_directories(filly_octave_mex PRIVATE ${_includes})
  target_link_libraries(filly_octave_mex PRIVATE filly_core)
  # On Linux and macOS, Octave uses the system libstdc++, so the C++ runtime stays dynamic. Only
  # the entry point is exported, so that Filament and libwebp cannot bind to other copies in the
  # process. The gateway is not compiled with -fvisibility=hidden: Octave's mex.h gives
  # mexFunction no visibility attribute, and a hidden symbol cannot be exported.
  if(WIN32)
    # A DLL exports only what it names. Octave's mex.h declares mexFunction without dllexport.
    _filly_msvc_mex(filly_octave_mex)
    target_link_options(filly_octave_mex PRIVATE /EXPORT:mexFunction)
    if(_octmex)
      target_link_options(filly_octave_mex PRIVATE /EXPORT:__octave_mex_soversion__,DATA)
    endif()
  elseif(APPLE)
    # Apple's linker takes a list of C names, with the underscore prefix. The bundle leaves the
    # MEX API to the running Octave, which loaded liboctmex already.
    string(REGEX MATCHALL "[A-Za-z_]+" _names "${_exports}")
    list(TRANSFORM _names PREPEND "_")
    list(JOIN _names "\n" _list)
    set(_map "${CMAKE_CURRENT_BINARY_DIR}/filly_octave_mex.exp")
    file(WRITE "${_map}" "${_list}\n")
    target_link_options(filly_octave_mex PRIVATE "LINKER:-exported_symbols_list,${_map}"
      "LINKER:-undefined,dynamic_lookup")
  else()
    set(_map "${CMAKE_CURRENT_BINARY_DIR}/filly_octave_mex.map")
    file(WRITE "${_map}" "{\n  global: ${_exports}\n  local: *;\n};\n")
    target_link_options(filly_octave_mex PRIVATE "LINKER:--version-script=${_map}")
  endif()
  if(NOT WIN32)
    set_property(TARGET filly_octave_mex APPEND PROPERTY LINK_DEPENDS "${_map}")
  endif()
endif()
