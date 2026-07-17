from __future__ import annotations

import json
from pathlib import Path

import pytest

import app_settings
from app_settings import (
    AppSettings,
    DEFAULT_SETTINGS,
    SettingsDataError,
    ShortcutBinding,
    keyboard_shortcut,
    load_settings,
    mouse_shortcut,
    save_settings_atomic,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_missing_settings_returns_enabled_xbutton1_default(tmp_path: Path):
    path = tmp_path / "settings.json"

    settings = load_settings(path)

    assert settings == DEFAULT_SETTINGS
    assert settings.enabled is True
    assert settings.shortcut == mouse_shortcut("xbutton1")
    assert settings.shortcut.display == "XButton1"
    assert not path.exists()


@pytest.mark.parametrize(
    "settings",
    [
        AppSettings(enabled=False, shortcut=mouse_shortcut("xbutton2")),
        AppSettings(enabled=True, shortcut=mouse_shortcut("middle")),
        AppSettings(
            enabled=True,
            shortcut=keyboard_shortcut(
                0x54,
                ("shift", "CTRL", "alt", "ctrl"),
                "Ctrl+Alt+Shift+T",
            ),
        ),
        AppSettings(enabled=False, shortcut=keyboard_shortcut(0x70, (), "F1")),
        AppSettings(enabled=True, shortcut=keyboard_shortcut(0x87, (), "F24")),
    ],
)
def test_settings_round_trip_is_utf8_and_canonical(
    tmp_path: Path, settings: AppSettings
):
    path = tmp_path / "data" / "settings.json"

    save_settings_atomic(path, settings)

    assert load_settings(path) == settings
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"enabled", "shortcut"}
    assert set(payload["shortcut"]) == {
        "kind",
        "mouse_button",
        "virtual_key",
        "modifiers",
        "display",
    }
    serialized = path.read_text(encoding="utf-8").casefold()
    assert "api_key" not in serialized
    assert "apikey" not in serialized
    assert not list(path.parent.glob("settings.json.*.tmp"))


def test_keyboard_factory_canonicalizes_modifiers():
    binding = keyboard_shortcut(
        0x41,
        ("WIN", "shift", "ctrl", "ALT", "ctrl"),
        "Ctrl+Alt+Shift+Win+A",
    )

    assert binding.modifiers == ("ctrl", "alt", "shift", "win")


@pytest.mark.parametrize("virtual_key", [0, 255, -1, True, 1.5, "65"])
def test_keyboard_factory_rejects_invalid_virtual_key(virtual_key: object):
    with pytest.raises(SettingsDataError):
        keyboard_shortcut(virtual_key, ("ctrl",), "Ctrl+A")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("virtual_key", "modifiers", "display"),
    [
        (0x41, (), "A"),
        (0x41, ("meta",), "Meta+A"),
        (0x41, "ctrl", "Ctrl+A"),
        (0x41, ("ctrl", 1), "Ctrl+A"),
        (0x41, ("ctrl",), ""),
        (0x41, ("ctrl",), " Ctrl+A"),
        (0x41, ("ctrl",), "Ctrl+A\n"),
        (0x6F, (), "F0"),
        (0x88, (), "F25"),
    ],
)
def test_keyboard_factory_rejects_invalid_binding(
    virtual_key: int, modifiers: object, display: str
):
    with pytest.raises(SettingsDataError):
        keyboard_shortcut(virtual_key, modifiers, display)  # type: ignore[arg-type]


@pytest.mark.parametrize("button", ["", "left", "right", "xbutton3", 1, None])
def test_mouse_factory_rejects_invalid_button(button: object):
    with pytest.raises(SettingsDataError):
        mouse_shortcut(button)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: AppSettings(enabled=1),
        lambda: AppSettings(shortcut="xbutton1"),
        lambda: ShortcutBinding("touch", None, None, (), "Touch"),
        lambda: ShortcutBinding("mouse", "xbutton1", 1, (), "XButton1"),
        lambda: ShortcutBinding("mouse", "xbutton1", None, ("ctrl",), "XButton1"),
        lambda: ShortcutBinding("mouse", "xbutton1", None, (), "xbutton1"),
        lambda: ShortcutBinding("keyboard", "xbutton1", 0x41, ("ctrl",), "Ctrl+A"),
        lambda: ShortcutBinding("keyboard", None, 0x41, ("shift", "ctrl"), "Ctrl+Shift+A"),
        lambda: ShortcutBinding("keyboard", None, 0x41, (), "A"),
    ],
)
def test_dataclasses_reject_invalid_direct_construction(constructor):
    with pytest.raises(SettingsDataError):
        constructor()


def _valid_mouse_payload() -> dict[str, object]:
    return {
        "enabled": True,
        "shortcut": {
            "kind": "mouse",
            "mouse_button": "xbutton1",
            "virtual_key": None,
            "modifiers": [],
            "display": "XButton1",
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"enabled": True},
        {"enabled": 1, "shortcut": _valid_mouse_payload()["shortcut"]},
        {**_valid_mouse_payload(), "api_key": "must-never-be-accepted"},
        {"enabled": True, "shortcut": None},
        {
            "enabled": True,
            "shortcut": {
                **_valid_mouse_payload()["shortcut"],
                "api_key": "must-never-be-accepted",
            },
        },
        {
            "enabled": True,
            "shortcut": {
                **_valid_mouse_payload()["shortcut"],
                "display": "XBUTTON1",
            },
        },
        {
            "enabled": True,
            "shortcut": {
                "kind": "keyboard",
                "mouse_button": None,
                "virtual_key": 0x41,
                "modifiers": [],
                "display": "A",
            },
        },
        {
            "enabled": True,
            "shortcut": {
                "kind": "keyboard",
                "mouse_button": None,
                "virtual_key": 0x41,
                "modifiers": ["shift", "ctrl"],
                "display": "Ctrl+Shift+A",
            },
        },
    ],
)
def test_load_rejects_malformed_or_secret_bearing_payload(
    tmp_path: Path, payload: object
):
    path = tmp_path / "settings.json"
    _write_json(path, payload)

    with pytest.raises(SettingsDataError):
        load_settings(path)


@pytest.mark.parametrize(
    "contents",
    [
        "{broken",
        '{"enabled":true,"enabled":false,"shortcut":{}}',
    ],
)
def test_load_rejects_invalid_json_and_duplicate_fields(
    tmp_path: Path, contents: str
):
    path = tmp_path / "settings.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(SettingsDataError):
        load_settings(path)


def test_load_rejects_invalid_utf8(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(SettingsDataError):
        load_settings(path)


def test_atomic_replace_failure_preserves_old_settings_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "settings.json"
    old_settings = AppSettings(enabled=True, shortcut=mouse_shortcut("xbutton1"))
    new_settings = AppSettings(enabled=False, shortcut=mouse_shortcut("middle"))
    save_settings_atomic(path, old_settings)
    original_bytes = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(app_settings.os, "replace", fail_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        save_settings_atomic(path, new_settings)

    assert path.read_bytes() == original_bytes
    assert load_settings(path) == old_settings
    assert not list(tmp_path.glob("settings.json.*.tmp"))


def test_save_rejects_non_settings_without_creating_file(tmp_path: Path):
    path = tmp_path / "settings.json"

    with pytest.raises(SettingsDataError):
        save_settings_atomic(path, {"api_key": "secret"})  # type: ignore[arg-type]

    assert not path.exists()
