"""Message boxes and prompts that suit the rest of the app.

Qt's stock dialogs bring Windows' yellow triangle, blue focus line and blue
progress bar into an app with its own greys, blue and teal. These helpers
give them the main window's buttons and colours, and keep message boxes
wide enough for their titles.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

# Room for a title such as "Check for updates" beside the window's icon and
# Close button, and for a short message to sit on one comfortable line.
MESSAGE_MIN_WIDTH = 360

WARNING_COLOR = QColor("#E6A23C")

# The main window's buttons: grey, with the default one tinted blue like
# Translate.
BUTTON_STYLE = """
    QPushButton {
        background-color: #F2F4F7;
        color: #5F6B7A;
        border: 1px solid #DDE3EA;
        border-radius: 6px;
        padding: 6px 16px;
        min-width: 56px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 13px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #E6EAF0;
        color: #303133;
    }
    QPushButton:focus {
        border-color: #B7CCF8;
    }
    QPushButton:default {
        background-color: #EAF1FE;
        color: #2F62D6;
        border-color: #CFDDFB;
    }
    QPushButton:default:hover {
        background-color: #DFE9FD;
        border-color: #B7CCF8;
    }
    QPushButton:default:pressed {
        background-color: #D3E1FC;
        border-color: #B7CCF8;
    }
    QPushButton:disabled {
        background-color: #F2F4F7;
        color: #C0C4CC;
        border-color: #E4E7ED;
    }
"""

DIALOG_STYLE = BUTTON_STYLE + """
    QMessageBox, QInputDialog, QProgressDialog {
        background-color: #FFFFFF;
    }
    QLabel {
        color: #303133;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 13px;
    }
    QLineEdit {
        background-color: #FFFFFF;
        color: #303133;
        border: 1px solid #DCDFE6;
        border-radius: 6px;
        padding: 6px 10px;
        font-family: 'Segoe UI', 'Microsoft YaHei UI';
        font-size: 13px;
        selection-background-color: #D3DFFB;
        selection-color: #303133;
    }
    QLineEdit:focus {
        border-color: #2F62D6;
    }
    QProgressBar {
        background-color: #EBEEF5;
        border: none;
        border-radius: 3px;
        min-height: 6px;
        max-height: 6px;
    }
    QProgressBar::chunk {
        background-color: #2F62D6;
        border-radius: 3px;
    }
"""


def fit_message_box(box: QMessageBox, min_width: int = MESSAGE_MIN_WIDTH) -> None:
    """Keep a message box wide enough that its title isn't cut off.

    QMessageBox fixes its size to its layout's when it shows, sized to the
    message, so a short one such as "You're up to date." left the title as
    "Check for upd…". A minimum width set on the box itself is overridden;
    a spacer in its layout counts.
    """
    layout = box.layout()
    layout.addItem(
        QSpacerItem(min_width, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum),
        layout.rowCount(),
        0,
        1,
        layout.columnCount(),
    )


def warning_icon(widget: QWidget, size: int = 32) -> QPixmap:
    """An amber disc with a white exclamation mark, in place of the triangle."""
    screen = widget.screen() or QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(WARNING_COLOR)
    painter.drawEllipse(QRectF(2, 2, size - 4, size - 4))
    painter.setBrush(QColor("#FFFFFF"))
    middle = size / 2
    painter.drawRoundedRect(QRectF(middle - 1.6, size * 0.25, 3.2, size * 0.36), 1.6, 1.6)
    painter.drawEllipse(QPointF(middle, size * 0.73), 1.9, 1.9)
    painter.end()
    return pixmap


def style_message_box(box: QMessageBox) -> None:
    box.setStyleSheet(DIALOG_STYLE)
    box.setIconPixmap(warning_icon(box))
    fit_message_box(box)


def warning_box(parent: QWidget | None, title: str, text: str) -> QMessageBox:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    style_message_box(box)
    return box


def show_warning(parent: QWidget | None, title: str, text: str) -> None:
    """QMessageBox.warning in the app's colours."""
    box = warning_box(parent, title, text)
    box.exec()
    box.deleteLater()


def text_prompt(
    parent: QWidget | None, title: str, label: str, *, password: bool = False
) -> QInputDialog:
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setInputMode(QInputDialog.InputMode.TextInput)
    if password:
        dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
    dialog.setStyleSheet(DIALOG_STYLE)
    dialog.setMinimumWidth(MESSAGE_MIN_WIDTH + 80)
    return dialog


def ask_text(
    parent: QWidget | None, title: str, label: str, *, password: bool = False
) -> tuple[str, bool]:
    """QInputDialog.getText in the app's colours."""
    dialog = text_prompt(parent, title, label, password=password)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    text = dialog.textValue()
    dialog.deleteLater()
    return text, accepted


def style_progress_dialog(dialog: QProgressDialog) -> None:
    """A slim accent progress bar without the percentage, and the app's buttons."""
    minimum, maximum = dialog.minimum(), dialog.maximum()
    bar = QProgressBar(dialog)
    bar.setTextVisible(False)
    dialog.setBar(bar)
    dialog.setRange(minimum, maximum)
    dialog.setStyleSheet(DIALOG_STYLE)
