from pathlib import Path

import filly


def test_filament_version_matches_pin():
    """The module reports the Filament release that FILAMENT_VERSION pins for every build."""
    pinned = (Path(__file__).resolve().parents[2] / "FILAMENT_VERSION").read_text().strip()
    assert filly.filament_version == pinned
