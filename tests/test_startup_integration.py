from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_settings import DEFAULT_SETTINGS


@pytest.fixture(scope="module")
def app_module():
    """Load the entry point without starting hooks, IPC, or the application."""
    module_path = Path(__file__).resolve().parents[1] / "snipdo_translate.pyw"
    module_name = "_snipdo_translate_startup_integration_target"
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
    def forbidden(*_args, **_kwargs):
        raise AssertionError("real registry, credentials, network, and IPC are forbidden")

    for name in (
        "WindowsStartupRegistration",
        "WindowsCredentialStore",
        "OpenAI",
        "resolve_api_key",
        "configure_api_client",
        "initialize_credentials",
        "send_request",
    ):
        monkeypatch.setattr(app_module, name, forbidden)
    monkeypatch.setattr(app_module, "client", None)
    monkeypatch.setattr(app_module, "record_event", lambda _event: None)
    return app_module


@pytest.mark.parametrize("enabled", [False, True])
def test_startup_checkbox_reflects_registration_and_returns_changed_choice(app, qapp, enabled):
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
        startup_enabled=enabled,
    )
    try:
        assert dialog.chk_startup.isEnabled()
        assert dialog.chk_startup.isChecked() is enabled
        assert dialog.launch_at_startup() is enabled
        dialog.chk_startup.setChecked(not enabled)
        assert dialog.launch_at_startup() is not enabled
    finally:
        dialog.reject()


def test_startup_checkbox_defaults_to_opt_out(app, qapp):
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
    )
    try:
        assert not dialog.chk_startup.isChecked()
        assert dialog.launch_at_startup() is False
    finally:
        dialog.reject()


def test_unavailable_registration_disables_startup_choice(app, qapp):
    dialog = app.SettingsDialog(
        DEFAULT_SETTINGS,
        api_key_configured=False,
        environment_key_active=False,
        startup_enabled=True,
        startup_available=False,
    )
    try:
        assert not dialog.chk_startup.isEnabled()
        assert dialog.launch_at_startup() is None
    finally:
        dialog.reject()


class _SettingsHarness:
    def __init__(self, app, monkeypatch):
        self.events = []
        self.dialog_options = []
        self.registry_writes = []
        self.warnings = []
        self.registered = False
        self.requested = True
        self.accepted = True
        self.apply_success = True
        self.read_error = False
        self.write_error = False

        def read_registration():
            self.events.append("registry-read")
            if self.read_error:
                raise app.StartupRegistrationError("registry read denied")
            return self.registered

        def write_registration(enabled):
            self.events.append("registry-write")
            self.registry_writes.append(enabled)
            if self.write_error:
                raise app.StartupRegistrationError("registry write denied")

        registration = SimpleNamespace(
            is_enabled=read_registration,
            set_enabled=write_registration,
        )

        def registration_factory(module_file):
            assert Path(module_file) == Path(app.__file__)
            return registration

        def dialog_factory(settings, **options):
            assert settings is DEFAULT_SETTINGS
            self.dialog_options.append(options)
            return SimpleNamespace(
                exec=lambda: (
                    app.QDialog.DialogCode.Accepted
                    if self.accepted
                    else app.QDialog.DialogCode.Rejected
                ),
                candidate_settings=lambda: DEFAULT_SETTINGS,
                api_key=lambda: "",
                launch_at_startup=lambda: (
                    self.requested if options["startup_available"] else None
                ),
            )

        def apply_settings(settings, api_key):
            assert settings is DEFAULT_SETTINGS
            assert api_key == ""
            self.events.append("apply-settings")
            return self.apply_success

        def manager(name):
            return SimpleNamespace(
                suspend=lambda: self.events.append(f"{name}-suspend") or True,
                resume=lambda: self.events.append(f"{name}-resume") or True,
            )

        self.window = SimpleNamespace(
            app_settings=DEFAULT_SETTINGS,
            force_show_window=lambda: self.events.append("show-window"),
            shortcut_manager=manager("translate"),
            show_window_shortcut_manager=manager("show"),
            refresh_shortcut_status=lambda: self.events.append("refresh-shortcuts"),
            apply_settings=apply_settings,
        )
        monkeypatch.setattr(app, "WindowsStartupRegistration", registration_factory)
        monkeypatch.setattr(app, "SettingsDialog", dialog_factory)
        monkeypatch.setattr(
            app, "QMessageBox",
            SimpleNamespace(warning=lambda *args: self.warnings.append(args)),
        )
        self.app = app

    def open_settings(self):
        self.app.TranslationWindow.show_settings(self.window)


@pytest.fixture
def settings(app, monkeypatch):
    return _SettingsHarness(app, monkeypatch)


def test_settings_cancel_does_not_write_registry_or_other_settings(settings):
    settings.registered = True
    settings.accepted = False
    settings.open_settings()

    assert settings.dialog_options[0]["startup_enabled"] is True
    assert settings.dialog_options[0]["startup_available"] is True
    assert settings.registry_writes == []
    assert "apply-settings" not in settings.events
    assert settings.events.count("translate-resume") == 1
    assert settings.events.count("show-resume") == 1


def test_registry_read_error_keeps_other_settings_usable(settings):
    settings.read_error = True
    settings.open_settings()

    assert settings.dialog_options[0]["startup_available"] is False
    assert "apply-settings" in settings.events
    assert settings.registry_writes == []


@pytest.mark.parametrize("enabled", [False, True])
def test_settings_save_applies_startup_choice_after_other_settings(settings, enabled):
    settings.registered = not enabled
    settings.requested = enabled
    settings.open_settings()

    assert settings.registry_writes == [enabled]
    assert settings.events.index("apply-settings") < settings.events.index("registry-write")
    assert settings.warnings == []


def test_failed_settings_save_does_not_change_startup_registration(settings):
    settings.apply_success = False
    settings.open_settings()

    assert "apply-settings" in settings.events
    assert settings.registry_writes == []


def test_registry_write_error_reports_partial_save_without_reapplying_settings(settings):
    settings.write_error = True
    settings.open_settings()

    assert settings.registry_writes == [True]
    assert settings.events.count("apply-settings") == 1
    assert len(settings.warnings) == 1
    assert settings.warnings[0][1] == "开机启动设置失败"
    assert "其他设置" in settings.warnings[0][2]
    assert "保存" in settings.warnings[0][2]


def test_each_settings_open_refreshes_the_registry_state(settings):
    settings.accepted = False
    settings.open_settings()
    settings.registered = True
    settings.open_settings()

    assert [options["startup_enabled"] for options in settings.dialog_options] == [False, True]
    assert settings.events.count("registry-read") == 2
    assert settings.registry_writes == []


class _MainHarness:
    def __init__(self, app, monkeypatch, tmp_path):
        self.primary = True
        self.events = []
        self.requests = []
        self.forwarded = []
        self.windows = []
        self.about_to_quit = []
        self.callbacks = []

        def event_loop():
            self.events.append("event-loop")
            for callback in self.callbacks:
                callback()
            return 0

        def forbidden(*_args, **_kwargs):
            raise AssertionError("startup must not show UI, prompt, or access the real host")

        application = SimpleNamespace(
            setQuitOnLastWindowClosed=lambda value: self.events.append(("quit-on-close", value)),
            setApplicationVersion=lambda value: self.events.append(("version", value)),
            aboutToQuit=SimpleNamespace(connect=self.about_to_quit.append),
            exec=event_loop,
        )
        self.bridge = SimpleNamespace(
            receive=forbidden,
            bind=lambda window: self.events.append(("bind", window)),
            fail=lambda reason: self.events.append(("bridge-fail", reason)),
        )
        self.server = SimpleNamespace(
            failed=False,
            running=True,
            start=lambda: self.primary,
            stop=lambda: self.events.append("server-stop"),
        )

        def create_window(*_args, **_kwargs):
            def handle_request(request, *, allow_key_prompt):
                self.requests.append((request, allow_key_prompt))
                return app.ReceiveResult.accept()

            window = SimpleNamespace(
                handle_app_request=handle_request,
                show=forbidden,
                show_manual_window=forbidden,
                force_show_window=forbidden,
                ensure_api_key=forbidden,
                shutdown_translation_shortcut=lambda: self.events.append("hooks-stop"),
                update_notifications=SimpleNamespace(
                    start=lambda: self.events.append("notifications-start"),
                    stop=lambda: self.events.append("notifications-stop"),
                ),
            )
            self.windows.append(window)
            return window

        def send_request(_app_id, request, *, timeout):
            self.forwarded.append(request)
            return SimpleNamespace(accepted=True)

        def initialize_credentials(*_args, **_kwargs):
            self.events.append("credentials-initialized")
            return object()

        monkeypatch.setattr(app, "resolve_app_paths", lambda _module: SimpleNamespace(data_dir=tmp_path))
        monkeypatch.setattr(app, "configure_runtime", lambda _paths: None)
        monkeypatch.setattr(app, "close_runtime_log", lambda: self.events.append("log-close"))
        monkeypatch.setattr(app, "initialize_history", lambda _paths: None)
        monkeypatch.setattr(app, "load_settings", lambda _path: DEFAULT_SETTINGS)
        monkeypatch.setattr(app, "initialize_credentials", initialize_credentials)
        monkeypatch.setattr(app, "QApplication", SimpleNamespace(instance=lambda: application))
        monkeypatch.setattr(app, "QMessageBox", SimpleNamespace(warning=forbidden, critical=forbidden))
        monkeypatch.setattr(app, "QInputDialog", SimpleNamespace(getText=forbidden))
        monkeypatch.setattr(app, "QtIpcRequestBridge", lambda: self.bridge)
        monkeypatch.setattr(app, "SingleInstanceServer", lambda *_args: self.server)
        monkeypatch.setattr(app, "TranslationWindow", create_window)
        monkeypatch.setattr(app, "send_request", send_request)
        monkeypatch.setattr(app, "QTimer", SimpleNamespace(
            singleShot=lambda _delay, callback: self.callbacks.append(callback)
        ))
        monkeypatch.setattr(app, "acknowledge_update_startup", lambda version: self.events.append("update-ack"))
        monkeypatch.setattr(app, "consume_update_error", lambda: "")
        self.app = app


@pytest.fixture
def launcher(app, monkeypatch, tmp_path):
    return _MainHarness(app, monkeypatch, tmp_path)


def test_primary_startup_enters_event_loop_without_initial_show_or_key_prompt(launcher):
    assert launcher.app.main(["--startup"]) == 0

    assert len(launcher.windows) == 1
    assert ("bind", launcher.windows[0]) in launcher.events
    assert launcher.requests == []
    assert launcher.forwarded == []
    assert "notifications-start" in launcher.events
    assert "event-loop" in launcher.events
    assert "server-stop" in launcher.events
    assert launcher.events.index("event-loop") < launcher.events.index("update-ack")


def test_secondary_startup_exits_without_ipc_show_or_focus_change(launcher):
    launcher.primary = False

    assert launcher.app.main(["--startup"]) == 0

    assert launcher.forwarded == []
    assert launcher.requests == []
    assert launcher.windows == []
    assert "credentials-initialized" not in launcher.events
    assert "event-loop" not in launcher.events
    assert "log-close" in launcher.events
    assert "update-ack" not in launcher.events


@pytest.mark.parametrize("arguments", [[], ["--show"]])
def test_normal_primary_launch_still_dispatches_show(launcher, arguments):
    assert launcher.app.main(arguments) == 0

    assert len(launcher.requests) == 1
    request, allow_key_prompt = launcher.requests[0]
    assert request.action == "show"
    assert allow_key_prompt is True
    assert "event-loop" in launcher.events


def test_normal_secondary_show_still_forwards_to_primary(launcher):
    launcher.primary = False

    assert launcher.app.main(["--show"]) == 0

    assert [request.action for request in launcher.forwarded] == ["show"]
    assert launcher.windows == []
    assert "event-loop" not in launcher.events
