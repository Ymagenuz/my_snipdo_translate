"""The input box's pieces for text read from an image.

While the image is read the box shows it with a line sweeping over it; once
its text is in the box a bar above the text says it came from an image, with
the image a click away. Dragging an image over the window shows where to drop
it.
"""

from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRect,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QWidget

import ui_motion


SOURCE_BLUE = "#2F62D6"
IMAGE_BORDER = "#E3E7ED"
# One sweep down and back up.
SCAN_PERIOD_SECONDS = 2.4
BAR_HEIGHT = 28
POPUP_MAX_SIZE = QSize(640, 420)


def _image_suffixes() -> frozenset[str]:
    return frozenset(
        "." + bytes(name).decode("ascii", "ignore").lower()
        for name in QImageReader.supportedImageFormats()
    )


def image_file_paths(mime) -> list[str]:
    """Local image files among the dragged or pasted items."""
    if mime is None or not mime.hasUrls():
        return []
    suffixes = _image_suffixes()
    return [
        url.toLocalFile()
        for url in mime.urls()
        if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in suffixes
    ]


def _bare_image(mime) -> bool:
    # Word and Excel put a picture of copied text beside the text; that's text.
    return bool(mime.hasImage() and not (mime.hasText() and mime.text().strip()))


def has_incoming_image(mime) -> bool:
    """Whether a paste or drop is an image to read rather than text."""
    if mime is None:
        return False
    return bool(image_file_paths(mime)) or _bare_image(mime)


def incoming_image(mime) -> QImage | str | None:
    """The image a paste or drop brings: a file's path, or the image itself."""
    if mime is None:
        return None
    paths = image_file_paths(mime)
    if paths:
        return paths[0]
    if _bare_image(mime):
        image = QImage(mime.imageData())
        if not image.isNull():
            return image
    return None


def scaled_pixmap(image: QImage, size: QSize, ratio: float) -> QPixmap:
    """The image shrunk to fit size, sharp on a screen with this pixel ratio."""
    pixmap = QPixmap.fromImage(
        image.scaled(
            QSize(max(1, round(size.width() * ratio)), max(1, round(size.height() * ratio))),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def natural_size(image: QImage, ratio: float) -> QSizeF:
    """The image's size on screen at 1:1, so a small snip isn't blown up."""
    return QSizeF(image.width(), image.height()) / max(ratio, 1.0)


class _Follower(QObject):
    """Keeps a widget over a part of the text box as the box resizes."""

    def __init__(self, widget: QWidget, watched: QWidget, place):
        super().__init__(widget)
        self._place = place
        watched.installEventFilter(self)

    def eventFilter(self, _watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self._place()
        return False


class SnipPreview(QWidget):
    """The image being read, shown in the input box with a sweeping line."""

    def __init__(self, editor: QTextEdit):
        viewport = editor.viewport()
        super().__init__(viewport)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._image = QImage()
        self._scanning = False
        self._phase = 0.0
        self._started = None
        _Follower(self, viewport, lambda: self.setGeometry(viewport.rect()))
        self.hide()

    def image(self) -> QImage:
        return self._image

    def show_image(self, image: QImage | None, *, scanning: bool = True) -> None:
        if image is None or image.isNull():
            self.hide_image()
            return
        self._image = image
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self._set_scanning(scanning)
        self.update()

    def hide_image(self) -> None:
        self._set_scanning(False)
        self._image = QImage()
        self.hide()

    def is_scanning(self) -> bool:
        return self._scanning

    def _set_scanning(self, scanning: bool) -> None:
        scanning = bool(scanning and ui_motion.animations_enabled())
        if scanning == self._scanning:
            return
        self._scanning = scanning
        self._started = None
        if scanning:
            ui_motion.frame_clock().subscribe(self._on_frame, self)
        else:
            ui_motion.frame_clock().unsubscribe(self._on_frame)

    def _on_frame(self, now: float) -> None:
        if self._started is None:
            self._started = now
        cycle = (now - self._started) / SCAN_PERIOD_SECONDS
        # Eases in and out at the top and bottom.
        self._phase = 0.5 - 0.5 * math.cos(2 * math.pi * cycle)
        self.update()

    def hideEvent(self, event):
        self._set_scanning(False)
        super().hideEvent(event)

    def image_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(4, 6, -10, -6)
        if self._image.isNull() or area.width() <= 0 or area.height() <= 0:
            return QRectF()
        size = natural_size(self._image, self.devicePixelRatioF())
        if size.width() > area.width() or size.height() > area.height():
            size = size.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(area.topLeft(), size)

    def paintEvent(self, _event):
        target = self.image_rect()
        if target.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frame = QPainterPath()
        frame.addRoundedRect(target, 4, 4)
        painter.save()
        painter.setClipPath(frame)
        painter.fillRect(target, QColor("#FFFFFF"))
        ratio = self.devicePixelRatioF()
        painter.drawPixmap(
            target.toRect(),
            scaled_pixmap(self._image, target.size().toSize(), ratio),
        )
        if self._scanning:
            line_y = target.top() + self._phase * (target.height() - 2)
            band = QColor(SOURCE_BLUE)
            band.setAlpha(24)
            painter.fillRect(QRectF(target.left(), line_y - 16, target.width(), 16), band)
            line = QColor(SOURCE_BLUE)
            line.setAlpha(150)
            painter.fillRect(QRectF(target.left(), line_y, target.width(), 2), line)
        painter.restore()
        painter.setPen(QPen(QColor(IMAGE_BORDER), 1))
        painter.drawPath(frame)
        painter.end()


_LINK_STYLE = f"""
    QPushButton {{
        background-color: transparent;
        color: {SOURCE_BLUE};
        border: none;
        padding: 0;
        font-size: 12px;
    }}
    QPushButton:hover {{
        color: #1F4FB8;
        text-decoration: underline;
    }}
"""


class SourceImageBar(QWidget):
    """"From image · Show image · Copy text" above text read from an image."""

    show_image_clicked = pyqtSignal()
    copy_text_clicked = pyqtSignal()

    def __init__(self, editor: QTextEdit, texts: dict[str, str]):
        super().__init__(editor)
        self._editor = editor
        self._image = QImage()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 2)
        layout.setSpacing(10)

        self.thumbnail = QLabel()
        self.thumbnail.setObjectName("sourceImageThumbnail")
        self.thumbnail.setFixedSize(40, 22)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setStyleSheet(
            f"#sourceImageThumbnail {{ background-color: #FFFFFF;"
            f" border: 1px solid {IMAGE_BORDER}; border-radius: 3px; }}"
        )
        layout.addWidget(self.thumbnail)

        self.label = QLabel()
        self.label.setObjectName("sourceImageLabel")
        self.label.setStyleSheet("#sourceImageLabel { color: #7C8798; font-size: 12px; }")
        layout.addWidget(self.label)

        self.btn_show = QPushButton()
        self.btn_copy = QPushButton()
        for button, signal in (
            (self.btn_show, self.show_image_clicked),
            (self.btn_copy, self.copy_text_clicked),
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_LINK_STYLE)
            button.clicked.connect(lambda _checked=False, signal=signal: signal.emit())
            layout.addWidget(button)
        layout.addStretch(1)

        # A child timer, so it can't outlive the button it renames.
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.timeout.connect(
            lambda: self.btn_copy.setText(self._texts["copy"])
        )
        self.set_texts(texts)
        # The viewport, not the box: it has its new geometry by the time it hears.
        _Follower(self, editor.viewport(), self._place)
        self.hide()

    def set_texts(self, texts: dict[str, str]) -> None:
        self._texts = dict(texts)
        self.label.setText(texts["from"])
        self.btn_show.setText(texts["show"])
        self.btn_copy.setText(texts["copy"])

    def show_copied(self, text: str, duration_ms: int = 1200) -> None:
        """Say the text was copied, then put the button's name back."""
        self.btn_copy.setText(text)
        self._copied_timer.start(duration_ms)

    def image(self) -> QImage:
        return self._image

    def show_image(self, image: QImage | None) -> None:
        if image is None or image.isNull():
            self.clear()
            return
        self._image = image
        ratio = self.devicePixelRatioF()
        self.thumbnail.setPixmap(scaled_pixmap(image, QSize(36, 18), ratio))
        self._editor.setViewportMargins(0, BAR_HEIGHT, 0, 0)
        self._place()
        self.show()
        self.raise_()

    def clear(self) -> None:
        if self.isHidden() and self._image.isNull():
            return
        self._image = QImage()
        self.thumbnail.clear()
        self.hide()
        self._editor.setViewportMargins(0, 0, 0, 0)

    def _place(self) -> None:
        viewport = self._editor.viewport().geometry()
        self.setGeometry(
            viewport.x(), viewport.y() - BAR_HEIGHT, viewport.width(), BAR_HEIGHT
        )


class ImagePopup(QLabel):
    """The image read, at up to its own size, until the next click."""

    def __init__(self, parent: QWidget):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("imagePopup")
        self.setStyleSheet(
            "#imagePopup { background-color: #FFFFFF; border: 1px solid #DDE3EA; padding: 6px; }"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def show_image(self, image: QImage, anchor: QPoint) -> None:
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen is not None else 1.0
        size = natural_size(image, ratio).toSize()
        limit = POPUP_MAX_SIZE
        if screen is not None:
            available = screen.availableGeometry().size() - QSize(40, 40)
            limit = limit.boundedTo(available)
        if size.width() > limit.width() or size.height() > limit.height():
            size = size.scaled(limit, Qt.AspectRatioMode.KeepAspectRatio)
        self.setPixmap(scaled_pixmap(image, size, ratio))
        self.adjustSize()
        position = QRect(anchor, self.size())
        if screen is not None:
            bounds = screen.availableGeometry()
            position.moveRight(min(position.right(), bounds.right()))
            position.moveBottom(min(position.bottom(), bounds.bottom()))
            position.moveLeft(max(position.left(), bounds.left()))
            position.moveTop(max(position.top(), bounds.top()))
        self.move(position.topLeft())
        self.show()

    def mousePressEvent(self, event):
        self.close()


class ImageDropHint(QWidget):
    """Covers the input box while an image is dragged over the window."""

    def __init__(self, editor: QTextEdit, text: str):
        super().__init__(editor)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text = text
        _Follower(self, editor, lambda: self.setGeometry(editor.rect()))
        self.hide()

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.setGeometry(self.parentWidget().rect())
            self.show()
            self.raise_()
        else:
            self.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.setBrush(QColor("#EAF1FE"))
        pen = QPen(QColor(SOURCE_BLUE), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(area, 8, 8)
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor(SOURCE_BLUE))
        painter.drawText(
            self.rect().adjusted(12, 0, -12, 0),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.text,
        )
        painter.end()


__all__ = [
    "BAR_HEIGHT",
    "ImageDropHint",
    "ImagePopup",
    "SnipPreview",
    "SourceImageBar",
    "has_incoming_image",
    "image_file_paths",
    "incoming_image",
]
