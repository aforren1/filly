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

if(FILLY_MATLAB)
  find_package(Matlab REQUIRED COMPONENTS MX_LIBRARY)
  # On Linux, matlab_add_mex also applies MATLAB's export map, so the file exports only
  # mexFunction and the API version symbols.
  matlab_add_mex(NAME filly_mex MODULE SRC filly_mex.cpp LINK_TO filly_core R2018a)
  target_compile_features(filly_mex PRIVATE cxx_std_20)
  _filly_mex_output(filly_mex)
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
  find_program(FILLY_MKOCTFILE NAMES mkoctfile REQUIRED)
  execute_process(COMMAND "${FILLY_MKOCTFILE}" -p INCFLAGS OUTPUT_VARIABLE _flags
    OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
  separate_arguments(_flags UNIX_COMMAND "${_flags}")
  set(_includes "")
  foreach(_flag IN LISTS _flags)
    if(_flag MATCHES "^-I(.+)$")
      list(APPEND _includes "${CMAKE_MATCH_1}")
    endif()
  endforeach()
  if(NOT _includes)
    message(FATAL_ERROR "${FILLY_MKOCTFILE} -p INCFLAGS returned no include directory")
  endif()
  add_library(filly_octave_mex MODULE filly_mex.cpp)
  set_target_properties(filly_octave_mex PROPERTIES PREFIX "" OUTPUT_NAME filly_mex SUFFIX ".mex")
  _filly_mex_output(filly_octave_mex)
  target_compile_features(filly_octave_mex PRIVATE cxx_std_20)
  target_include_directories(filly_octave_mex PRIVATE ${_includes})
  target_link_libraries(filly_octave_mex PRIVATE filly_core)
  # Octave uses the system libstdc++, so the C++ runtime stays dynamic. Only the entry point is
  # exported, so that Filament and libwebp cannot bind to other copies in the process. The
  # gateway is not compiled with -fvisibility=hidden: Octave's mex.h gives mexFunction no
  # visibility attribute, and a hidden symbol cannot be exported.
  set(_map "${CMAKE_CURRENT_BINARY_DIR}/filly_octave_mex.map")
  file(WRITE "${_map}" "{\n  global: mexFunction;\n  local: *;\n};\n")
  target_link_options(filly_octave_mex PRIVATE "LINKER:--version-script=${_map}")
  set_property(TARGET filly_octave_mex APPEND PROPERTY LINK_DEPENDS "${_map}")
endif()
