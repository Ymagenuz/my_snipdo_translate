from __future__ import annotations

import json
from pathlib import Path

import pytest

import app_settings
from api_providers import (
    DEFAULT_API_PROVIDER,
    DEEPSEEK_API_PROVIDER,
    OPENAI_COMPATIBLE_API_PROVIDER,
    OPENROUTER_API_PROVIDER,
    ApiProviderConfig,
    get_api_provider,
    provider_default_config,
)
from app_settings import (
    AppSettings,
    DEFAULT_SETTINGS,
    DEFAULT_SHOW_WINDOW_SHORTCUT,
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
    assert settings.show_window_shortcut == keyboard_shortcut(
        0x57,
        ("ctrl", "alt"),
        "Ctrl+Alt+W",
    )
    assert not path.exists()


@pytest.mark.parametrize(
    "settings",
    [
        AppSettings(enabled=False, shortcut=mouse_shortcut("xbutton2")),
        AppSettings(enabled=True, shortcut=mouse_shortcut("middle")),
        AppSettings(
            enabled=True,
            shortcut=mouse_shortcut("xbutton1"),
            show_window_shortcut=mouse_shortcut("xbutton2"),
        ),
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
        AppSettings(api_provider=OPENROUTER_API_PROVIDER),
        AppSettings(api_provider=OPENAI_COMPATIBLE_API_PROVIDER),
        AppSettings(
            api_provider=OPENAI_COMPATIBLE_API_PROVIDER,
            api_configs={
                OPENAI_COMPATIBLE_API_PROVIDER: ApiProviderConfig(
                    "http://localhost:1234/v1", "local-model", False,
                ),
                DEFAULT_API_PROVIDER: ApiProviderConfig(
                    "https://example.org/v1", "chosen-model", True,
                ),
            },
        ),
        AppSettings(
            enabled=True,
            shortcut=mouse_shortcut("xbutton1"),
            show_window_shortcut=keyboard_shortcut(
                0x52,
                ("ctrl", "shift"),
                "Ctrl+Shift+R",
            ),
            api_provider=DEEPSEEK_API_PROVIDER,
        ),
    ],
)
def test_settings_round_trip_is_utf8_and_canonical(
    tmp_path: Path, settings: AppSettings
):
    path = tmp_path / "data" / "settings.json"

    save_settings_atomic(path, settings)

    assert load_settings(path) == settings
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "enabled",
        "shortcut",
        "show_window_shortcut",
        "api_provider",
        "api_configs",
    }
    assert payload["api_provider"] == settings.api_provider
    assert set(payload["api_configs"]) == set(settings.api_configs)
    assert set(payload["shortcut"]) == {
        "kind",
        "mouse_button",
        "virtual_key",
        "modifiers",
        "display",
    }
    assert set(payload["show_window_shortcut"]) == {
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
        lambda: AppSettings(show_window_shortcut="Ctrl+Alt+W"),
        lambda: AppSettings(api_provider="unknown"),
        lambda: AppSettings(api_provider=1),
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


def test_load_migrates_legacy_payload_to_default_provider(tmp_path: Path):
    path = tmp_path / "settings.json"
    _write_json(path, _valid_mouse_payload())

    settings = load_settings(path)

    assert settings.api_provider == DEFAULT_API_PROVIDER
    assert settings.shortcut == mouse_shortcut("xbutton1")
    assert settings.show_window_shortcut == DEFAULT_SHOW_WINDOW_SHORTCUT
    assert not settings.api_configs


@pytest.mark.parametrize("provider_id", [DEEPSEEK_API_PROVIDER, OPENROUTER_API_PROVIDER])
def test_load_migrates_provider_payload_to_default_show_shortcut(
    tmp_path: Path, provider_id: str,
):
    path = tmp_path / "settings.json"
    _write_json(
        path,
        {
            **_valid_mouse_payload(),
            "api_provider": provider_id,
        },
    )

    settings = load_settings(path)

    assert settings.api_provider == provider_id
    assert settings.show_window_shortcut == DEFAULT_SHOW_WINDOW_SHORTCUT
    assert not settings.api_configs


@pytest.mark.parametrize("api_provider", ["unknown", "DeepSeek", 1, None, []])
def test_load_rejects_unsupported_api_provider(
    tmp_path: Path,
    api_provider: object,
):
    path = tmp_path / "settings.json"
    _write_json(
        path,
        {**_valid_mouse_payload(), "api_provider": api_provider},
    )

    with pytest.raises(SettingsDataError):
        load_settings(path)


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


def test_settings_defensively_copy_provider_configurations():
    config = ApiProviderConfig("https://example.org/v1", "custom-model", True)
    supplied = {DEFAULT_API_PROVIDER: config}

    settings = AppSettings(api_configs=supplied)
    supplied.clear()

    assert settings.config_for(DEFAULT_API_PROVIDER) == config
    assert settings.resolved_provider().model == "custom-model"
    assert settings.resolved_provider(DEEPSEEK_API_PROVIDER) == get_api_provider(
        DEEPSEEK_API_PROVIDER,
    )
    assert settings.config_for(OPENAI_COMPATIBLE_API_PROVIDER) == provider_default_config(
        OPENAI_COMPATIBLE_API_PROVIDER,
    )
    with pytest.raises(TypeError):
        settings.api_configs[DEFAULT_API_PROVIDER] = config  # type: ignore[index]


@pytest.mark.parametrize(
    "configs",
    [
        None,
        [],
        {"unknown": ApiProviderConfig("https://example.org", "model", False)},
        {DEFAULT_API_PROVIDER: {}},
        {DEFAULT_API_PROVIDER: ApiProviderConfig("https://example.org", "", False)},
        {
            OPENAI_COMPATIBLE_API_PROVIDER: provider_default_config(
                OPENAI_COMPATIBLE_API_PROVIDER,
            ),
        },
    ],
)
def test_settings_reject_invalid_explicit_configurations(configs: object):
    with pytest.raises(SettingsDataError):
        AppSettings(api_configs=configs)  # type: ignore[arg-type]


def test_load_migrates_window_shortcut_payload_without_configs(tmp_path: Path):
    path = tmp_path / "settings.json"
    save_settings_atomic(path, AppSettings(api_provider=DEEPSEEK_API_PROVIDER))
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["api_configs"]
    _write_json(path, payload)

    settings = load_settings(path)

    assert settings.api_provider == DEEPSEEK_API_PROVIDER
    assert not settings.api_configs
    assert settings.resolved_provider() == get_api_provider(DEEPSEEK_API_PROVIDER)


def _valid_api_config_payload() -> dict[str, object]:
    return {
        "base_url": "https://example.org/v1",
        "model": "custom-model",
        "supports_vision": True,
        "disable_thinking": False,
    }


@pytest.mark.parametrize(
    "configs",
    [
        None,
        [],
        {"unknown": _valid_api_config_payload()},
        {DEFAULT_API_PROVIDER: None},
        {DEFAULT_API_PROVIDER: {}},
        {DEFAULT_API_PROVIDER: {**_valid_api_config_payload(), "model": " "}},
        {DEFAULT_API_PROVIDER: {**_valid_api_config_payload(), "model": "a\nb"}},
        {DEFAULT_API_PROVIDER: {**_valid_api_config_payload(), "supports_vision": 1}},
        {DEFAULT_API_PROVIDER: {**_valid_api_config_payload(), "disable_thinking": 0}},
        {DEFAULT_API_PROVIDER: {**_valid_api_config_payload(), "api_key": "secret"}},
        {
            DEFAULT_API_PROVIDER: {
                **_valid_api_config_payload(),
                "base_url": "https://user:secret@example.org/v1",
            },
        },
    ],
)
def test_load_rejects_invalid_provider_configuration_payload(
    tmp_path: Path, configs: object,
):
    path = tmp_path / "settings.json"
    save_settings_atomic(path, AppSettings())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["api_configs"] = configs
    _write_json(path, payload)

    with pytest.raises(SettingsDataError):
        load_settings(path)


def test_loading_configuration_normalizes_fields(tmp_path: Path):
    path = tmp_path / "settings.json"
    save_settings_atomic(path, AppSettings(api_provider=OPENAI_COMPATIBLE_API_PROVIDER))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["api_configs"] = {
        OPENAI_COMPATIBLE_API_PROVIDER: {
            **_valid_api_config_payload(),
            "base_url": " https://example.org/v1/// ",
            "model": " custom-model ",
        },
    }
    _write_json(path, payload)

    provider = load_settings(path).resolved_provider()

    assert provider.base_url == "https://example.org/v1"
    assert provider.model == "custom-model"
    assert provider.credential_target == "SnipDoTranslate/OpenAICompatible"
