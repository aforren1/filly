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
  # Apple's linker has no version scripts; its list names symbols with the C underscore prefix.
  if(APPLE)
    set(FILLY_EXPORTS "${CMAKE_CURRENT_BINARY_DIR}/_native.exp")
    file(WRITE "${FILLY_EXPORTS}" "_PyInit__native\n")
    target_link_options(_native PRIVATE "LINKER:-exported_symbols_list,${FILLY_EXPORTS}")
  else()
    set(FILLY_EXPORTS "${CMAKE_CURRENT_BINARY_DIR}/_native.map")
    file(WRITE "${FILLY_EXPORTS}" "{\n  global: PyInit__native;\n  local: *;\n};\n")
    target_link_options(_native PRIVATE "LINKER:--version-script=${FILLY_EXPORTS}")
  endif()
  set_property(TARGET _native APPEND PROPERTY LINK_DEPENDS "${FILLY_EXPORTS}")
endif()
install(TARGETS _native LIBRARY DESTINATION filly RUNTIME DESTINATION filly)
filly_install_licenses(filly/licenses)
# Shared with the MATLAB and Octave packages; filly/samples/ATTRIBUTION.md credits it.
install(FILES "${PROJECT_SOURCE_DIR}/samples/horse.glb" DESTINATION filly/samples)
