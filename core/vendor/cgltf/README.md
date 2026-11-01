# cgltf header

`cgltf.h` is the cgltf 1.15 header from Filament 1.79.0 (`third_party/cgltf/cgltf.h`),
unchanged. That copy has Filament's patch (`third_party/cgltf/tnt/cgltf-filament.patch`) to
three accessor functions. The MIT license is in LICENSE and is included in wheels.

filly includes the header without `CGLTF_IMPLEMENTATION`. The functions come from the
Filament SDK's `gltfio_core` library, which compiles this header. The structure layouts must
match that build, so update this file only together with the Filament SDK.
