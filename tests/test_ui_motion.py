import ctypes
import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QLabel,
    QTextEdit,
)

import ui_motion
from ui_motion import SmoothScroller, Tween, fade_in, fade_opacity


# conftest replaces it for every test; keep the real one to test it.
_windows_animations_enabled = ui_motion.animations_enabled


def _text_view(qapp, *, read_only=True):
    view = QTextEdit()
    view.setReadOnly(read_only)
    view.setPlainText("\n".join(f"Line {number}" for number in range(400)))
    view.resize(300, 200)
    scroller = SmoothScroller(view)
    view.show()
    qapp.processEvents()
    return view, scroller


@pytest.fixture
def view(qapp):
    view, scroller = _text_view(qapp)
    yield view, scroller
    view.deleteLater()
    qapp.processEvents()


def _wheel(view, delta, modifiers=Qt.KeyboardModifier.NoModifier):
    viewport = view.viewport()
    event = QWheelEvent(
        QPointF(10, 10), QPointF(viewport.mapToGlobal(QPoint(10, 10))),
        QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
        modifiers, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(viewport, event)
    return event.isAccepted()


def _notch(bar):
    """How far Qt scrolls for one wheel notch."""
    return min(QApplication.wheelScrollLines() * bar.singleStep(), bar.pageStep())


def test_wheel_glides_as_far_as_qt_jumps(view, motion_on, wait_until):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()

    assert _wheel(text_view, -120)

    assert bar.value() == 0
    assert scroller.is_scrolling()
    wait_until(lambda: not scroller.is_scrolling())
    assert bar.value() == _notch(bar)


def test_quick_wheel_turns_add_up(view, motion_on, wait_until):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()

    for _turn in range(3):
        _wheel(text_view, -120)
    wait_until(lambda: not scroller.is_scrolling())
    assert bar.value() == 3 * _notch(bar)

    _wheel(text_view, 120)
    wait_until(lambda: not scroller.is_scrolling())
    assert bar.value() == 2 * _notch(bar)


def test_wheel_jumps_when_windows_animations_are_off(view):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()

    _wheel(text_view, -120)

    assert not scroller.is_scrolling()
    assert bar.value() == _notch(bar)


def test_wheel_at_the_edge_or_with_a_modifier_is_left_to_qt(view, motion_on):
    text_view, scroller = view

    # Nothing above the top: the wheel goes on to the parent, as before.
    assert not _wheel(text_view, 120)
    assert not scroller.is_scrolling()

    _wheel(text_view, -120, Qt.KeyboardModifier.ControlModifier)
    assert not scroller.is_scrolling()


def test_keys_glide_in_read_only_text(view, motion_on, wait_until):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()
    text_view.setFocus()

    QTest.keyClick(text_view, Qt.Key.Key_PageDown)
    assert bar.value() == 0
    assert scroller.is_scrolling()
    wait_until(lambda: not scroller.is_scrolling())
    assert bar.value() == bar.pageStep()

    QTest.keyClick(text_view, Qt.Key.Key_End)
    assert scroller.is_scrolling()
    wait_until(lambda: not scroller.is_scrolling())
    assert bar.value() == bar.maximum()


def test_keys_in_editable_text_still_follow_the_cursor_at_once(qapp, motion_on):
    text_view, scroller = _text_view(qapp, read_only=False)
    try:
        bar = text_view.verticalScrollBar()
        text_view.setFocus()

        QTest.keyClick(text_view, Qt.Key.Key_PageDown)

        assert not scroller.is_scrolling()
        assert bar.value() > 0
        assert text_view.viewport().rect().contains(text_view.cursorRect().center())
    finally:
        text_view.deleteLater()
        qapp.processEvents()


def test_anything_else_moving_the_view_takes_over(view, motion_on):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()
    for _turn in range(3):
        _wheel(text_view, -120)

    bar.setValue(7)
    QTest.qWait(ui_motion.SCROLL_DURATION_MS + 100)

    assert not scroller.is_scrolling()
    assert bar.value() == 7


def test_glide_travels_to_where_the_block_jumped(view, motion_on, wait_until):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()

    with scroller.glide():
        bar.setValue(1000)

    assert bar.value() == 0
    wait_until(lambda: not scroller.is_scrolling())
    assert bar.value() == 1000


def test_glide_jumps_without_animations(view):
    text_view, scroller = view
    bar = text_view.verticalScrollBar()

    with scroller.glide():
        bar.setValue(1000)

    assert not scroller.is_scrolling()
    assert bar.value() == 1000


def test_hidden_views_jump(view, motion_on):
    text_view, scroller = view
    text_view.hide()

    scroller.scroll_to(100)

    assert not scroller.is_scrolling()
    assert text_view.verticalScrollBar().value() == 100


def test_a_view_destroyed_mid_glide_leaves_the_clock_idle(motion_on, qapp):
    text_view, scroller = _text_view(qapp)
    _wheel(text_view, -120)
    assert scroller.is_scrolling()

    sip.delete(text_view)
    QTest.qWait(100)

    clock = ui_motion.frame_clock()
    assert not clock._timer.isActive()
    assert not clock.uses_vblank()


class _FakeWaiter:
    """Stands in for a monitor: a vertical blank every few milliseconds."""

    opened = []

    def __init__(self, device):
        self.device = device
        self.opened.append(device)

    def wait(self):
        time.sleep(0.005)

    def close(self):
        return None


@pytest.fixture
def clock(qapp, monkeypatch):
    _FakeWaiter.opened = []
    monkeypatch.setattr(ui_motion, "_VBlankWaiter", _FakeWaiter)
    monkeypatch.setattr(ui_motion, "_display_device", lambda _widget: "DISPLAY2")
    return ui_motion.FrameClock(qapp)


def test_frame_clock_ticks_on_each_vertical_blank(clock, wait_until):
    frames = []

    def on_frame(now):
        frames.append(now)

    clock.subscribe(on_frame, QLabel())
    assert clock.uses_vblank()
    wait_until(lambda: len(frames) >= 5)
    assert frames == sorted(frames)
    assert _FakeWaiter.opened == ["DISPLAY2"]

    clock.unsubscribe(on_frame)
    seen = len(frames)
    QTest.qWait(50)
    assert len(frames) == seen
    assert not clock.uses_vblank()


def test_frame_clock_falls_back_to_a_timer_if_waiting_fails(
    clock, monkeypatch, wait_until
):
    class BrokenWaiter(_FakeWaiter):
        def wait(self):
            raise OSError("no vertical blanks here")

    monkeypatch.setattr(ui_motion, "_VBlankWaiter", BrokenWaiter)
    frames = []

    def on_frame(now):
        frames.append(now)

    clock.subscribe(on_frame, QLabel())
    wait_until(lambda: len(frames) >= 3)

    assert not clock.uses_vblank()
    assert clock._timer.isActive()
    clock.unsubscribe(on_frame)
    assert not clock._timer.isActive()


def test_frames_keep_coming_when_a_monitor_stops_reporting_refreshes(
    clock, monkeypatch, wait_until
):
    release = threading.Event()

    class StalledWaiter(_FakeWaiter):
        def wait(self):
            release.wait()

    monkeypatch.setattr(ui_motion, "_VBlankWaiter", StalledWaiter)
    frames = []

    def on_frame(now):
        frames.append(now)

    clock.subscribe(on_frame, QLabel())
    try:
        wait_until(lambda: len(frames) >= 2, timeout_ms=1000)
        assert clock.uses_vblank()
    finally:
        clock.unsubscribe(on_frame)
        release.set()


def test_without_vertical_blanks_the_timer_follows_the_refresh_rate(qapp, monkeypatch):
    monkeypatch.setattr(ui_motion, "_display_device", lambda _widget: None)
    clock = ui_motion.FrameClock(qapp)
    monitor = SimpleNamespace(refreshRate=lambda: 144.0)
    widget = SimpleNamespace(screen=lambda: monitor)

    def on_frame(_now):
        return None

    clock.subscribe(on_frame, widget)
    try:
        assert not clock.uses_vblank()
        assert clock._timer.interval() == 7
    finally:
        clock.unsubscribe(on_frame)


def test_vertical_blanks_need_the_windows_platform(qapp):
    # The tests run on Qt's offscreen platform, which has no monitors to wait on.
    assert ui_motion._display_device(QLabel()) is None


def test_tween_eases_to_its_target_frame_by_frame(qapp, motion_on, wait_until):
    widget = QLabel()
    widget.show()
    values = []
    try:
        tween = Tween(widget, values.append)
        tween.to(1.0, 120)
        assert tween.is_running()
        assert values == []
        wait_until(lambda: not tween.is_running())

        assert len(values) > 2
        assert values == sorted(values)
        assert values[-1] == 1.0
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_tween_jumps_without_animations_or_when_hidden(qapp, motion_on, monkeypatch):
    hidden = QLabel()
    values = []
    tween = Tween(hidden, values.append)

    tween.to(1.0, 120)
    assert values == [1.0]
    assert not tween.is_running()

    monkeypatch.setattr(ui_motion, "animations_enabled", lambda: False)
    hidden.show()
    tween.to(0.0, 120)
    assert values == [1.0, 0.0]
    hidden.deleteLater()
    qapp.processEvents()


def test_fade_in_ends_painting_the_widget_directly(qapp, motion_on, wait_until):
    label = QLabel("Locate in translation")
    label.show()
    try:
        fade_in(label)

        effect = label.graphicsEffect()
        assert isinstance(effect, QGraphicsOpacityEffect)
        assert effect.isEnabled()
        assert effect.opacity() < 1
        wait_until(lambda: not effect.isEnabled())
        assert effect.opacity() == 1
    finally:
        label.deleteLater()
        qapp.processEvents()


def test_fade_in_leaves_other_effects_and_hidden_widgets_alone(qapp, motion_on):
    shadowed = QLabel("Card")
    shadow = QGraphicsDropShadowEffect()
    shadowed.setGraphicsEffect(shadow)
    shadowed.show()
    hidden = QLabel("Hidden")
    try:
        fade_in(shadowed)
        fade_in(hidden)

        assert shadowed.graphicsEffect() is shadow
        assert hidden.graphicsEffect() is None
    finally:
        shadowed.deleteLater()
        hidden.deleteLater()
        qapp.processEvents()


def test_fades_finish_at_once_without_animations(qapp):
    label = QLabel("Result")
    label.show()
    finished = []
    try:
        fade_in(label)
        assert label.graphicsEffect() is None

        effect = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effect)
        fade_opacity(effect, 0.45, finished=lambda: finished.append(True))
        assert effect.opacity() == pytest.approx(0.45)
        assert finished == [True]
    finally:
        label.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    ("setting", "read", "expected"),
    [(1, True, True), (0, True, False), (0, False, True)],
    ids=["on", "off", "unreadable"],
)
def test_animations_follow_windows_animation_effects(
    monkeypatch, setting, read, expected
):
    actions = []

    def system_parameters_info(action, _param, pointer, _flags):
        actions.append(action)
        pointer.contents.value = setting
        return read

    monkeypatch.setattr(ui_motion, "ctypes", SimpleNamespace(
        windll=SimpleNamespace(
            user32=SimpleNamespace(SystemParametersInfoW=system_parameters_info)
        ),
        c_int=ctypes.c_int,
        pointer=ctypes.pointer,
    ))

    assert _windows_animations_enabled() is expected
    assert actions == [0x1042]  # SPI_GETCLIENTAREAANIMATION


def test_animations_are_on_without_windows(monkeypatch):
    monkeypatch.setattr(ui_motion, "ctypes", SimpleNamespace())

    assert _windows_animations_enabled() is True
