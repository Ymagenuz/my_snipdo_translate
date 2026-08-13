from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api_providers import API_PROVIDER_IDS, DEFAULT_API_PROVIDER


SETTINGS_FILE_NAME = "settings.json"

MOUSE_BUTTONS = frozenset({"xbutton1", "xbutton2", "middle"})
MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
MODIFIERS = frozenset(MODIFIER_ORDER)

VK_F1 = 0x70
VK_F24 = 0x87

_MOUSE_DISPLAY = {
    "xbutton1": "XButton1",
    "xbutton2": "XButton2",
    "middle": "Middle Mouse",
}
_LEGACY_ROOT_FIELDS = frozenset({"enabled", "shortcut"})
_PROVIDER_ROOT_FIELDS = frozenset({"enabled", "shortcut", "api_provider"})
_ROOT_FIELDS = frozenset(
    {"enabled", "shortcut", "show_window_shortcut", "api_provider"}
)
_SHORTCUT_FIELDS = frozenset(
    {"kind", "mouse_button", "virtual_key", "modifiers", "display"}
)


class SettingsDataError(ValueError):
    """The settings payload is malformed or violates the supported schema."""


def _validate_display(display: object) -> str:
    if not isinstance(display, str):
        raise SettingsDataError("shortcut display must be a string")
    normalized = display.strip()
    if not normalized:
        raise SettingsDataError("shortcut display must not be empty")
    if normalized != display:
        raise SettingsDataError("shortcut display must not have surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in display):
        raise SettingsDataError("shortcut display must not contain control characters")
    return display


def _validate_virtual_key(virtual_key: object) -> int:
    if isinstance(virtual_key, bool) or not isinstance(virtual_key, int):
        raise SettingsDataError("keyboard virtual_key must be an integer")
    if not 1 <= virtual_key <= 254:
        raise SettingsDataError("keyboard virtual_key must be between 1 and 254")
    return virtual_key


def _canonical_modifiers(modifiers: Iterable[str]) -> tuple[str, ...]:
    if isinstance(modifiers, (str, bytes)):
        raise SettingsDataError("keyboard modifiers must be an iterable of names")
    try:
        supplied = tuple(modifiers)
    except TypeError as exc:
        raise SettingsDataError("keyboard modifiers must be iterable") from exc

    normalized: set[str] = set()
    for modifier in supplied:
        if not isinstance(modifier, str):
            raise SettingsDataError("keyboard modifier names must be strings")
        name = modifier.strip().lower()
        if name not in MODIFIERS:
            raise SettingsDataError("unsupported keyboard modifier")
        normalized.add(name)
    return tuple(name for name in MODIFIER_ORDER if name in normalized)


@dataclass(frozen=True)
class ShortcutBinding:
    kind: str
    mouse_button: str | None
    virtual_key: int | None
    modifiers: tuple[str, ...]
    display: str

    def __post_init__(self) -> None:
        if self.kind == "mouse":
            if self.mouse_button not in MOUSE_BUTTONS:
                raise SettingsDataError("unsupported mouse shortcut")
            if self.virtual_key is not None:
                raise SettingsDataError("mouse shortcut must not have a virtual_key")
            if not isinstance(self.modifiers, tuple) or self.modifiers:
                raise SettingsDataError("mouse shortcut must not have modifiers")
            if self.display != _MOUSE_DISPLAY[self.mouse_button]:
                raise SettingsDataError("mouse shortcut display is not canonical")
            return

        if self.kind == "keyboard":
            if self.mouse_button is not None:
                raise SettingsDataError("keyboard shortcut must not have a mouse button")
            virtual_key = _validate_virtual_key(self.virtual_key)
            if not isinstance(self.modifiers, tuple):
                raise SettingsDataError("keyboard modifiers must be a tuple")
            canonical = _canonical_modifiers(self.modifiers)
            if canonical != self.modifiers:
                raise SettingsDataError("keyboard modifiers are not canonical")
            if not canonical and not VK_F1 <= virtual_key <= VK_F24:
                raise SettingsDataError(
                    "ordinary keyboard shortcuts require at least one modifier"
                )
            _validate_display(self.display)
            return

        raise SettingsDataError("unsupported shortcut kind")


def mouse_shortcut(mouse_button: str) -> ShortcutBinding:
    if not isinstance(mouse_button, str):
        raise SettingsDataError("mouse button must be a string")
    normalized = mouse_button.strip().lower()
    if normalized not in MOUSE_BUTTONS:
        raise SettingsDataError("unsupported mouse shortcut")
    return ShortcutBinding(
        kind="mouse",
        mouse_button=normalized,
        virtual_key=None,
        modifiers=(),
        display=_MOUSE_DISPLAY[normalized],
    )


def keyboard_shortcut(
    virtual_key: int,
    modifiers: Iterable[str],
    display: str,
) -> ShortcutBinding:
    normalized_key = _validate_virtual_key(virtual_key)
    normalized_modifiers = _canonical_modifiers(modifiers)
    normalized_display = _validate_display(display)
    if not normalized_modifiers and not VK_F1 <= normalized_key <= VK_F24:
        raise SettingsDataError(
            "ordinary keyboard shortcuts require at least one modifier"
        )
    return ShortcutBinding(
        kind="keyboard",
        mouse_button=None,
        virtual_key=normalized_key,
        modifiers=normalized_modifiers,
        display=normalized_display,
    )


DEFAULT_SHORTCUT = mouse_shortcut("xbutton1")
DEFAULT_SHOW_WINDOW_SHORTCUT = keyboard_shortcut(
    0x57,
    ("ctrl", "alt"),
    "Ctrl+Alt+W",
)


@dataclass(frozen=True)
class AppSettings:
    enabled: bool = True
    shortcut: ShortcutBinding = field(default_factory=lambda: DEFAULT_SHORTCUT)
    show_window_shortcut: ShortcutBinding = field(
        default_factory=lambda: DEFAULT_SHOW_WINDOW_SHORTCUT
    )
    api_provider: str = DEFAULT_API_PROVIDER

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise SettingsDataError("enabled must be a boolean")
        if not isinstance(self.shortcut, ShortcutBinding):
            raise SettingsDataError("shortcut must be a ShortcutBinding")
        if not isinstance(self.show_window_shortcut, ShortcutBinding):
            raise SettingsDataError(
                "show_window_shortcut must be a ShortcutBinding"
            )
        if (
            not isinstance(self.api_provider, str)
            or self.api_provider not in API_PROVIDER_IDS
        ):
            raise SettingsDataError("unsupported API provider")


DEFAULT_SETTINGS = AppSettings()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SettingsDataError("settings contain a duplicate field")
        result[key] = value
    return result


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str]) -> None:
    if frozenset(value) != expected:
        raise SettingsDataError("settings fields do not match the supported schema")


def _decode_shortcut(value: object) -> ShortcutBinding:
    if not isinstance(value, dict):
        raise SettingsDataError("shortcut must be an object")
    _require_exact_fields(value, _SHORTCUT_FIELDS)

    modifiers = value["modifiers"]
    if not isinstance(modifiers, list):
        raise SettingsDataError("shortcut modifiers must be an array")

    kind = value["kind"]
    if kind == "mouse":
        if value["virtual_key"] is not None or modifiers:
            raise SettingsDataError("mouse shortcut has keyboard-only fields")
        binding = mouse_shortcut(value["mouse_button"])
        if value["display"] != binding.display:
            raise SettingsDataError("mouse shortcut display is not canonical")
        return binding

    if kind == "keyboard":
        if value["mouse_button"] is not None:
            raise SettingsDataError("keyboard shortcut has a mouse-only field")
        binding = keyboard_shortcut(
            value["virtual_key"],
            modifiers,
            value["display"],
        )
        if modifiers != list(binding.modifiers) or value["display"] != binding.display:
            raise SettingsDataError("keyboard shortcut is not canonical")
        return binding

    raise SettingsDataError("unsupported shortcut kind")


def _decode_settings(value: object) -> AppSettings:
    if not isinstance(value, dict):
        raise SettingsDataError("settings root must be an object")
    fields = frozenset(value)
    if fields == _LEGACY_ROOT_FIELDS:
        api_provider = DEFAULT_API_PROVIDER
        show_window_shortcut = DEFAULT_SHOW_WINDOW_SHORTCUT
    elif fields == _PROVIDER_ROOT_FIELDS:
        api_provider = value["api_provider"]
        show_window_shortcut = DEFAULT_SHOW_WINDOW_SHORTCUT
    elif fields == _ROOT_FIELDS:
        api_provider = value["api_provider"]
        show_window_shortcut = _decode_shortcut(value["show_window_shortcut"])
    else:
        raise SettingsDataError("settings fields do not match the supported schema")
    if not isinstance(value["enabled"], bool):
        raise SettingsDataError("enabled must be a boolean")
    return AppSettings(
        enabled=value["enabled"],
        shortcut=_decode_shortcut(value["shortcut"]),
        show_window_shortcut=show_window_shortcut,
        api_provider=api_provider,
    )


def load_settings(path: Path) -> AppSettings:
    settings_path = Path(path)
    if not settings_path.exists():
        return DEFAULT_SETTINGS
    try:
        contents = settings_path.read_text(encoding="utf-8")
        decoded = json.loads(contents, object_pairs_hook=_reject_duplicate_keys)
        return _decode_settings(decoded)
    except SettingsDataError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsDataError("settings could not be loaded") from exc


def _encode_settings(settings: AppSettings) -> dict[str, object]:
    if not isinstance(settings, AppSettings):
        raise SettingsDataError("settings must be an AppSettings instance")
    shortcut = settings.shortcut
    show_window_shortcut = settings.show_window_shortcut
    return {
        "enabled": settings.enabled,
        "api_provider": settings.api_provider,
        "shortcut": {
            "kind": shortcut.kind,
            "mouse_button": shortcut.mouse_button,
            "virtual_key": shortcut.virtual_key,
            "modifiers": list(shortcut.modifiers),
            "display": shortcut.display,
        },
        "show_window_shortcut": {
            "kind": show_window_shortcut.kind,
            "mouse_button": show_window_shortcut.mouse_button,
            "virtual_key": show_window_shortcut.virtual_key,
            "modifiers": list(show_window_shortcut.modifiers),
            "display": show_window_shortcut.display,
        },
    }


def save_settings_atomic(path: Path, settings: AppSettings) -> None:
    settings_path = Path(path)
    payload = _encode_settings(settings)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{settings_path.name}.",
        suffix=".tmp",
        dir=settings_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, settings_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


__all__ = [
    "AppSettings",
    "DEFAULT_API_PROVIDER",
    "DEFAULT_SETTINGS",
    "DEFAULT_SHOW_WINDOW_SHORTCUT",
    "DEFAULT_SHORTCUT",
    "MOUSE_BUTTONS",
    "MODIFIERS",
    "SETTINGS_FILE_NAME",
    "SettingsDataError",
    "ShortcutBinding",
    "keyboard_shortcut",
    "load_settings",
    "mouse_shortcut",
    "save_settings_atomic",
]
