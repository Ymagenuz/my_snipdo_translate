"""Gentle motion: scroll views that glide and widgets that fade in.

Everything follows Windows' "Animation effects" setting (Settings >
Accessibility > Visual effects). With it off, views jump and widgets appear
at once, exactly as they did before.
"""
from __future__ import annotations

import ctypes
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from functools import lru_cache
from types import SimpleNamespace
from typing import Callable

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QApplication,
    QGraphicsOpacityEffect,
    QWidget,
)

# Short enough to feel immediate, long enough for the eye to follow.
SCROLL_DURATION_MS = 180
# Fine-grained wheels and touchpads send many small steps; a shorter glide
# keeps the view close to the finger.
FINE_SCROLL_DURATION_MS = 90
# Jumps to a far-away place, such as a located sentence, travel further.
GLIDE_DURATION_MS = 320
FADE_DURATION_MS = 160
# Frames keep coming at least this often if a monitor stops reporting
# refreshes, for example while its panel self-refreshes or wakes up.
FRAME_WATCHDOG_MS = 50

_SPI_GETCLIENTAREAANIMATION = 0x1042
_MONITOR_DEFAULTTONEAREST = 2
_WHEEL_NOTCH = 120

_SliderAction = QAbstractSlider.SliderAction
# Steps glide by the distance they used to jump; edges glide to the end.
_STEP_ACTIONS = frozenset(action.value for action in (
    _SliderAction.SliderSingleStepAdd,
    _SliderAction.SliderSingleStepSub,
    _SliderAction.SliderPageStepAdd,
    _SliderAction.SliderPageStepSub,
))
_EDGE_ACTIONS = frozenset(action.value for action in (
    _SliderAction.SliderToMinimum,
    _SliderAction.SliderToMaximum,
))


def animations_enabled() -> bool:
    """Whether Windows' "Animation effects" setting is on; True elsewhere."""
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return True
    enabled = ctypes.c_int(1)
    if not user32.SystemParametersInfoW(
        _SPI_GETCLIENTAREAANIMATION, 0, ctypes.pointer(enabled), 0
    ):
        return True
    return bool(enabled.value)


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _OpenAdapterFromHdc(ctypes.Structure):
    _fields_ = [
        ("hDc", wintypes.HDC),
        ("hAdapter", wintypes.UINT),
        ("AdapterLuid", _Luid),
        ("VidPnSourceId", wintypes.UINT),
    ]


class _WaitForVerticalBlank(ctypes.Structure):
    _fields_ = [
        ("hAdapter", wintypes.UINT),
        ("hDevice", wintypes.UINT),
        ("VidPnSourceId", wintypes.UINT),
    ]


class _CloseAdapter(ctypes.Structure):
    _fields_ = [("hAdapter", wintypes.UINT)]


@lru_cache(maxsize=1)
def _win32() -> SimpleNamespace | None:
    """The Windows calls vertical-blank waits need, or None elsewhere."""
    try:
        # Own library handles, so these prototypes don't change anyone else's.
        user32 = ctypes.WinDLL("user32")
        gdi32 = ctypes.WinDLL("gdi32")
        calls = SimpleNamespace(
            MonitorFromWindow=user32.MonitorFromWindow,
            GetMonitorInfoW=user32.GetMonitorInfoW,
            CreateDCW=gdi32.CreateDCW,
            DeleteDC=gdi32.DeleteDC,
            OpenAdapterFromHdc=gdi32.D3DKMTOpenAdapterFromHdc,
            WaitForVerticalBlankEvent=gdi32.D3DKMTWaitForVerticalBlankEvent,
            CloseAdapter=gdi32.D3DKMTCloseAdapter,
        )
    except (AttributeError, OSError):
        return None
    calls.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    calls.MonitorFromWindow.restype = wintypes.HMONITOR
    calls.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    calls.GetMonitorInfoW.restype = wintypes.BOOL
    calls.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    calls.CreateDCW.restype = wintypes.HDC
    calls.DeleteDC.argtypes = [wintypes.HDC]
    calls.DeleteDC.restype = wintypes.BOOL
    for call, argument in (
        (calls.OpenAdapterFromHdc, _OpenAdapterFromHdc),
        (calls.WaitForVerticalBlankEvent, _WaitForVerticalBlank),
        (calls.CloseAdapter, _CloseAdapter),
    ):
        call.argtypes = [ctypes.POINTER(argument)]
        call.restype = wintypes.LONG  # NTSTATUS
    return calls


def _display_device(widget: QWidget) -> str | None:
    """The device name of the monitor showing widget, for vertical-blank waits."""
    win32 = _win32()
    if win32 is None or QGuiApplication.platformName() != "windows":
        return None
    monitor = win32.MonitorFromWindow(int(widget.window().winId()), _MONITOR_DEFAULTTONEAREST)
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    if not monitor or not win32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info.szDevice


class _VBlankWaiter:
    """Blocks until the next vertical blank of one monitor."""

    def __init__(self, device: str):
        win32 = _win32()
        hdc = win32.CreateDCW(device, None, None, None)
        if not hdc:
            raise OSError(f"CreateDC failed for {device}")
        request = _OpenAdapterFromHdc(hDc=hdc)
        try:
            status = win32.OpenAdapterFromHdc(ctypes.byref(request))
        finally:
            win32.DeleteDC(hdc)
        if status:
            raise OSError(f"D3DKMTOpenAdapterFromHdc failed: {status & 0xFFFFFFFF:#x}")
        self.device = device
        self._adapter = request.hAdapter
        self._wait = _WaitForVerticalBlank(request.hAdapter, 0, request.VidPnSourceId)

    def wait(self) -> None:
        status = _win32().WaitForVerticalBlankEvent(ctypes.byref(self._wait))
        if status:
            raise OSError(f"D3DKMTWaitForVerticalBlankEvent failed: {status & 0xFFFFFFFF:#x}")

    def close(self) -> None:
        _win32().CloseAdapter(ctypes.byref(_CloseAdapter(self._adapter)))


class FrameClock(QObject):
    """Calls listeners once per refresh of the monitor they are shown on.

    Qt's own animations step on a fixed 16 ms timer that runs out of step with
    the screen: on a 100 Hz or 144 Hz monitor each step stays up for an uneven
    number of refreshes, and motion judders. On Windows a background thread
    waits for the monitor's vertical blank instead, so every refresh gets
    exactly one new frame. Elsewhere, or if waiting fails, a timer runs at the
    screen's refresh rate. Listeners get the frame's time.perf_counter().
    """

    _vblank = pyqtSignal(float)
    _vblank_failed = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._listeners: dict[Callable[[float], None], None] = {}
        self._device: str | None = None
        self._wanted = threading.Event()
        self._thread: threading.Thread | None = None
        self._vblank_usable = True
        # One queued frame at a time, so a busy UI thread never falls behind.
        self._frame_pending = False
        self._interval_ms = 16
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(FRAME_WATCHDOG_MS)
        self._watchdog.timeout.connect(self._on_timer)
        self._vblank.connect(self._on_vblank)
        self._vblank_failed.connect(self._on_vblank_failed)

    def uses_vblank(self) -> bool:
        return self._wanted.is_set()

    def subscribe(self, listener: Callable[[float], None], widget: QWidget) -> None:
        """Call listener every refresh of the monitor showing widget."""
        self._listeners[listener] = None
        screen = widget.screen()
        rate = screen.refreshRate() if screen is not None else 0
        self._interval_ms = max(1, round(1000 / rate)) if rate > 0 else 16
        device = _display_device(widget) if self._vblank_usable else None
        if device is None:
            if not self._wanted.is_set() and not self._timer.isActive():
                self._timer.start(self._interval_ms)
            return
        self._device = device
        self._timer.stop()
        self._watchdog.start()
        self._wanted.set()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._wait_for_vblanks, name="vblank", daemon=True
            )
            self._thread.start()

    def unsubscribe(self, listener: Callable[[float], None]) -> None:
        self._listeners.pop(listener, None)
        if not self._listeners:
            self._go_idle()

    def forget(self, listener: Callable[[float], None]) -> None:
        """Drop a listener whose view is being destroyed; the next frame idles."""
        self._listeners.pop(listener, None)

    def _go_idle(self) -> None:
        self._wanted.clear()
        self._timer.stop()
        self._watchdog.stop()

    def _tick(self, now: float) -> None:
        if not self._listeners:
            self._go_idle()
            return
        for listener in list(self._listeners):
            listener(now)

    def _on_timer(self) -> None:
        self._tick(time.perf_counter())

    def _on_vblank(self, now: float) -> None:
        self._frame_pending = False
        self._watchdog.start()
        self._tick(now)

    def _on_vblank_failed(self) -> None:
        # Stay on the timer for the rest of the session.
        self._vblank_usable = False
        self._wanted.clear()
        self._watchdog.stop()
        if self._listeners:
            self._timer.start(self._interval_ms)

    def _wait_for_vblanks(self) -> None:
        """Background thread: report each vertical blank while anyone listens."""
        waiter = None
        while True:
            self._wanted.wait()
            try:
                if waiter is None or waiter.device != self._device:
                    if waiter is not None:
                        waiter.close()
                        waiter = None
                    waiter = _VBlankWaiter(self._device)
                waiter.wait()
            except OSError:
                if waiter is not None:
                    waiter.close()
                self._emit("_vblank_failed")
                return
            if self._wanted.is_set() and not self._frame_pending:
                self._frame_pending = True
                if not self._emit("_vblank", time.perf_counter()):
                    return

    def _emit(self, name: str, *args) -> bool:
        # Looked up here: once Qt has deleted the clock at quit, even reading
        # the signal raises, and it must not escape the thread.
        try:
            getattr(self, name).emit(*args)
        except RuntimeError:  # The app is closing.
            return False
        return True


_frame_clock: FrameClock | None = None


def frame_clock() -> FrameClock:
    global _frame_clock
    if _frame_clock is None:
        _frame_clock = FrameClock(QApplication.instance())
    return _frame_clock


def _forget_frames(listener: Callable[[float], None]) -> None:
    if _frame_clock is not None:
        _frame_clock.forget(listener)


class Tween(QObject):
    """A number that eases toward its target, one step per screen refresh.

    changed(value) runs on every step, typically to repaint. With animations
    off or the widget hidden, the value jumps straight to its target.
    """

    def __init__(
        self,
        widget: QWidget,
        changed: Callable[[float], None],
        value: float = 0.0,
    ):
        super().__init__(widget)
        self._widget = widget
        self._changed = changed
        self.value = value
        self.target = value
        self._start = value
        self._started_at = 0.0
        self._duration = 0.0
        self._running = False
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)
        on_frame = self._on_frame
        self.destroyed.connect(lambda _object=None: _forget_frames(on_frame))

    def is_running(self) -> bool:
        return self._running

    def to(self, target: float, duration: int) -> None:
        if target == self.target:
            return
        self.target = target
        if not (self._widget.isVisible() and animations_enabled()):
            self._stop()
            self._set(target)
            return
        self._start = self.value
        self._started_at = time.perf_counter()
        self._duration = duration / 1000
        if not self._running:
            self._running = True
            frame_clock().subscribe(self._on_frame, self._widget)

    def _on_frame(self, now: float) -> None:
        progress = min(1.0, max(0.0, (now - self._started_at) / self._duration))
        eased = self._easing.valueForProgress(progress)
        self._set(self._start + (self.target - self._start) * eased)
        if progress >= 1.0:
            self._stop()

    def _stop(self) -> None:
        if self._running:
            self._running = False
            frame_clock().unsubscribe(self._on_frame)

    def _set(self, value: float) -> None:
        self.value = value
        self._changed(value)


class SmoothScroller(QObject):
    """Glides a scroll area's vertical scroll bar instead of jumping it.

    Mouse wheel notches, clicks in the scroll bar's track, and the keys a
    read-only view scrolls with (Page Up/Down, Space, Home/End, arrows) glide
    over the same distance they used to jump. Dragging the handle, wheel
    turns with a modifier key (zoom, sideways scrolling) and pixel-precise
    touchpads keep Qt's own behaviour. Whatever else moves the view while it
    glides, such as new content, the text cursor or a drag, takes over.
    """

    def __init__(self, area: QAbstractScrollArea):
        super().__init__(area)
        self._area = area
        self._bar = area.verticalScrollBar()
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)
        self._gliding = False
        self._start = 0.0
        self._target = 0.0
        self._started_at = 0.0
        self._duration = 0.0
        self._last_value = 0
        area.viewport().installEventFilter(self)
        # Wheel turns over the scroll bar itself skip the viewport.
        self._bar.installEventFilter(self)
        self._bar.actionTriggered.connect(self._on_action)
        self._bar.sliderPressed.connect(self.stop)
        on_frame = self._on_frame
        self.destroyed.connect(lambda _object=None: _forget_frames(on_frame))

    def is_scrolling(self) -> bool:
        return self._gliding

    def stop(self) -> None:
        if self._gliding:
            self._gliding = False
            frame_clock().unsubscribe(self._on_frame)

    def scroll_to(self, value: float, duration: int = SCROLL_DURATION_MS) -> None:
        """Glide to value; jump there if animations are off or the view is hidden."""
        bar = self._bar
        target = float(max(bar.minimum(), min(bar.maximum(), value)))
        if not self._should_animate():
            self.stop()
            bar.setValue(round(target))
            return
        self._start = float(bar.value())
        self._target = target
        self._started_at = time.perf_counter()
        self._duration = duration / 1000
        self._last_value = bar.value()
        if not self._gliding:
            self._gliding = True
            frame_clock().subscribe(self._on_frame, self._area)

    @contextmanager
    def glide(self, duration: int = GLIDE_DURATION_MS):
        """Glide to wherever the code in the block jumps the view."""
        self.stop()
        start = self._bar.value()
        yield
        target = self._bar.value()
        if target != start and self._should_animate():
            self._bar.setValue(start)
            self.scroll_to(target, duration)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and self._glide_wheel(event):
            return True
        return super().eventFilter(watched, event)

    def _should_animate(self) -> bool:
        return self._area.isVisible() and animations_enabled()

    def _glide_wheel(self, event) -> bool:
        angle = event.angleDelta()
        delta = angle.y()
        if (
            delta == 0
            or abs(angle.x()) > abs(delta)
            or not event.pixelDelta().isNull()
            or event.modifiers() != Qt.KeyboardModifier.NoModifier
            or not self._should_animate()
        ):
            return False

        bar = self._bar
        gliding = self.is_scrolling()
        at_edge = bar.value() <= bar.minimum() if delta > 0 else bar.value() >= bar.maximum()
        if at_edge and not gliding:
            # Nothing to scroll here: let the parent have the wheel, as Qt does.
            return False

        # The distance Qt scrolls per notch, so only the motion changes.
        lines = max(1, QApplication.wheelScrollLines())
        step = min(lines * bar.singleStep(), bar.pageStep())
        base = self._target if gliding else float(bar.value())
        duration = (
            SCROLL_DURATION_MS if abs(delta) >= _WHEEL_NOTCH else FINE_SCROLL_DURATION_MS
        )
        self.scroll_to(base - delta * step / _WHEEL_NOTCH, duration)
        return True

    def _on_action(self, action: int) -> None:
        if (
            (action not in _STEP_ACTIONS and action not in _EDGE_ACTIONS)
            or self._keys_move_text_cursor()
            or not self._should_animate()
        ):
            # Drags, wheel turns Qt handles itself and keys that also move a
            # text cursor, which the view must follow, move the view at once.
            self.stop()
            return

        # Qt has moved the slider but not the view yet: glide the view there.
        bar = self._bar
        target = float(bar.sliderPosition())
        if action in _STEP_ACTIONS and self.is_scrolling():
            target += self._target - bar.value()
        bar.setSliderPosition(bar.value())
        duration = GLIDE_DURATION_MS if action in _EDGE_ACTIONS else SCROLL_DURATION_MS
        self.scroll_to(target, duration)

    def _keys_move_text_cursor(self) -> bool:
        """Whether this action comes from a key in editable text."""
        read_only = getattr(self._area, "isReadOnly", None)
        return (
            read_only is not None
            and not read_only()
            and QApplication.mouseButtons() == Qt.MouseButton.NoButton
        )

    def _on_frame(self, now: float) -> None:
        bar = self._bar
        if bar.value() != self._last_value:
            # Something else moved the view meanwhile; it wins.
            self.stop()
            return
        progress = min(1.0, max(0.0, (now - self._started_at) / self._duration))
        eased = self._easing.valueForProgress(progress)
        bar.setValue(round(self._start + (self._target - self._start) * eased))
        self._last_value = bar.value()
        if progress >= 1.0:
            self.stop()


def fade_opacity(
    effect: QGraphicsOpacityEffect,
    opacity: float,
    *,
    duration: int = FADE_DURATION_MS,
    finished: Callable[[], None] | None = None,
) -> None:
    """Fade an opacity effect to opacity, replacing any fade still running on it."""
    for running in effect.findChildren(QPropertyAnimation):
        running.stop()
    if not animations_enabled():
        effect.setOpacity(opacity)
        if finished is not None:
            finished()
        return
    animation = QPropertyAnimation(effect, b"opacity", effect)
    animation.setDuration(duration)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.setEndValue(opacity)
    if finished is not None:
        animation.finished.connect(finished)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def fade_in(widget: QWidget, duration: int = FADE_DURATION_MS) -> None:
    """Let a widget that just appeared fade in instead of popping up."""
    if not (widget.isVisible() and animations_enabled()):
        return
    effect = widget.graphicsEffect()
    if effect is None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    elif not isinstance(effect, QGraphicsOpacityEffect):
        # Leave other effects, such as shadows, alone.
        return
    effect.setOpacity(0.0)
    effect.setEnabled(True)
    # Once opaque, the widget paints directly again instead of through the effect.
    fade_opacity(
        effect, 1.0, duration=duration, finished=lambda: effect.setEnabled(False)
    )
