from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from app_settings import DEFAULT_SETTINGS, keyboard_shortcut, load_settings, mouse_shortcut


@pytest.fixture(scope="module")
def app_module():
    path = Path(__file__).resolve().parents[1] / "snipdo_translate.pyw"
    name = "_snipdo_translate_shortcut_removal_target"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def app(app_module, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("shortcut tests must not access native hooks or external services")

    monkeypatch.setattr(app_module, "OpenAI", forbidden)
    monkeypatch.setattr(app_module, "create_api_client", forbidden)
    monkeypatch.setattr(app_module, "WindowsMouseShortcutHook", forbidden)
    monkeypatch.setattr(app_module, "QMessageBox", SimpleNamespace(warning=forbidden))
    monkeypatch.setattr(app_module, "credential_store_for_window", lambda *_args: None)
    monkeypatch.setattr(app_module, "user32", SimpleNamespace(
        RegisterHotKey=forbidden, UnregisterHotKey=forbidden,
    ))
    return app_module


@pytest.fixture
def dialogs(qapp):
    created = []
    yield created
    for dialog in created:
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


def _key_press(widget, key=Qt.Key.Key_Escape, *, repeat=False, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, "", repeat, 1)
    QApplication.sendEvent(widget, event)
    return event


def _capture_dialog(app, dialogs, *, allow_clear):
    dialog = QDialog()
    dialogs.append(dialog)
    button = app.ShortcutCaptureButton(
        DEFAULT_SETTINGS.show_window_shortcut, dialog, allow_clear=allow_clear,
    )
    QVBoxLayout(dialog).addWidget(button)
    rejected = []
    dialog.rejected.connect(lambda: rejected.append(True))
    dialog.show()
    return dialog, button, rejected


def test_escape_clears_optional_binding_and_releases_capture(app, dialogs):
    dialog, button, rejected = _capture_dialog(app, dialogs, allow_clear=True)
    button.begin_capture()

    event = _key_press(button)

    assert event.isAccepted()
    assert button.binding is None
    assert button.text() == "未设置"
    assert not button._capturing
    assert QWidget.keyboardGrabber() is not button
    assert QWidget.mouseGrabber() is not button
    assert dialog.isVisible()
    assert rejected == []


def test_repeated_escape_after_removal_does_not_close_dialog(app, dialogs):
    dialog, button, rejected = _capture_dialog(app, dialogs, allow_clear=True)
    button.begin_capture()
    _key_press(button)

    repeated = _key_press(button, repeat=True)

    assert repeated.isAccepted()
    assert dialog.isVisible()
    assert rejected == []
    assert button.binding is None


def test_fresh_escape_outside_capture_still_closes_dialog(app, dialogs):
    dialog, button, rejected = _capture_dialog(app, dialogs, allow_clear=True)

    _key_press(button)

    assert rejected == [True]
    assert not dialog.isVisible()
    assert button.binding == DEFAULT_SETTINGS.show_window_shortcut


def test_required_translation_escape_cancels_without_clearing(app, dialogs):
    dialog, button, rejected = _capture_dialog(app, dialogs, allow_clear=False)
    original = button.binding
    button.begin_capture()

    event = _key_press(button)

    assert event.isAccepted()
    assert button.binding == original
    assert button.text() == original.display
    assert not button._capturing
    assert dialog.isVisible()
    assert rejected == []
    with pytest.raises((ValueError, TypeError)):
        button.set_binding(None)
    assert button.binding == original


def test_cleared_shortcut_can_capture_a_new_keyboard_binding(app, dialogs):
    _, button, _ = _capture_dialog(app, dialogs, allow_clear=True)
    button.set_binding(None)
    button.begin_capture()

    _key_press(button, Qt.Key.Key_F8)

    assert button.binding == keyboard_shortcut(0x77, (), "F8")
    assert button.text() == "F8"
    assert not button._capturing


def test_settings_dialog_exposes_optional_show_shortcut_and_retains_translation(app, dialogs):
    settings = replace(DEFAULT_SETTINGS, show_window_shortcut=None)
    dialog = app.SettingsDialog(settings, api_key_configured=False, environment_key_active=False)
    dialogs.append(dialog)

    assert dialog.show_window_shortcut_button.text() == "未设置"
    assert "Esc" in dialog.show_window_shortcut_button.toolTip()
    assert "移除" in dialog.show_window_shortcut_button.toolTip()
    assert dialog.candidate_settings().show_window_shortcut is None
    assert dialog.candidate_settings().shortcut == DEFAULT_SETTINGS.shortcut
    with pytest.raises((ValueError, TypeError)):
        dialog.shortcut_button.set_binding(None)


class _MouseBackend:
    def __init__(self, on_trigger):
        self.on_trigger = on_trigger
        self.running = False

    def start(self):
        self.running = True
        return True

    def stop(self, timeout=2.0):
        self.running = False
        return True

    def is_running(self):
        return self.running


@pytest.fixture
def manager_env(app, monkeypatch, qapp):
    registrations = []
    unregistrations = []
    hooks = []
    host = QWidget()
    managers = []

    def register(*args):
        registrations.append(args)
        return True

    def unregister(*args):
        unregistrations.append(args)
        return True

    def mouse_factory(_button, on_trigger):
        backend = _MouseBackend(on_trigger)
        hooks.append(backend)
        return backend

    monkeypatch.setattr(app, "user32", SimpleNamespace(RegisterHotKey=register, UnregisterHotKey=unregister))
    monkeypatch.setattr(app, "WindowsMouseShortcutHook", mouse_factory)
    monkeypatch.setattr(app.TranslationShortcutManager, "_install_native_event_filter", lambda self: True)
    monkeypatch.setattr(app.TranslationShortcutManager, "_remove_native_event_filter", lambda self: None)

    def create(binding):
        manager = app.TranslationShortcutManager(host, binding, hotkey_id=app.SHOW_WINDOW_HOTKEY_ID)
        managers.append(manager)
        return manager

    yield SimpleNamespace(create=create, registrations=registrations, unregistrations=unregistrations, hooks=hooks)
    for manager in managers:
        manager.uninstall()
    host.close()
    host.deleteLater()
    qapp.processEvents()


def test_explicitly_removed_manager_never_installs_default_shortcut(manager_env):
    manager = manager_env.create(None)

    assert manager.binding is None
    assert manager.install()
    assert manager.suspend()
    assert manager.resume()
    assert manager.configure(None, True)
    assert manager.binding is None
    assert not manager.enabled
    assert not manager.is_installed()
    assert manager_env.registrations == []
    assert manager_env.hooks == []


@pytest.mark.parametrize("binding", [DEFAULT_SETTINGS.show_window_shortcut, mouse_shortcut("xbutton2")])
def test_removal_while_suspended_does_not_reregister_and_can_be_readded(manager_env, binding):
    manager = manager_env.create(binding)
    assert manager.configure(binding, True)
    assert manager.is_installed()
    assert manager.suspend()
    assert manager.configure(None, True)
    register_count = len(manager_env.registrations)
    hook_count = len(manager_env.hooks)

    assert manager.resume()
    assert manager.install()

    assert manager.binding is None
    assert not manager.enabled
    assert not manager.is_installed()
    assert len(manager_env.registrations) == register_count
    assert len(manager_env.hooks) == hook_count
    assert manager.configure(binding, True)
    assert manager.binding == binding
    assert manager.is_installed()


def test_queued_mouse_event_is_ignored_after_removal(manager_env, qapp):
    binding = mouse_shortcut("xbutton2")
    manager = manager_env.create(binding)
    triggered = []
    manager.triggered.connect(lambda: triggered.append(True))
    assert manager.configure(binding, True)
    old_hook = manager_env.hooks[-1]
    old_hook.on_trigger()

    assert manager.configure(None, False)
    qapp.processEvents()

    assert triggered == []
    assert not manager.is_installed()


def test_queued_keyboard_event_is_ignored_after_removal(app, manager_env, qapp):
    binding = DEFAULT_SETTINGS.show_window_shortcut
    manager = manager_env.create(binding)
    triggered = []
    manager.triggered.connect(lambda: triggered.append(True))
    assert manager.configure(binding, True)
    message = app.wintypes.MSG()
    message.message = app.WM_HOTKEY
    message.wParam = app.SHOW_WINDOW_HOTKEY_ID
    assert manager.handle_native_message(ctypes.addressof(message))

    assert manager.configure(None, False)
    qapp.processEvents()

    assert triggered == []
    assert not manager.handle_native_message(ctypes.addressof(message))


class _ManagerStub:
    def __init__(self):
        self.calls = []
        self.triggered = SimpleNamespace(connect=lambda _callback: None)

    def configure(self, binding, enabled):
        self.calls.append((binding, enabled))
        return True


def _settings_window(path, settings):
    return SimpleNamespace(
        settings_path=path,
        app_settings=settings,
        shortcut_manager=_ManagerStub(),
        show_window_shortcut_manager=_ManagerStub(),
        force_quit=False,
        refresh_shortcut_status=lambda: None,
    )


def test_remove_save_reload_and_reassign_shortcut(app, dialogs, tmp_path):
    dialog = app.SettingsDialog(DEFAULT_SETTINGS, api_key_configured=False, environment_key_active=False)
    dialogs.append(dialog)
    dialog.show_window_shortcut_button.begin_capture()
    _key_press(dialog.show_window_shortcut_button)
    candidate = dialog.candidate_settings()
    window = _settings_window(tmp_path / "settings.json", DEFAULT_SETTINGS)

    assert app.TranslationWindow.apply_settings(window, candidate)

    assert window.show_window_shortcut_manager.calls == [(None, False)]
    persisted = load_settings(window.settings_path)
    assert persisted == candidate
    assert persisted.show_window_shortcut is None
    reopened = app.SettingsDialog(persisted, api_key_configured=False, environment_key_active=False)
    dialogs.append(reopened)
    assert reopened.show_window_shortcut_button.text() == "未设置"
    reopened.show_window_shortcut_button.set_binding(DEFAULT_SETTINGS.show_window_shortcut)

    assert app.TranslationWindow.apply_settings(window, reopened.candidate_settings())

    assert load_settings(window.settings_path).show_window_shortcut == DEFAULT_SETTINGS.show_window_shortcut
    assert window.show_window_shortcut_manager.calls[-1] == (DEFAULT_SETTINGS.show_window_shortcut, True)


@pytest.mark.parametrize("previous_binding", [None, DEFAULT_SETTINGS.show_window_shortcut])
def test_save_failure_restores_previous_optional_shortcut_state(app, monkeypatch, tmp_path, previous_binding):
    previous = replace(DEFAULT_SETTINGS, show_window_shortcut=previous_binding)
    replacement = DEFAULT_SETTINGS.show_window_shortcut if previous_binding is None else None
    candidate = replace(previous, show_window_shortcut=replacement)
    window = _settings_window(tmp_path / "settings.json", previous)
    warnings = []

    def fail_save(*_args):
        raise OSError("synthetic save failure")

    monkeypatch.setattr(app, "save_settings_atomic", fail_save)
    monkeypatch.setattr(app, "QMessageBox", SimpleNamespace(warning=lambda *args: warnings.append(args)))

    assert not app.TranslationWindow.apply_settings(window, candidate)

    assert window.app_settings == previous
    assert window.show_window_shortcut_manager.calls == [
        (replacement, replacement is not None),
        (previous_binding, previous_binding is not None),
    ]
    assert warnings and warnings[-1][1] == "设置保存失败"


def test_unset_shortcut_status_and_tray_menu_are_readable(app):
    labels = []
    tooltips = []
    window = SimpleNamespace(
        app_settings=replace(DEFAULT_SETTINGS, show_window_shortcut=None),
        shortcut_manager=SimpleNamespace(is_installed=lambda: True),
        show_window_shortcut_manager=SimpleNamespace(is_installed=lambda: False),
        enabled_app_icon=object(),
        disabled_app_icon=object(),
        refresh_notification_visuals=lambda: None,
        btn_settings=SimpleNamespace(setToolTip=tooltips.append),
        show_window_action=SimpleNamespace(setText=labels.append),
    )

    app.TranslationWindow.refresh_shortcut_status(window)

    assert "显示窗口 未设置" in window._base_tray_tooltip
    assert labels == ["显示主窗口"]
    assert tooltips and "未设置" in tooltips[-1]


def test_setup_with_removed_shortcut_disables_manager_without_warning(app, monkeypatch):
    managers = []
    warnings = []

    def manager_factory(_window, binding, **_kwargs):
        manager = _ManagerStub()
        manager.binding = binding
        managers.append(manager)
        return manager

    monkeypatch.setattr(app, "TranslationShortcutManager", manager_factory)
    window = SimpleNamespace(
        app_settings=replace(DEFAULT_SETTINGS, show_window_shortcut=None),
        tray_icon=SimpleNamespace(showMessage=lambda *args: warnings.append(args)),
        on_translation_shortcut_triggered=lambda: None,
        on_show_window_shortcut_triggered=lambda: None,
        refresh_shortcut_status=lambda: None,
    )

    app.TranslationWindow.setup_translation_shortcut(window)

    assert len(managers) == 2
    assert managers[1].binding is None
    assert managers[1].calls == [(None, False)]
    assert warnings == []
