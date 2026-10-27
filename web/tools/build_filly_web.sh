#!/usr/bin/env bash
# Build filly for the web: web/filly.js and the module it loads, filly-core.js and
# filly-core.wasm, in OUT_DIR.
#
# Usage: web/tools/build_filly_web.sh [OUT_DIR] [WORK_DIR]
#
# Set FILLY_WEB_SIMD=ON for a build with WebAssembly SIMD, which browsers without SIMD cannot
# load. The default is OFF.
#
# WORK_DIR is the work folder of web/tools/build_filament_web.sh (default ~/filly-web), which holds
# the Filament WebAssembly SDK, emsdk, CMake, and the host tools. Run that script first. OUT_DIR
# defaults to build/web in the repository.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT=$(realpath -m "${1:-$REPO/build/web}")
WORK=$(realpath -m "${2:-$HOME/filly-web}")
for required in "$WORK/sdk/include/filament/Engine.h" "$WORK/host-tools/matc" "$WORK/emsdk/emsdk_env.sh"; do
    if [[ ! -e "$required" ]]; then
        echo "Missing $required; run web/tools/build_filament_web.sh $WORK first" >&2
        exit 1
    fi
done
export PATH=$WORK/cmake/bin:$PATH
# shellcheck disable=SC1091
source "$WORK/emsdk/emsdk_env.sh" >/dev/null 2>&1

# The build tree stays on the build machine's file system; WSL builds from /mnt/c are slow.
BUILD=$WORK/filly-build
emcmake cmake -S "$REPO" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DFILAMENT_ROOT="$WORK/sdk" -DFILLY_MATERIAL_TOOLS="$WORK/host-tools" -DFILLY_WEB_SIMD="${FILLY_WEB_SIMD:-OFF}"
cmake --build "$BUILD" -j "${JOBS:-$(nproc)}"

mkdir -p "$OUT"
cp "$BUILD/web/filly-core.js" "$BUILD/web/filly-core.wasm" "$REPO/web/filly.js" "$OUT/"
# Licenses of everything the module links; the core's configuration collects them.
mkdir -p "$OUT/licenses"
cp "$BUILD"/licenses/* "$OUT/licenses/"
cp "$REPO/LICENSE" "$OUT/licenses/filly.txt"
ls -la "$OUT"
