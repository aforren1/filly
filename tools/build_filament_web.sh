#!/usr/bin/env bash
# Build a pinned Filament WebAssembly SDK (libraries, headers, and the stock filament.js).
#
# Usage: tools/build_filament_web.sh [WORK_DIR]
#
# Run it on Linux x86_64 with glibc 2.38 or later (Ubuntu 24.04; WSL works). It needs curl, git,
# python3, and ninja. No native compiler: the host tools (matc, resgen, ...) come from the Linux
# release archive, whose binaries need that glibc. The script fetches a pinned CMake.
# filly's web build (web/) links the libraries in WORK_DIR/sdk.
#
# Environment overrides: FILAMENT_SOURCE_TGZ and FILAMENT_LINUX_TGZ (local copies of the pinned
# archives), EMSDK_DIR (an existing emsdk checkout), JOBS.
set -euo pipefail

VERSION=1.77.1
SOURCE_SHA256=c55e2f99fd5e8840f132d03f1b019bc820c37d9df6d2c2ce2f3930367b081a45
LINUX_SHA256=ec0f5287a3a2fb801a93fd7b0ffd80c894aac980716d6f91ec48e5370d2d0674
# Filament 1.77.1 CI pins this emsdk (build/common/versions). Material packages and the
# generated JavaScript glue depend on it, so do not float it to "latest".
EMSDK_VERSION=5.0.4

WORK=$(realpath -m "${1:-$HOME/filly-web}")
JOBS=${JOBS:-$(nproc)}
mkdir -p "$WORK/downloads"

fetch() {  # url path sha256
    if [[ ! -f "$2" ]]; then
        curl -fL --retry 3 -o "$2.download" "$1"
        mv "$2.download" "$2"
    fi
    echo "$3  $2" | sha256sum -c --quiet -
}

SOURCE_TGZ=${FILAMENT_SOURCE_TGZ:-$WORK/downloads/filament-v$VERSION-source.tar.gz}
LINUX_TGZ=${FILAMENT_LINUX_TGZ:-$WORK/downloads/filament-v$VERSION-linux.tgz}
fetch "https://codeload.github.com/google/filament/tar.gz/refs/tags/v$VERSION" "$SOURCE_TGZ" $SOURCE_SHA256
fetch "https://github.com/google/filament/releases/download/v$VERSION/filament-v$VERSION-linux.tgz" \
    "$LINUX_TGZ" $LINUX_SHA256

CMAKE_VERSION=3.31.6
CMAKE_TGZ=$WORK/downloads/cmake-$CMAKE_VERSION-linux-x86_64.tar.gz
fetch "https://github.com/Kitware/CMake/releases/download/v$CMAKE_VERSION/cmake-$CMAKE_VERSION-linux-x86_64.tar.gz" \
    "$CMAKE_TGZ" 5a1133ff103c71eb5120e2cc3de922733e7d8a26a98ae716397e8676adb367bf
if [[ ! -x "$WORK/cmake/bin/cmake" ]]; then
    mkdir -p "$WORK/cmake"
    tar -xzf "$CMAKE_TGZ" -C "$WORK/cmake" --strip-components=1
fi
export PATH=$WORK/cmake/bin:$PATH

SRC=$WORK/filament-$VERSION
if [[ ! -f "$SRC/CMakeLists.txt" ]]; then
    tar -xzf "$SOURCE_TGZ" -C "$WORK"
fi

# Host tools. The web build runs them at build time to make materials and resources.
TOOLS=$WORK/host-tools
if [[ ! -x "$TOOLS/matc" ]]; then
    mkdir -p "$TOOLS"
    tar -xzf "$LINUX_TGZ" -C "$TOOLS" --strip-components=2 \
        filament/bin/matc filament/bin/cmgen filament/bin/filamesh filament/bin/mipgen \
        filament/bin/resgen filament/bin/uberz filament/bin/glslminifier
fi
# matc --help exits nonzero even when it runs, so look for the loader's error instead.
if "$TOOLS/matc" --help 2>&1 | grep -q "GLIBC_"; then
    echo "The release host tools need glibc 2.38 or later: $(ldd --version | head -1)" >&2
    exit 1
fi

# Filament imports host tools from <source>/<dir>/ImportExecutables-Prebuilt.cmake, with <dir>
# relative to the source tree. Write the file that a split tools build would export.
PREBUILT_REL=out/prebuilt-tools
mkdir -p "$SRC/$PREBUILT_REL"
{
    for tool in matc cmgen filamesh mipgen resgen uberz glslminifier; do
        echo "add_executable($tool IMPORTED)"
        echo "set_target_properties($tool PROPERTIES IMPORTED_LOCATION \"$TOOLS/$tool\")"
    done
} > "$SRC/$PREBUILT_REL/ImportExecutables-Prebuilt.cmake"

EMSDK_DIR=${EMSDK_DIR:-$WORK/emsdk}
if [[ ! -x "$EMSDK_DIR/emsdk" ]]; then
    git clone --depth 1 --branch "$EMSDK_VERSION" https://github.com/emscripten-core/emsdk.git "$EMSDK_DIR"
fi
"$EMSDK_DIR/emsdk" install "$EMSDK_VERSION"
"$EMSDK_DIR/emsdk" activate "$EMSDK_VERSION" >/dev/null
# shellcheck disable=SC1091
source "$EMSDK_DIR/emsdk_env.sh" >/dev/null

BUILD=$WORK/build-release
SDK=$WORK/sdk
# Single-threaded (no WASM_PTHREADS): the module needs no SharedArrayBuffer, so pages need no
# cross-origin isolation.
cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$EMSDK/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$SDK" \
    -DWASM=1 \
    -DFILAMENT_IMPORT_PREBUILT_EXECUTABLES_DIR=$PREBUILT_REL \
    -DFILAMENT_SKIP_SAMPLES=ON \
    -DFILAMENT_BUILD_TESTING=OFF
cmake --build "$BUILD" -j "$JOBS"
cmake --install "$BUILD"

# The stock module, for experiments that compare filly's build with Filament's own bindings.
mkdir -p "$SDK/filament-js"
cp "$BUILD/web/filament-js/filament.js" "$BUILD/web/filament-js/filament.wasm" \
    "$BUILD/web/filament-js/filament.d.ts" "$SDK/filament-js/"
emcc --version | head -1 > "$SDK/EMSCRIPTEN_VERSION"
echo "Filament $VERSION wasm SDK: $SDK"
