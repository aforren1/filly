# Build environment for tools/build_filament_web.sh and tools/build_filly_web.sh.
# tools/build_web_container.ps1 builds and runs it with wslc.
#
# Ubuntu 24.04: Filament's release host tools (matc, ...) need glibc 2.38 or later. The scripts
# fetch CMake and emsdk themselves, into the work volume, so the image holds no toolchain.
FROM ubuntu:24.04
# xz-utils: emsdk's node and LLVM archives are .tar.xz.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git ninja-build python3 xz-utils \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
