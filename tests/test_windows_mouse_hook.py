from __future__ import annotations

import ctypes
import threading

import pytest

from windows_mouse_hook import (
    HC_ACTION,
    MSLLHOOKSTRUCT,
    WM_MBUTTONDOWN,
    WM_MBUTTONUP,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
    WindowsMouseShortcutHook,
    XBUTTON1,
    XBUTTON2,
)


class _CallNextHarness:
    def __init__(self):
        self.calls = []

    def CallNextHookEx(self, hook, n_code, w_param, l_param):
        self.calls.append((hook, n_code, int(w_param), int(l_param)))
        return 91


@pytest.mark.parametrize(
    ("button", "down_message", "up_message", "mouse_data"),
    [
        ("xbutton1", WM_XBUTTONDOWN, WM_XBUTTONUP, XBUTTON1 << 16),
        ("xbutton2", WM_XBUTTONDOWN, WM_XBUTTONUP, XBUTTON2 << 16),
        ("middle", WM_MBUTTONDOWN, WM_MBUTTONUP, 0),
    ],
)
def test_hook_suppresses_only_configured_button_and_notifies_on_release(
    button, down_message, up_message, mouse_data
):
    user32 = _CallNextHarness()
    triggers = []
    hook = WindowsMouseShortcutHook(
        button,
        lambda: triggers.append(button),
        user32_api=user32,
        kernel32_api=object(),
    )
    hook._accept_events.set()
    hook._armed = True
    mouse_info = MSLLHOOKSTRUCT()
    mouse_info.mouseData = mouse_data
    pointer = ctypes.addressof(mouse_info)

    assert hook._hook_proc(HC_ACTION, down_message, pointer) == 1
    assert triggers == []
    assert hook._hook_proc(HC_ACTION, up_message, pointer) == 1
    assert triggers == [button]

    unrelated_message = (
        WM_MBUTTONDOWN if button != "middle" else WM_XBUTTONDOWN
    )
    assert hook._hook_proc(HC_ACTION, unrelated_message, pointer) == 91
    assert hook._hook_proc(-1, up_message, pointer) == 91
    assert hook._hook_proc(1, up_message, pointer) == 91
    assert len(user32.calls) == 3


def test_xbutton_hook_passes_the_other_side_button_to_next_hook():
    user32 = _CallNextHarness()
    hook = WindowsMouseShortcutHook(
        "xbutton1",
        lambda: pytest.fail("the other side button must not trigger"),
        user32_api=user32,
        kernel32_api=object(),
    )
    hook._accept_events.set()
    hook._armed = True
    mouse_info = MSLLHOOKSTRUCT()
    mouse_info.mouseData = XBUTTON2 << 16

    result = hook._hook_proc(
        HC_ACTION,
        WM_XBUTTONUP,
        ctypes.addressof(mouse_info),
    )

    assert result == 91
    assert len(user32.calls) == 1


def test_unmatched_release_is_passed_through_before_hook_arms():
    user32 = _CallNextHarness()
    triggers = []
    hook = WindowsMouseShortcutHook(
        "xbutton1",
        lambda: triggers.append(True),
        user32_api=user32,
        kernel32_api=object(),
    )
    hook._accept_events.set()
    hook._armed = False
    mouse_info = MSLLHOOKSTRUCT()
    mouse_info.mouseData = XBUTTON1 << 16
    pointer = ctypes.addressof(mouse_info)

    assert hook._hook_proc(HC_ACTION, WM_XBUTTONUP, pointer) == 91
    assert hook._armed is True
    assert triggers == []

    assert hook._hook_proc(HC_ACTION, WM_XBUTTONDOWN, pointer) == 1
    assert hook._hook_proc(HC_ACTION, WM_XBUTTONUP, pointer) == 1
    assert triggers == [True]


class _ThreadedUser32Harness:
    def __init__(self, *, install_result=0xCAFE, async_key_state=0):
        self.install_result = install_result
        self.async_key_state = async_key_state
        self.quit_event = threading.Event()
        self.install_thread_ident = None
        self.posted_thread_ids = []
        self.unhooked = []
        self.callback = None

    def PeekMessageW(self, *_args):
        return 0

    def GetAsyncKeyState(self, _virtual_key):
        return self.async_key_state

    def SetWindowsHookExW(self, _kind, callback, _module, _thread_id):
        self.install_thread_ident = threading.get_ident()
        self.callback = callback
        return self.install_result

    def GetMessageW(self, *_args):
        self.quit_event.wait(2.0)
        return 0

    def TranslateMessage(self, *_args):
        return 1

    def DispatchMessageW(self, *_args):
        return 0

    def PostThreadMessageW(self, thread_id, *_args):
        self.posted_thread_ids.append(int(thread_id))
        self.quit_event.set()
        return 1

    def UnhookWindowsHookEx(self, hook):
        self.unhooked.append(hook)
        return 1

    def CallNextHookEx(self, *_args):
        return 0


class _Kernel32Harness:
    @staticmethod
    def GetCurrentThreadId():
        return 4242

    @staticmethod
    def GetModuleHandleW(_name):
        return 0x1234

    @staticmethod
    def GetLastError():
        return 123


def test_hook_install_and_message_loop_run_on_dedicated_thread():
    user32 = _ThreadedUser32Harness()
    main_thread_ident = threading.get_ident()
    hook = WindowsMouseShortcutHook(
        "xbutton1",
        lambda: None,
        user32_api=user32,
        kernel32_api=_Kernel32Harness(),
    )

    assert hook.start(timeout=1.0) is True
    assert hook.is_running() is True
    assert user32.install_thread_ident != main_thread_ident

    assert hook.stop(timeout=1.0) is True
    assert hook.is_running() is False
    assert user32.posted_thread_ids == [4242]
    assert user32.unhooked == [0xCAFE]


def test_hook_install_failure_is_reported_without_entering_message_loop():
    user32 = _ThreadedUser32Harness(install_result=0)
    hook = WindowsMouseShortcutHook(
        "xbutton1",
        lambda: None,
        user32_api=user32,
        kernel32_api=_Kernel32Harness(),
    )

    assert hook.start(timeout=1.0) is False
    assert hook.is_running() is False
    assert hook.last_error == 123
    assert hook.stop(timeout=1.0) is True
    assert user32.unhooked == []


def test_hook_starts_unarmed_when_button_is_already_held():
    user32 = _ThreadedUser32Harness(async_key_state=0x8000)
    hook = WindowsMouseShortcutHook(
        "xbutton1",
        lambda: None,
        user32_api=user32,
        kernel32_api=_Kernel32Harness(),
    )

    assert hook.start(timeout=1.0) is True
    assert hook._armed is False
    assert hook.stop(timeout=1.0) is True
