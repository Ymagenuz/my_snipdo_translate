"""Slim scroll bars, dropdowns and check boxes, drawn to match the app.

Qt's Windows 11 style draws scroll bars with arrow buttons and combo boxes
with a boxed arrow button, which look dated beside the app's flat, rounded
controls, and under a style sheet draws a ticked check box as a bare tick.
These widgets paint those parts themselves, and ease between states with
ui_motion's Tween, so they follow the screen's refresh like the glides do.
"""
from __future__ import annotations

import ctypes
import html
from ctypes import wintypes
from functools import lru_cache

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QScrollBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionSlider,
    QStylePainter,
    QTextEdit,
    QToolTip,
    QWidget,
)

import ui_motion
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

# Add to a window's style sheet. A tooltip takes its style from the widget it
# belongs to and is a label itself, so rules in the style sheet of a widget
# with a tooltip should name the widget rather than match every QLabel.
TOOLTIP_STYLE = """
    QToolTip {
        background-color: #303133;
        color: #FFFFFF;
        border: none;
        padding: 6px 8px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 12px;
        font-weight: 400;
        letter-spacing: 0;
    }
"""

TOOLTIP_WIDTH = 320

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


class ModernCheckBox(QCheckBox):
    """A check box with a rounded box that fills with the accent when ticked.

    Under a style sheet, Qt's Windows 11 style draws a ticked box as a bare
    tick and an unticked one as an empty square, so on and off looked like
    two different controls. This draws the box itself and leaves the label
    to the style.
    """

    SIZE = 16.0
    RADIUS = 4.0
    BORDER_COLOR = QColor("#C0C4CC")
    HOVER_COLOR = QColor("#9B59B6")
    DISABLED_COLOR = QColor("#E4E7ED")
    DISABLED_FILL = QColor("#F5F7FA")
    DISABLED_TICKED = QColor("#D7BDE2")

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        # Sizes the box Qt lays out and hit-tests; paintEvent draws it.
        self.setStyleSheet(
            f"QCheckBox::indicator {{ width: {self.SIZE:g}px; height: {self.SIZE:g}px; }}"
        )

    def indicator_rect(self) -> QRectF:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        area = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        return QRectF(
            area.left() + (area.width() - self.SIZE) / 2,
            area.top() + (area.height() - self.SIZE) / 2,
            self.SIZE,
            self.SIZE,
        )

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        label = QStyleOptionButton(option)
        label.rect = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxContents, option, self
        )
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_CheckBoxLabel, label)

        box = self.indicator_rect()
        state = self.checkState()
        enabled = self.isEnabled()
        hovered = enabled and self.underMouse()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if (
            option.state & QStyle.StateFlag.State_HasFocus
            and option.state & QStyle.StateFlag.State_KeyboardFocusChange
        ):
            ring = QColor(ACCENT)
            ring.setAlphaF(0.35)
            painter.setPen(QPen(ring, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                box.adjusted(-2.0, -2.0, 2.0, 2.0), self.RADIUS + 2, self.RADIUS + 2
            )
        if state == Qt.CheckState.Unchecked:
            border = (
                self.DISABLED_COLOR if not enabled
                else ACCENT if hovered
                else self.BORDER_COLOR
            )
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(QColor("#FFFFFF") if enabled else self.DISABLED_FILL)
            painter.drawRoundedRect(
                box.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS
            )
            painter.end()
            return

        fill = (
            self.DISABLED_TICKED if not enabled
            else self.HOVER_COLOR if hovered
            else ACCENT
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, self.RADIUS, self.RADIUS)
        pen = QPen(QColor("#FFFFFF"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left, top = box.left(), box.top()
        if state == Qt.CheckState.PartiallyChecked:
            painter.drawLine(QPointF(left + 4.5, top + 8.0), QPointF(left + 11.5, top + 8.0))
        else:
            tick = QPainterPath(QPointF(left + 4.2, top + 8.4))
            tick.lineTo(left + 7.0, top + 11.1)
            tick.lineTo(left + 11.9, top + 5.4)
            painter.drawPath(tick)
        painter.end()


def wrapped_tooltip(text: str) -> str:
    """text as rich text that wraps at TOOLTIP_WIDTH, or as it is if it fits."""
    if not text or text.lstrip().startswith("<"):
        return text
    font = QFont()
    font.setFamilies(["Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(12)
    metrics = QFontMetrics(font)
    lines = text.split("\n")
    if max(metrics.horizontalAdvance(line) for line in lines) <= TOOLTIP_WIDTH:
        return text
    # A table is the one rich-text box Qt lets a tooltip take its width from.
    body = "<br>".join(html.escape(line) for line in lines)
    return f"<table width='{TOOLTIP_WIDTH}'><tr><td>{body}</td></tr></table>"


class _ModernToolTips(QObject):
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.ToolTip or not isinstance(watched, QWidget):
            return False
        text = watched.toolTip()
        if not text:
            return False
        # Qt hands a tooltip that is still showing to the next widget, and
        # the handed-over one loses its style sheet's padding; start afresh.
        for widget in QApplication.topLevelWidgets():
            if widget.metaObject().className() == "QTipLabel" and widget.isVisible():
                widget.hide()
        QToolTip.showText(
            event.globalPos(), wrapped_tooltip(text), watched, QRect(), watched.toolTipDuration()
        )
        return True


_modern_tooltips: _ModernToolTips | None = None


def use_modern_tooltips(root: QWidget) -> None:
    """Show the tooltips of root and the widgets in it wrapped and padded.

    Qt shows a plain-text tooltip on one line, so the longer ones ran across
    the screen. Widgets that have a tooltip when this is called get it
    wrapped at TOOLTIP_WIDTH whenever it shows, whatever it has changed to.
    """
    global _modern_tooltips
    if _modern_tooltips is None:
        _modern_tooltips = _ModernToolTips()
    for widget in (root, *root.findChildren(QWidget)):
        if widget.toolTip():
            widget.installEventFilter(_modern_tooltips)


class BusyPlaceholder(QObject):
    """Says what an empty text box is waiting for, such as "Translating...".

    The dots count up while Windows' animation effects are on, so a slow
    first answer still shows that something is under way.
    """

    INTERVAL_MS = 450

    def __init__(self, view: QTextEdit):
        super().__init__(view)
        self._view = view
        self._text = ""
        self._step = 0
        self._idle_text = ""
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    def is_active(self) -> bool:
        return bool(self._text)

    def set_idle_text(self, text: str) -> None:
        """What the box says when nothing is under way."""
        self._idle_text = text
        if not self._text:
            self._view.setPlaceholderText(text)

    def start(self, text: str) -> None:
        self._text = text
        self._step = 0
        if ui_motion.animations_enabled():
            self._text = text.rstrip(".…")
            self._advance()
            self._timer.start()
        else:
            self._view.setPlaceholderText(text)

    def stop(self) -> None:
        self._timer.stop()
        self._text = ""
        self._view.setPlaceholderText(self._idle_text)

    def _advance(self) -> None:
        self._step = self._step % 3 + 1
        self._view.setPlaceholderText(self._text + "." * self._step)
