# meshoptimizer decoder

These files come from meshoptimizer v1.0 (https://github.com/zeux/meshoptimizer/tree/v1.0).
The MIT license is in LICENSE.md and is included in wheels.

All `meshopt_` identifiers have the prefix `fp_meshopt_`, and the internal `meshopt`
namespace is renamed `fp_meshopt`, to isolate this decoder from
Filament 1.77.1's statically linked meshoptimizer 0.18. Source behavior is otherwise unchanged.
The modern decoder supports KHR_meshopt_compression's version 1 attributes and COLOR filter.
