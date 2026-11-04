from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QFormLayout

from app_i18n import set_ui_language, ui_language
from app_paths import AppPaths
from app_settings import AppSettings, DEFAULT_SETTINGS, load_settings


@pytest.fixture(scope="module")
def app_module():
    module_path = Path(__file__).resolve().parents[1] / "snipdo_translate.pyw"
    module_name = "_snipdo_translate_ui_language_target"
    loader = importlib.machinery.SourceFileLoader(module_name, str(module_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)


@pytest.fixture
def app(app_module, monkeypatch):
    def forbidden_openai(*_args, **_kwargs):
        raise AssertionError("language tests must not create a real API client")

    monkeypatch.setattr(app_module, "OpenAI", forbidden_openai)
    return app_module


def _app_paths(tmp_path: Path) -> AppPaths:
    data_dir = tmp_path / "data"
    return AppPaths(
        bundle_dir=tmp_path,
        executable_dir=tmp_path,
        data_dir=data_dir,
        history_path=data_dir / "translation_history.json",
        log_dir=data_dir / "logs",
        icon_path=tmp_path / "missing-icon.png",
        legacy_dirs=(tmp_path,),
    )


@pytest.fixture
def make_window(app, monkeypatch, qapp, tmp_path):
    monkeypatch.setattr(
        app.TranslationWindow, "setup_translation_shortcut", lambda self: None
    )
    windows = []

    def make(settings: AppSettings | None = None):
        window = app.TranslationWindow(
            _app_paths(tmp_path), object(), initial_settings=settings
        )
        windows.append(window)
        return window

    yield make
    for window in windows:
        window.tray_icon.hide()
        window.deleteLater()
    qapp.processEvents()


def _dialog(app, settings=DEFAULT_SETTINGS):
    return app.SettingsDialog(
        settings, api_key_configured=False, environment_key_active=False
    )


def test_language_is_the_first_choice_on_the_general_page(app, qapp):
    dialog = _dialog(app)
    try:
        general = dialog.pages.widget(0)
        assert dialog.page_list.item(0).text() == "General"
        assert general.isAncestorOf(dialog.language_combo)
        form = general.findChild(QFormLayout)
        first_field = form.itemAt(0, QFormLayout.ItemRole.FieldRole).widget()
        first_label = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()
        assert first_field is dialog.language_combo
        assert first_label.text() == "Language"
        assert [
            (dialog.language_combo.itemText(index), dialog.language_combo.itemData(index))
            for index in range(dialog.language_combo.count())
        ] == [("English", "en"), ("简体中文", "zh-CN")]
        assert dialog.language_combo.currentData() == "en"

        dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("zh-CN"))

        assert dialog.candidate_settings() == replace(
            DEFAULT_SETTINGS, ui_language="zh-CN"
        )
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_settings_dialog_uses_the_saved_language(app, qapp):
    set_ui_language("zh-CN")
    dialog = _dialog(app, replace(DEFAULT_SETTINGS, ui_language="zh-CN"))
    try:
        assert dialog.windowTitle() == "设置"
        assert [
            dialog.page_list.item(index).text()
            for index in range(dialog.page_list.count())
        ] == ["常规", "快捷键", "翻译", "API 接口"]
        assert dialog.language_combo.currentData() == "zh-CN"
        assert dialog.chk_enabled.text() == "启用全局划词翻译"
        assert dialog.fetch_models_button.text() == "获取模型"
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_window_starts_in_the_saved_language(make_window):
    window = make_window(replace(DEFAULT_SETTINGS, ui_language="zh-CN"))

    assert ui_language() == "zh-CN"
    assert window.btn_back_translation.text() == "回译"
    assert window.btn_content_mode.text() == "模式: Auto"
    assert window.settings_action.text() == "设置…"
    assert window.cbo_dictionary_target.itemText(0) == "默认"


def test_saving_a_language_relabels_the_window_and_tray_at_once(make_window):
    window = make_window()
    assert window.btn_back_translation.text() == "Back-translate"
    assert window.settings_action.text() == "Settings…"
    assert window.cbo_dictionary_target.itemText(0) == "Default"
    # English leaves the caption out so long headings fit.
    assert window.lbl_dictionary_lang.isHidden()
    window.btn_align_selection.setText("Locate in translation")

    assert window.apply_settings(replace(window.app_settings, ui_language="zh-CN"))

    assert window.app_settings.ui_language == "zh-CN"
    assert load_settings(window.settings_path).ui_language == "zh-CN"
    assert window.btn_back_translation.text() == "回译"
    assert window.btn_stop_back_translation.text() == "停止"
    assert window.btn_content_mode.text() == "模式: Auto"
    assert window.lbl_dictionary_lang.text() == "语言"
    assert not window.lbl_dictionary_lang.isHidden()
    assert window.cbo_dictionary_target.itemText(0) == "默认"
    assert window.btn_settings.accessibleName() == "设置"
    assert window.btn_align_selection.text() == "定位译文"
    assert window.toggle_translation_action.text() == "禁用"
    assert window.settings_action.text() == "设置…"
    assert window.check_updates_action.text() == "检查更新…"
    assert window.quit_action.text() == "彻底退出"
    assert window.show_window_action.text() == "显示主窗口（Ctrl+Alt+W）"
    assert window.btn_settings.toolTip().startswith("设置 · 翻译快捷键：XButton1")
    assert "当前接口" in window.lbl_model_name.toolTip()

    assert window.apply_settings(replace(window.app_settings, ui_language="en"))

    assert load_settings(window.settings_path).ui_language == "en"
    assert window.btn_back_translation.text() == "Back-translate"
    assert window.settings_action.text() == "Settings…"
    assert window.show_window_action.text() == "Show main window (Ctrl+Alt+W)"
    assert window.btn_align_selection.text() == "Locate in translation"
    assert window.cbo_dictionary_target.itemText(0) == "Default"
    assert window.lbl_dictionary_lang.isHidden()


def _options(combo):
    return [combo.itemText(index) for index in range(combo.count())]


NATIVE_OPTIONS = [
    "中文", "English", "日本語", "한국어", "Français",
    "Deutsch", "Español", "Русский", "Italiano",
]


def test_language_lists_name_each_language_in_its_own_language(make_window):
    window = make_window()
    assert _options(window.cbo_dictionary_source) == ["Auto", *NATIVE_OPTIONS]
    assert _options(window.cbo_dictionary_target) == ["Default", *NATIVE_OPTIONS]

    assert window.apply_settings(replace(window.app_settings, ui_language="zh-CN"))

    assert _options(window.cbo_dictionary_source) == ["Auto", *NATIVE_OPTIONS]
    assert _options(window.cbo_dictionary_target) == ["默认", *NATIVE_OPTIONS]


def test_language_switch_keeps_the_translation_languages(app, make_window):
    window = make_window()
    window.cbo_dictionary_source.setCurrentIndex(
        window.cbo_dictionary_source.findData("en")
    )
    window.cbo_dictionary_target.setCurrentIndex(
        window.cbo_dictionary_target.findData("ja")
    )
    assert window.lbl_result.text() == "JAPANESE TRANSLATION"
    english_prompt = app.TranslationThread(
        "serendipity", "dictionary", "en", "ja"
    ).build_prompt()

    assert window.apply_settings(replace(window.app_settings, ui_language="zh-CN"))

    assert window.cbo_dictionary_source.currentData() == "en"
    assert window.cbo_dictionary_target.currentData() == "ja"
    assert window.current_dictionary_languages() == ("en", "ja")
    assert window.lbl_result.text() == "日本語 TRANSLATION"
    # Prompts never follow the interface language.
    assert app.TranslationThread(
        "serendipity", "dictionary", "en", "ja"
    ).build_prompt() == english_prompt
    assert "原文语言：English" in english_prompt
    assert "释义/对应表达语言：日本語" in english_prompt


def test_back_translation_heading_follows_the_language(make_window):
    window = make_window()
    window.back_translation_language_code = "zh"
    window.back_translation_panel.show()
    window.lbl_back_translation.setText("BACK TRANSLATION · Chinese")

    assert window.apply_settings(replace(window.app_settings, ui_language="zh-CN"))

    assert window.lbl_back_translation.text() == "BACK TRANSLATION · 中文"


def test_default_target_is_still_sent_to_the_model_as_before(app):
    set_ui_language("en")
    english = app.TranslationThread("word", "dictionary", "auto", "default").build_prompt()
    set_ui_language("zh-CN")
    chinese = app.TranslationThread("word", "dictionary", "auto", "default").build_prompt()

    assert english == chinese
    assert "释义/对应表达语言：默认" in english
