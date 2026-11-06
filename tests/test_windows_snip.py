from __future__ import annotations

import pytest

import windows_snip as snip
from windows_snip import SnipOutcome, SnipWatch


def _watch_until_done(watch, steps):
    """Feed (seconds, sequence, has_image, overlay_open) looks; return the outcome."""
    for now, sequence, has_image, overlay_open in steps:
        outcome = watch.update(
            now,
            clipboard_sequence=sequence,
            clipboard_has_image=has_image,
            overlay_open=overlay_open,
        )
        if outcome is not None:
            return now, outcome
    return None


def test_a_snip_on_the_clipboard_is_captured_while_the_overlay_is_open():
    watch = SnipWatch(clipboard_sequence=7, started=0.0)

    assert _watch_until_done(watch, [
        (0.1, 7, False, False),
        (0.4, 7, False, True),
        (3.0, 8, True, True),
    ]) == (3.0, SnipOutcome.CAPTURED)


def test_a_snip_arriving_just_after_the_overlay_closes_is_still_captured():
    watch = SnipWatch(clipboard_sequence=7, started=0.0)

    assert _watch_until_done(watch, [
        (0.4, 7, False, True),
        (2.0, 7, False, False),
        (2.6, 8, True, False),
    ]) == (2.6, SnipOutcome.CAPTURED)


def test_closing_the_overlay_without_a_snip_cancels_after_the_clipboard_settles():
    watch = SnipWatch(clipboard_sequence=7, started=0.0)

    now, outcome = _watch_until_done(watch, [
        (0.4, 7, False, True),
        (2.0, 7, False, False),
        (2.0 + snip.CLIPBOARD_SETTLE_SECONDS - 0.1, 7, False, False),
        (2.0 + snip.CLIPBOARD_SETTLE_SECONDS, 7, False, False),
    ])

    assert outcome is SnipOutcome.CANCELLED
    assert now == 2.0 + snip.CLIPBOARD_SETTLE_SECONDS


def test_text_copied_to_the_clipboard_is_not_a_snip():
    watch = SnipWatch(clipboard_sequence=7, started=0.0)

    assert watch.update(
        1.0, clipboard_sequence=8, clipboard_has_image=False, overlay_open=True
    ) is None


def test_an_overlay_that_never_opens_is_unavailable():
    watch = SnipWatch(clipboard_sequence=7, started=0.0)

    assert watch.update(
        snip.OVERLAY_OPEN_TIMEOUT_SECONDS - 0.1,
        clipboard_sequence=7, clipboard_has_image=False, overlay_open=False,
    ) is None
    assert watch.update(
        snip.OVERLAY_OPEN_TIMEOUT_SECONDS,
        clipboard_sequence=7, clipboard_has_image=False, overlay_open=False,
    ) is SnipOutcome.UNAVAILABLE


def test_an_overlay_left_open_stops_being_watched():
    watch = SnipWatch(clipboard_sequence=7, started=0.0)
    watch.update(0.4, clipboard_sequence=7, clipboard_has_image=False, overlay_open=True)

    assert watch.update(
        snip.SNIP_TIMEOUT_SECONDS,
        clipboard_sequence=7, clipboard_has_image=False, overlay_open=True,
    ) is SnipOutcome.CANCELLED


def test_open_snip_overlay_launches_the_snipping_link():
    launched = []

    assert snip.open_snip_overlay(start=launched.append) is True
    assert launched == ["ms-screenclip:"]


def test_open_snip_overlay_reports_a_missing_handler():
    def missing(_uri):
        raise OSError("no application is associated")

    assert snip.open_snip_overlay(start=missing) is False


class _User32:
    def __init__(self, foreground):
        self.foreground = foreground
        self.windows = {}

    def GetForegroundWindow(self):
        return self.foreground

    def IsWindow(self, hwnd):
        return hwnd in self.windows

    def IsWindowVisible(self, hwnd):
        return self.windows.get(hwnd, False)


@pytest.mark.parametrize("process", ["snippingtool.exe", "screenclippinghost.exe"])
def test_tracker_follows_the_overlay_until_it_closes(process):
    user32 = _User32(foreground=10)
    names = {10: "claude.exe", 20: process}
    tracker = snip.SnipOverlayTracker(user32_api=user32, process_name=names.get)

    assert tracker.is_open() is False
    user32.foreground = 20
    user32.windows[20] = True
    assert tracker.is_open() is True
    # Focus moving elsewhere doesn't close it; the window going away does.
    user32.foreground = 10
    assert tracker.is_open() is True
    del user32.windows[20]
    assert tracker.is_open() is False


def test_tracker_ignores_snipping_tool_already_in_front():
    user32 = _User32(foreground=30)
    user32.windows[30] = True
    tracker = snip.SnipOverlayTracker(
        user32_api=user32, process_name={30: "snippingtool.exe"}.get
    )

    assert tracker.is_open() is False


def test_window_process_name_is_empty_without_a_window():
    assert snip.window_process_name(0) == ""
