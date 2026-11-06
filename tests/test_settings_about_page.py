from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap, QTextCursor
from PyQt6.QtWidgets import QDialog, QFrame, QLabel, QWidget

from app_paths import AppPaths
from app_settings import DEFAULT_SETTINGS
from app_updates import REPOSITORY_URL, ReleaseInfo


RELEASE = ReleaseInfo(
    version="12.9.0",
    url="https://github.com/Ymagenuz/my_snipdo_translate/releases/tag/v12.9.0",
)
CHANGELOG = (
    "## 12.6.0 — 2026-11-05\n\n- Blue download icon.\n\n"
    "## 12.5.0 — 2026-11-04\n\n- English."
)


@pytest.fixture(scope="module")
def app_module():
    module_path = Path(__file__).resolve().parents[1] / "snipdo_translate.pyw"
    module_name = "_snipdo_translate_about_page_target"
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
        raise AssertionError("About page tests must not create a real API client")

    monkeypatch.setattr(app_module, "OpenAI", forbidden_openai)
    return app_module


class FakeUpdates(QObject):
    changed = pyqtSignal()

    def __init__(self, release=None):
        super().__init__()
        self.available_release = release
        self.is_checking = False
        self.is_installing = False
        self.last_result = ""
        self.manual_checks = 0
        self.browser_opens = True
        self.opened = []

    def check_manually(self):
        self.manual_checks += 1

    def open_release_page(self, release):
        self.opened.append(release.url)
        return self.browser_opens


@pytest.fixture
def make_dialog(app, qapp):
    dialogs = []

    def make(updates=None, *, page="", changelog="", app_icon=None):
        dialog = app.SettingsDialog(
            DEFAULT_SETTINGS,
            api_key_configured=False,
            environment_key_active=False,
            updates=updates,
            page=page,
            changelog=changelog,
            app_icon=app_icon,
        )
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.deleteLater()
    qapp.processEvents()


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
def window(app, monkeypatch, qapp, tmp_path):
    monkeypatch.setattr(
        app.TranslationWindow, "setup_translation_shortcut", lambda self: None
    )
    window = app.TranslationWindow(_app_paths(tmp_path), object())
    yield window
    window.update_notifications.stop()
    window.tray_icon.hide()
    window.deleteLater()
    qapp.processEvents()


def _dot_color(dialog) -> str:
    style = dialog.update_status_dot.styleSheet()
    return style.split("background-color:")[1].split(";")[0].strip()


def _about_labels(dialog):
    return dialog.pages.widget(dialog._about_page).findChildren(QLabel)


def test_about_is_the_last_page_and_introduces_the_app(app, make_dialog):
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor("#409EFF"))
    dialog = make_dialog(app_icon=QIcon(pixmap))
    about = dialog.pages.widget(dialog._about_page)
    labels = _about_labels(dialog)

    assert dialog.page_list.item(dialog.page_list.count() - 1).text() == "About"
    assert dialog.pages.currentIndex() == 0
    assert about.isAncestorOf(dialog.version_label)
    assert dialog.version_label.text() == f"Version {app.APP_VERSION}"
    assert any(label.text() == "SnipDo Translate" for label in labels)
    assert any(label.pixmap() is not None and not label.pixmap().isNull() for label in labels)
    assert any(REPOSITORY_URL in label.text() for label in labels)
    # Without update state there is nothing to install or check.
    assert not hasattr(dialog, "install_update_button")


def test_card_text_sits_directly_on_the_white_card(app, qapp):
    # The main window's selector-less background reaches every child widget,
    # Settings included; text inside the card must not pick it up.
    parent = QWidget()
    parent.setStyleSheet("background-color: #F5F7FA;")
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
        updates=FakeUpdates(),
        page="about",
        parent=parent,
    )
    try:
        dialog.resize(780, 640)
        dialog.show()
        qapp.processEvents()
        card = dialog.findChild(QFrame, "aboutCard")
        image = card.grab().toImage()
        labels = [
            label for label in card.findChildren(QLabel)
            if label.isVisible() and label.text() and label is not dialog.version_label
        ]
        assert len(labels) == 4  # Name, tagline, GitHub link and update status.
        for label in labels:
            # Count only the inherited grey: glyphs may cover any given pixel,
            # and how much they cover depends on the fonts available.
            area = QRect(label.mapTo(card, QPoint(0, 0)), label.size())
            grey = sum(
                image.pixelColor(x, y).name() == "#f5f7fa"
                for x in range(area.left(), area.right() + 1)
                for y in range(area.top(), area.bottom() + 1)
            )
            assert grey == 0, label.text()
    finally:
        dialog.deleteLater()
        parent.deleteLater()
        qapp.processEvents()


def test_about_page_can_open_first(make_dialog):
    dialog = make_dialog(FakeUpdates(), page="about")

    assert dialog.pages.currentIndex() == dialog._about_page
    assert dialog.page_list.currentItem().text() == "About"


def test_about_page_offers_an_available_update(app, make_dialog):
    dialog = make_dialog(FakeUpdates(RELEASE), page="about")

    assert dialog.update_status_label.text() == (
        f"Version 12.9.0 is available. You have {app.APP_VERSION}."
    )
    assert _dot_color(dialog) == "#409EFF"
    assert not dialog.install_update_button.isHidden()
    assert dialog.install_update_button.text() == "Update and restart"
    assert not dialog.release_notes_button.isHidden()
    assert dialog.check_updates_button.isEnabled()
    assert dialog.update_note_label.isHidden()


def test_about_page_without_an_update_only_offers_a_check(make_dialog):
    updates = FakeUpdates()
    dialog = make_dialog(updates)

    assert dialog.update_status_label.text() == "Checked automatically every 6 hours."
    assert dialog.install_update_button.isHidden()
    assert dialog.release_notes_button.isHidden()

    dialog.check_updates_button.click()
    assert updates.manual_checks == 1


def test_about_page_follows_a_check_while_open(make_dialog):
    updates = FakeUpdates()
    dialog = make_dialog(updates, page="about")

    updates.is_checking = True
    updates.changed.emit()
    assert dialog.update_status_label.text() == "Checking for updates…"
    assert _dot_color(dialog) == "#E6A23C"
    assert not dialog.check_updates_button.isEnabled()

    updates.is_checking = False
    updates.last_result = "current"
    updates.changed.emit()
    assert dialog.update_status_label.text() == "You're up to date."
    assert _dot_color(dialog) == "#67C23A"
    assert dialog.check_updates_button.isEnabled()

    updates.last_result = "failed"
    updates.changed.emit()
    assert dialog.update_status_label.text().startswith("Can't check for updates")
    assert _dot_color(dialog) == "#F56C6C"

    updates.available_release = RELEASE
    updates.changed.emit()
    assert "12.9.0" in dialog.update_status_label.text()
    assert not dialog.install_update_button.isHidden()

    # A closed dialog stops listening.
    dialog.reject()
    updates.available_release = None
    updates.changed.emit()
    assert not dialog.install_update_button.isHidden()


def test_check_result_follows_the_interface_language(app, make_dialog):
    updates = FakeUpdates()
    updates.last_result = "current"
    app.set_ui_language("zh-CN")
    dialog = make_dialog(updates)

    assert dialog.update_status_label.text() == "已是最新版本。"
    assert dialog.version_label.text() == f"版本 {app.APP_VERSION}"


def test_about_page_during_a_download_offers_nothing_to_click(make_dialog):
    updates = FakeUpdates(RELEASE)
    updates.is_installing = True
    dialog = make_dialog(updates)

    assert dialog.update_status_label.text().startswith("Downloading the update")
    assert dialog.install_update_button.isHidden()
    assert not dialog.check_updates_button.isEnabled()


def test_update_and_restart_saves_changes_and_requests_the_update(make_dialog):
    dialog = make_dialog(FakeUpdates(RELEASE), page="about")
    dialog.chk_back_translation.setChecked(True)

    dialog.install_update_button.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.requested_update == RELEASE
    assert dialog.candidate_settings() == replace(DEFAULT_SETTINGS, back_translation=True)


def test_update_and_restart_waits_for_invalid_settings_to_be_fixed(app, make_dialog):
    dialog = make_dialog(FakeUpdates(RELEASE), page="about")
    dialog.model_combo.setCurrentText("")

    dialog.install_update_button.click()

    assert dialog.requested_update is None
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.pages.currentIndex() == dialog._api_page
    assert dialog.status_label.text() == app.tr(
        "settings.api.invalid_config",
        provider=app.provider_name(DEFAULT_SETTINGS.api_provider),
    )


def test_release_notes_show_the_address_when_no_browser_opens(make_dialog):
    updates = FakeUpdates(RELEASE)
    dialog = make_dialog(updates, page="about")

    dialog.release_notes_button.click()
    assert updates.opened == [RELEASE.url]
    assert dialog.update_note_label.isHidden()

    updates.browser_opens = False
    dialog.release_notes_button.click()
    assert not dialog.update_note_label.isHidden()
    assert RELEASE.url in dialog.update_note_label.text()
    assert dialog.requested_update is None


def test_about_page_follows_the_interface_language(app, make_dialog):
    app.set_ui_language("zh-CN")
    dialog = make_dialog(FakeUpdates(RELEASE))

    assert dialog.page_list.item(dialog._about_page).text() == "关于"
    assert dialog.install_update_button.text() == "更新并重启"
    assert dialog.release_notes_button.text() == "发行说明"
    assert "12.9.0" in dialog.update_status_label.text()


def test_about_page_shows_the_changelog_from_the_top(app, make_dialog, monkeypatch):
    monkeypatch.setattr(app, "APP_VERSION", "12.6.0")
    dialog = make_dialog(FakeUpdates(), changelog=CHANGELOG)
    text = dialog.changelog_view.toPlainText()

    assert dialog.pages.widget(dialog._about_page).isAncestorOf(dialog.changelog_view)
    assert dialog.changelog_view.isReadOnly()
    assert text.startswith("12.6.0 — 2026-11-05    Installed \nBlue download icon.")
    assert "12.5.0 — 2026-11-04\nEnglish." in text
    assert dialog.changelog_view.textCursor().position() == 0


def test_changelog_shows_available_release_notes_first(make_dialog):
    updates = FakeUpdates()
    dialog = make_dialog(updates, changelog=CHANGELOG)

    updates.available_release = replace(
        RELEASE, notes="## What's changed\n\n- <b>Faster</b> checks.", published="2026-11-20"
    )
    updates.changed.emit()

    text = dialog.changelog_view.toPlainText()
    assert text.startswith("12.9.0 — 2026-11-20    Available \nWhat's changed\n")
    # HTML from GitHub is shown as text, never interpreted.
    assert "<b>Faster</b> checks." in text
    assert text.index("12.9.0") < text.index("12.6.0 — 2026-11-05")

    updates.available_release = None
    updates.changed.emit()
    assert dialog.changelog_view.toPlainText().startswith("12.6.0 — 2026-11-05")


def test_changelog_keeps_its_scroll_position_while_a_check_runs(make_dialog):
    updates = FakeUpdates(RELEASE)
    dialog = make_dialog(updates, changelog=CHANGELOG)
    dialog.changelog_view.moveCursor(QTextCursor.MoveOperation.End)

    updates.is_checking = True
    updates.changed.emit()

    assert dialog.changelog_view.textCursor().position() > 0


def test_missing_changelog_says_so(make_dialog):
    dialog = make_dialog()

    assert dialog.changelog_view.toPlainText() == (
        "The changelog isn't included in this copy of the app."
    )


def _has_blue(icon):
    image = icon.pixmap(20, 20).toImage()
    for x in range(image.width()):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if color.alpha() > 128 and color.blue() > color.red() + 80:
                return True
    return False


def test_settings_button_shows_a_blue_download_icon_while_an_update_waits(window):
    button = window.btn_settings
    updates = window.update_notifications
    assert button.icon().cacheKey() == window._settings_icon.cacheKey()
    assert not _has_blue(window._settings_icon)
    assert _has_blue(window._update_icon)

    updates.available_release = RELEASE
    updates.changed.emit()

    assert button.icon().cacheKey() == window._update_icon.cacheKey()
    assert button.accessibleName() == "Settings · Version 12.9.0 is available"
    assert button.toolTip().startswith("Version 12.9.0 is available. Click to update.\n")
    assert "Mouse back button" in button.toolTip()

    updates.available_release = None
    updates.changed.emit()

    assert button.icon().cacheKey() == window._settings_icon.cacheKey()
    assert button.accessibleName() == "Settings"
    assert button.toolTip().startswith("Settings · Translation shortcut: Mouse back button")


def test_update_indicator_survives_a_language_switch(window):
    window.update_notifications.available_release = RELEASE
    window.update_notifications.changed.emit()

    assert window.apply_settings(replace(window.app_settings, ui_language="zh-CN"))

    assert window.btn_settings.icon().cacheKey() == window._update_icon.cacheKey()
    assert window.btn_settings.accessibleName() == "设置 · 新版本 12.9.0 可用"
    assert window.btn_settings.toolTip().startswith("新版本 12.9.0 可用")


def test_download_icon_leads_straight_to_the_about_page(window, monkeypatch):
    pages = []
    monkeypatch.setattr(window, "show_settings", lambda page="": pages.append(page))

    window.btn_settings.click()
    window.update_notifications.available_release = RELEASE
    window.btn_settings.click()

    assert pages == ["", "about"]


def test_tray_about_and_update_check_open_the_about_page(window, monkeypatch):
    events = []
    monkeypatch.setattr(window, "show_settings", lambda page="": events.append(page))
    monkeypatch.setattr(
        window.update_notifications, "check_manually", lambda: events.append("check")
    )

    window.about_action.trigger()
    window.check_updates_action.trigger()
    window.settings_action.trigger()

    assert events == ["about", "check", "about", ""]


def test_tray_update_check_during_a_download_shows_only_progress(window, monkeypatch):
    events = []
    monkeypatch.setattr(window, "show_settings", lambda page="": events.append(page))
    monkeypatch.setattr(
        window.update_notifications,
        "updater",
        SimpleNamespace(
            is_active=True,
            show_progress=lambda: events.append("progress"),
            stop=lambda: None,
        ),
    )

    window.check_updates_action.trigger()

    assert events == ["progress"]
