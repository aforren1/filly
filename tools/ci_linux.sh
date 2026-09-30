#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
dnf install -y clang libX11-devel mesa-libGL-devel mesa-libEGL-devel mesa-libGLU mesa-dri-drivers \
    libXrandr libXi libXcursor libXinerama xorg-x11-server-Xvfb xauth
export PATH="/opt/python/cp312-cp312/bin:$PATH"
python -m pip install 'ninja>=1.11'
if [[ ! -f .deps/filament-linux/build-info.json || ! -x .deps/filament-linux/bin/matc ]]; then
    python tools/build_filament_linux.py --work /tmp/filament-work --output .deps/filament-linux --jobs 2
fi
