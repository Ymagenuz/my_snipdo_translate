"""Take a screen snip with the Windows Snipping Tool and say how it ended.

Snipping Tool's documented protocol only answers packaged (MSIX) apps, so the
app opens the plain ms-screenclip: overlay and watches it: a snip arrives as
an image on the clipboard, and Esc closes the overlay without one.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from enum import Enum


SNIP_URI = "ms-screenclip:"
# Windows 11 runs the overlay in Snipping Tool, Windows 10 in Snip & Sketch's host.
SNIP_PROCESS_NAMES = frozenset({"snippingtool.exe", "screenclippinghost.exe"})

# A cold Snipping Tool takes about a second to show its overlay.
OVERLAY_OPEN_TIMEOUT_SECONDS = 5.0
# The snip can reach the clipboard just after the overlay closes.
CLIPBOARD_SETTLE_SECONDS = 1.5
# Stop watching an overlay left open; a later snip then stays on the clipboard.
SNIP_TIMEOUT_SECONDS = 300.0

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

try:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
except (AttributeError, OSError):
    _USER32 = None
    _KERNEL32 = None
else:
    _USER32.GetForegroundWindow.restype = wintypes.HWND
    _USER32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _USER32.IsWindow.argtypes = [wintypes.HWND]
    _USER32.IsWindowVisible.argtypes = [wintypes.HWND]
    _KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]


class SnipOutcome(Enum):
    CAPTURED = "captured"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"


def open_snip_overlay(*, start=None) -> bool:
    """Ask Windows for its snip overlay; false when nothing handles the link."""
    launch = os.startfile if start is None else start
    try:
        launch(SNIP_URI)
    except (AttributeError, OSError):
        return False
    return True


def window_process_name(hwnd: int, *, user32_api=None, kernel32_api=None) -> str:
    """The lower-case file name of the program that owns a window, or ""."""
    user32 = _USER32 if user32_api is None else user32_api
    kernel32 = _KERNEL32 if kernel32_api is None else kernel32_api
    if not hwnd or user32 is None or kernel32 is None:
        return ""
    try:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return ""
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value
        )
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ""
            return os.path.basename(buffer.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


class SnipOverlayTracker:
    """Find the overlay as it comes to the front, then tell when it closes."""

    def __init__(self, *, user32_api=None, process_name=None):
        self._user32 = _USER32 if user32_api is None else user32_api
        self._process_name = (
            window_process_name if process_name is None else process_name
        )
        # Snipping Tool's own window may already be in front; it isn't the overlay.
        self._ignored = self._foreground()
        self._overlay = 0

    def _foreground(self) -> int:
        try:
            return int(self._user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def is_open(self) -> bool:
        try:
            if self._overlay:
                return bool(
                    self._user32.IsWindow(self._overlay)
                    and self._user32.IsWindowVisible(self._overlay)
                )
        except Exception:
            return False
        hwnd = self._foreground()
        if hwnd and hwnd != self._ignored and self._process_name(hwnd) in SNIP_PROCESS_NAMES:
            self._overlay = hwnd
            return True
        return False


class SnipWatch:
    """Decide, from repeated looks at the overlay and clipboard, how a snip ended."""

    def __init__(self, clipboard_sequence: int, started: float):
        self._sequence = clipboard_sequence
        self._started = started
        self._overlay_seen = False
        self._overlay_closed_at: float | None = None

    def update(
        self,
        now: float,
        *,
        clipboard_sequence: int,
        clipboard_has_image: bool,
        overlay_open: bool,
    ) -> SnipOutcome | None:
        if clipboard_sequence != self._sequence and clipboard_has_image:
            return SnipOutcome.CAPTURED
        if now - self._started >= SNIP_TIMEOUT_SECONDS:
            return SnipOutcome.CANCELLED
        if overlay_open:
            self._overlay_seen = True
            self._overlay_closed_at = None
            return None
        if not self._overlay_seen:
            if now - self._started >= OVERLAY_OPEN_TIMEOUT_SECONDS:
                return SnipOutcome.UNAVAILABLE
            return None
        if self._overlay_closed_at is None:
            self._overlay_closed_at = now
        if now - self._overlay_closed_at >= CLIPBOARD_SETTLE_SECONDS:
            return SnipOutcome.CANCELLED
        return None


__all__ = [
    "CLIPBOARD_SETTLE_SECONDS",
    "OVERLAY_OPEN_TIMEOUT_SECONDS",
    "SNIP_PROCESS_NAMES",
    "SNIP_TIMEOUT_SECONDS",
    "SNIP_URI",
    "SnipOutcome",
    "SnipOverlayTracker",
    "SnipWatch",
    "open_snip_overlay",
    "window_process_name",
]
