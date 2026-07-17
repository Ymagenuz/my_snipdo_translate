from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_paths import AppPaths
from api_providers import (
    DEFAULT_API_PROVIDER,
    DEEPSEEK_API_PROVIDER,
    get_api_provider,
)
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


class _MouseHookBackend:
    def __init__(self, harness, button, on_trigger):
        self.harness = harness
        self.button = button
        self.on_trigger = on_trigger
        self.running = False
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1
        result = (
            self.harness.start_results.popleft()
            if self.harness.start_results
            else True
        )
        self.running = bool(result)
        return self.running

    def stop(self, timeout=2.0):
        self.stop_calls += 1
        self.harness.stop_timeouts.append(timeout)
        result = (
            self.harness.stop_results.popleft()
            if self.harness.stop_results
            else True
        )
        if result:
            self.running = False
        return bool(result)

    def is_running(self):
        return self.running

    def fire(self):
        self.on_trigger()


class _MouseHookHarness:
    def __init__(self):
        self.instances = []
        self.start_results = deque()
        self.stop_results = deque()
        self.stop_timeouts = []

    def factory(self, button, on_trigger):
        backend = _MouseHookBackend(self, button, on_trigger)
        self.instances.append(backend)
        return backend


@pytest.fixture
def mocked_win32(app, monkeypatch):
    harness = _Win32Harness()
    monkeypatch.setattr(app, "user32", harness)
    return harness


@pytest.fixture
def mocked_mouse_hook(app, monkeypatch):
    harness = _MouseHookHarness()
    monkeypatch.setattr(app, "WindowsMouseShortcutHook", harness.factory)
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


def test_settings_dialog_lists_api_providers_and_returns_stable_id(
    app, qapp
):
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
    )
    try:
        provider_ids = [
            dialog.api_provider_combo.itemData(index)
            for index in range(dialog.api_provider_combo.count())
        ]
        assert provider_ids == [
            DEFAULT_API_PROVIDER,
            DEEPSEEK_API_PROVIDER,
        ]
        assert dialog.selected_api_provider() == DEFAULT_API_PROVIDER

        deepseek_index = dialog.api_provider_combo.findData(
            DEEPSEEK_API_PROVIDER
        )
        dialog.api_provider_combo.setCurrentIndex(deepseek_index)

        assert (
            dialog.candidate_settings().api_provider
            == DEEPSEEK_API_PROVIDER
        )
        assert "不支持图片 OCR" in dialog.status_label.text()
        assert "DEEPSEEK_API_KEY" in dialog.status_label.text()
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_create_api_client_uses_selected_provider_endpoint(
    app, monkeypatch
):
    calls = []
    expected_client = object()

    def fake_openai(**kwargs):
        calls.append(kwargs)
        return expected_client

    monkeypatch.setattr(app, "OpenAI", fake_openai)

    result = app.create_api_client("deepseek-test-key", DEEPSEEK_API_PROVIDER)

    assert result is expected_client
    assert calls == [
        {
            "api_key": "deepseek-test-key",
            "base_url": "https://api.deepseek.com",
            "timeout": app.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 1,
        }
    ]
    provider = get_api_provider(DEEPSEEK_API_PROVIDER)
    assert app.chat_completion_options(provider) == {
        "model": "deepseek-v4-flash",
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_initialize_deepseek_credentials_uses_separate_sources(
    app,
    monkeypatch,
    tmp_path: Path,
):
    store = object()
    resolutions = []
    configurations = []
    events = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-env-key")
    monkeypatch.setattr(
        app,
        "create_credential_store",
        lambda provider: (
            store
            if provider == DEEPSEEK_API_PROVIDER
            else pytest.fail("unexpected provider")
        ),
    )

    def fake_resolve(environment_key, selected_store, legacy_dirs):
        resolutions.append((environment_key, selected_store, legacy_dirs))
        return SimpleNamespace(
            key="deepseek-env-key",
            source="environment",
            persisted=False,
            migrated=False,
        )

    monkeypatch.setattr(app, "resolve_api_key", fake_resolve)
    monkeypatch.setattr(
        app,
        "configure_api_client",
        lambda key, provider: configurations.append((key, provider)) or True,
    )
    monkeypatch.setattr(app, "record_event", events.append)

    result = app.initialize_credentials(
        _app_paths(tmp_path),
        DEEPSEEK_API_PROVIDER,
    )

    assert result is store
    assert resolutions == [("deepseek-env-key", store, ())]
    assert configurations == [
        ("deepseek-env-key", DEEPSEEK_API_PROVIDER)
    ]
    assert events == [app.AppEvent.CREDENTIAL_AVAILABLE]
    assert get_api_provider(DEEPSEEK_API_PROVIDER).credential_target == (
        "SnipDoTranslate/DeepSeek"
    )


def test_translation_thread_keeps_creation_time_api_runtime(
    app,
    qapp,
):
    old_calls = []
    new_calls = []

    class CompletionsStub:
        def __init__(self, calls):
            self.calls = calls

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return []

    old_client = SimpleNamespace(
        chat=SimpleNamespace(completions=CompletionsStub(old_calls))
    )
    new_client = SimpleNamespace(
        chat=SimpleNamespace(completions=CompletionsStub(new_calls))
    )
    app.activate_api_runtime(DEFAULT_API_PROVIDER, old_client)
    thread = app.TranslationThread("runtime snapshot")
    app.activate_api_runtime(DEEPSEEK_API_PROVIDER, new_client)

    thread.run()

    assert len(old_calls) == 1
    assert old_calls[0]["model"] == "gpt-5.4-nano"
    assert "extra_body" not in old_calls[0]
    assert new_calls == []


def test_cancelled_translation_thread_never_calls_api(app, qapp):
    calls = []

    class CompletionsStub:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return []

    api_client = SimpleNamespace(
        chat=SimpleNamespace(completions=CompletionsStub())
    )
    app.activate_api_runtime(DEFAULT_API_PROVIDER, api_client)
    thread = app.TranslationThread("cancel before run")
    thread.request_stop()

    thread.run()

    assert calls == []


def test_tray_menu_hover_style_has_explicit_foreground_and_background(
    app, monkeypatch, qapp, tmp_path: Path
):
    monkeypatch.setattr(
        app.TranslationWindow,
        "setup_translation_shortcut",
        lambda self: None,
    )
    window = app.TranslationWindow(_app_paths(tmp_path), object())
    try:
        compact_style = "".join(window.tray_menu.styleSheet().split()).lower()
        assert window.tray_icon.contextMenu() is window.tray_menu
        assert window.tray_menu.parent() is window
        assert "qmenu::item:selected{" in compact_style
        selected_style = compact_style.split(
            "qmenu::item:selected{", 1
        )[1].split("}", 1)[0]
        assert "background-color:#ede7f6;" in selected_style
        assert "color:#303133;" in selected_style
    finally:
        window.tray_icon.hide()
        window.progress_window.close()
        window.deleteLater()
        qapp.processEvents()


def test_shortcut_selection_is_forwarded_directly_to_translation(
    app, monkeypatch, qapp
):
    requests = []
    scheduled = []
    monkeypatch.setattr(
        app,
        "QTimer",
        SimpleNamespace(
            singleShot=lambda delay, callback: scheduled.append((delay, callback))
        ),
    )
    window = SimpleNamespace(
        app_settings=DEFAULT_SETTINGS,
        selection_capture_busy=True,
        capture_current_selection_text=lambda: "selected text",
        handle_new_request=lambda text: requests.append(text) or True,
        restore_xbutton1_hook=lambda: None,
    )

    app.TranslationWindow.translate_current_selection(window)

    assert requests == ["selected text"]
    assert window.selection_capture_busy is False
    assert [delay for delay, _callback in scheduled] == [150]


def test_empty_shortcut_selection_does_not_open_main_window(
    app, monkeypatch, qapp
):
    messages = []
    scheduled = []
    monkeypatch.setattr(
        app,
        "QTimer",
        SimpleNamespace(
            singleShot=lambda delay, callback: scheduled.append((delay, callback))
        ),
    )
    window = SimpleNamespace(
        app_settings=DEFAULT_SETTINGS,
        selection_capture_busy=True,
        capture_current_selection_text=lambda: "",
        handle_new_request=lambda _text: pytest.fail(
            "empty selection must not start translation"
        ),
        tray_icon=SimpleNamespace(
            showMessage=lambda *args: messages.append(args)
        ),
        restore_xbutton1_hook=lambda: None,
    )

    app.TranslationWindow.translate_current_selection(window)

    assert len(messages) == 1
    assert "未检测到选中文字" in messages[0][1]
    assert window.selection_capture_busy is False
    assert [delay for delay, _callback in scheduled] == [150]


def test_shortcut_manager_enable_suspend_resume_disable_lifecycle(
    app, mocked_mouse_hook, qapp
):
    binding = mouse_shortcut("xbutton1")
    manager = app.TranslationShortcutManager(binding=binding)

    assert manager.configure(binding, True) is True
    assert manager.enabled is True
    assert manager.is_installed() is True
    assert [hook.button for hook in mocked_mouse_hook.instances] == ["xbutton1"]

    assert manager.suspend() is True
    assert manager.is_installed() is False
    assert mocked_mouse_hook.instances[0].stop_calls == 1

    assert manager.suspend() is True
    assert manager.resume() is True
    assert manager.is_installed() is False

    assert manager.resume() is True
    assert manager.is_installed() is True
    assert [hook.button for hook in mocked_mouse_hook.instances] == [
        "xbutton1",
        "xbutton1",
    ]

    assert manager.configure(binding, False) is True
    assert manager.enabled is False
    assert manager.is_installed() is False

    assert manager.resume() is True
    assert manager.is_installed() is False


@pytest.mark.parametrize(
    "button_name",
    ["xbutton1", "xbutton2", "middle"],
)
def test_mouse_hook_notifications_are_queued_and_debounced(
    app,
    mocked_mouse_hook,
    monkeypatch,
    qapp,
    button_name,
):
    trigger_times = iter((10.0, 10.1, 11.0))
    monkeypatch.setattr(app.time, "monotonic", lambda: next(trigger_times))

    manager = app.TranslationShortcutManager(binding=mouse_shortcut(button_name))
    emitted = []
    manager.triggered.connect(lambda: emitted.append(button_name))
    assert manager.install() is True

    backend = mocked_mouse_hook.instances[-1]
    backend.fire()
    backend.fire()
    backend.fire()
    qapp.processEvents()

    assert emitted == [button_name, button_name]
    assert manager.uninstall() is True


def test_queued_mouse_notification_from_old_binding_is_ignored(
    app, mocked_mouse_hook, qapp
):
    manager = app.TranslationShortcutManager(
        binding=mouse_shortcut("xbutton1")
    )
    emitted = []
    manager.triggered.connect(
        lambda: emitted.append(manager.binding.mouse_button)
    )
    assert manager.install() is True

    old_backend = mocked_mouse_hook.instances[-1]
    old_backend.fire()
    assert manager.configure(mouse_shortcut("xbutton2"), True) is True
    qapp.processEvents()
    assert emitted == []

    new_backend = mocked_mouse_hook.instances[-1]
    new_backend.fire()
    qapp.processEvents()
    assert emitted == ["xbutton2"]
    assert manager.uninstall() is True


def test_mouse_hook_isolated_from_gui_thread_and_callback_work():
    source = (
        Path(__file__).resolve().parents[1] / "gemini_translate.pyw"
    ).read_text(encoding="utf-8")
    hook_source = (
        Path(__file__).resolve().parents[1] / "windows_mouse_hook.py"
    ).read_text(encoding="utf-8")

    assert "SetWindowsHookEx" not in source
    assert "GetMessageW" not in source
    assert 'name="SnipDoTranslateMouseHook"' in hook_source
    assert "SetWindowsHookExW" in hook_source
    assert "GetMessageW" in hook_source
    hook_callback = hook_source.split("    def _hook_proc", 1)[1].split(
        "    def _call_next", 1
    )[0]
    assert "return 1" in hook_callback
    assert not any(
        forbidden in hook_callback
        for forbidden in ("clipboard", "sleep(", "join(", "translation")
    )
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


def test_mouse_hook_install_failure_does_not_report_active(
    app, mocked_mouse_hook, qapp
):
    mocked_mouse_hook.start_results.append(False)
    manager = app.TranslationShortcutManager(
        binding=mouse_shortcut("xbutton1")
    )

    assert manager.install() is False
    assert manager.is_installed() is False
    assert mocked_mouse_hook.instances[0].stop_calls == 1
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
    app, mocked_mouse_hook, qapp
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
    assert mocked_mouse_hook.instances[0].stop_calls == 1


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


@pytest.mark.parametrize(
    ("stored_key", "expects_client"),
    [("deepseek-key", True), ("", False)],
)
def test_switching_provider_uses_only_the_target_provider_credential(
    app,
    monkeypatch,
    tmp_path: Path,
    stored_key: str,
    expects_client: bool,
):
    old_client = object()
    deepseek_client = object()
    requested_clients = []
    events = []

    class DeepSeekStoreStub:
        def read(self):
            return stored_key

    target_store = DeepSeekStoreStub()
    candidate = AppSettings(
        shortcut=mouse_shortcut("xbutton1"),
        api_provider=DEEPSEEK_API_PROVIDER,
    )
    manager = _ShortcutManagerStub()
    window, _refreshes = _settings_window_stub(
        tmp_path / "settings.json",
        DEFAULT_SETTINGS,
        manager,
        credential_store=object(),
    )
    app.activate_api_runtime(DEFAULT_API_PROVIDER, old_client)
    monkeypatch.setattr(
        app,
        "create_credential_store",
        lambda provider: (
            target_store
            if provider == DEEPSEEK_API_PROVIDER
            else pytest.fail("unexpected provider")
        ),
    )

    def fake_create_client(key, provider):
        requested_clients.append((key, provider))
        return deepseek_client

    monkeypatch.setattr(app, "create_api_client", fake_create_client)
    monkeypatch.setattr(app, "record_event", events.append)

    result = app.TranslationWindow.apply_settings(window, candidate)

    assert result is True
    assert window.app_settings == candidate
    assert window.credential_store is target_store
    assert app.api_runtime.provider.provider_id == DEEPSEEK_API_PROVIDER
    assert app.client is (deepseek_client if expects_client else None)
    assert requested_clients == (
        [("deepseek-key", DEEPSEEK_API_PROVIDER)]
        if expects_client
        else []
    )
    assert events == [
        app.AppEvent.CREDENTIAL_AVAILABLE
        if expects_client
        else app.AppEvent.CREDENTIAL_MISSING
    ]


def test_provider_switch_is_deferred_while_cancelled_thread_is_still_running(
    app,
    monkeypatch,
    tmp_path: Path,
):
    class RunningThreadStub:
        @staticmethod
        def isRunning():
            return True

    class StoreStub:
        @staticmethod
        def read():
            return ""

    manager = _ShortcutManagerStub()
    window, refreshes = _settings_window_stub(
        tmp_path / "settings.json",
        DEFAULT_SETTINGS,
        manager,
        credential_store=object(),
    )
    window.ocr_thread = RunningThreadStub()
    window.align_thread = None
    window.trans_thread = None
    window.cancel_current_ocr = lambda: None
    warnings = []
    monkeypatch.setattr(app, "create_credential_store", lambda _provider: StoreStub())
    monkeypatch.setattr(
        app,
        "QMessageBox",
        SimpleNamespace(warning=lambda *args: warnings.append(args)),
    )
    candidate = AppSettings(api_provider=DEEPSEEK_API_PROVIDER)

    result = app.TranslationWindow.apply_settings(window, candidate)

    assert result is False
    assert window.app_settings == DEFAULT_SETTINGS
    assert manager.calls == []
    assert refreshes == []
    assert not (tmp_path / "settings.json").exists()
    assert warnings and warnings[-1][1] == "接口暂未切换"


def test_mismatched_credential_target_is_not_reused(app, monkeypatch):
    wrong_store = SimpleNamespace(target_name="SnipDoTranslate/GPTSAPI")
    correct_store = SimpleNamespace(target_name="SnipDoTranslate/DeepSeek")
    window = SimpleNamespace(
        app_settings=AppSettings(api_provider=DEEPSEEK_API_PROVIDER),
        credential_store=wrong_store,
    )
    monkeypatch.setattr(
        app,
        "create_credential_store",
        lambda provider: (
            correct_store
            if provider == DEEPSEEK_API_PROVIDER
            else pytest.fail("unexpected provider")
        ),
    )

    selected = app.credential_store_for_window(
        window,
        DEEPSEEK_API_PROVIDER,
    )

    assert selected is correct_store


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
        target_name = "SnipDoTranslate/GPTSAPI"

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
        lambda key, provider=DEFAULT_API_PROVIDER: (
            candidate_client
            if key == "new-test-key" and provider == DEFAULT_API_PROVIDER
            else None
        ),
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
    app, mocked_mouse_hook, qapp, tmp_path: Path
):
    previous = DEFAULT_SETTINGS
    candidate = AppSettings(
        enabled=False,
        shortcut=previous.shortcut,
    )
    manager = app.TranslationShortcutManager(binding=previous.shortcut)
    assert manager.configure(previous.shortcut, True) is True
    assert manager.suspend() is True
    assert manager.is_installed() is False

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
    assert len(mocked_mouse_hook.instances) == 1
    assert len(refreshes) == 2
