# cgltf header

`cgltf.h` is the cgltf 1.15 header from Filament 1.77.1 (`third_party/cgltf/cgltf.h`),
unchanged. The MIT license is in LICENSE and is included in wheels.

filly includes the header without `CGLTF_IMPLEMENTATION`. The functions come from the
Filament SDK's `gltfio_core` library, which compiles this header. The structure layouts must
match that build, so update this file only together with the Filament SDK.
