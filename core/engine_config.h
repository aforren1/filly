#pragma once

#include <filament/Engine.h>

namespace filly::detail {
inline filament::Engine::Config engine_config() {
    filament::Engine::Config config;
    // AssetLoader::createAsset() queues all of an asset's commands with no flush point.
    // NodePerformanceTest (10,000 meshes) queues 6.13 MiB, about 640 bytes per mesh; the
    // 3 MiB SDK default aborts the process. 12 MiB gives about 2x headroom. Windows commits
    // twice this size for the mirrored ring buffer. The minimum batch stays at the default.
    config.commandBufferSizeMB = 12;
    // The SDK's handle arena fills on the same asset and logs a warning. 24 MiB fits;
    // the heap fallback did not measurably slow loading or frames.
    config.driverHandleArenaSizeMB = 32;
    // Every render() is a Filament frame, and each frame ages the cache of transient frame-graph
    // textures (shadow maps, MSAA and postprocessing buffers). At the default age of 1, a scene
    // with such buffers rendered next to one other scene per refresh freed and recreated one
    // texture per refresh; with four renders per refresh, two. Eight frames keep them for up to
    // eight renders per refresh. A texture that frames stop using is freed at least eight frames
    // later instead of one.
    config.resourceAllocatorCacheMaxAge = 8;
    return config;
}

// Engine::flushAndWait() that also covers Filament 1.77.1's single-threaded (WebAssembly) build.
// There, flushAndWait() executes only the commands that an earlier flush submitted; the current
// command buffer, including the finish() that flushAndWait() queues, waits for the next flush.
// Callers free upload sources after the wait, so the uploads would read freed memory later.
inline void flush_and_wait(filament::Engine& engine) {
#if defined(__EMSCRIPTEN__)
    engine.flush();
#endif
    engine.flushAndWait();
}
}
