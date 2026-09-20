import re

from app_version import APP_VERSION, WINDOWS_VERSION


def test_release_version_is_canonical_and_fits_windows_metadata():
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", APP_VERSION)
    assert WINDOWS_VERSION == tuple(int(part) for part in APP_VERSION.split(".")) + (0,)
    assert len(WINDOWS_VERSION) == 4
    assert all(0 <= part <= 65535 for part in WINDOWS_VERSION)
