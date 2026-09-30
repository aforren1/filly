# Fails the build if the optimized refraction shader no longer contains filly's orthographic
# refraction LOD. cmake -P script: MATINFO, PACKAGE, and STAMP are set by materials.cmake.
# The hook derives the LOD from the height of the refraction texture, sampler0_ssr, with
# textureSize(); Filament's own shader code does not query that sampler's size. Unoptimized
# shaders (matc -g) keep the hook's function, fpTextureLod, instead.
execute_process(COMMAND "${MATINFO}" "${PACKAGE}" OUTPUT_VARIABLE info RESULT_VARIABLE status)
if(status)
  message(FATAL_ERROR "matinfo failed on ${PACKAGE}")
endif()
# The first fragment shader of the default variant.
string(REGEX MATCH "#([0-9]+) +desktop fs 0x00" match "${info}")
if(NOT match)
  message(FATAL_ERROR "No default fragment shader in ${PACKAGE}")
endif()
execute_process(COMMAND "${MATINFO}" "--print-glsl=${CMAKE_MATCH_1}" "${PACKAGE}"
  OUTPUT_VARIABLE glsl RESULT_VARIABLE status)
if(status OR NOT (glsl MATCHES "textureSize\\(sampler0_ssr" OR glsl MATCHES "fpTextureLod"))
  message(FATAL_ERROR "The optimized refraction shader lacks filly's hook. "
    "Review native/refraction.h against the Filament version.")
endif()
file(TOUCH "${STAMP}")
