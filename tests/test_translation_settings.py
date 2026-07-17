from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_paths import AppPaths
from app_settings import (
    AppSettings,
    DEFAULT_SETTINGS,
    keyboard_shortcut,
    load_settings,
    mouse_shortcut,
)


@pytest.fixture(scope="module")
def app_module():
    """Load the GUI entry point without invoking main()."""
    module_path = Path(__file__).resolve().parents[1] / "gemini_translate.pyw"
    module_name = "_snipdo_translate_settings_target"
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
        raise AssertionError("settings tests must not create a real API client")

    monkeypatch.setattr(app_module, "OpenAI", forbidden_openai)
    return app_module


class _Win32Harness:
    def __init__(self):
        self.async_key_calls = []
        self.async_key_results = deque()
        self.register_calls = []
        self.unregister_calls = []
        self.register_results = deque()
        self.unregister_results = deque()

    def GetAsyncKeyState(self, virtual_key):
        self.async_key_calls.append(virtual_key)
        return self.async_key_results.popleft() if self.async_key_results else 0

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, virtual_key):
        self.register_calls.append((hwnd, hotkey_id, modifiers, virtual_key))
        return self.register_results.popleft() if self.register_results else True

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregister_calls.append((hwnd, hotkey_id))
        return (
            self.unregister_results.popleft()
            if self.unregister_results
            else True
        )


@pytest.fixture
def mocked_win32(app, monkeypatch):
    harness = _Win32Harness()
    monkeypatch.setattr(app, "user32", harness)
    return harness


class _ShortcutManagerStub:
    def __init__(self, *results: bool):
        self.calls = []
        self._results = deque(results)

    def configure(self, binding, enabled):
        self.calls.append((binding, enabled))
        return self._results.popleft() if self._results else True


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


def _settings_window_stub(
    settings_path: Path,
    settings: AppSettings,
    manager,
    *,
    credential_store=None,
):
    refreshes = []
    window = SimpleNamespace(
        app_settings=settings,
        shortcut_manager=manager,
        settings_path=settings_path,
        credential_store=credential_store,
        force_quit=False,
        refresh_shortcut_status=lambda: refreshes.append(settings_path),
    )
    return window, refreshes


def test_default_main_settings_button_and_dialog_display_xbutton1(
    app, monkeypatch, qapp, tmp_path: Path
):
    class TrayStub:
        @staticmethod
        def setToolTip(_text):
            pass

    monkeypatch.setattr(
        app.TranslationWindow,
        "setup_tray_icon",
        lambda self: setattr(self, "tray_icon", TrayStub()),
    )
    monkeypatch.setattr(
        app.TranslationWindow,
        "setup_translation_shortcut",
        lambda self: None,
    )

    window = app.TranslationWindow(_app_paths(tmp_path), object())
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
        parent=window,
    )
    try:
        assert window.app_settings == DEFAULT_SETTINGS
        assert window.btn_settings.text() == "XButton1"
        assert dialog.shortcut_button.text() == "XButton1"
        assert dialog.candidate_settings() == DEFAULT_SETTINGS
    finally:
        dialog.deleteLater()
        window.progress_window.close()
        window.deleteLater()
        qapp.processEvents()


def test_shortcut_manager_enable_suspend_resume_disable_lifecycle(
    app, mocked_win32, qapp
):
    binding = mouse_shortcut("xbutton1")
    manager = app.TranslationShortcutManager(binding=binding)

    assert manager.configure(binding, True) is True
    assert manager.enabled is True
    assert manager.is_installed() is True
    assert manager._mouse_timer.isActive() is True
    assert mocked_win32.async_key_calls == [app.VK_XBUTTON1]

    assert manager.suspend() is True
    assert manager.is_installed() is False

    assert manager.suspend() is True
    assert manager.resume() is True
    assert manager.is_installed() is False

    assert manager.resume() is True
    assert manager.is_installed() is True
    assert mocked_win32.async_key_calls == [
        app.VK_XBUTTON1,
        app.VK_XBUTTON1,
    ]

    assert manager.configure(binding, False) is True
    assert manager.enabled is False
    assert manager.is_installed() is False

    assert manager.resume() is True
    assert manager.is_installed() is False


@pytest.mark.parametrize(
    ("button_name", "virtual_key_name"),
    [
        ("xbutton1", "VK_XBUTTON1"),
        ("xbutton2", "VK_XBUTTON2"),
        ("middle", "VK_MBUTTON"),
    ],
)
def test_mouse_polling_emits_once_per_complete_press_release_transition(
    app,
    mocked_win32,
    monkeypatch,
    qapp,
    button_name,
    virtual_key_name,
):
    mocked_win32.async_key_results.extend(
        (
            0,       # initial idle state used to arm polling
            0x8000,  # first press
            0x8001,  # still held; low-order bit must be ignored
            0,       # first release -> one trigger
            0,       # remains released; no duplicate
            0x8000,  # second press
            0x8000,  # still held; no duplicate
            0,       # second release -> one trigger
        )
    )
    trigger_times = iter((10.0, 11.0))
    monkeypatch.setattr(app.time, "monotonic", lambda: next(trigger_times))

    manager = app.TranslationShortcutManager(binding=mouse_shortcut(button_name))
    emitted = []
    manager.triggered.connect(lambda: emitted.append(button_name))
    assert manager.install() is True

    for _ in range(7):
        manager._poll_mouse_shortcut()

    assert emitted == [button_name, button_name]
    expected_virtual_key = getattr(app, virtual_key_name)
    assert mocked_win32.async_key_calls == [expected_virtual_key] * 8
    assert manager.uninstall() is True


def test_mouse_path_contains_no_low_level_hook_or_mouse_event_interception():
    source = (
        Path(__file__).resolve().parents[1] / "gemini_translate.pyw"
    ).read_text(encoding="utf-8")
    forbidden_symbols = (
        "SetWindowsHookEx",
        "CallNextHookEx",
        "UnhookWindowsHookEx",
        "WH_MOUSE_LL",
        "LowLevelMouseProc",
        "WM_MOUSEMOVE",
    )

    assert not [symbol for symbol in forbidden_symbols if symbol in source]
    assert "app.aboutToQuit.connect(window.shutdown_translation_shortcut)" in source
    assert "    def nativeEvent(self, event_type, message):" not in source

    constructor = source.split("class TranslationWindow(QWidget):", 1)[1].split(
        "    def load_app_settings", 1
    )[0]
    assert ".winId()" not in constructor


def test_keyboard_native_filter_never_returns_a_null_result_pointer(app):
    class ManagerStub:
        @staticmethod
        def handle_native_message(_message):
            return False

    event_filter = app._KeyboardHotkeyEventFilter(ManagerStub())

    handled, result = event_filter.nativeEventFilter(b"windows_generic_MSG", 0)

    assert handled is False
    assert result == 0
    assert isinstance(result, int)


def test_polling_started_while_button_is_held_waits_for_release_before_arming(
    app, mocked_win32, monkeypatch, qapp
):
    mocked_win32.async_key_results.extend(
        (
            0x8000,  # held before the manager starts
            0x8000,  # still held: must not trigger
            0,       # release only arms the manager
            0x8000,  # a new press
            0,       # its release triggers once
        )
    )
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.0)
    manager = app.TranslationShortcutManager(
        binding=mouse_shortcut("xbutton1")
    )
    emitted = []
    manager.triggered.connect(lambda: emitted.append("triggered"))

    assert manager.install() is True
    for _ in range(4):
        manager._poll_mouse_shortcut()

    assert emitted == ["triggered"]
    assert manager.uninstall() is True


def test_quit_path_deactivates_shortcut_before_qapplication_quit(app, monkeypatch):
    events = []
    window = SimpleNamespace(
        force_quit=False,
        shortcut_manager=SimpleNamespace(
            uninstall=lambda: events.append("shortcut-stop") or True
        ),
        tray_icon=SimpleNamespace(hide=lambda: events.append("tray-hide")),
        cancel_current_ocr=lambda: events.append("cancel-ocr"),
        cancel_current_translation=lambda: events.append("cancel-translation"),
        hide_translation_progress=lambda: events.append("hide-progress"),
    )
    window.shutdown_translation_shortcut = lambda: (
        app.TranslationWindow.shutdown_translation_shortcut(window)
    )
    monkeypatch.setattr(
        app,
        "QApplication",
        SimpleNamespace(quit=lambda: events.append("qt-quit")),
    )

    app.TranslationWindow.quit_app(window)

    assert window.force_quit is True
    assert events.index("shortcut-stop") < events.index("qt-quit")
    assert events[-2:] == ["tray-hide", "qt-quit"]


def test_shutdown_shortcut_is_idempotent_for_explicit_and_about_to_quit_paths(
    app, mocked_win32, qapp
):
    manager = app.TranslationShortcutManager(
        binding=mouse_shortcut("xbutton1")
    )
    assert manager.install() is True
    window = SimpleNamespace(shortcut_manager=manager)

    app.TranslationWindow.shutdown_translation_shortcut(window)
    app.TranslationWindow.shutdown_translation_shortcut(window)

    assert manager.enabled is False
    assert manager.is_installed() is False
    assert manager._mouse_timer.isActive() is False
    assert mocked_win32.async_key_calls == [app.VK_XBUTTON1]


def test_keyboard_hotkey_uses_win32_flags_and_rolls_back_on_conflict(
    app, mocked_win32, qapp
):
    class HostWindow(app.QObject):
        def winId(self):
            return 0xBEEF

    host = HostWindow()
    previous = keyboard_shortcut(
        0x54,
        ("ctrl", "alt"),
        "Ctrl+Alt+T",
    )
    conflicting = keyboard_shortcut(
        0x59,
        ("ctrl", "shift", "win"),
        "Ctrl+Shift+Win+Y",
    )
    mocked_win32.register_results.extend((True, False, True))
    manager = app.TranslationShortcutManager(host, previous)

    assert manager.configure(previous, True) is True
    assert manager.configure(conflicting, True) is False

    previous_flags = app.MOD_NOREPEAT | app.MOD_CONTROL | app.MOD_ALT
    conflicting_flags = (
        app.MOD_NOREPEAT | app.MOD_CONTROL | app.MOD_SHIFT | app.MOD_WIN
    )
    assert mocked_win32.register_calls == [
        (0xBEEF, app.TRANSLATION_HOTKEY_ID, previous_flags, 0x54),
        (0xBEEF, app.TRANSLATION_HOTKEY_ID, conflicting_flags, 0x59),
        (0xBEEF, app.TRANSLATION_HOTKEY_ID, previous_flags, 0x54),
    ]
    assert mocked_win32.unregister_calls == [
        (0xBEEF, app.TRANSLATION_HOTKEY_ID)
    ]
    assert manager.binding == previous
    assert manager.enabled is True
    assert manager.is_installed() is True
    assert manager._native_filter_installed is True
    assert manager.uninstall() is True
    assert manager._native_filter_installed is False


def test_apply_settings_persists_successful_configuration(
    app, tmp_path: Path
):
    previous = DEFAULT_SETTINGS
    candidate = AppSettings(
        enabled=False,
        shortcut=mouse_shortcut("middle"),
    )
    manager = _ShortcutManagerStub()
    settings_path = tmp_path / "settings.json"
    window, refreshes = _settings_window_stub(
        settings_path,
        previous,
        manager,
    )

    assert app.TranslationWindow.apply_settings(window, candidate) is True

    assert manager.calls == [(candidate.shortcut, candidate.enabled)]
    assert load_settings(settings_path) == candidate
    assert window.app_settings == candidate
    assert refreshes == [settings_path]


def test_apply_settings_rolls_shortcut_back_when_save_fails(
    app, monkeypatch, tmp_path: Path
):
    previous = DEFAULT_SETTINGS
    candidate = AppSettings(
        enabled=False,
        shortcut=mouse_shortcut("xbutton2"),
    )
    manager = _ShortcutManagerStub()
    settings_path = tmp_path / "settings.json"
    window, refreshes = _settings_window_stub(
        settings_path,
        previous,
        manager,
    )
    warnings = []

    def fail_save(_path, _settings):
        raise OSError("synthetic settings write failure")

    monkeypatch.setattr(app, "save_settings_atomic", fail_save)
    monkeypatch.setattr(
        app,
        "QMessageBox",
        SimpleNamespace(warning=lambda *args: warnings.append(args)),
    )

    assert app.TranslationWindow.apply_settings(window, candidate) is False

    assert manager.calls == [
        (candidate.shortcut, candidate.enabled),
        (previous.shortcut, previous.enabled),
    ]
    assert window.app_settings == previous
    assert not settings_path.exists()
    assert refreshes == [settings_path]
    assert warnings and warnings[-1][1] == "设置保存失败"


@pytest.mark.parametrize("stored", [True, False])
def test_apply_settings_replaces_client_only_after_credential_write_succeeds(
    app, monkeypatch, tmp_path: Path, stored: bool
):
    old_client = object()
    candidate_client = object()
    candidate = AppSettings(
        enabled=True,
        shortcut=mouse_shortcut("xbutton2"),
    )
    observed_clients = []
    stored_keys = []
    warnings = []
    events = []

    class CredentialStoreStub:
        def write(self, key):
            stored_keys.append(key)
            observed_clients.append(app.client)
            return stored

    manager = _ShortcutManagerStub()
    window, _refreshes = _settings_window_stub(
        tmp_path / "settings.json",
        DEFAULT_SETTINGS,
        manager,
        credential_store=CredentialStoreStub(),
    )
    monkeypatch.setattr(app, "client", old_client)
    monkeypatch.setattr(
        app,
        "create_api_client",
        lambda key: candidate_client if key == "new-test-key" else None,
    )
    monkeypatch.setattr(app, "save_settings_atomic", lambda _path, _value: None)
    monkeypatch.setattr(app, "record_event", events.append)
    monkeypatch.setattr(
        app,
        "QMessageBox",
        SimpleNamespace(warning=lambda *args: warnings.append(args)),
    )

    result = app.TranslationWindow.apply_settings(
        window,
        candidate,
        "  new-test-key  ",
    )

    assert stored_keys == ["new-test-key"]
    assert observed_clients == [old_client]
    assert window.app_settings == candidate
    if stored:
        assert result is True
        assert app.client is candidate_client
        assert events == [app.AppEvent.CREDENTIAL_AVAILABLE]
        assert warnings == []
    else:
        assert result is False
        assert app.client is old_client
        assert events == [app.AppEvent.CREDENTIAL_UNAVAILABLE]
        assert warnings and warnings[-1][1] == "API Key 保存失败"


def test_disable_while_suspended_prevents_delayed_resume_from_reinstalling(
    app, mocked_win32, qapp, tmp_path: Path
):
    previous = DEFAULT_SETTINGS
    candidate = AppSettings(
        enabled=False,
        shortcut=previous.shortcut,
    )
    manager = app.TranslationShortcutManager(binding=previous.shortcut)
    assert manager.configure(previous.shortcut, True) is True
    assert manager.suspend() is True
    assert mocked_win32.async_key_calls == [app.VK_XBUTTON1]
    assert manager._mouse_timer.isActive() is False

    window, refreshes = _settings_window_stub(
        tmp_path / "settings.json",
        previous,
        manager,
    )
    assert app.TranslationWindow.apply_settings(window, candidate) is True

    # This is the same callback that an already queued 150 ms QTimer invokes.
    app.TranslationWindow.restore_xbutton1_hook(window)

    assert window.app_settings == candidate
    assert manager.enabled is False
    assert manager.is_installed() is False
    assert mocked_win32.async_key_calls == [app.VK_XBUTTON1]
    assert len(refreshes) == 2
