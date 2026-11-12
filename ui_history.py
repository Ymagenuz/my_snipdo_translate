"""The History panel: past translations to browse and read over the window.

The History button used to open a menu of the last twelve entries, and
choosing one stopped any translation that was running and replaced both
panes. The panel instead sits over the window, above the button, and
leaves the window alone: entries are searched, read and copied in the
panel, and reach the main window only through Open, which asks first
while a translation is running.
"""
from __future__ import annotations

import html
import math
import re
from datetime import datetime, timedelta
from typing import Callable, Iterable, Mapping

from PyQt6.QtCore import QEvent, QLocale, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_i18n import tr, ui_language
from ui_controls import render_soft_shadow, use_modern_context_menu, use_modern_scroll_bar
from ui_motion import SmoothScroller

STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Room the panel leaves to the card's edges and to the History button.
EDGE = 8
# Wide enough that a window of the usual size is covered edge to edge.
MAX_WIDTH = 560
MAX_HEIGHT = 520
# On the smallest window the panel may rise past the card's top edge.
MIN_HEIGHT = 200

FONT_FAMILIES = ["Segoe UI", "Microsoft YaHei UI"]
TEXT = "#303133"
SECONDARY = "#5F6B7A"
MUTED = "#909399"
ACCENT = "#2F62D6"
HOVER_ROW = "#F2F6FE"
CHOSEN_ROW = "#EAF1FE"

_RESULT_LABELS = {
    "en2zh": "main.label.chinese_translation",
    "zh2en": "main.label.english_translation",
    "dictionary": "main.label.dictionary",
}
_MODE_LABELS = {
    "en2zh": "main.history.mode.en2zh",
    "zh2en": "main.history.mode.zh2en",
    "dictionary": "main.history.mode.dictionary",
}

# ---------- Text for the rows ----------

_RULES_AND_FENCES = re.compile(
    r"^[ \t]*(?:(?:[-*_][ \t]*){3,}|`{3,}.*|~{3,}.*)$", re.MULTILINE
)
_LINE_MARKS = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t>]*|(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)",
    re.MULTILINE,
)
_LINKS = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_INLINE_MARKS = re.compile(r"\*\*|__|~~|`")
_ITALICS = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])")
_SPACES = re.compile(r"\s+")


def history_preview(text: str) -> str:
    """One line to recognise text by: Markdown marks dropped, spaces collapsed."""
    text = _RULES_AND_FENCES.sub(" ", text or "")
    text = _LINE_MARKS.sub("", text)
    text = _LINKS.sub(r"\1", text)
    text = _INLINE_MARKS.sub("", text)
    text = _ITALICS.sub(r"\1", text)
    return _SPACES.sub(" ", text).strip()


def _moment(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, STAMP_FORMAT)
    except (TypeError, ValueError):
        return None


def _date_text(moment: datetime, now: datetime) -> str:
    locale = QLocale(ui_language().replace("-", "_"))
    key = "main.history.day_format" if moment.year == now.year else "main.history.year_format"
    return locale.toString(moment.date(), tr(key))


def history_time_label(stamp: str, now: datetime | None = None) -> str:
    """"Today 04:00" for a History entry saved as "2026-11-05 04:00:07"."""
    moment = _moment(stamp)
    if moment is None:
        return stamp or ""
    now = now or datetime.now()
    time_text = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return tr("main.history.today", time=time_text)
    if moment.date() == (now - timedelta(days=1)).date():
        return tr("main.history.yesterday", time=time_text)
    if moment.year == now.year:
        return f"{_date_text(moment, now)} {time_text}"
    return _date_text(moment, now)


def history_day_label(stamp: str, now: datetime | None = None) -> str:
    """The heading an entry is listed under: "Today", "Yesterday", "Nov 10"."""
    moment = _moment(stamp)
    if moment is None:
        return ""
    now = now or datetime.now()
    if moment.date() == now.date():
        return tr("main.history.group.today")
    if moment.date() == (now - timedelta(days=1)).date():
        return tr("main.history.group.yesterday")
    return _date_text(moment, now)


def history_clock(stamp: str) -> str:
    """"14:02" for an entry saved at 14:02:37; the stamp itself if unreadable."""
    moment = _moment(stamp)
    return moment.strftime("%H:%M") if moment is not None else (stamp or "")


def history_mode_label(mode: str) -> str:
    return tr(_MODE_LABELS.get(mode, "main.history.mode.auto"))


def history_matches(entry: Mapping[str, str], query: str) -> bool:
    """Every word of query appears in the entry's source or translation."""
    words = query.casefold().split()
    if not words:
        return True
    haystack = f"{entry.get('source', '')}\n{entry.get('result', '')}".casefold()
    return all(word in haystack for word in words)


def history_groups(
    entries: Iterable[Mapping[str, str]], query: str = "", now: datetime | None = None
) -> list[tuple[str, list[int]]]:
    """Positions of the entries matching query, newest first, under their day."""
    now = now or datetime.now()
    groups: list[tuple[str, list[int]]] = []
    for position, entry in enumerate(entries):
        if not history_matches(entry, query):
            continue
        day = history_day_label(entry.get("time", ""), now)
        if groups and groups[-1][0] == day:
            groups[-1][1].append(position)
        else:
            groups.append((day, [position]))
    return groups


# ---------- Styles ----------

PANEL_STYLE = """
    #historyPanel {
        background-color: #FFFFFF;
        border: 1px solid #DCDFE6;
        border-radius: 8px;
    }
"""

SEARCH_STYLE = """
    #historySearch {
        background-color: #FBFCFF;
        color: #303133;
        border: 1px solid #DDE3EA;
        border-radius: 6px;
        padding: 5px 4px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 13px;
        selection-background-color: #D3DFFB;
        selection-color: #303133;
    }
    #historySearch:focus {
        border-color: #A9C1F5;
    }
"""

_LINK_STYLE = """
    QPushButton {{
        background-color: transparent;
        color: {color};
        border: none;
        border-radius: 5px;
        padding: 3px 7px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {hover};
        color: {hover_color};
    }}
"""
LINK_STYLE = _LINK_STYLE.format(color=ACCENT, hover=CHOSEN_ROW, hover_color=ACCENT)
MUTED_LINK_STYLE = _LINK_STYLE.format(color=SECONDARY, hover="#E6EAF0", hover_color=TEXT)
DANGER_LINK_STYLE = _LINK_STYLE.format(color="#D64545", hover="#FEF0F0", hover_color="#D64545")

_BUTTON_STYLE = """
    QPushButton {{
        background-color: {background};
        color: {color};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 5px 12px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-weight: 600;
        font-size: 12px;
    }}
    QPushButton:hover {{
        background-color: {hover};
    }}
"""
GREY_BUTTON_STYLE = _BUTTON_STYLE.format(
    background="#F2F4F7", color=SECONDARY, border="#DDE3EA", hover="#E6EAF0"
)
BLUE_BUTTON_STYLE = _BUTTON_STYLE.format(
    background="#EAF1FE", color=ACCENT, border="#CFDDFB", hover="#DFE9FD"
)

_TAG_STYLE = """
    QLabel {{
        background-color: {background};
        color: {color};
        border-radius: 4px;
        padding: 1px 6px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 11px;
    }}
"""
TAG_STYLE = _TAG_STYLE.format(background="#F2F4F7", color=SECONDARY)
DICTIONARY_TAG_STYLE = _TAG_STYLE.format(background="#F3EEFD", color="#5B3FA8")

SMALL_TEXT_STYLE = (
    "color: {color}; font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: {size}px;"
)


def _tag_colors(mode: str) -> tuple[QColor, QColor]:
    if mode == "dictionary":
        return QColor("#F3EEFD"), QColor("#5B3FA8")
    return QColor("#F2F4F7"), QColor(SECONDARY)


def _sized(font: QFont, pixel_size: int) -> QFont:
    """font at pixel_size, keeping the hinting the screen gave it."""
    font = QFont(font)
    font.setFamilies(FONT_FAMILIES)
    font.setPixelSize(pixel_size)
    return font


def _search_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(size / 16, size / 16)
        pen = QPen(QColor(MUTED), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(3.5, 3.5, 7, 7))
        painter.drawLine(QPointF(9.6, 9.6), QPointF(12.6, 12.6))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("historyDivider")
    line.setFixedHeight(1)
    line.setStyleSheet("#historyDivider { background-color: #EBEEF5; border: none; }")
    return line


# ---------- Rows ----------

_POSITION = Qt.ItemDataRole.UserRole
_DAY = Qt.ItemDataRole.UserRole + 1
_SOURCE = Qt.ItemDataRole.UserRole + 2
_RESULT = Qt.ItemDataRole.UserRole + 3
_MODE = Qt.ItemDataRole.UserRole + 4
_CLOCK = Qt.ItemDataRole.UserRole + 5


class _HistoryRows(QStyledItemDelegate):
    """Draws a day's heading, or an entry's source over its translation."""

    ROW_HEIGHT = 52
    DAY_HEIGHT = 26

    def sizeHint(self, option, index):
        return QSize(0, self.DAY_HEIGHT if index.data(_DAY) is not None else self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        try:
            day = index.data(_DAY)
            if day is not None:
                self._paint_day(painter, option, day)
            else:
                self._paint_entry(painter, option, index)
        finally:
            painter.restore()

    def _paint_day(self, painter, option, day: str) -> None:
        painter.setFont(_sized(option.font, 11))
        painter.setPen(QColor(MUTED))
        painter.drawText(
            option.rect.adjusted(10, 0, -10, -4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            day,
        )

    def _paint_entry(self, painter, option, index) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        state = option.state
        if state & QStyle.StateFlag.State_Selected:
            fill = QColor(CHOSEN_ROW)
        elif state & QStyle.StateFlag.State_MouseOver:
            fill = QColor(HOVER_ROW)
        else:
            fill = None
        if fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(option.rect.adjusted(2, 1, -2, -1)), 6, 6)

        inner = option.rect.adjusted(10, 7, -10, -7)
        mode = index.data(_MODE) or ""
        tag = history_mode_label(mode)
        tag_font = _sized(option.font, 11)
        tag_metrics = QFontMetrics(tag_font)
        tag_width = tag_metrics.horizontalAdvance(tag) + 12
        tag_rect = QRectF(
            inner.right() + 1 - tag_width, inner.top(), tag_width, tag_metrics.height() + 2
        )
        background, color = _tag_colors(mode)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(tag_rect, 4, 4)
        painter.setFont(tag_font)
        painter.setPen(color)
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag)

        clock = index.data(_CLOCK) or ""
        painter.setPen(QColor(MUTED))
        painter.drawText(inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, clock)

        side = max(tag_width, tag_metrics.horizontalAdvance(clock))
        width = max(0, inner.width() - side - 12)
        source_font = _sized(option.font, 13)
        source_metrics = QFontMetrics(source_font)
        painter.setFont(source_font)
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRect(inner.left(), inner.top(), width, source_metrics.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            source_metrics.elidedText(index.data(_SOURCE) or "", Qt.TextElideMode.ElideRight, width),
        )
        result_font = _sized(option.font, 12)
        result_metrics = QFontMetrics(result_font)
        painter.setFont(result_font)
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRect(
                inner.left(),
                inner.bottom() + 1 - result_metrics.height(),
                width,
                result_metrics.height(),
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            result_metrics.elidedText(index.data(_RESULT) or "", Qt.TextElideMode.ElideRight, width),
        )


# ---------- Panel ----------


class _ConfirmBar(QFrame):
    """A question in the panel itself, answered by one of two links."""

    accepted = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("historyConfirm")
        self.setStyleSheet("#historyConfirm { background-color: #F2F4F7; border-radius: 6px; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 5, 5)
        layout.setSpacing(2)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setStyleSheet(SMALL_TEXT_STYLE.format(color=SECONDARY, size=12))
        layout.addWidget(self.label, 1)
        self.yes = QPushButton()
        self.no = QPushButton()
        for button in (self.yes, self.no):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.no.setStyleSheet(MUTED_LINK_STYLE)
        self.yes.clicked.connect(self._accept)
        self.no.clicked.connect(self._dismiss)
        self.hide()

    def ask(self, text: str, yes: str, no: str, *, danger: bool = False) -> None:
        self.label.setText(text)
        self.yes.setText(yes)
        self.no.setText(no)
        self.yes.setStyleSheet(DANGER_LINK_STYLE if danger else LINK_STYLE)
        self.show()

    def _accept(self) -> None:
        self.hide()
        self.accepted.emit()

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()


class _PanelShadow(QWidget):
    """The panel's soft shadow, painted beneath it.

    A sibling rather than part of the panel, so clicks beside the panel
    still reach the window under the shadow.
    """

    MARGIN = 40

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._face = QSize()
        self._key = None
        self._image = QPixmap()
        self._offset = QPoint()

    def follow(self, face: QRect) -> None:
        self._face = face.size()
        margin = self.MARGIN
        self.setGeometry(face.adjusted(-margin, -margin, margin, margin))
        self.update()

    def paintEvent(self, event):
        if self._face.isEmpty():
            return
        key = (QSize(self._face), self.devicePixelRatioF())
        if key != self._key:
            self._image, self._offset = render_soft_shadow(
                self._face,
                key[1],
                radius=8,
                blur=28,
                offset=(0, 6),
                color=QColor(31, 45, 61, 40),
            )
            self._key = key
        painter = QPainter(self)
        painter.drawPixmap(QPoint(self.MARGIN, self.MARGIN) + self._offset, self._image)
        painter.end()


class HistoryPanel(QFrame):
    """Past translations to search, read and copy without leaving the window.

    It never touches the window's panes or a running translation itself;
    open_requested asks the window to open an entry, and only after the
    person agreed to stop a translation that is running. A press anywhere
    else in the window closes it and still reaches what was pressed;
    toggle, the History button, opens and closes it itself.
    """

    open_requested = pyqtSignal(dict)
    clear_requested = pyqtSignal()
    closed = pyqtSignal()

    COPIED_MS = 900

    def __init__(
        self,
        parent: QWidget,
        render: Callable[[QTextEdit, str, str], object],
        *,
        toggle: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("historyPanel")
        self.setStyleSheet(PANEL_STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._render = render
        self._toggle = toggle
        self._watching_clicks = False
        self._entries: list[dict] = []
        self._entry: dict | None = None
        self._translating = False
        self._added = False
        self._return_focus: QWidget | None = None
        self._shadow = _PanelShadow(parent)

        self.pages = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.pages)
        self.pages.addWidget(self._build_list_page())
        self.pages.addWidget(self._build_entry_page())

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(self.COPIED_MS)
        self._copied_timer.timeout.connect(self._show_copy_text)

        self.retranslate()
        self._shadow.hide()
        self.hide()

    # ----- Building -----

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.search = QLineEdit()
        self.search.setObjectName("historySearch")
        self.search.setStyleSheet(SEARCH_STYLE)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(_search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._read_chosen)
        use_modern_context_menu(self.search)
        layout.addWidget(self.search)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setContentsMargins(8, 2, 8, 0)
        self.status.setStyleSheet(SMALL_TEXT_STYLE.format(color=SECONDARY, size=11))
        self.status.hide()
        layout.addWidget(self.status)

        self.list = QListWidget()
        self.list.setItemDelegate(_HistoryRows(self.list))
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; outline: 0; }")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.list.itemClicked.connect(self._read_item)
        self.list.itemActivated.connect(self._read_item)
        use_modern_scroll_bar(self.list)
        # Glides like the window's panes; after the bar, which it watches.
        self.list_scroller = SmoothScroller(self.list)
        layout.addWidget(self.list, 1)

        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(SMALL_TEXT_STYLE.format(color=MUTED, size=12))
        layout.addWidget(self.empty, 1)

        self.clear_bar = _ConfirmBar()
        self.clear_bar.accepted.connect(self.clear_requested)
        layout.addWidget(self.clear_bar)

        layout.addWidget(_divider())
        footer = QHBoxLayout()
        footer.setContentsMargins(8, 0, 0, 0)
        self.count = QLabel()
        self.count.setStyleSheet(SMALL_TEXT_STYLE.format(color=MUTED, size=11))
        footer.addWidget(self.count)
        footer.addStretch(1)
        self.btn_clear = QPushButton()
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setStyleSheet(MUTED_LINK_STYLE)
        self.btn_clear.clicked.connect(self._ask_to_clear)
        footer.addWidget(self.btn_clear)
        layout.addLayout(footer)
        return page

    def _build_entry_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 4, 0)
        header.setSpacing(6)
        self.btn_back = QPushButton()
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setStyleSheet(MUTED_LINK_STYLE)
        self.btn_back.clicked.connect(self.show_list)
        header.addWidget(self.btn_back)
        header.addStretch(1)
        self.tag = QLabel()
        header.addWidget(self.tag)
        self.when = QLabel()
        self.when.setStyleSheet(SMALL_TEXT_STYLE.format(color=MUTED, size=11))
        header.addWidget(self.when)
        layout.addLayout(header)
        layout.addWidget(_divider())

        body = QVBoxLayout()
        body.setContentsMargins(4, 2, 4, 0)
        body.setSpacing(6)
        self.lbl_source = QLabel()
        self.lbl_source.setStyleSheet(
            "color: #2F62D6; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        body.addWidget(self.lbl_source)
        self.source_view = QTextEdit()
        self.source_view.setReadOnly(True)
        self.source_view.setStyleSheet("""
            QTextEdit {
                background-color: #FBFCFF;
                border: none;
                border-radius: 8px;
                padding: 4px 4px 4px 8px;
                color: #303133;
                selection-background-color: #D3DFFB;
                selection-color: #303133;
            }
        """)
        body.addWidget(self.source_view, 2)
        body.addSpacing(4)
        self.lbl_result = QLabel()
        self.lbl_result.setStyleSheet(
            "color: #0E8A7D; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        body.addWidget(self.lbl_result)
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                padding: 0 4px 0 8px;
                selection-background-color: #CBEFEB;
                selection-color: #303133;
            }
        """)
        body.addWidget(self.result_view, 3)
        for view in (self.source_view, self.result_view):
            use_modern_scroll_bar(view)
            use_modern_context_menu(view)
            view.smooth_scroller = SmoothScroller(view)
        layout.addLayout(body, 1)

        self.open_bar = _ConfirmBar()
        self.open_bar.accepted.connect(self._open_now)
        layout.addWidget(self.open_bar)

        layout.addWidget(_divider())
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)
        self.btn_copy = QPushButton()
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet(GREY_BUTTON_STYLE)
        self.btn_copy.clicked.connect(self._copy)
        footer.addWidget(self.btn_copy)
        self.btn_open = QPushButton()
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.setStyleSheet(BLUE_BUTTON_STYLE)
        self.btn_open.clicked.connect(self._open)
        footer.addWidget(self.btn_open)
        layout.addLayout(footer)
        return page

    # ----- What the window tells it -----

    def set_entries(self, entries: Iterable[Mapping[str, str]], *, added: bool = False) -> None:
        """The entries to list, newest first; added when one just finished."""
        self._entries = [dict(entry) for entry in entries]
        if added:
            self._added = True
        if not self._entries:
            self.clear_bar.hide()
            if self.pages.currentIndex() == 1:
                self.show_list()
        self._update_status()
        self._refill()

    def set_translating(self, running: bool) -> None:
        """Whether the window has a translation running behind the panel."""
        if running and not self._translating:
            self._added = False
        self._translating = running
        if not running:
            # Opening no longer stops anything.
            self.open_bar.hide()
        self._update_status()

    def is_translating(self) -> bool:
        return self._translating

    def place(self, anchor: QRect, bounds: QRect) -> None:
        """Sit above anchor, the History button, within bounds, the card.

        Both are in the parent's coordinates. The panel grows up from the
        button, stays inside the card, and keeps to its size limits on a
        large or maximized window.
        """
        width = min(bounds.width() - 2 * EDGE, MAX_WIDTH)
        bottom = anchor.top() - EDGE
        height = min(bottom - (bounds.top() + EDGE), MAX_HEIGHT)
        height = max(height, min(MIN_HEIGHT, bottom))
        left = anchor.center().x() - width // 2
        left = max(bounds.left() + EDGE, min(left, bounds.right() + 1 - EDGE - width))
        self.setGeometry(left, bottom - height, width, height)

    def open_panel(self) -> None:
        focus = QApplication.focusWidget()
        self._return_focus = focus if focus is not None and not self.isAncestorOf(focus) else None
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.clear_bar.hide()
        self.open_bar.hide()
        self._entry = None
        self.pages.setCurrentIndex(0)
        self._refill()
        self.list.setCurrentRow(-1)
        self.list.scrollToTop()
        self._update_status()
        self._shadow.follow(self.geometry())
        self._shadow.show()
        self._shadow.raise_()
        self.show()
        self.raise_()
        self.search.setFocus()
        if not self._watching_clicks:
            QApplication.instance().installEventFilter(self)
            self._watching_clicks = True

    def close_panel(self, *, restore_focus: bool = True) -> None:
        """Close; focus goes back where it was unless a click put it elsewhere."""
        self._stop_watching_clicks()
        if self.isHidden():
            return
        self.hide()
        self._shadow.hide()
        self._added = False
        self._copied_timer.stop()
        focus, self._return_focus = self._return_focus, None
        if restore_focus and focus is not None and focus.isVisible():
            focus.setFocus()
        self.closed.emit()

    def retranslate(self) -> None:
        self.search.setPlaceholderText(tr("main.history.search"))
        self.btn_clear.setText(tr("main.history.clear"))
        self.btn_back.setText("\N{LEFTWARDS ARROW} " + tr("main.history"))
        self.lbl_source.setText(tr("main.label.original"))
        self.btn_open.setText(tr("main.history.open"))
        self._keep_copy_width()
        self._show_copy_text()
        self.clear_bar.hide()
        self.open_bar.hide()
        if self._entry is not None:
            self._label_entry(self._entry)
        self._update_status()
        self._refill()

    # ----- Pages -----

    def show_list(self) -> None:
        self._entry = None
        self.open_bar.hide()
        self._copied_timer.stop()
        self.pages.setCurrentIndex(0)
        self._refill()
        self.search.setFocus()

    def show_entry(self, entry: Mapping[str, str]) -> None:
        self._entry = dict(entry)
        self.clear_bar.hide()
        self.open_bar.hide()
        self._copied_timer.stop()
        self._show_copy_text()
        self._label_entry(self._entry)
        self.pages.setCurrentIndex(1)
        self.layout().activate()
        self._render(self.source_view, self._entry.get("source", ""), "origin")
        self._render(self.result_view, self._entry.get("result", ""), "result")
        for view in (self.source_view, self.result_view):
            view.verticalScrollBar().setValue(0)
        self._fit_source()
        self.setFocus()

    def shown_entry(self) -> dict | None:
        return self._entry

    def _label_entry(self, entry: Mapping[str, str]) -> None:
        mode = entry.get("mode", "")
        self.tag.setText(history_mode_label(mode))
        self.tag.setStyleSheet(DICTIONARY_TAG_STYLE if mode == "dictionary" else TAG_STYLE)
        self.when.setText(history_time_label(entry.get("time", "")))
        self.lbl_result.setText(tr(_RESULT_LABELS.get(mode, "main.label.auto_translation")))

    def _fit_source(self) -> None:
        """A short source takes a short box, leaving the room to the translation."""
        view = self.source_view
        if self.pages.currentIndex() != 1 or not view.isVisible():
            return
        document = view.document()
        document.setTextWidth(view.viewport().width())
        chrome = view.height() - view.viewport().height()
        wanted = math.ceil(document.size().height()) + chrome + 2
        view.setMaximumHeight(max(36, min(wanted, int(self.height() * 0.4))))

    # ----- The list -----

    def _refill(self) -> None:
        query = self.search.text()
        bar = self.list.verticalScrollBar()
        position = bar.value()
        current = self.list.currentItem()
        chosen = current.data(_POSITION) if current is not None else None
        chosen_entry = self._entries[chosen] if chosen is not None and chosen < len(self._entries) else None

        self.list.clear()
        shown = 0
        choose = None
        for day, positions in history_groups(self._entries, query):
            if day:
                heading = QListWidgetItem()
                heading.setData(_DAY, day)
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(heading)
            for position_in_list in positions:
                entry = self._entries[position_in_list]
                item = QListWidgetItem()
                item.setData(_POSITION, position_in_list)
                item.setData(_SOURCE, history_preview(entry.get("source", "")))
                item.setData(_RESULT, history_preview(entry.get("result", "")))
                item.setData(_MODE, entry.get("mode", ""))
                item.setData(_CLOCK, history_clock(entry.get("time", "")))
                item.setToolTip("")
                self.list.addItem(item)
                if chosen_entry is not None and entry == chosen_entry and choose is None:
                    choose = item
                shown += 1

        self.list.setVisible(shown > 0)
        self.empty.setVisible(shown == 0)
        self.empty.setText(tr("main.history.no_match" if self._entries else "main.history.empty"))
        total = len(self._entries)
        if query.strip():
            self.count.setText(tr("main.history.matches", shown=shown, count=total))
        elif total == 1:
            self.count.setText(tr("main.history.count_one"))
        else:
            self.count.setText(tr("main.history.count", count=total))
        self.btn_clear.setVisible(total > 0)

        if choose is not None:
            self.list.setCurrentItem(choose)
        elif query.strip():
            self._move_choice(1)
        bar.setValue(position)

    def _entry_rows(self) -> list[int]:
        return [
            row for row in range(self.list.count())
            if self.list.item(row).data(_POSITION) is not None
        ]

    def _move_choice(self, step: int) -> None:
        rows = self._entry_rows()
        if not rows:
            return
        current = self.list.currentRow()
        if current in rows:
            index = rows.index(current) + step
        else:
            index = 0 if step > 0 else len(rows) - 1
        row = rows[max(0, min(index, len(rows) - 1))]
        with self.list_scroller.glide():
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))

    def _search_changed(self, _text: str) -> None:
        self.list.setCurrentRow(-1)
        self._refill()
        self.list.scrollToTop()

    def _read_item(self, item: QListWidgetItem) -> None:
        position = item.data(_POSITION)
        if position is not None and position < len(self._entries):
            self.show_entry(self._entries[position])

    def _read_chosen(self) -> None:
        item = self.list.currentItem()
        if item is None or item.data(_POSITION) is None:
            rows = self._entry_rows()
            if not rows:
                return
            item = self.list.item(rows[0])
        self._read_item(item)

    def _update_status(self) -> None:
        if self._translating:
            mark, color, key = "\N{BLACK CIRCLE}", ACCENT, "main.history.running"
        elif self._added:
            mark, color, key = "\N{CHECK MARK}", "#2E8B57", "main.history.added"
        else:
            self.status.hide()
            return
        self.status.setText(
            f'<span style="color:{color};">{mark}</span>&nbsp;&nbsp;{html.escape(tr(key))}'
        )
        self.status.show()

    # ----- Actions -----

    def _ask_to_clear(self) -> None:
        self.clear_bar.ask(
            tr("main.history.clear_confirm"),
            tr("main.history.clear_yes"),
            tr("main.history.cancel"),
            danger=True,
        )

    def _copy(self) -> None:
        if self._entry is None:
            return
        QApplication.clipboard().setText((self._entry.get("result") or "").strip())
        self.btn_copy.setText(tr("main.history.copied"))
        self._copied_timer.start()

    def _show_copy_text(self) -> None:
        self.btn_copy.setText(tr("main.history.copy"))

    def _keep_copy_width(self) -> None:
        """Copy keeps one width as it says Copied, so Open stays put."""
        self.btn_copy.ensurePolished()
        self.btn_copy.setMinimumWidth(0)
        width = 0
        for key in ("main.history.copy", "main.history.copied"):
            self.btn_copy.setText(tr(key))
            width = max(width, self.btn_copy.sizeHint().width())
        self.btn_copy.setMinimumWidth(width)

    def _open(self) -> None:
        if self._entry is None:
            return
        if self._translating:
            self.open_bar.ask(
                tr("main.history.open_confirm"),
                tr("main.history.open_anyway"),
                tr("main.history.keep_translating"),
            )
            return
        self._open_now()

    def _open_now(self) -> None:
        if self._entry is not None:
            self.open_requested.emit(dict(self._entry))

    # ----- Events -----

    def _stop_watching_clicks(self) -> None:
        if self._watching_clicks:
            QApplication.instance().removeEventFilter(self)
            self._watching_clicks = False

    def _is_outside(self, point: QPoint) -> bool:
        """Whether a press at point, on the screen, is off the panel and toggle."""
        if self.rect().contains(self.mapFromGlobal(point)):
            return False
        toggle = self._toggle
        return not (
            toggle is not None
            and toggle.isVisible()
            and toggle.rect().contains(toggle.mapFromGlobal(point))
        )

    def eventFilter(self, watched, event):
        # Watches the whole app while open. A press reaches the pressed widget
        # and then, unless taken, each parent in turn; judged by where it
        # landed, they all agree. Menus and dialogs are windows of their own.
        # Qt has already focused the pressed widget, so focus stays there.
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(watched, QWidget)
            and watched.window() is self.window()
            and self._is_outside(event.globalPosition().toPoint())
        ):
            self.close_panel(restore_focus=False)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self.clear_bar.isVisible():
                self.clear_bar.hide()
            elif self.open_bar.isVisible():
                self.open_bar.hide()
            elif self.pages.currentIndex() == 1:
                self.show_list()
            else:
                self.close_panel()
            return
        if self.pages.currentIndex() == 0 and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._move_choice(-1 if key == Qt.Key.Key_Up else 1)
            return
        super().keyPressEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._shadow.follow(self.geometry())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._shadow.follow(self.geometry())
        self._fit_source()
