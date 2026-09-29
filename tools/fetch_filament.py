"""Download the pinned SDK for the initial Windows build."""

import hashlib
from pathlib import Path
import platform
import tarfile
import urllib.request


VERSION = "1.77.1"
SHA256 = "6f1e16102e4feec3dada51c31237e1b07d8fcf9d4893376668bf3276530457ec"


def main():
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("The initial SDK build supports Windows x64 only.")
    destination = Path(__file__).resolve().parents[1] / ".deps"
    destination.mkdir(exist_ok=True)
    name = f"filament-v{VERSION}-windows.tgz"
    archive = destination / name
    if not archive.exists():
        url = f"https://github.com/google/filament/releases/download/v{VERSION}/{name}"
        temporary = archive.with_suffix(".download")
        print(f"Download {url} (about 813 MiB)", flush=True)
        urllib.request.urlretrieve(url, temporary)
        temporary.replace(archive)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SHA256:
        raise SystemExit(f"SHA-256 mismatch. Remove {archive} and run this command again.")
    with tarfile.open(archive) as bundle:
        if not hasattr(tarfile, "data_filter"):
            raise SystemExit("Use Python 3.12 or later to extract the SDK safely.")
        bundle.extractall(destination, filter="data")
    print(f"Filament {VERSION} extracted to {destination}")


if __name__ == "__main__":
    main()
