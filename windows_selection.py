"""Win32 keyboard input used to copy a selection without held shortcut keys."""

from __future__ import annotations

import ctypes


VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_C = 0x43
VK_LWIN = 0x5B
VK_RWIN = 0x5C
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

# Win32 scalar widths stay fixed on both 32-bit and 64-bit Windows. Only
# ULONG_PTR changes with the process architecture.
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    ]


class _INPUTUNION(ctypes.Union):
    # The mouse member determines the union size even for keyboard input.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_uint32), ("data", _INPUTUNION)]


try:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
except (AttributeError, OSError):
    _USER32 = None
else:
    _USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _USER32.GetAsyncKeyState.restype = ctypes.c_short
    _USER32.SendInput.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    _USER32.SendInput.restype = ctypes.c_uint32


_COPY_KEYS = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN, VK_C)


def copy_keys_released(trigger_key: int | None = None, *, user32_api=None) -> bool:
    """Check current key state, including the shortcut's non-modifier key.

    The low bit of GetAsyncKeyState reports an earlier press, not a held key.
    An unavailable backend or failed query must not authorize copy input.
    """
    user32 = _USER32 if user32_api is None else user32_api
    keys = _COPY_KEYS
    if trigger_key is not None and trigger_key not in keys:
        keys += (trigger_key,)
    try:
        return all(not (user32.GetAsyncKeyState(key) & 0x8000) for key in keys)
    except Exception:
        return False


def _keyboard_input(key: int, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki.wVk = key
    event.ki.dwFlags = flags
    return event


def _release_injected_keys(user32, inserted: int) -> None:
    # SendInput inserts the batch in order. A partial prefix can leave Ctrl,
    # or Ctrl+C, down. Both were up before our batch; never release Alt/Shift/
    # Win or any other physically held shortcut keys as a workaround.
    keys = (VK_C, VK_CONTROL) if inserted == 2 else (VK_CONTROL,)
    while keys:
        events = (INPUT * len(keys))(
            *(_keyboard_input(key, KEYEVENTF_KEYUP) for key in keys)
        )
        try:
            released = user32.SendInput(len(events), events, ctypes.sizeof(INPUT))
        except Exception:
            return
        if not 0 < released <= len(keys):
            return
        keys = keys[released:]


def send_ctrl_c(*, user32_api=None) -> bool:
    """Send one ordered Ctrl+C batch only when copy-related keys are up.

    Return false if input was blocked or only partly inserted. Partial input
    receives best-effort key-up cleanup for keys introduced by our own batch.
    """
    user32 = _USER32 if user32_api is None else user32_api
    if not copy_keys_released(user32_api=user32):
        return False
    events = (INPUT * 4)(
        _keyboard_input(VK_CONTROL),
        _keyboard_input(VK_C),
        _keyboard_input(VK_C, KEYEVENTF_KEYUP),
        _keyboard_input(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    try:
        inserted = user32.SendInput(len(events), events, ctypes.sizeof(INPUT))
    except Exception:
        return False
    if inserted == len(events):
        return True
    if 0 < inserted < len(events):
        _release_injected_keys(user32, inserted)
    return False
