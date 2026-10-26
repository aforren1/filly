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

# The frontend has its own version, apart from the Python package: filly.version reports it,
# and the release tag matlab-v<version> must match it.
file(STRINGS VERSION FILLY_FRONTEND_VERSION LIMIT_COUNT 1)

# One toolbox package per MEX file, which users unzip and add to the path with SetupFilly:
#   cmake --install <build> --component <matlab|octave> --prefix <folder>
function(_filly_package target component)
  target_compile_definitions(${target} PRIVATE FILLY_FRONTEND_VERSION="${FILLY_FRONTEND_VERSION}")
  set(_where COMPONENT ${component} EXCLUDE_FROM_ALL)
  install(TARGETS ${target} LIBRARY DESTINATION . ${_where} RUNTIME DESTINATION . ${_where})
  install(DIRECTORY +filly DESTINATION . ${_where})
  install(FILES SetupFilly.m VERSION README.md "${PROJECT_SOURCE_DIR}/LICENSE" DESTINATION . ${_where})
  filly_install_licenses(licenses ${_where})
endfunction()

if(FILLY_MATLAB)
  find_package(Matlab REQUIRED COMPONENTS MX_LIBRARY)
  # On Linux, matlab_add_mex also applies MATLAB's export map, so the file exports only
  # mexFunction and the API version symbols.
  matlab_add_mex(NAME filly_mex MODULE SRC filly_mex.cpp LINK_TO filly_core R2018a)
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
    # dlsym and dladdr, for the check of which library provides Mesa's _glapi symbols.
    target_link_libraries(filly_mex PRIVATE ${CMAKE_DL_LIBS})
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
