from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from app_settings import DEFAULT_SETTINGS, keyboard_shortcut, mouse_shortcut


@pytest.fixture(scope="module")
def app_module():
    """Import the entry point without starting a GUI or API client."""
    path = Path(__file__).resolve().parents[1] / "snipdo_translate.pyw"
    name = "_snipdo_translate_selection_capture_target"
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


class _MimeData:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class _Clipboard:
    def __init__(self):
        self.original_mime = _MimeData("previous clipboard content")
        self.current_mime = self.original_mime
        self.sequence = 10
        self.reject_text = False
        self.text_writes = []
        self.mime_restores = []

    def text(self):
        return self.current_mime.text()

    def mimeData(self):
        return self.current_mime

    def setText(self, text):
        self.text_writes.append(text)
        if not self.reject_text:
            self.current_mime = _MimeData(text)
            self.sequence += 1

    def setMimeData(self, mime):
        self.mime_restores.append(mime)
        self.current_mime = mime
        self.sequence += 1

    def clear(self):
        self.current_mime = _MimeData("")
        self.sequence += 1


class _ShortcutManager:
    def __init__(self):
        self.suspends = 0
        self.resumes = 0
        self.suspend_result = True

    def suspend(self):
        self.suspends += 1
        if isinstance(self.suspend_result, Exception):
            raise self.suspend_result
        return self.suspend_result

    def resume(self):
        self.resumes += 1
        return True


class _CaptureHarness:
    def __init__(self, app, monkeypatch):
        self.app = app
        self.now = 100.0
        self.clipboard = _Clipboard()
        self.foreground = 0x1234
        self.keys_released = True
        self.release_queries = []
        self.timers = []
        self.timer_delays = []
        self.timer_order = 0
        self.send_calls = []
        self.send_result = True
        self.copy_delay = 0.0
        self.copy_text = "selected text"
        self.copy_due = None
        self.on_process_events = None
        self.process_count = 0
        self.messages = []
        self.requests = []
        self.refreshes = []
        self.logs = []

        def forbidden_api(*_args, **_kwargs):
            raise AssertionError("capture tests must not create an API client")

        monkeypatch.setattr(app, "OpenAI", forbidden_api)
        monkeypatch.setattr(app, "log", self.logs.append)
        monkeypatch.setattr(app, "time", SimpleNamespace(
            monotonic=lambda: self.now, sleep=self.sleep,
        ))
        monkeypatch.setattr(app, "QApplication", SimpleNamespace(
            clipboard=lambda: self.clipboard, processEvents=self.process_events,
        ))
        monkeypatch.setattr(app, "QTimer", SimpleNamespace(singleShot=self.schedule))
        monkeypatch.setattr(app, "user32", SimpleNamespace(
            GetForegroundWindow=lambda: self.foreground,
        ))
        monkeypatch.setattr(app, "get_clipboard_sequence_number", lambda: self.clipboard.sequence)
        monkeypatch.setattr(app, "clone_clipboard_mime_data", lambda: self.clipboard.original_mime)
        monkeypatch.setattr(app, "clipboard_mime_to_formatted_text", lambda mime, _sentinel: mime.text())
        monkeypatch.setattr(app, "copy_keys_released", self.copy_keys_released)
        monkeypatch.setattr(app, "send_ctrl_c", self.send_ctrl_c)

        self.window = SimpleNamespace(
            app_settings=replace(
                DEFAULT_SETTINGS,
                shortcut=keyboard_shortcut(0x54, ("ctrl", "alt"), "Ctrl+Alt+T"),
            ),
            force_quit=False,
            selection_capture_busy=False,
            _selection_capture_hwnd=None,
            _selection_capture_binding=None,
            _selection_release_deadline=0.0,
            _selection_suspended_managers=[],
            shortcut_manager=_ShortcutManager(),
            show_window_shortcut_manager=_ShortcutManager(),
            tray_icon=SimpleNamespace(showMessage=lambda *args: self.messages.append(args)),
            handle_new_request=self.requests.append,
            refresh_shortcut_status=lambda: self.refreshes.append(self.now),
        )
        for name in (
            "on_translation_shortcut_triggered",
            "_wait_for_selection_shortcut_release",
            "_selection_capture_is_current",
            "_finish_selection_capture",
            "_resume_selection_shortcuts",
            "capture_current_selection_text",
            "translate_current_selection",
        ):
            setattr(self.window, name, MethodType(getattr(app.TranslationWindow, name), self.window))

    def sleep(self, seconds):
        self.now += seconds

    def schedule(self, milliseconds, callback):
        self.timer_delays.append(milliseconds)
        self.timer_order += 1
        self.timers.append((self.now + milliseconds / 1000, self.timer_order, callback))

    def run_next_timer(self):
        assert self.timers, "expected a scheduled callback"
        self.timers.sort(key=lambda item: item[:2])
        due, _, callback = self.timers.pop(0)
        self.now = max(self.now, due)
        callback()

    def drain_timers(self):
        for _ in range(250):
            if not self.timers:
                return
            self.run_next_timer()
        raise AssertionError("timer callbacks did not finish")

    def process_events(self):
        self.process_count += 1
        if self.on_process_events is not None:
            self.on_process_events()
        if self.copy_due is not None and self.now >= self.copy_due:
            self.copy_due = None
            self.clipboard.setText(self.copy_text)

    def copy_keys_released(self, virtual_key=None):
        self.release_queries.append(virtual_key)
        return self.keys_released

    def send_ctrl_c(self):
        self.send_calls.append(self.now)
        if self.send_result and self.copy_text is not None:
            self.copy_due = self.now + self.copy_delay
        return self.send_result

    def arm_capture(self):
        """Seed a valid trigger context for focused clipboard tests."""
        self.window.selection_capture_busy = True
        self.window._selection_capture_hwnd = self.foreground
        self.window._selection_capture_binding = self.window.app_settings.shortcut
        self.window._selection_release_deadline = self.now + 2.0

    def assert_clipboard_restored(self):
        assert self.clipboard.current_mime is self.clipboard.original_mime
        assert self.clipboard.mime_restores[-1] is self.clipboard.original_mime


@pytest.fixture
def capture(app_module, monkeypatch):
    return _CaptureHarness(app_module, monkeypatch)


@pytest.mark.parametrize("binding", [
    keyboard_shortcut(0x54, ("ctrl", "alt"), "Ctrl+Alt+T"),
    keyboard_shortcut(0x43, ("ctrl", "shift", "win"), "Ctrl+Shift+Win+C"),
    mouse_shortcut("xbutton1"),
])
def test_capture_waits_for_trigger_and_modifiers_to_be_released(capture, binding):
    capture.window.app_settings = replace(DEFAULT_SETTINGS, shortcut=binding)
    capture.keys_released = False

    capture.window.on_translation_shortcut_triggered()
    assert capture.timer_delays == [35]
    capture.run_next_timer()
    capture.run_next_timer()

    assert capture.send_calls == []
    assert capture.clipboard.text_writes == []
    assert capture.window.selection_capture_busy
    assert capture.release_queries == [binding.virtual_key, binding.virtual_key]
    assert capture.timer_delays[-1] == 15

    capture.keys_released = True
    capture.drain_timers()

    assert len(capture.send_calls) == 1
    assert capture.requests == ["selected text"]
    assert not capture.window.selection_capture_busy
    assert capture.window.shortcut_manager.resumes == 1
    assert capture.window.show_window_shortcut_manager.resumes == 1
    capture.assert_clipboard_restored()


def test_duplicate_trigger_does_not_schedule_another_capture(capture):
    capture.keys_released = False
    capture.window.on_translation_shortcut_triggered()
    capture.window.on_translation_shortcut_triggered()

    assert capture.window.shortcut_manager.suspends == 1
    assert capture.window.show_window_shortcut_manager.suspends == 1
    assert len(capture.timers) == 1


def test_held_shortcut_times_out_and_resumes_managers(capture):
    capture.keys_released = False
    started = capture.now
    capture.window.on_translation_shortcut_triggered()
    capture.drain_timers()

    assert capture.now >= started + 2.0
    assert capture.send_calls == []
    assert capture.clipboard.text_writes == []
    assert capture.requests == []
    assert capture.messages
    assert not capture.window.selection_capture_busy
    assert capture.window.shortcut_manager.resumes == 1
    assert capture.window.show_window_shortcut_manager.resumes == 1


@pytest.mark.parametrize("change", ["focus", "shortcut", "disabled", "quit"])
def test_pending_capture_cancels_when_trigger_context_changes(capture, change):
    capture.keys_released = False
    capture.window.on_translation_shortcut_triggered()
    capture.run_next_timer()
    _invalidate_context(capture, change)
    capture.keys_released = True
    capture.drain_timers()

    assert capture.send_calls == []
    assert capture.clipboard.text_writes == []
    assert capture.requests == []
    assert not capture.window.selection_capture_busy
    assert capture.window.shortcut_manager.resumes == (0 if change == "quit" else 1)


def _invalidate_context(capture, change):
    if change == "focus":
        capture.foreground = 0x9876
    elif change == "shortcut":
        capture.window.app_settings = replace(DEFAULT_SETTINGS, shortcut=mouse_shortcut("xbutton2"))
    elif change == "disabled":
        capture.window.app_settings = replace(capture.window.app_settings, enabled=False)
    elif change == "quit":
        capture.window.force_quit = True


@pytest.mark.parametrize("change", ["disabled", "quit"])
def test_disabled_or_quitting_app_ignores_trigger(capture, change):
    _invalidate_context(capture, change)
    capture.window.on_translation_shortcut_triggered()

    assert capture.timers == []
    assert capture.window.shortcut_manager.suspends == 0
    assert not capture.window.selection_capture_busy


def test_show_window_ctrl_c_shortcut_is_suspended_during_copy(capture):
    capture.window.app_settings = replace(
        capture.window.app_settings,
        show_window_shortcut=keyboard_shortcut(0x43, ("ctrl",), "Ctrl+C"),
    )

    def check_shortcuts_are_suspended():
        if capture.send_calls:
            assert capture.window.shortcut_manager.suspends == 1
            assert capture.window.show_window_shortcut_manager.suspends == 1
            assert capture.window.shortcut_manager.resumes == 0
            assert capture.window.show_window_shortcut_manager.resumes == 0

    capture.on_process_events = check_shortcuts_are_suspended
    capture.window.on_translation_shortcut_triggered()
    capture.drain_timers()

    assert capture.requests == ["selected text"]
    assert capture.window.shortcut_manager.resumes == 1
    assert capture.window.show_window_shortcut_manager.resumes == 1


@pytest.mark.parametrize("result", [False, RuntimeError("registration is busy")])
def test_partial_shortcut_suspension_failure_releases_only_acquired_manager(capture, result):
    capture.window.show_window_shortcut_manager.suspend_result = result
    capture.window.on_translation_shortcut_triggered()
    capture.drain_timers()

    assert capture.send_calls == []
    assert capture.window.shortcut_manager.resumes == 1
    assert capture.window.show_window_shortcut_manager.resumes == 0
    assert not capture.window.selection_capture_busy


def test_capture_accepts_text_from_slow_copy_operation(capture):
    capture.arm_capture()
    capture.copy_delay = 0.60
    capture.copy_text = "  slow selected text  "
    started = capture.now

    assert capture.window.capture_current_selection_text() == "slow selected text"

    assert capture.now - started >= 0.60
    assert len(capture.send_calls) == 1
    capture.assert_clipboard_restored()


def test_no_selection_does_not_return_previous_clipboard_contents(capture):
    capture.arm_capture()
    capture.copy_text = None
    started = capture.now

    assert capture.window.capture_current_selection_text() == ""

    assert capture.now - started >= 1.0
    capture.assert_clipboard_restored()


def test_failed_sentinel_write_does_not_copy_or_return_old_content(capture):
    capture.arm_capture()
    capture.clipboard.reject_text = True

    assert capture.window.capture_current_selection_text() == ""

    assert capture.send_calls == []
    capture.assert_clipboard_restored()


def test_unavailable_clipboard_sequence_prevents_copy(capture, monkeypatch):
    capture.arm_capture()
    monkeypatch.setattr(capture.app, "get_clipboard_sequence_number", lambda: 0)

    assert capture.window.capture_current_selection_text() == ""

    assert capture.send_calls == []
    capture.assert_clipboard_restored()


def test_failed_send_input_restores_original_clipboard(capture):
    capture.arm_capture()
    capture.send_result = False

    assert capture.window.capture_current_selection_text() == ""

    assert len(capture.send_calls) == 1
    capture.assert_clipboard_restored()


def test_keys_pressed_before_capture_leave_clipboard_untouched(capture):
    capture.arm_capture()
    capture.keys_released = False

    assert capture.window.capture_current_selection_text() == ""

    assert capture.clipboard.text_writes == []
    assert capture.send_calls == []
    assert capture.clipboard.current_mime is capture.clipboard.original_mime


@pytest.mark.parametrize("change", ["focus", "shortcut", "disabled", "quit", "key"])
def test_context_or_keys_changed_after_sentinel_prevents_copy(capture, change):
    capture.arm_capture()

    def change_after_sentinel():
        if capture.clipboard.text_writes:
            if change == "key":
                capture.keys_released = False
            else:
                _invalidate_context(capture, change)

    capture.on_process_events = change_after_sentinel

    assert capture.window.capture_current_selection_text() == ""

    assert capture.send_calls == []
    capture.assert_clipboard_restored()


@pytest.mark.parametrize("change", ["focus", "shortcut", "disabled", "quit"])
def test_context_change_while_copy_is_pending_discards_result(capture, change):
    capture.arm_capture()
    capture.copy_delay = 0.6

    def change_during_copy():
        if capture.send_calls and capture.now >= capture.send_calls[0] + 0.1:
            _invalidate_context(capture, change)

    capture.on_process_events = change_during_copy

    assert capture.window.capture_current_selection_text() == ""

    assert len(capture.send_calls) == 1
    assert capture.now - capture.send_calls[0] < 0.6
    capture.assert_clipboard_restored()


def test_translate_discards_capture_if_context_changed_before_delivery(capture):
    capture.arm_capture()

    def capture_then_change_focus():
        capture.foreground = 0x9876
        return "stale selected text"

    capture.window.capture_current_selection_text = capture_then_change_focus
    capture.window.translate_current_selection()

    assert capture.requests == []
    assert not capture.window.selection_capture_busy


def test_translate_exception_clears_busy_and_resumes_shortcuts(capture):
    def fail_capture():
        raise RuntimeError("clipboard temporarily unavailable")

    capture.window.capture_current_selection_text = fail_capture
    capture.window.on_translation_shortcut_triggered()
    capture.drain_timers()

    assert capture.requests == []
    assert not capture.window.selection_capture_busy
    assert capture.window.shortcut_manager.resumes == 1
    assert capture.window.show_window_shortcut_manager.resumes == 1
