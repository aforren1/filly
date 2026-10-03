# Materials of filly's output passes (native/output_pass.h): the encode pass, with and without
# Filament's color grading, and FXAA. Every build compiles them with the SDK's matc and embeds
# them, so the output passes need no run-time material compiler. The web build has none.
#
# Inputs: FILAMENT_ROOT and FILLY_SOURCE_DIR. Outputs: the target filly_output_materials, a
# static library that defines filly_encode_material_*, filly_encode_graded_material_*, and
# filly_fxaa_material_* (_data and _size).

set(FILLY_MATERIAL_TOOLS "${FILAMENT_ROOT}/bin" CACHE PATH
  "Directory with matc and uberz from the same Filament version as the SDK")
find_program(FILLY_MATC matc PATHS "${FILLY_MATERIAL_TOOLS}" NO_DEFAULT_PATH NO_CMAKE_FIND_ROOT_PATH)
if(NOT FILLY_MATC)
  message(FATAL_ERROR "filly's output passes need matc in ${FILLY_MATERIAL_TOOLS}. See docs/how-to/build.md.")
endif()
# Desktop GL; the web build passes -p mobile (GLSL ES 3.00).
set(FILLY_OUTPUT_MATC_FLAGS -a opengl -p desktop CACHE STRING "matc flags for the output-pass materials")

set(_out_dir "${CMAKE_CURRENT_BINARY_DIR}/output_materials")
set(_out_src "${FILLY_SOURCE_DIR}/native/materials")
file(MAKE_DIRECTORY "${_out_dir}")
file(READ "${_out_src}/encode.mat.in" _encode)
foreach(GRADED 0 1)
  if(GRADED)
    set(NAME "filly encode graded")
    set(_symbol filly_encode_graded_material)
    # The scene viewport (x0, y0, x1, y1) in pixels, and the clear color.
    set(GRADED_PARAMETERS ",\n        { type : float4, name : inner, precision : high },\n        { type : float4, name : background, precision : high }")
  else()
    set(NAME "filly encode")
    set(_symbol filly_encode_material)
    set(GRADED_PARAMETERS "")
  endif()
  file(CONFIGURE OUTPUT "${_out_dir}/${_symbol}.mat" CONTENT "${_encode}" @ONLY)
endforeach()
configure_file("${_out_src}/fxaa.mat" "${_out_dir}/filly_fxaa_material.mat" COPYONLY)

set(_out_sources "")
foreach(_symbol filly_encode_material filly_encode_graded_material filly_fxaa_material)
  add_custom_command(OUTPUT "${_out_dir}/${_symbol}.filamat"
    COMMAND "${FILLY_MATC}" ${FILLY_OUTPUT_MATC_FLAGS} -o "${_symbol}.filamat" "${_symbol}.mat"
    DEPENDS "${_out_dir}/${_symbol}.mat" "${FILLY_MATC}"
    WORKING_DIRECTORY "${_out_dir}"
    COMMENT "matc ${_symbol}"
    VERBATIM)
  add_custom_command(OUTPUT "${_out_dir}/${_symbol}.c"
    COMMAND "${CMAKE_COMMAND}" "-DINPUT=${_out_dir}/${_symbol}.filamat" "-DOUTPUT=${_out_dir}/${_symbol}.c"
      "-DNAME=${_symbol}" -P "${_out_src}/embed.cmake"
    DEPENDS "${_out_dir}/${_symbol}.filamat" "${_out_src}/embed.cmake"
    COMMENT "Embed ${_symbol}"
    VERBATIM)
  list(APPEND _out_sources "${_out_dir}/${_symbol}.c")
endforeach()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${_out_src}/encode.mat.in" "${_out_src}/fxaa.mat" "${_out_src}/output.cmake")
add_library(filly_output_materials STATIC ${_out_sources})
set_target_properties(filly_output_materials PROPERTIES POSITION_INDEPENDENT_CODE ON LINKER_LANGUAGE C)
