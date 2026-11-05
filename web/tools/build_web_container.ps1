<#
.SYNOPSIS
Builds filly for the web on Windows, in a wslc container.

.DESCRIPTION
Builds the image from the Dockerfile in this folder, then runs build_filament_web.sh and
build_filly_web.sh in it. The named volume holds their work folder (the Filament
WebAssembly SDK, emsdk, CMake, the host tools, and filly's build tree), so only the first run
builds the SDK. The container mounts the repository read-only and writes only to OutDir.

.EXAMPLE
web\tools\build_web_container.ps1

.EXAMPLE
web\tools\build_web_container.ps1 -OutDir C:\tmp\filly-web
#>
param(
    # Receives filly.js, filly-core.js, filly-core.wasm, and licenses/.
    [string]$OutDir = (Join-Path $PSScriptRoot '..\..\build\web'),
    [string]$Volume = 'filly-web',
    [string]$Image = 'filly-web-build'
)
# No global ErrorActionPreference = 'Stop': Windows PowerShell 5.1 then stops at the first line
# that a native command writes to stderr if the caller redirects it. Check exit codes instead.

function Invoke-Wslc {
    wslc @args
    if ($LASTEXITCODE -ne 0) { throw "wslc $($args[0]) failed with exit code $LASTEXITCODE" }
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..') -ErrorAction Stop).Path
New-Item -ItemType Directory -Force $OutDir -ErrorAction Stop | Out-Null
$OutDir = (Resolve-Path $OutDir -ErrorAction Stop).Path

Invoke-Wslc build --progress quiet -t $Image $PSScriptRoot
wslc volume inspect $Volume *> $null
if ($LASTEXITCODE -ne 0) { Invoke-Wslc volume create $Volume }

$envArgs = foreach ($name in 'JOBS', 'FILLY_WEB_SIMD') {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { '-e'; "$name=$value" }
}
# build_filament_web.sh takes about 20 s even when the SDK is complete (hash checks, CMake
# configure), so skip it when the volume's SDK has a copy of FILAMENT_VERSION. The build logs to
# stdout because PowerShell 5.1 shows each stderr line of a redirected native command as an
# error. The command has no double quotes: PowerShell 5.1 does not escape them for wslc.
$command = 'exec 2>&1; cmp -s /work/sdk/FILAMENT_VERSION /src/FILAMENT_VERSION || ' +
    'bash /src/web/tools/build_filament_web.sh /work; ' +
    'bash /src/web/tools/build_filly_web.sh /out /work'
# The build reads its version from the git tags. Git refuses a repository that another user owns,
# as the mount appears to the container, unless safe.directory allows it.
$gitArgs = '-e', 'GIT_CONFIG_COUNT=1', '-e', 'GIT_CONFIG_KEY_0=safe.directory', '-e', 'GIT_CONFIG_VALUE_0=/src'
Invoke-Wslc run --rm @envArgs @gitArgs -v "${Volume}:/work" -v "${repo}:/src:ro" -v "${OutDir}:/out" `
    $Image bash -euc $command
