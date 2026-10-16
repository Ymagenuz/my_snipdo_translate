from __future__ import annotations

import ctypes

import pytest

import windows_selection as selection


class _User32Harness:
    def __init__(self, *, key_states=None, send_results=()):
        self.key_states = key_states or {}
        self.send_results = list(send_results)
        self.queries = []
        self.batches = []

    def GetAsyncKeyState(self, key):
        self.queries.append(key)
        state = self.key_states.get(key, 0)
        if isinstance(state, Exception):
            raise state
        return state

    def SendInput(self, count, events, size):
        assert isinstance(events, ctypes.Array)
        assert events._type_ is selection.INPUT
        assert count == len(events)
        assert size == ctypes.sizeof(selection.INPUT)
        self.batches.append(
            [
                (event.type, event.ki.wVk, event.ki.dwFlags,
                 event.ki.wScan, event.ki.time, event.ki.dwExtraInfo)
                for event in events
            ]
        )
        result = self.send_results.pop(0) if self.send_results else count
        if isinstance(result, Exception):
            raise result
        return result


COPY_KEYS = (
    selection.VK_CONTROL,
    selection.VK_MENU,
    selection.VK_SHIFT,
    selection.VK_LWIN,
    selection.VK_RWIN,
    selection.VK_C,
)


@pytest.mark.parametrize("key", COPY_KEYS)
@pytest.mark.parametrize("state", [0x8000, -32768, 0x8001])
def test_held_modifier_or_c_prevents_copy(key, state):
    user32 = _User32Harness(key_states={key: state})

    assert not selection.copy_keys_released(user32_api=user32)
    assert not selection.send_ctrl_c(user32_api=user32)
    assert user32.batches == []


def test_recent_press_bit_does_not_block_copy():
    user32 = _User32Harness(key_states={key: 1 for key in COPY_KEYS})

    assert selection.copy_keys_released(user32_api=user32)
    assert selection.send_ctrl_c(user32_api=user32)
    assert len(user32.batches) == 1


def test_held_trigger_blocks_until_released():
    trigger = 0x54
    user32 = _User32Harness(key_states={trigger: 0x8000})

    assert not selection.copy_keys_released(trigger, user32_api=user32)
    user32.key_states[trigger] = 1
    assert selection.copy_keys_released(trigger, user32_api=user32)


@pytest.mark.parametrize("trigger", [None, *COPY_KEYS])
def test_modifier_and_c_trigger_are_queried_once(trigger):
    user32 = _User32Harness()

    assert selection.copy_keys_released(trigger, user32_api=user32)
    assert user32.queries == list(COPY_KEYS)


@pytest.mark.parametrize("key", [*COPY_KEYS, 0x54])
def test_state_query_error_fails_closed(key):
    user32 = _User32Harness(key_states={key: OSError("query unavailable")})

    assert not selection.copy_keys_released(0x54, user32_api=user32)
    if key in COPY_KEYS:
        assert not selection.send_ctrl_c(user32_api=user32)
    assert user32.batches == []


def test_missing_backend_fails_closed(monkeypatch):
    monkeypatch.setattr(selection, "_USER32", None)

    assert not selection.copy_keys_released()
    assert not selection.send_ctrl_c()


def test_copy_is_one_batch_with_balanced_key_order(monkeypatch):
    user32 = _User32Harness()
    monkeypatch.setattr(selection, "_USER32", user32)

    assert selection.send_ctrl_c()
    assert user32.queries == list(COPY_KEYS)
    assert user32.batches == [[
        (1, 0x11, 0, 0, 0, 0),
        (1, 0x43, 0, 0, 0, 0),
        (1, 0x43, 2, 0, 0, 0),
        (1, 0x11, 2, 0, 0, 0),
    ]]


def test_input_has_full_win32_union_and_native_abi():
    is_64_bit = ctypes.sizeof(ctypes.c_void_p) == 8

    assert ctypes.sizeof(selection.INPUT) == (40 if is_64_bit else 28)
    assert selection.INPUT.data.offset == (8 if is_64_bit else 4)
    assert ctypes.sizeof(selection.MOUSEINPUT) == (32 if is_64_bit else 24)
    assert ctypes.sizeof(selection.KEYBDINPUT) == (24 if is_64_bit else 16)
    assert ctypes.sizeof(selection.HARDWAREINPUT) == 8
    assert selection.KEYBDINPUT.dwExtraInfo.offset == (16 if is_64_bit else 12)
    assert selection.MOUSEINPUT.dwExtraInfo.offset == (24 if is_64_bit else 20)
    assert selection.INPUT.mi.offset == selection.INPUT.ki.offset
    assert selection.INPUT.mi.offset == selection.INPUT.hi.offset


def test_native_api_signatures_match_win32():
    if selection._USER32 is None:
        pytest.skip("Win32 backend unavailable")
    assert selection._USER32.GetAsyncKeyState.argtypes == [ctypes.c_int]
    assert selection._USER32.GetAsyncKeyState.restype is ctypes.c_short
    assert selection._USER32.SendInput.argtypes == [
        ctypes.c_uint32, ctypes.POINTER(selection.INPUT), ctypes.c_int
    ]
    assert selection._USER32.SendInput.restype is ctypes.c_uint32


def test_zero_insertions_do_not_release_any_keys():
    user32 = _User32Harness(send_results=[0])

    assert not selection.send_ctrl_c(user32_api=user32)
    assert len(user32.batches) == 1


@pytest.mark.parametrize(
    ("inserted", "released"),
    [(1, [0x11]), (2, [0x43, 0x11]), (3, [0x11])],
)
def test_partial_input_releases_only_keys_left_down_by_our_batch(inserted, released):
    user32 = _User32Harness(send_results=[inserted])

    assert not selection.send_ctrl_c(user32_api=user32)
    assert user32.batches[1:] == [
        [(1, key, 2, 0, 0, 0) for key in released]
    ]


def test_partial_cleanup_retries_only_remaining_key_up():
    user32 = _User32Harness(send_results=[2, 1, 1])

    assert not selection.send_ctrl_c(user32_api=user32)
    assert user32.batches[1:] == [
        [(1, 0x43, 2, 0, 0, 0), (1, 0x11, 2, 0, 0, 0)],
        [(1, 0x11, 2, 0, 0, 0)],
    ]


@pytest.mark.parametrize("error", [0, OSError("cleanup blocked")])
def test_failed_cleanup_stops_without_retry_loop(error):
    user32 = _User32Harness(send_results=[2, error])

    assert not selection.send_ctrl_c(user32_api=user32)
    assert len(user32.batches) == 2


def test_send_exception_is_reported_as_failure():
    user32 = _User32Harness(send_results=[OSError("input unavailable")])

    assert not selection.send_ctrl_c(user32_api=user32)
    assert len(user32.batches) == 1
