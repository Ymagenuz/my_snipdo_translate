from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable


WH_MOUSE_LL = 14
HC_ACTION = 0

WM_APP = 0x8000
WM_STOP_HOOK = WM_APP + 0x534
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_XBUTTONDBLCLK = 0x020D

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
PM_NOREMOVE = 0x0000
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


_USER32 = ctypes.windll.user32
_KERNEL32 = ctypes.windll.kernel32

_USER32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelMouseProc,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
_USER32.SetWindowsHookExW.restype = ctypes.c_void_p
_USER32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_USER32.CallNextHookEx.restype = LRESULT
_USER32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_USER32.UnhookWindowsHookEx.restype = wintypes.BOOL
_USER32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
_USER32.GetMessageW.restype = wintypes.BOOL
_USER32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_USER32.TranslateMessage.restype = wintypes.BOOL
_USER32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_USER32.DispatchMessageW.restype = LRESULT
_USER32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
_USER32.PeekMessageW.restype = wintypes.BOOL
_USER32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_USER32.PostThreadMessageW.restype = wintypes.BOOL
_USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_USER32.GetAsyncKeyState.restype = ctypes.c_short
_KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_KERNEL32.GetModuleHandleW.restype = wintypes.HMODULE
_KERNEL32.GetCurrentThreadId.argtypes = []
_KERNEL32.GetCurrentThreadId.restype = wintypes.DWORD
_KERNEL32.GetLastError.argtypes = []
_KERNEL32.GetLastError.restype = wintypes.DWORD


_BUTTON_SPECS = {
    "xbutton1": (
        (WM_XBUTTONDOWN, WM_XBUTTONDBLCLK),
        WM_XBUTTONUP,
        XBUTTON1,
        VK_XBUTTON1,
    ),
    "xbutton2": (
        (WM_XBUTTONDOWN, WM_XBUTTONDBLCLK),
        WM_XBUTTONUP,
        XBUTTON2,
        VK_XBUTTON2,
    ),
    "middle": (
        (WM_MBUTTONDOWN, WM_MBUTTONDBLCLK),
        WM_MBUTTONUP,
        None,
        VK_MBUTTON,
    ),
}


class WindowsMouseShortcutHook:
    """Suppress one global mouse button on a dedicated Win32 message thread.

    The low-level callback performs no clipboard, UI, or translation work.  It
    only filters the configured button, invokes a non-blocking notifier on the
    button-up edge, and returns immediately.  The caller is responsible for
    queueing that notification to its UI/worker thread.
    """

    def __init__(
        self,
        button: str,
        on_trigger: Callable[[], None],
        *,
        user32_api=None,
        kernel32_api=None,
    ) -> None:
        normalized_button = str(button).strip().lower()
        if normalized_button not in _BUTTON_SPECS:
            raise ValueError("unsupported mouse shortcut")
        if not callable(on_trigger):
            raise TypeError("on_trigger must be callable")

        self.button = normalized_button
        self._on_trigger = on_trigger
        self._user32 = user32_api or _USER32
        self._kernel32 = kernel32_api or _KERNEL32

        self._state_lock = threading.Lock()
        self._ready = threading.Event()
        self._installed = threading.Event()
        self._accept_events = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook_handle = None
        self._callback_proc = None
        self._armed = False
        self._down_seen = False
        self.last_error = 0

    def start(self, timeout: float = 2.0) -> bool:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return bool(
                    self._installed.is_set()
                    and self._accept_events.is_set()
                )

            self._ready.clear()
            self._installed.clear()
            self._accept_events.clear()
            self._stop_requested.clear()
            self._thread_id = None
            self._hook_handle = None
            self._callback_proc = None
            self.last_error = 0
            thread = threading.Thread(
                target=self._run,
                name="SnipDoTranslateMouseHook",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        if not self._ready.wait(max(0.05, float(timeout))):
            self.stop(timeout=0.25)
            return False
        return self.is_running()

    def stop(self, timeout: float = 2.0) -> bool:
        self._stop_requested.set()
        self._accept_events.clear()
        with self._state_lock:
            thread = self._thread
            thread_id = self._thread_id

        if thread is None:
            return True
        if not thread.is_alive():
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
            return True
        if threading.current_thread() is thread:
            return False

        if thread_id:
            try:
                self._user32.PostThreadMessageW(
                    thread_id,
                    WM_STOP_HOOK,
                    0,
                    0,
                )
            except Exception:
                pass

        thread.join(max(0.05, float(timeout)))
        stopped = not thread.is_alive()
        if stopped:
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
        return stopped

    def is_running(self) -> bool:
        with self._state_lock:
            thread = self._thread
        return bool(
            thread is not None
            and thread.is_alive()
            and self._installed.is_set()
            and self._accept_events.is_set()
            and not self._stop_requested.is_set()
        )

    def _run(self) -> None:
        hook_handle = None
        try:
            with self._state_lock:
                self._thread_id = int(self._kernel32.GetCurrentThreadId())

            # Explicitly create the native thread message queue before start()
            # can attempt to stop it with PostThreadMessageW.
            message = wintypes.MSG()
            self._user32.PeekMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
                PM_NOREMOVE,
            )
            if self._stop_requested.is_set():
                return

            self._callback_proc = LowLevelMouseProc(self._hook_proc)
            module_handle = self._kernel32.GetModuleHandleW(None)
            hook_handle = self._user32.SetWindowsHookExW(
                WH_MOUSE_LL,
                self._callback_proc,
                module_handle,
                0,
            )
            if not hook_handle:
                try:
                    self.last_error = int(self._kernel32.GetLastError())
                except Exception:
                    self.last_error = 0
                return

            if self._stop_requested.is_set():
                return

            # Sample only after installation.  The hook thread has not entered
            # GetMessage yet, so no callback can race this initialization; an
            # already-held button is allowed to release without stealing the
            # foreground application's unmatched UP event.
            _down_messages, _up_message, _xbutton, virtual_key = (
                _BUTTON_SPECS[self.button]
            )
            initial_state = int(
                self._user32.GetAsyncKeyState(virtual_key)
            ) & 0xFFFF
            self._armed = not bool(initial_state & 0x8000)
            self._down_seen = False

            self._hook_handle = hook_handle
            self._installed.set()
            self._accept_events.set()
            self._ready.set()

            while True:
                if self._stop_requested.is_set():
                    break
                result = int(
                    self._user32.GetMessageW(
                        ctypes.byref(message),
                        None,
                        0,
                        0,
                    )
                )
                if result <= 0:
                    break
                if int(message.message) == WM_STOP_HOOK:
                    break
                self._user32.TranslateMessage(ctypes.byref(message))
                self._user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            return
        finally:
            self._accept_events.clear()
            if hook_handle:
                try:
                    self._user32.UnhookWindowsHookEx(hook_handle)
                except Exception:
                    pass
            self._installed.clear()
            self._hook_handle = None
            self._callback_proc = None
            self._armed = False
            self._down_seen = False
            with self._state_lock:
                self._thread_id = None
            self._ready.set()

    def _hook_proc(self, n_code, w_param, l_param):
        if (
            n_code != HC_ACTION
            or self._stop_requested.is_set()
            or not self._accept_events.is_set()
        ):
            return self._call_next(n_code, w_param, l_param)

        try:
            (
                down_messages,
                up_message,
                expected_xbutton,
                _virtual_key,
            ) = _BUTTON_SPECS[self.button]
            native_message = int(w_param)
            if native_message not in (*down_messages, up_message):
                return self._call_next(n_code, w_param, l_param)

            if expected_xbutton is not None:
                if not l_param:
                    return self._call_next(n_code, w_param, l_param)
                mouse_info = ctypes.cast(
                    l_param,
                    ctypes.POINTER(MSLLHOOKSTRUCT),
                ).contents
                actual_xbutton = (int(mouse_info.mouseData) >> 16) & 0xFFFF
                if actual_xbutton != expected_xbutton:
                    return self._call_next(n_code, w_param, l_param)

            if not self._armed:
                if native_message == up_message:
                    self._armed = True
                    self._down_seen = False
                return self._call_next(n_code, w_param, l_param)

            if native_message in down_messages:
                self._down_seen = True
                return 1

            if not self._down_seen:
                # The DOWN happened before installation or while suspended.
                # Preserve the foreground application's input pair by passing
                # the unmatched UP through instead of swallowing it.
                return self._call_next(n_code, w_param, l_param)

            self._down_seen = False
            if native_message == up_message:
                try:
                    self._on_trigger()
                except Exception:
                    pass

            # A non-zero return prevents the configured mouse message from
            # reaching the foreground application (for example browser Back).
            return 1
        except Exception:
            return self._call_next(n_code, w_param, l_param)

    def _call_next(self, n_code, w_param, l_param):
        try:
            return self._user32.CallNextHookEx(
                self._hook_handle,
                n_code,
                w_param,
                l_param,
            )
        except Exception:
            return 0


__all__ = [
    "HC_ACTION",
    "MSLLHOOKSTRUCT",
    "WM_MBUTTONDOWN",
    "WM_MBUTTONUP",
    "WM_XBUTTONDOWN",
    "WM_XBUTTONUP",
    "WindowsMouseShortcutHook",
    "XBUTTON1",
    "XBUTTON2",
]
