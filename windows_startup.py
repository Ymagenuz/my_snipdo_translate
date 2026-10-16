"""Opt-in, current-user Windows sign-in registration."""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path, PureWindowsPath
from typing import Callable

try:
    import winreg as _WINREG
except ImportError:
    _WINREG = None


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SnipDoTranslate"
MAX_RUN_COMMAND_LENGTH = 260


class StartupRegistrationError(OSError):
    """The startup command or current-user registration could not be used."""


def _is_file(path: Path) -> bool:
    return path.is_file()


def _command_path(value: str | Path, label: str, suffix: str) -> Path:
    raw = str(value)
    if not raw or any(unicodedata.category(char) == "Cc" for char in raw):
        raise StartupRegistrationError(f"The {label} path is empty or contains control characters.")
    colon_tail = raw[2:] if len(raw) >= 2 and raw[0].isalpha() and raw[1] == ":" else raw
    if any(char in '<>"|?*' for char in raw) or ":" in colon_tail:
        raise StartupRegistrationError(f"The {label} path contains an invalid Windows file name.")
    windows_path = PureWindowsPath(raw)
    if raw.startswith(("\\\\?\\", "\\\\.\\")):
        raise StartupRegistrationError(f"The {label} path must be a regular Windows file path.")
    reserved_names = {"CON", "PRN", "AUX", "NUL"}
    reserved_names.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
    parts = windows_path.parts[1:] if windows_path.anchor else windows_path.parts
    for part in parts:
        if part in (".", ".."):
            continue
        if (
            any(char in '<>:"|?*' for char in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in reserved_names
        ):
            raise StartupRegistrationError(f"The {label} path contains an invalid Windows file name.")
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise StartupRegistrationError(f"The {label} path could not be resolved: {exc}") from exc
    if path.suffix.lower() != suffix:
        raise StartupRegistrationError(f"The {label} must be a {suffix} file.")
    return path


def build_startup_command(
    module_file: str | Path,
    *,
    executable: str | Path | None = None,
    frozen: bool | None = None,
    file_exists: Callable[[Path], bool] | None = None,
) -> str:
    """Build a quoted GUI launch command without reading or writing registry.

    Frozen applications use the durable executable, never the extracted bundle
    directory. Source launches use pythonw.exe to avoid a console at sign-in.
    """
    executable_path = _command_path(
        sys.executable if executable is None else executable, "executable", ".exe"
    )
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    exists = _is_file if file_exists is None else file_exists
    if is_frozen:
        launch_paths = (executable_path,)
    else:
        source_path = _command_path(module_file, "application source", ".pyw")
        launch_paths = (executable_path.with_name("pythonw.exe"), source_path)
    command = " ".join(f'"{path}"' for path in launch_paths) + " --startup"
    try:
        command_length = len(command.encode("utf-16-le")) // 2
    except UnicodeError as exc:
        raise StartupRegistrationError("The startup command contains invalid Unicode.") from exc
    if command_length > MAX_RUN_COMMAND_LENGTH:
        raise StartupRegistrationError(
            "Windows startup commands are limited to 260 characters. "
            "Move the application or Python installation to a shorter path."
        )
    for path in launch_paths:
        try:
            available = exists(path)
        except OSError as exc:
            raise StartupRegistrationError(f"The startup file could not be checked: {exc}") from exc
        if not available:
            if not is_frozen and path == launch_paths[0]:
                raise StartupRegistrationError(
                    f"Start with Windows requires pythonw.exe beside the Python executable: {path}"
                )
            raise StartupRegistrationError(f"The startup file does not exist: {path}")
    return command


class WindowsStartupRegistration:
    """Keep this app's HKCU Run value as the sole startup-setting state."""

    def __init__(
        self,
        module_file: str | Path,
        *,
        executable: str | Path | None = None,
        frozen: bool | None = None,
        winreg_api=None,
    ) -> None:
        self._module_file = module_file
        self._executable = executable
        self._frozen = frozen
        self._winreg = _WINREG if winreg_api is None else winreg_api

    def _registry(self):
        if self._winreg is None:
            raise StartupRegistrationError("Windows startup registration is unavailable.")
        return self._winreg

    def is_enabled(self) -> bool:
        registry = self._registry()
        try:
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_QUERY_VALUE
            ) as key:
                value, value_type = registry.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StartupRegistrationError(f"Could not read Start with Windows: {exc}") from exc
        # A prior install's registration is still enabled and can be removed,
        # even if its launch files are no longer available.
        return value_type == registry.REG_SZ and isinstance(value, str) and bool(value.strip())

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        registry = self._registry()
        if enabled:
            command = build_startup_command(
                self._module_file, executable=self._executable, frozen=self._frozen
            )
            try:
                with registry.CreateKeyEx(
                    registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE
                ) as key:
                    registry.SetValueEx(key, VALUE_NAME, 0, registry.REG_SZ, command)
            except OSError as exc:
                raise StartupRegistrationError(f"Could not enable Start with Windows: {exc}") from exc
        else:
            try:
                with registry.OpenKey(
                    registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE
                ) as key:
                    registry.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise StartupRegistrationError(f"Could not disable Start with Windows: {exc}") from exc
