"""Slim scroll bars and dropdowns, drawn to match the rest of the app.

Qt's Windows 11 style draws scroll bars with arrow buttons and combo boxes
with a boxed arrow button, which look dated beside the app's flat, rounded
controls. These widgets paint those parts themselves, and ease between states
with ui_motion's Tween, so they follow the screen's refresh like the glides do.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QComboBox,
    QScrollBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QWidget,
)

from ui_motion import Tween

ACCENT = QColor("#8E44AD")

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2

# Only sizes the parts Qt uses to hit-test clicks and drags; paintEvent draws.
SCROLL_BAR_STYLE = """
    QScrollBar:vertical {
        background: transparent;
        border: none;
        padding: 0;
        margin: 6px 0;
        width: 12px;
    }
    QScrollBar::handle:vertical {
        background: transparent;
        border: none;
        min-height: 32px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0;
        border: none;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
"""

# The widget Qt keeps a scroll area's bar in. Bare rules such as a text box's
# "border-left: 2px solid ..." reach it too and paint it as a strip of its
# own; clear them so the bar floats over the text box's background instead.
SCROLL_BAR_GUTTER_STYLE = """
    #qt_scrollarea_vcontainer {
        background: transparent;
        border: none;
        padding: 0;
        margin: 0;
    }
"""

# Hands the arrow area to DropdownComboBox, which draws its own chevron.
DROPDOWN_STYLE = """
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 22px;
        border: none;
        background: transparent;
    }
    QComboBox::down-arrow {
        image: none;
        width: 0;
        height: 0;
    }
"""

# Lists that drop down: rounded rows highlighted like the History menu.
POPUP_LIST_STYLE = """
    QAbstractItemView {
        background-color: #FFFFFF;
        color: #303133;
        border: none;
        outline: 0;
        padding: 4px;
    }
    QAbstractItemView::item {
        min-height: 28px;
        padding: 0 10px;
        border: none;
        border-radius: 6px;
    }
    QAbstractItemView::item:hover {
        background-color: #F5F7FA;
        color: #303133;
    }
    QAbstractItemView::item:selected {
        background-color: #F4ECF7;
        color: #8E44AD;
    }
"""


def _mix(start: QColor, end: QColor, amount: float) -> QColor:
    amount = min(1.0, max(0.0, amount))
    return QColor.fromRgbF(
        start.redF() + (end.redF() - start.redF()) * amount,
        start.greenF() + (end.greenF() - start.greenF()) * amount,
        start.blueF() + (end.blueF() - start.blueF()) * amount,
        start.alphaF() + (end.alphaF() - start.alphaF()) * amount,
    )


class ModernScrollBar(QScrollBar):
    """A slim vertical scroll bar that widens smoothly under the pointer.

    At rest the handle is a thin rounded line. While the pointer is over the
    bar, or the handle is dragged, it eases wider and darker over a faint
    track, then settles back a moment after the pointer leaves, as Windows 11
    scroll bars do. The bar itself keeps one width, so text never re-wraps.
    """

    REST_THICKNESS = 4.0
    HOVER_THICKNESS = 8.0
    EXPAND_MS = 150
    COLLAPSE_MS = 240
    COLLAPSE_DELAY_MS = 500
    # Gap between the handle and the bar's outer edge.
    EDGE = 2.0

    REST_COLOR = QColor("#C9CDD4")
    HOVER_COLOR = QColor("#A8ABB2")
    PRESSED_COLOR = QColor("#909399")
    TRACK_COLOR = QColor("#F0F2F5")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setStyleSheet(SCROLL_BAR_STYLE)
        self._expansion = Tween(self, lambda _value: self.update())
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(self.COLLAPSE_DELAY_MS)
        self._collapse_timer.timeout.connect(self._collapse)
        self.sliderReleased.connect(self._collapse_later)

    def expansion(self) -> float:
        """0 at rest, 1 when fully widened."""
        return self._expansion.value

    def thickness(self) -> float:
        return self.REST_THICKNESS + (
            self.HOVER_THICKNESS - self.REST_THICKNESS
        ) * self._expansion.value

    def enterEvent(self, event):
        self._collapse_timer.stop()
        self._expansion.to(1.0, self.EXPAND_MS)
        super().enterEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._collapse_later()

    def _collapse_later(self) -> None:
        if not self.underMouse() and not self.isSliderDown():
            self._collapse_timer.start()

    def _collapse(self) -> None:
        if not self.underMouse() and not self.isSliderDown():
            self._expansion.to(0.0, self.COLLAPSE_MS)

    def paintEvent(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        control = QStyle.ComplexControl.CC_ScrollBar
        groove = style.subControlRect(control, option, QStyle.SubControl.SC_ScrollBarGroove, self)
        handle = style.subControlRect(control, option, QStyle.SubControl.SC_ScrollBarSlider, self)

        expansion = self._expansion.value
        thickness = self.thickness()
        left = self.width() - self.EDGE - thickness
        radius = thickness / 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        if expansion > 0:
            track = QColor(self.TRACK_COLOR)
            track.setAlphaF(expansion)
            painter.setBrush(track)
            painter.drawRoundedRect(
                QRectF(left, groove.top(), thickness, groove.height()), radius, radius
            )
        if self.isSliderDown():
            color = self.PRESSED_COLOR
        else:
            color = _mix(self.REST_COLOR, self.HOVER_COLOR, expansion)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(left, handle.top(), thickness, handle.height()), radius, radius
        )
        painter.end()


# Scroll bars whose Python half must outlive the wrappers that installed them.
_installed_bars: dict[int, ModernScrollBar] = {}


def use_modern_scroll_bar(area: QAbstractScrollArea) -> ModernScrollBar:
    """Give a scroll area a ModernScrollBar that lives as long as the area."""
    bar = ModernScrollBar()
    area.setVerticalScrollBar(bar)
    bar.parentWidget().setStyleSheet(SCROLL_BAR_GUTTER_STYLE)
    # setVerticalScrollBar() only ties the bar's Python half to the area's
    # Python wrapper. A temporary one, such as the list combo.view() returns,
    # takes it along when collected, and Qt's widget then paints like a plain
    # scroll bar again. Hold it here until Qt destroys the bar.
    key = id(bar)
    _installed_bars[key] = bar
    bar.destroyed.connect(lambda _object=None, key=key: _installed_bars.pop(key, None))
    return bar


@lru_cache(maxsize=1)
def _dwm_set_window_attribute():
    """DwmSetWindowAttribute, or None off Windows."""
    try:
        call = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
    except (AttributeError, OSError):
        return None
    call.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    call.restype = ctypes.c_long  # HRESULT
    return call


def use_native_popup_frame(window: QWidget) -> None:
    """Let Windows draw a list window's rounded border and shadow.

    Qt 6.11 does this by itself. Qt 6.10's Windows 11 style instead makes a
    combo box's list window see-through and frameless to paint its own
    border, which the list's white rows cover, and leaves completer lists
    square. Call it before the window shows.
    """
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    # Before the native window exists, a flag change re-polishes a window
    # under a style sheet, and the style puts the flags straight back.
    window.winId()
    for hint in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.NoDropShadowWindowHint):
        if window.windowFlags() & hint:
            window.setWindowFlag(hint, False)
    set_attribute = _dwm_set_window_attribute()
    if set_attribute is None or QGuiApplication.platformName() != "windows":
        return
    preference = ctypes.c_int(_DWMWCP_ROUND)
    # Windows 10 refuses the attribute and keeps square corners, as it should.
    set_attribute(
        int(window.winId()),
        _DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(preference),
        ctypes.sizeof(preference),
    )


def style_popup_list(view: QAbstractItemView) -> None:
    """Give a list that drops down rounded rows and a slim scroll bar."""
    # Style-sheet rows need the styled delegate; combo boxes and completers
    # otherwise draw rows themselves.
    view.setItemDelegate(QStyledItemDelegate(view))
    view.setStyleSheet(POPUP_LIST_STYLE)
    use_modern_scroll_bar(view)
    if view.isWindow():
        # A completer's list is a window of its own. A combo box's list sits
        # in a window the style may change later, so DropdownComboBox sets
        # it up each time it opens.
        use_native_popup_frame(view)


class DropdownComboBox(QComboBox):
    """A combo box with a drawn chevron that turns over while its list is open.

    Add DROPDOWN_STYLE to any style sheet set on it, so the style's boxed
    arrow button stays out of the way.
    """

    CHEVRON_INSET = 12.0
    TURN_MS = 180
    REST_COLOR = QColor("#909399")
    HOVER_COLOR = QColor("#606266")
    DISABLED_COLOR = QColor("#C0C4CC")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(DROPDOWN_STYLE)
        self._turn = Tween(self, lambda _value: self.update())
        style_popup_list(self.view())
        self.view().window().installEventFilter(self)

    def is_open(self) -> bool:
        return self._turn.target == 1.0

    def showPopup(self):
        # Qt 6.10's style marks the list window see-through whenever it
        # polishes the combo box, so undo that right before each opening.
        use_native_popup_frame(self.view().window())
        super().showPopup()

    def eventFilter(self, watched, event):
        if watched is self.view().window():
            if event.type() == QEvent.Type.Show:
                self._turn.to(1.0, self.TURN_MS)
            elif event.type() == QEvent.Type.Hide:
                self._turn.to(0.0, self.TURN_MS)
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        super().paintEvent(event)
        turn = self._turn.value
        if not self.isEnabled():
            color = self.DISABLED_COLOR
        else:
            resting = self.HOVER_COLOR if self.underMouse() else self.REST_COLOR
            color = _mix(resting, ACCENT, turn)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        chevron = QPainterPath(QPointF(-4.0, -2.0))
        chevron.lineTo(0.0, 2.0)
        chevron.lineTo(4.0, -2.0)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.translate(self.width() - self.CHEVRON_INSET, self.height() / 2)
        painter.rotate(180.0 * turn)
        painter.drawPath(chevron)
        painter.end()
