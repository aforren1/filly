# filly's material archive: one package per entry and blending mode, compiled with the SDK's
# matc, packed with uberz, and embedded in the module as a byte array. See
# docs/explanation/material-precompilation.md for the entry list.
#
# Inputs: FILAMENT_ROOT, FILLY_SOURCE_DIR, and FILLY_MATERIAL_TOOLS and FILLY_MATC from
# output.cmake. Outputs: the target filly_material_archive, a static library that defines
# filly_material_archive_data and filly_material_archive_size.

find_program(FILLY_UBERZ uberz PATHS "${FILLY_MATERIAL_TOOLS}" NO_DEFAULT_PATH NO_CMAKE_FIND_ROOT_PATH)
find_program(FILLY_MATINFO matinfo PATHS "${FILLY_MATERIAL_TOOLS}" NO_DEFAULT_PATH NO_CMAKE_FIND_ROOT_PATH)
if(NOT FILLY_MATC OR NOT FILLY_UBERZ)
  message(FATAL_ERROR "FILLY_MATERIALS=archive needs matc and uberz in ${FILLY_MATERIAL_TOOLS}. "
    "See docs/how-to/build.md.")
endif()

set(_dir "${CMAKE_CURRENT_BINARY_DIR}/materials")
set(_src "${FILLY_SOURCE_DIR}/native/materials")
file(MAKE_DIRECTORY "${_dir}")
# Desktop GL only. filly uses no stereo, screen-space reflection, or VSM shadow variants.
set(FILLY_MATC_FLAGS -a opengl -p desktop -V stereo,ssr,vsm CACHE STRING "matc flags for the archive")

# Parameters: "type name" or "type name high" (high precision).
# Appends to the variable named out; local names are prefixed so that they cannot shadow it.
function(_filly_parameters out)
  set(_fp_text "")
  foreach(_fp_item IN LISTS ARGN)
    string(REPLACE " " ";" _fp_parts "${_fp_item}")
    list(GET _fp_parts 0 _fp_type)
    list(GET _fp_parts 1 _fp_name)
    list(LENGTH _fp_parts _fp_count)
    if(_fp_count GREATER 2)
      string(APPEND _fp_text "        { type : ${_fp_type}, name : ${_fp_name}, precision : high },\n")
    else()
      string(APPEND _fp_text "        { type : ${_fp_type}, name : ${_fp_name} },\n")
    endif()
  endforeach()
  set(${out} "${${out}}${_fp_text}" PARENT_SCOPE)
endfunction()
# An extension texture role: its sampler slot, UV set, and UV matrix.
function(_filly_roles out)
  set(_fr_items "")
  foreach(_fr_role IN LISTS ARGN)
    list(APPEND _fr_items "int ${_fr_role}Slot" "int ${_fr_role}Index" "mat3 ${_fr_role}UvMatrix high")
  endforeach()
  set(_fr_text "")
  _filly_parameters(_fr_text ${_fr_items})
  set(${out} "${${out}}${_fr_text}" PARENT_SCOPE)
endfunction()

set(_core
  "int baseColorIndex" "float4 baseColorFactor" "sampler2d baseColorMap" "mat3 baseColorUvMatrix high"
  "int metallicRoughnessIndex" "float metallicFactor" "float roughnessFactor"
  "sampler2d metallicRoughnessMap" "mat3 metallicRoughnessUvMatrix high"
  "int normalIndex" "float normalScale" "sampler2d normalMap" "mat3 normalUvMatrix high"
  "int aoIndex" "float aoStrength" "sampler2d occlusionMap" "mat3 occlusionUvMatrix high"
  "int emissiveIndex" "float3 emissiveFactor" "float emissiveStrength" "sampler2d emissiveMap"
  "mat3 emissiveUvMatrix high")

# Refraction code shared with the runtime path: the three raw strings in native/refraction.h,
# in variables rather than a list because the GLSL contains semicolons.
file(READ "${FILLY_SOURCE_DIR}/native/refraction.h" _hook)
foreach(_i RANGE 2)
  string(FIND "${_hook}" "R\"SHADER(" _start)
  string(FIND "${_hook}" ")SHADER\"" _end)
  if(_start LESS 0 OR _end LESS _start)
    message(FATAL_ERROR "native/refraction.h must contain three R\"SHADER(...)SHADER\" strings")
  endif()
  math(EXPR _begin "${_start} + 9")
  math(EXPR _length "${_end} - ${_begin}")
  string(SUBSTRING "${_hook}" ${_begin} ${_length} _refraction_${_i})
  math(EXPR _next "${_end} + 8")
  string(SUBSTRING "${_hook}" ${_next} -1 _hook)
endforeach()

# Only entries with volume have vertex code. A material with vertex code gets its own depth
# program; without it, shadow maps use the engine's default depth program, as the runtime path's
# materials do, and shadow edges stay the same.
set(_volume_vertex [=[
vertex {
    void materialVertex(inout MaterialVertexInputs material) {
        highp mat4 worldFromModel = getWorldFromModelMatrix();
        float scale = (length(worldFromModel[0].xyz) + length(worldFromModel[1].xyz)
                + length(worldFromModel[2].xyz)) / 3.0;
        material.volumeScale = vec4(scale);
    }
}]=])

# Entry kinds. The uberz spec of each package carries one flag with the kind's name, which the
# provider looks up; see native/archive_materials.cpp.
set(_kinds LitCore LitExtended LitSpecular LitAnisotropy RefractionThin RefractionThinSpecular
  RefractionSolid RefractionSolidSpecular RefractionThinAnisotropy RefractionSolidAnisotropy
  SpecularGlossiness Unlit DiffuseTransmission)
set(_blendings opaque masked fade)

set(_packages "")
set(_names "")
foreach(_kind IN LISTS _kinds)
  set(template "${_src}/surface.mat.in")
  set(shading lit)
  set(OPTICS "")
  set(VERTEX_BLOCK "")
  set(DEFINES "")
  set(REFRACTION_PREFIX "")
  set(REFRACTION_MATERIAL "")
  set(REFRACTION_SUFFIX "")
  set(PARAMETERS "")
  set(slots 0)
  set(lit FALSE)
  set(lobes "")
  # KHR_materials_specular inputs change the F90 of Filament's isotropic lobe even at their
  # defaults, so only the Specular entries have them. The anisotropic lobe always uses the F90
  # from F0, so the anisotropic entries serve materials with and without the extension.
  if(_kind MATCHES "Specular$|Anisotropy$")
    list(APPEND lobes SPECULAR)
  endif()
  if(_kind MATCHES "^Lit")
    # LitCore has no extension textures.
    set(slots 0)
    set(lit TRUE)
    if(NOT _kind STREQUAL "LitCore")
      set(slots 4)
      list(APPEND lobes CLEARCOAT SHEEN IRIDESCENCE)
    endif()
    if(_kind STREQUAL "LitAnisotropy")
      list(APPEND lobes ANISOTROPY)
    endif()
  elseif(_kind MATCHES "^Refraction")
    # The screen-space refraction sampler takes the ninth user sampler.
    set(slots 3)
    set(lit TRUE)
    list(APPEND lobes TRANSMISSION CLEARCOAT SHEEN IRIDESCENCE)
    if(_kind MATCHES "Solid")
      list(APPEND lobes VOLUME DISPERSION)
      set(type solid)
      set(OPTICS "    variables : [ volumeScale ],\n")
      set(VERTEX_BLOCK "${_volume_vertex}")
    else()
      set(type thin)
    endif()
    if(_kind MATCHES "Anisotropy")
      list(APPEND lobes ANISOTROPY)
    endif()
    string(APPEND OPTICS "    refractionMode : screenspace,\n    refractionType : ${type},")
    set(REFRACTION_PREFIX "${_refraction_0}")
    set(REFRACTION_MATERIAL "${_refraction_1}")
    set(REFRACTION_SUFFIX "${_refraction_2}")
  elseif(_kind STREQUAL "SpecularGlossiness")
    set(template "${_src}/specular_glossiness.mat.in")
    set(shading specularGlossiness)
    _filly_parameters(PARAMETERS ${_core} "float3 specularFactor" "float glossinessFactor")
  elseif(_kind STREQUAL "Unlit")
    set(template "${_src}/unlit.mat.in")
    set(shading unlit)
    _filly_parameters(PARAMETERS ${_core})
  else()
    set(template "${_src}/diffuse_transmission.mat.in")
  endif()
  if(lit)
    _filly_parameters(PARAMETERS ${_core} "float ior")
    string(APPEND DEFINES "#define FILLY_EXT_SLOTS ${slots}\n")
    foreach(lobe IN LISTS lobes)
      string(APPEND DEFINES "#define FILLY_${lobe}\n")
    endforeach()
    if(SPECULAR IN_LIST lobes)
      _filly_parameters(PARAMETERS "float specularStrength" "float3 specularColorFactor")
      _filly_roles(PARAMETERS specular specularColor)
    endif()
    if(TRANSMISSION IN_LIST lobes)
      _filly_parameters(PARAMETERS "float transmissionFactor")
      _filly_roles(PARAMETERS transmission)
    endif()
    if(VOLUME IN_LIST lobes)
      _filly_parameters(PARAMETERS "float3 volumeAbsorption" "float volumeThicknessFactor")
      _filly_roles(PARAMETERS volumeThickness)
    endif()
    if(DISPERSION IN_LIST lobes)
      _filly_parameters(PARAMETERS "float dispersion")
    endif()
    if(CLEARCOAT IN_LIST lobes)
      _filly_parameters(PARAMETERS "float clearCoatFactor" "float clearCoatRoughnessFactor" "float clearCoatNormalScale")
      _filly_roles(PARAMETERS clearCoat clearCoatRoughness clearCoatNormal)
    endif()
    if(SHEEN IN_LIST lobes)
      _filly_parameters(PARAMETERS "float3 sheenColorFactor" "float sheenRoughnessFactor")
      _filly_roles(PARAMETERS sheenColor sheenRoughness)
    endif()
    if(ANISOTROPY IN_LIST lobes)
      _filly_parameters(PARAMETERS "float anisotropyStrength" "float anisotropyRotation")
      _filly_roles(PARAMETERS anisotropy)
    endif()
    if(IRIDESCENCE IN_LIST lobes)
      _filly_parameters(PARAMETERS "float iridescenceFactor" "float iridescenceIor"
        "float iridescenceThicknessMinimum" "float iridescenceThicknessMaximum")
      _filly_roles(PARAMETERS iridescence iridescenceThickness)
    endif()
    set(samplers "")
    if(slots GREATER 0)
      math(EXPR last "${slots} - 1")
      foreach(slot RANGE ${last})
        list(APPEND samplers "sampler2d ext${slot}")
      endforeach()
    endif()
    _filly_parameters(PARAMETERS ${samplers})
  endif()
  # The last parameter must not end with a comma.
  string(REGEX REPLACE ",\n$" "\n" PARAMETERS "${PARAMETERS}")
  file(READ "${template}" _text)
  foreach(BLENDING IN LISTS _blendings)
    set(NAME "filly_${_kind}_${BLENDING}")
    # file(CONFIGURE) rewrites a file only when its content changes.
    file(CONFIGURE OUTPUT "${_dir}/${NAME}.mat" CONTENT "${_text}" @ONLY)
    file(CONFIGURE OUTPUT "${_dir}/${NAME}.spec" CONTENT
      "ShadingModel = ${shading}\nBlendingMode = ${BLENDING}\n${_kind} = required\n" @ONLY)
    add_custom_command(OUTPUT "${_dir}/${NAME}.filamat"
      COMMAND "${FILLY_MATC}" ${FILLY_MATC_FLAGS} -o "${NAME}.filamat" "${NAME}.mat"
      DEPENDS "${_dir}/${NAME}.mat" "${FILLY_MATC}"
      WORKING_DIRECTORY "${_dir}"
      COMMENT "matc ${NAME}"
      VERBATIM)
    list(APPEND _packages "${_dir}/${NAME}.filamat" "${_dir}/${NAME}.spec")
    list(APPEND _names "${NAME}")
  endforeach()
endforeach()
# Configure again when a template changes.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${_src}/surface.mat.in" "${_src}/unlit.mat.in" "${_src}/specular_glossiness.mat.in"
  "${_src}/diffuse_transmission.mat.in" "${_src}/materials.cmake" "${_src}/embed.cmake"
  "${FILLY_SOURCE_DIR}/native/refraction.h")

add_custom_command(OUTPUT "${_dir}/filly.uberz"
  COMMAND "${FILLY_UBERZ}" -q -o filly.uberz ${_names}
  DEPENDS ${_packages} "${FILLY_UBERZ}"
  WORKING_DIRECTORY "${_dir}"
  COMMENT "uberz filly.uberz"
  VERBATIM)
set(_check "")
if(FILLY_MATINFO)
  # The refraction hook depends on the order of the generated shader and on Filament's sampler
  # names. Check that the optimized fragment shader still calls it.
  set(_check "${_dir}/refraction-hook.stamp")
  add_custom_command(OUTPUT "${_check}"
    COMMAND "${CMAKE_COMMAND}" -DMATINFO=${FILLY_MATINFO}
      "-DPACKAGE=${_dir}/filly_RefractionSolid_opaque.filamat" "-DSTAMP=${_check}"
      -P "${_src}/check_refraction.cmake"
    DEPENDS "${_dir}/filly_RefractionSolid_opaque.filamat" "${_src}/check_refraction.cmake"
    COMMENT "Check the refraction hook in the optimized shader"
    VERBATIM)
endif()
add_custom_command(OUTPUT "${_dir}/filly_material_archive.c"
  COMMAND "${CMAKE_COMMAND}" "-DINPUT=${_dir}/filly.uberz" "-DOUTPUT=${_dir}/filly_material_archive.c"
    -P "${_src}/embed.cmake"
  DEPENDS "${_dir}/filly.uberz" "${_src}/embed.cmake" ${_check}
  COMMENT "Embed filly.uberz"
  VERBATIM)
add_library(filly_material_archive STATIC "${_dir}/filly_material_archive.c")
set_target_properties(filly_material_archive PROPERTIES POSITION_INDEPENDENT_CODE ON LINKER_LANGUAGE C)
