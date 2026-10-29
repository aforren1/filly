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

# filly.version reports the version of the vX.Y.Z tags, the string that setuptools-scm computes
# for the Python package (pyproject.toml): X.Y.Z on a clean tagged commit, else X.Y.(Z+1).devN,
# N commits after the tag. It is computed at configure time, so a new commit shows after the
# next configure. A source tree without git history needs FILLY_VERSION, or reports 0.0.0.
set(FILLY_VERSION "" CACHE STRING "Version to report instead of the one from the git tags")
if(NOT FILLY_VERSION)
  set(FILLY_VERSION 0.0.0)
  find_package(Git QUIET)
  if(Git_FOUND)
    execute_process(COMMAND "${GIT_EXECUTABLE}" describe --dirty --tags --long --match "v[0-9]*"
                    WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}" OUTPUT_VARIABLE _describe
                    RESULT_VARIABLE _result OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(_result EQUAL 0 AND _describe MATCHES "^v([0-9]+)\\.([0-9]+)\\.([0-9]+)-([0-9]+)-g[0-9a-f]+(-dirty)?$")
      if(CMAKE_MATCH_4 EQUAL 0 AND NOT CMAKE_MATCH_5)
        set(FILLY_VERSION "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}.${CMAKE_MATCH_3}")
      else()
        math(EXPR _patch "${CMAKE_MATCH_3} + 1")
        set(FILLY_VERSION "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}.${_patch}.dev${CMAKE_MATCH_4}")
      endif()
    else()
      # No tag in the history, for example in a shallow clone: setuptools-scm counts from 0.0.
      execute_process(COMMAND "${GIT_EXECUTABLE}" rev-list --count HEAD
                      WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}" OUTPUT_VARIABLE _count
                      RESULT_VARIABLE _result OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
      if(_result EQUAL 0)
        set(FILLY_VERSION "0.0.1.dev${_count}")
      endif()
    endif()
  endif()
endif()
message(STATUS "filly version ${FILLY_VERSION}")

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

if(FILLY_MATLAB)
  find_package(Matlab REQUIRED COMPONENTS MX_LIBRARY)
  # On Linux, matlab_add_mex also applies MATLAB's export map, so the file exports only
  # mexFunction and the API version symbols.
  # CMAKE_DL_LIBS: dlsym and dladdr, for the check of which library provides Mesa's _glapi
  # symbols. Through LINK_TO, because FindMatlab links with the plain target_link_libraries
  # signature, which a keyword call on the same target cannot mix with.
  matlab_add_mex(NAME filly_mex MODULE SRC filly_mex.cpp LINK_TO filly_core ${CMAKE_DL_LIBS} R2018a)
  target_compile_features(filly_mex PRIVATE cxx_std_20)
  _filly_mex_output(filly_mex)
  _filly_package(filly_mex matlab)
  if(MSVC)
    target_compile_options(filly_mex PRIVATE /Zc:__cplusplus /EHsc)
    # As for the Python module: the static C++ runtime avoids the older msvcp140.dll that MATLAB
    # loads, and one shared UCRT keeps one heap per process.
    target_link_options(filly_mex PRIVATE /NODEFAULTLIB:libucrt.lib /DEFAULTLIB:ucrt.lib)
  else()
    # MATLAB puts its own libstdc++ and libgcc_s first in the search path, and they are older
    # than the ones that the core is built with. Static copies make the file independent of
    # them; the export map keeps their symbols out of the process.
    target_link_options(filly_mex PRIVATE -static-libstdc++ -static-libgcc)
  endif()
endif()

if(FILLY_OCTAVE)
  if(WIN32)
    message(FATAL_ERROR "FILLY_OCTAVE supports Linux only. See phase 6 of matlab/PLAN.md.")
  endif()
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
    separate_arguments(_flags UNIX_COMMAND "${_flags}")
    foreach(_flag IN LISTS _flags)
      if(_flag MATCHES "^-I(.+)$")
        list(APPEND _includes "${CMAKE_MATCH_1}")
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
  if(_octave_major GREATER_EQUAL 10)
    set(FILLY_OCTAVE_LIBRARY_DIR "" CACHE PATH "Folder of Octave's liboctmex; empty asks mkoctfile")
    set(_libdir "${FILLY_OCTAVE_LIBRARY_DIR}")
    if(NOT _libdir)
      find_program(FILLY_MKOCTFILE NAMES mkoctfile REQUIRED)
      execute_process(COMMAND "${FILLY_MKOCTFILE}" -p OCTLIBDIR OUTPUT_VARIABLE _libdir
        OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
    endif()
    file(GLOB _candidates RELATIVE "${_libdir}" "${_libdir}/liboctmex.so.*")
    foreach(_name IN LISTS _candidates)
      if(_name MATCHES "^liboctmex\\.so\\.([0-9]+)$")
        set(_octmex "${_libdir}/${_name}")
        set(_soversion "${CMAKE_MATCH_1}")
      endif()
    endforeach()
    if(NOT _octmex)
      message(FATAL_ERROR "No liboctmex.so.<version> in ${_libdir}, which Octave ${_octave_major} MEX files link")
    endif()
    string(APPEND _exports " __octave_mex_soversion__;")
  endif()
  add_library(filly_octave_mex MODULE filly_mex.cpp)
  if(_octmex)
    target_link_libraries(filly_octave_mex PRIVATE "${_octmex}")
    target_compile_definitions(filly_octave_mex PRIVATE FILLY_OCTAVE_MEX_SOVERSION=${_soversion})
  endif()
  set_target_properties(filly_octave_mex PROPERTIES PREFIX "" OUTPUT_NAME filly_mex SUFFIX ".mex")
  _filly_mex_output(filly_octave_mex)
  _filly_package(filly_octave_mex octave)
  target_compile_features(filly_octave_mex PRIVATE cxx_std_20)
  target_include_directories(filly_octave_mex PRIVATE ${_includes})
  target_link_libraries(filly_octave_mex PRIVATE filly_core)
  # Octave uses the system libstdc++, so the C++ runtime stays dynamic. Only the entry point is
  # exported, so that Filament and libwebp cannot bind to other copies in the process. The
  # gateway is not compiled with -fvisibility=hidden: Octave's mex.h gives mexFunction no
  # visibility attribute, and a hidden symbol cannot be exported.
  set(_map "${CMAKE_CURRENT_BINARY_DIR}/filly_octave_mex.map")
  file(WRITE "${_map}" "{\n  global: ${_exports}\n  local: *;\n};\n")
  target_link_options(filly_octave_mex PRIVATE "LINKER:--version-script=${_map}")
  set_property(TARGET filly_octave_mex APPEND PROPERTY LINK_DEPENDS "${_map}")
endif()
