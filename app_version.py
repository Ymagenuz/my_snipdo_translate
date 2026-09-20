"""Application release version shared by the UI, updater, and Windows build."""

APP_VERSION = "12.0.0"

# Windows version resources use four unsigned 16-bit components. Releases use
# major.minor.patch; the fourth component is reserved for Windows metadata.
WINDOWS_VERSION = tuple(int(part) for part in APP_VERSION.split(".")) + (0,)
if len(WINDOWS_VERSION) != 4 or any(
    part < 0 or part > 65535 for part in WINDOWS_VERSION
):
    raise ValueError("APP_VERSION must contain three components between 0 and 65535")
