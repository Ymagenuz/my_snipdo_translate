from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

import app_i18n
from app_i18n import (
    DEFAULT_UI_LANGUAGE,
    UI_LANGUAGE_CODES,
    UI_LANGUAGES,
    language_name,
    messages,
    native_language_name,
    provider_name,
    set_ui_language,
    tr,
    ui_language,
)
from api_providers import api_provider_options


def _placeholders(text: str) -> set[str]:
    return {
        field for _literal, field, _spec, _conversion in string.Formatter().parse(text)
        if field
    }


def test_english_is_the_default_and_simplified_chinese_is_offered():
    assert DEFAULT_UI_LANGUAGE == "en"
    assert ui_language() == "en"
    assert UI_LANGUAGES[0] == ("en", "English")
    assert ("zh-CN", "简体中文") in UI_LANGUAGES
    assert UI_LANGUAGE_CODES == {code for code, _name in UI_LANGUAGES}


@pytest.mark.parametrize("language", sorted(UI_LANGUAGE_CODES))
def test_every_language_has_every_key_with_the_same_placeholders(language: str):
    english = messages(DEFAULT_UI_LANGUAGE)
    table = messages(language)

    assert set(table) == set(english)
    for key, text in english.items():
        assert _placeholders(table[key]) == _placeholders(text), key
        if text:
            assert table[key].strip(), key


def test_switching_language_changes_lookups():
    assert tr("settings.general") == "General"
    set_ui_language("zh-CN")
    assert ui_language() == "zh-CN"
    assert tr("settings.general") == "常规"
    assert tr("update.button.install") == "更新并重启"
    set_ui_language("en")
    assert tr("update.button.install") == "Update and restart"


def test_unknown_languages_are_rejected_and_keep_the_current_one():
    set_ui_language("zh-CN")
    with pytest.raises(ValueError):
        set_ui_language("xx")
    assert ui_language() == "zh-CN"


def test_values_are_formatted_into_the_text():
    assert tr("settings.title", version="1.2.3") == "SnipDo Translate Settings · v1.2.3"
    assert tr(
        "update.progress.download", received=1.25, total=10
    ).startswith("Downloading update: 1.2 / 10.0 MB")


def test_missing_text_falls_back_to_english(monkeypatch):
    chinese = dict(messages("zh-CN"))
    del chinese["settings.general"]
    monkeypatch.setattr(
        app_i18n, "_MESSAGES", {**app_i18n._MESSAGES, "zh-CN": chinese}
    )
    set_ui_language("zh-CN")

    assert tr("settings.general") == "General"


def test_every_provider_and_dictionary_language_has_a_name():
    for provider in api_provider_options():
        assert provider_name(provider.provider_id) == provider.display_name
        assert provider_name(provider.provider_id, short=True) == provider.short_name
    for code in ("auto", "default", "zh", "en", "ja", "ko", "fr", "de", "es", "ru", "it"):
        assert language_name(code)


def test_languages_keep_their_own_names_in_every_ui_language():
    names = {}
    for language in sorted(UI_LANGUAGE_CODES):
        set_ui_language(language)
        names[language] = [
            native_language_name(code)
            for code in ("zh", "en", "ja", "ko", "fr", "de", "es", "ru", "it")
        ]
    assert names["en"] == names["zh-CN"] == [
        "中文", "English", "日本語", "한국어", "Français",
        "Deutsch", "Español", "Русский", "Italiano",
    ]
    set_ui_language("en")
    assert native_language_name("default") == "Default"
    set_ui_language("zh-CN")
    assert native_language_name("default") == "默认"


def test_every_key_used_in_the_code_exists(project_root: Path):
    # tr("literal") calls and f"language.{...}"-style lookups both resolve.
    english = messages(DEFAULT_UI_LANGUAGE)
    used: set[str] = set()
    sources = [*project_root.glob("*.py"), project_root / "snipdo_translate.pyw"]
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
            ):
                argument = node.args[0]
                if isinstance(argument, ast.Constant):
                    used.add(argument.value)
                elif isinstance(argument, ast.IfExp):
                    for branch in (argument.body, argument.orelse):
                        assert isinstance(branch, ast.Constant), path
                        used.add(branch.value)
    assert used
    assert used - set(english) == set()


def test_ui_modules_contain_no_untranslated_chinese(project_root: Path):
    # Only model prompts, comments and text-processing tables may hold Chinese.
    han = re.compile(r"[\u4e00-\u9fff]")
    for name in (
        "app_update_ui.py",
        "app_update_flow.py",
        "app_update_download.py",
        "app_updates.py",
        "app_update_install.py",
        "app_settings.py",
        "api_providers.py",
        "app_cli.py",
    ):
        source = (project_root / name).read_text(encoding="utf-8")
        assert not han.search(source), name
