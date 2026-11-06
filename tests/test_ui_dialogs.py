from PyQt6.QtWidgets import QLineEdit, QProgressBar, QProgressDialog, QWidget
import pytest

from ui_dialogs import (
    DIALOG_STYLE,
    MESSAGE_MIN_WIDTH,
    WARNING_COLOR,
    style_progress_dialog,
    text_prompt,
    warning_box,
)


@pytest.fixture
def parent(qapp):
    widget = QWidget()
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def test_warnings_use_the_app_icon_buttons_and_width(parent, qapp):
    box = warning_box(parent, "Couldn't save settings", "Try again.")
    box.show()
    qapp.processEvents()

    # An amber disc in place of Windows' yellow triangle.
    icon = box.iconPixmap().toImage()
    ratio = box.iconPixmap().devicePixelRatio()
    assert icon.pixelColor(round(6 * ratio), round(16 * ratio)).name() == WARNING_COLOR.name()
    assert box.styleSheet() == DIALOG_STYLE
    assert box.width() >= MESSAGE_MIN_WIDTH
    box.close()


def test_key_prompt_hides_what_is_typed_and_uses_the_app_style(parent):
    dialog = text_prompt(parent, "Enter API key", "Key:", password=True)

    assert dialog.textEchoMode() == QLineEdit.EchoMode.Password
    assert dialog.styleSheet() == DIALOG_STYLE
    assert dialog.minimumWidth() > MESSAGE_MIN_WIDTH


def test_progress_bar_is_slim_and_keeps_its_range(parent):
    dialog = QProgressDialog("Downloading", "Cancel", 0, 0, parent)

    style_progress_dialog(dialog)

    assert (dialog.minimum(), dialog.maximum()) == (0, 0)
    assert dialog.styleSheet() == DIALOG_STYLE
    assert not dialog.findChild(QProgressBar).isTextVisible()
