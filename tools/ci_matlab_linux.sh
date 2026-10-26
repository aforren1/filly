#!/usr/bin/env bash
# Builds the Linux toolbox packages in manylinux_2_28, the same container and compiler as the
# Filament SDK and the Python wheels, so that the MEX files load on glibc 2.28 and later:
#   dist/filly-matlab-linux       filly_mex.mexa64, for MATLAB_ROOT's release and later
#   dist/filly-octave-linux-6.4   filly_mex.mex for Octave 6.4 through 9, from OCTAVE6_INCLUDE
#   dist/filly-octave-linux-10    filly_mex.mex for Octave 10 and later, from OCTAVE10_INCLUDE,
#                                 linked to the liboctmex in OCTAVE10_LIB
# MATLAB_ROOT is a MATLAB installation mounted into the container; the build runs no MATLAB.
# Each *_INCLUDE is the octave-<version> folder of an Octave include tree.
set -euo pipefail

cd "$(dirname "$0")/.."
# libstdc++-static: the MATLAB file links the C++ runtime statically (matlab/CMakeLists.txt).
dnf install -y clang libX11-devel mesa-libGL-devel mesa-libEGL-devel libstdc++-static
export PATH="/opt/python/cp312-cp312/bin:$PATH"
python -m pip install 'ninja>=1.11'

configure() {
    local dir=$1
    shift
    # Each tree writes its MEX file to its own folder: both Octave builds name it filly_mex.mex.
    CC=clang CXX=clang++ cmake -S . -B "$dir" -G Ninja -DCMAKE_BUILD_TYPE=Release -DFILLY_PYTHON=OFF \
        -DFILAMENT_ROOT="$PWD/.deps/filament-linux" -DFILLY_MEX_OUTPUT_DIR="$PWD/$dir/mex" "$@"
}
configure build/mex-linux -DFILLY_MATLAB=ON -DMatlab_ROOT_DIR="$MATLAB_ROOT" \
    -DFILLY_OCTAVE=ON "-DFILLY_OCTAVE_INCLUDE_DIRS=$OCTAVE6_INCLUDE/octave;$OCTAVE6_INCLUDE"
cmake --build build/mex-linux --target filly_mex filly_octave_mex
# Octave 10 changed the MEX ABI, so its file builds against its own headers and liboctmex.
configure build/mex-octave10 -DFILLY_OCTAVE=ON "-DFILLY_OCTAVE_INCLUDE_DIRS=$OCTAVE10_INCLUDE/octave;$OCTAVE10_INCLUDE" \
    -DFILLY_OCTAVE_LIBRARY_DIR="$OCTAVE10_LIB"
cmake --build build/mex-octave10 --target filly_octave_mex

rm -rf dist/filly-matlab-linux dist/filly-octave-linux-6.4 dist/filly-octave-linux-10
cmake --install build/mex-linux --component matlab --prefix dist/filly-matlab-linux
cmake --install build/mex-linux --component octave --prefix dist/filly-octave-linux-6.4
cmake --install build/mex-octave10 --component octave --prefix dist/filly-octave-linux-10
