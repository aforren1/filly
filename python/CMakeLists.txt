# The Python extension module filly._native, which the package in filly/ wraps. pyproject.toml at
# the repository root builds it into the wheel with scikit-build-core.
find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module OPTIONAL_COMPONENTS Development.SABIModule)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_native STABLE_ABI NOSTRIP bindings.cpp)
target_link_libraries(_native PRIVATE filly_core)
if(MSVC)
  # The UCRT is part of Windows 10 and later, and one shared copy keeps a single heap and
  # locale state per process. The static runtime objects request libucrt.lib by default.
  target_link_options(_native PRIVATE /NODEFAULTLIB:libucrt.lib /DEFAULTLIB:ucrt.lib)
else()
  # Filament marks its public API visible, and the C++ template instantiations are visible
  # regardless of -fvisibility. Exported, they could bind to another copy of Filament, libwebp,
  # or a different libstdc++ build in the process, so only the module entry point is exported.
  set(FILLY_EXPORTS "${CMAKE_CURRENT_BINARY_DIR}/_native.map")
  file(WRITE "${FILLY_EXPORTS}" "{\n  global: PyInit__native;\n  local: *;\n};\n")
  target_link_options(_native PRIVATE "LINKER:--version-script=${FILLY_EXPORTS}")
  set_property(TARGET _native APPEND PROPERTY LINK_DEPENDS "${FILLY_EXPORTS}")
endif()
install(TARGETS _native LIBRARY DESTINATION filly RUNTIME DESTINATION filly)
filly_install_licenses(filly/licenses)
# Shared with the MATLAB and Octave packages; filly/samples/ATTRIBUTION.md credits it.
install(FILES "${PROJECT_SOURCE_DIR}/samples/wooden_playground_horse.glb" DESTINATION filly/samples)
