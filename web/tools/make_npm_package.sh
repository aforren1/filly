#!/usr/bin/env bash
# Assemble the npm package @aforren1/filly from a web build.
#
# Usage: web/tools/make_npm_package.sh BUILD_DIR OUT_DIR VERSION
#
# BUILD_DIR is the output of web/tools/build_filly_web.sh, or an unzipped filly-web-<version>.zip
# of a release. OUT_DIR is replaced. VERSION is the package version, X.Y.Z for a release. Then
# run `npm publish` in OUT_DIR (with `--access public` for the first version of the package).
set -euo pipefail

if [[ $# -ne 3 ]]; then
    sed -n '2,8p' "$0" >&2
    exit 2
fi
REPO=$(cd "$(dirname "$0")/../.." && pwd)
BUILD=$1
OUT=$2
VERSION=$3
# npm pack accepts versions that npm publish rejects, such as setuptools-scm's X.Y.Z.devN.
if [[ ! $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "$VERSION is not a semver version such as 1.2.3 or 1.2.3-ci.0" >&2
    exit 1
fi
for required in filly.js filly-core.js filly-core.wasm licenses; do
    if [[ ! -e "$BUILD/$required" ]]; then
        echo "$BUILD is not a filly web build: it has no $required" >&2
        exit 1
    fi
done

rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$BUILD/filly.js" "$BUILD/filly-core.js" "$BUILD/filly-core.wasm" "$BUILD/licenses" "$OUT/"
cp "$REPO/web/package/package.json" "$REPO/web/package/README.md" "$REPO/LICENSE" "$OUT/"
(cd "$OUT" && npm pkg set "version=$VERSION")
# Lists what the package will contain.
(cd "$OUT" && npm pack --dry-run)
