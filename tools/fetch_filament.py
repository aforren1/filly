"""Download the pinned Filament SDK for a Windows x64 or macOS arm64 build."""

import hashlib
from pathlib import Path, PurePosixPath
import platform
import tarfile
import urllib.request


# The Filament release of every filly build (FILAMENT_VERSION at the repository root). The
# hashes below belong to that release; change them together.
VERSION = (Path(__file__).resolve().parents[1] / "FILAMENT_VERSION").read_text().strip()
# The release archives of github.com/google/filament. The macOS archive has one top-level folder,
# `filament/`, which is removed, so that both SDKs have `include` at the root of their folder.
ARCHIVES = {
    "Windows": {"name": f"filament-v{VERSION}-windows.tgz", "size": "826 MiB", "folder": "",
                "sha256": "5fc8ea5a06bd3de3f43da9ec72a09458193db3e0b574e06b6b3f8ef94c21fce9"},
    "Darwin": {"name": f"filament-v{VERSION}-mac.tgz", "size": "44 MiB", "folder": "filament-mac",
               "sha256": "a1c4dd85b2ec5789d202630026febc019bc8a774e231ce0bb4407032c2bd2103"},
}
MACHINES = {"Windows": {"amd64", "x86_64"}, "Darwin": {"arm64"}}


def strip_top_folder(member, path):
    member = tarfile.data_filter(member, path)
    parts = PurePosixPath(member.name).parts
    if parts[:1] != ("filament",):
        raise tarfile.FilterError(f"Unexpected member outside filament/: {member.name}")
    if len(parts) == 1:
        return None
    return member.replace(name=str(PurePosixPath(*parts[1:])))


def main():
    system = platform.system()
    if system not in ARCHIVES or platform.machine().lower() not in MACHINES[system]:
        raise SystemExit("The SDK archive is for Windows x64 and macOS arm64 only. See docs/how-to/build.md for Linux.")
    if not hasattr(tarfile, "data_filter"):
        raise SystemExit("Use Python 3.12 or later to extract the SDK safely.")
    pin = ARCHIVES[system]
    deps = Path(__file__).resolve().parents[1] / ".deps"
    deps.mkdir(exist_ok=True)
    archive = deps / pin["name"]
    if not archive.exists():
        url = f"https://github.com/google/filament/releases/download/v{VERSION}/{pin['name']}"
        temporary = archive.with_suffix(".download")
        print(f"Download {url} (about {pin['size']})", flush=True)
        urllib.request.urlretrieve(url, temporary)
        temporary.replace(archive)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != pin["sha256"]:
        raise SystemExit(f"SHA-256 mismatch. Remove {archive} and run this command again.")
    destination = deps / pin["folder"]
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter=strip_top_folder if pin["folder"] else "data")
    print(f"Filament {VERSION} extracted to {destination}")


if __name__ == "__main__":
    main()
