# Writes INPUT as a C byte array named NAME_data, with its size in NAME_size, to OUTPUT.
# cmake -P script; no compiler-specific embedding.
if(NOT NAME)
  set(NAME filly_material_archive)
endif()
file(READ "${INPUT}" hex HEX)
string(LENGTH "${hex}" digits)
math(EXPR size "${digits} / 2")
# Sixteen bytes per line keeps each source line short.
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," bytes "${hex}")
string(REGEX REPLACE "((0x..,){16})" "\\1\n" bytes "${bytes}")
file(WRITE "${OUTPUT}.tmp" "/* Generated from ${INPUT}; do not edit. */\n#include <stddef.h>\n"
  "const unsigned char ${NAME}_data[${size}] = {\n${bytes}\n};\n"
  "const size_t ${NAME}_size = ${size};\n")
file(COPY_FILE "${OUTPUT}.tmp" "${OUTPUT}" ONLY_IF_DIFFERENT)
file(REMOVE "${OUTPUT}.tmp")
