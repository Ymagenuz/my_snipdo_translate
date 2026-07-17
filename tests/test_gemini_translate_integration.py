from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import queue
import socket
import sys
import threading
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from app_cli import AppRequest, PreparedRequest
from app_paths import AppPaths
from windows_ipc import RejectionReason


@pytest.fixture(scope="module")
def app_module():
    """Load the .pyw entry point without invoking main()."""
    module_path = Path(__file__).resolve().parents[1] / "gemini_translate.pyw"
    module_name = "_snipdo_translate_integration_target"
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
    """Make every integration test credential- and network-deny-by-default."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("credential, client, or IPC access is forbidden")

    monkeypatch.setattr(app_module, "OpenAI", forbidden)
    monkeypatch.setattr(app_module, "WindowsCredentialStore", forbidden)
    monkeypatch.setattr(app_module, "resolve_api_key", forbidden)
    monkeypatch.setattr(app_module, "configure_api_client", forbidden)
    monkeypatch.setattr(app_module, "initialize_credentials", forbidden)
    monkeypatch.setattr(app_module, "send_request", forbidden)
    monkeypatch.setattr(app_module, "record_event", lambda _event: None)
    monkeypatch.setattr(app_module, "client", None)
    return app_module


class _SignalStub:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _WidgetStub:
    def __init__(self):
        self.values = []

    def setText(self, value):
        self.values.append(value)

    def setEnabled(self, value):
        self.values.append(value)

    def setReadOnly(self, value):
        self.values.append(value)

    def clear(self):
        self.values.append("clear")


class _CursorStub:
    def __init__(self):
        self.insertions = []

    def insertText(self, text, *_args):
        self.insertions.append(text)


class _TextEditStub(_WidgetStub):
    def __init__(self):
        super().__init__()
        self.cursor = _CursorStub()

    def textCursor(self):
        return self.cursor


def _bind(instance, owner, method_name):
    setattr(instance, method_name, MethodType(getattr(owner, method_name), instance))


def _ocr_window(app):
    window = SimpleNamespace(
        ocr_thread=None,
        source_mode="snipdo",
        txt_origin=_TextEditStub(),
        txt_result=_TextEditStub(),
        lbl_origin=_WidgetStub(),
        lbl_result=_WidgetStub(),
        btn_copy=_WidgetStub(),
        result_char_fmt=object(),
        ensure_api_key=lambda *, allow_prompt=True: True,
        cancel_current_translation=lambda: None,
        setup_result_format=lambda: None,
        force_show_window=lambda: None,
        resize=lambda *_args: None,
        set_result_message=lambda _message: None,
        on_ocr_finished=lambda *_args: None,
    )
    _bind(window, app.TranslationWindow, "start_ocr")
    _bind(window, app.TranslationWindow, "start_image_file_ocr")
    return window


def _translation_window(app):
    window = SimpleNamespace(
        ocr_thread=None,
        trans_thread=None,
        source_mode="snipdo",
        btn_copy=_WidgetStub(),
        txt_result=_TextEditStub(),
        result_char_fmt=object(),
        ensure_api_key=lambda *, allow_prompt=True: True,
        cancel_current_ocr=lambda: None,
        cancel_current_translation=lambda: None,
        resolve_effective_mode=lambda _text: "auto",
        apply_snipdo_mode_ui=lambda: None,
        adjust_window_height=lambda: None,
        populate_original_text=lambda: None,
        setup_result_format=lambda: None,
        hide=lambda: None,
        show_translation_progress=lambda _mode: None,
        hide_translation_progress=lambda: None,
        force_show_window=lambda: None,
        set_result_message=lambda _message: None,
        current_dictionary_languages=lambda: ("auto", "default"),
        append_translation_chunk=lambda _chunk: None,
        on_translation_finished=lambda *_args: None,
    )
    _bind(window, app.TranslationWindow, "start_translation")
    _bind(window, app.TranslationWindow, "handle_new_request")
    return window


def test_offline_self_test_never_touches_credentials_client_or_network(
    app, monkeypatch, tmp_path: Path
):
    # Use the checked-in bitmap so this also exercises the entry point's
    # offline image decoder check without generating a test-only image.
    icon = Path(app.__file__).resolve().parent / "snipdo_script_logo" / "gemini-color.png"
    assert icon.is_file()
    paths = AppPaths(
        bundle_dir=tmp_path,
        executable_dir=tmp_path,
        data_dir=tmp_path / "data",
        history_path=tmp_path / "data" / "history.json",
        log_dir=tmp_path / "data" / "logs",
        icon_path=icon,
        legacy_dirs=(tmp_path,),
    )

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)

    class LogStub:
        flushed = False

        def flush(self):
            self.flushed = True

    event_log = LogStub()
    monkeypatch.setattr(app, "EVENT_LOG", event_log)

    assert app.run_offline_self_test(paths) == 0
    assert event_log.flushed is True


def test_handle_ocr_rejects_busy_before_reading_source(app, monkeypatch):
    class RunningThread:
        @staticmethod
        def isRunning():
            return True

    window = SimpleNamespace(ocr_thread=RunningThread())
    monkeypatch.setattr(app, "client", object())
    monkeypatch.setattr(
        app,
        "image_file_to_data_url",
        lambda _path: pytest.fail("busy OCR must not read the source"),
    )

    result = app.TranslationWindow.handle_app_request(
        window,
        AppRequest("ocr_image", {"path": "unread-source.png"}),
        allow_key_prompt=False,
    )

    assert result.accepted is False
    assert result.reason is RejectionReason.BUSY


def test_handle_ocr_rejects_missing_key_without_reading_or_prompting(app, monkeypatch):
    window = SimpleNamespace(ocr_thread=None)
    monkeypatch.setattr(
        app,
        "image_file_to_data_url",
        lambda _path: pytest.fail("missing-key OCR must not read the source"),
    )

    result = app.TranslationWindow.handle_app_request(
        window,
        AppRequest("ocr_image", {"path": "unread-source.png"}),
        allow_key_prompt=False,
    )

    assert result.accepted is False
    assert result.reason is RejectionReason.MISSING_KEY


def test_handle_ocr_rejects_when_source_cannot_be_read(app, monkeypatch, tmp_path: Path):
    window = _ocr_window(app)
    monkeypatch.setattr(app, "client", object())
    missing_source = tmp_path / "missing.png"

    result = app.TranslationWindow.handle_app_request(
        window,
        AppRequest("ocr_image", {"path": str(missing_source)}),
        allow_key_prompt=False,
    )

    assert result.accepted is False
    assert result.reason is RejectionReason.NOT_OWNED
    assert not missing_source.exists()


def test_handle_ocr_rejects_when_thread_start_fails_and_retains_source(
    app, monkeypatch, tmp_path: Path
):
    source = tmp_path / "thread-start-failure.png"
    source.write_bytes(b"synthetic-image")
    window = _ocr_window(app)

    class FailingOcrThread:
        def __init__(self, image_data_url):
            assert image_data_url.startswith("data:image/png;base64,")
            self.finished = _SignalStub()

        @staticmethod
        def start():
            raise RuntimeError("synthetic start failure")

    monkeypatch.setattr(app, "client", object())
    monkeypatch.setattr(app, "OcrThread", FailingOcrThread)

    result = app.TranslationWindow.handle_app_request(
        window,
        AppRequest("ocr_image", {"path": str(source)}),
        allow_key_prompt=False,
    )

    assert result.accepted is False
    assert result.reason is RejectionReason.STARTUP_FAILED
    assert source.exists()
    assert window.ocr_thread is None


def test_ocr_accepts_only_after_in_memory_ownership_and_leaves_source_to_caller(
    app, monkeypatch, tmp_path: Path
):
    image_bytes = b"synthetic-offline-image-content"
    source = tmp_path / "owned-before-ack.png"
    source.write_bytes(image_bytes)
    window = _ocr_window(app)
    created_threads = []

    class OwningOcrThread:
        def __init__(self, image_data_url):
            self.image_data_url = image_data_url
            self.finished = _SignalStub()
            self.started = False
            created_threads.append(self)

        def start(self):
            assert source.exists(), "the caller still controls the source before ACK"
            self.started = True

    monkeypatch.setattr(app, "client", object())
    monkeypatch.setattr(app, "OcrThread", OwningOcrThread)

    request = AppRequest("ocr_image", {"path": str(source)})
    result = app.TranslationWindow.handle_app_request(
        window,
        request,
        allow_key_prompt=False,
    )

    assert result.accepted is True
    assert source.exists()
    assert len(created_threads) == 1
    thread = created_threads[0]
    assert thread.started is True
    prefix, encoded = thread.image_data_url.split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == image_bytes
    assert str(source) not in thread.image_data_url


def test_handle_translation_rejects_missing_key_without_starting_client(app):
    window = SimpleNamespace()

    result = app.TranslationWindow.handle_app_request(
        window,
        AppRequest("translate_text", {"text": "offline sample"}),
        allow_key_prompt=False,
    )

    assert result.accepted is False
    assert result.reason is RejectionReason.MISSING_KEY


def test_handle_translation_rejects_when_thread_start_fails(app, monkeypatch):
    window = _translation_window(app)

    class FailingTranslationThread:
        def __init__(self, *_args):
            self.chunk_received = _SignalStub()
            self.finished = _SignalStub()

        @staticmethod
        def start():
            raise RuntimeError("synthetic start failure")

    monkeypatch.setattr(app, "client", object())
    monkeypatch.setattr(app, "TranslationThread", FailingTranslationThread)

    result = app.TranslationWindow.handle_app_request(
        window,
        AppRequest("translate_text", {"text": "offline sample"}),
        allow_key_prompt=False,
    )

    assert result.accepted is False
    assert result.reason is RejectionReason.STARTUP_FAILED
    assert window.trans_thread is None


def test_settle_source_file_deletes_only_after_accepted_ownership(
    app, monkeypatch, tmp_path: Path
):
    source = tmp_path / "caller-owned.txt"
    source.write_text("offline sample", encoding="utf-8")
    prepared = PreparedRequest(AppRequest("translate_text", {"text": "offline sample"}), source)
    events = []
    monkeypatch.setattr(app, "record_event", events.append)

    app.settle_source_file(prepared, accepted=False)
    assert source.exists()
    assert events == [app.AppEvent.SOURCE_DELETE_SKIPPED]

    app.settle_source_file(prepared, accepted=True)
    assert not source.exists()
    assert events[-1] is app.AppEvent.SOURCE_DELETE_COMPLETED


def test_qt_bridge_timeout_cancels_queued_request_before_late_dispatch(
    app, monkeypatch, qapp
):
    calls = []

    class WindowStub:
        def handle_app_request(self, request, *, allow_key_prompt):
            calls.append((request, allow_key_prompt))
            return app.ReceiveResult.accept()

    bridge = app.QtIpcRequestBridge()
    bridge.bind(WindowStub())
    monkeypatch.setattr(app, "IPC_UI_TIMEOUT_SECONDS", 0.02)
    results = queue.Queue()

    worker = threading.Thread(
        target=lambda: results.put(bridge.receive(AppRequest("show"))),
        daemon=True,
    )
    worker.start()
    worker.join(1.0)
    assert not worker.is_alive()

    result = results.get_nowait()
    assert result.accepted is False
    assert result.reason is RejectionReason.NOT_READY

    # Deliver the signal after the receiver timed out. The cancelled completion
    # must make _dispatch return before handing the request to the window.
    qapp.processEvents()
    assert calls == []


@pytest.mark.parametrize(
    ("acknowledged", "expected_exit", "source_exists"),
    [(True, 0, False), (False, 3, True)],
)
def test_secondary_main_uses_explicit_ack_for_source_deletion(
    app,
    monkeypatch,
    tmp_path: Path,
    acknowledged: bool,
    expected_exit: int,
    source_exists: bool,
):
    source = tmp_path / ("accepted.png" if acknowledged else "rejected.png")
    source.write_bytes(b"synthetic-image")

    class ApplicationStub:
        @staticmethod
        def instance():
            return None

        def __init__(self, _argv):
            pass

        @staticmethod
        def setQuitOnLastWindowClosed(_value):
            pass

    class BridgeStub:
        @staticmethod
        def receive(_request):
            pytest.fail("a secondary instance must not receive requests")

    class SecondaryServerStub:
        def __init__(self, _app_id, _handler):
            pass

        @staticmethod
        def start():
            return False

    paths = SimpleNamespace()
    monkeypatch.setattr(app, "resolve_app_paths", lambda _module_file: paths)
    monkeypatch.setattr(app, "configure_runtime", lambda _paths: None)
    monkeypatch.setattr(app, "close_runtime_log", lambda: None)
    monkeypatch.setattr(app, "QApplication", ApplicationStub)
    monkeypatch.setattr(app, "QtIpcRequestBridge", BridgeStub)
    monkeypatch.setattr(app, "SingleInstanceServer", SecondaryServerStub)
    monkeypatch.setattr(
        app,
        "send_request",
        lambda _app_id, _request, *, timeout: SimpleNamespace(accepted=acknowledged),
    )

    exit_code = app.main(["--image", str(source), "--delete-after"])

    assert exit_code == expected_exit
    assert source.exists() is source_exists
