import gc

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QEnterEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QTextEdit,
)
import pytest

from ui_controls import (
    DROPDOWN_STYLE,
    DropdownComboBox,
    ModernScrollBar,
    POPUP_LIST_STYLE,
    use_modern_scroll_bar,
)


@pytest.fixture
def scroll_bar(qapp):
    view = QTextEdit()
    view.setStyleSheet("background-color: #FFFFFF;")
    bar = use_modern_scroll_bar(view)
    view.setPlainText("\n".join(f"Line {number}" for number in range(300)))
    view.resize(300, 200)
    view.show()
    qapp.processEvents()
    yield bar
    view.deleteLater()
    qapp.processEvents()


def _enter(bar):
    QApplication.sendEvent(bar, QEnterEvent(
        QPointF(5, 5), QPointF(5, 5), QPointF(bar.mapToGlobal(QPoint(5, 5)))
    ))


def _leave(bar):
    QApplication.sendEvent(bar, QEvent(QEvent.Type.Leave))


def _painted_width(bar):
    """Columns of the handle that grab() shows across its middle row."""
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    handle = bar.style().subControlRect(
        QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarSlider, bar
    )
    image = bar.grab().toImage()
    row = handle.center().y()
    handle_color = QColor(ModernScrollBar.REST_COLOR).lightness()
    return sum(
        1 for x in range(image.width())
        if image.pixelColor(x, row).alpha() > 128
        and image.pixelColor(x, row).lightness() <= handle_color + 8
    )


def test_scroll_bar_rests_thin_and_widens_smoothly_under_the_pointer(
    scroll_bar, motion_on, wait_until
):
    assert scroll_bar.thickness() == ModernScrollBar.REST_THICKNESS
    assert _painted_width(scroll_bar) == ModernScrollBar.REST_THICKNESS

    _enter(scroll_bar)
    assert scroll_bar.expansion() < 1  # Eases in rather than jumping.
    wait_until(lambda: scroll_bar.expansion() == 1)

    assert scroll_bar.thickness() == ModernScrollBar.HOVER_THICKNESS
    assert _painted_width(scroll_bar) == ModernScrollBar.HOVER_THICKNESS


def test_scroll_bar_settles_back_a_moment_after_the_pointer_leaves(
    scroll_bar, motion_on, wait_until
):
    _enter(scroll_bar)
    wait_until(lambda: scroll_bar.expansion() == 1)

    _leave(scroll_bar)
    QTest.qWait(ModernScrollBar.COLLAPSE_DELAY_MS // 2)
    assert scroll_bar.expansion() == 1
    wait_until(lambda: scroll_bar.expansion() == 0)


def test_scroll_bar_stays_wide_while_its_handle_is_dragged(
    scroll_bar, motion_on, wait_until
):
    _enter(scroll_bar)
    wait_until(lambda: scroll_bar.expansion() == 1)
    scroll_bar.setSliderDown(True)

    _leave(scroll_bar)
    QTest.qWait(ModernScrollBar.COLLAPSE_DELAY_MS + 100)
    assert scroll_bar.expansion() == 1

    scroll_bar.setSliderDown(False)
    scroll_bar.sliderReleased.emit()
    wait_until(lambda: scroll_bar.expansion() == 0)


def test_scroll_bar_widens_at_once_without_animations(scroll_bar):
    _enter(scroll_bar)
    assert scroll_bar.expansion() == 1


def test_scroll_bar_has_no_arrow_buttons_and_its_handle_can_be_grabbed(scroll_bar):
    option = QStyleOptionSlider()
    scroll_bar.initStyleOption(option)

    def rect(control):
        return scroll_bar.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar, option, control, scroll_bar
        )

    assert rect(QStyle.SubControl.SC_ScrollBarAddLine).height() == 0
    assert rect(QStyle.SubControl.SC_ScrollBarSubLine).height() == 0
    assert scroll_bar.width() == 12

    QTest.mousePress(
        scroll_bar, Qt.MouseButton.LeftButton,
        pos=rect(QStyle.SubControl.SC_ScrollBarSlider).center(),
    )
    assert scroll_bar.isSliderDown()
    QTest.mouseRelease(scroll_bar, Qt.MouseButton.LeftButton)


@pytest.fixture
def combo(qapp):
    combo = DropdownComboBox()
    combo.addItems(["Auto", "English", "Français"])
    combo.resize(120, 28)
    combo.show()
    qapp.processEvents()
    yield combo
    combo.hidePopup()
    combo.deleteLater()
    qapp.processEvents()


def test_dropdown_chevron_turns_over_while_the_list_is_open(
    combo, qapp, motion_on, wait_until
):
    assert not combo.is_open()

    combo.showPopup()
    qapp.processEvents()
    assert combo.is_open()
    wait_until(lambda: combo._turn.value == 1)

    combo.hidePopup()
    qapp.processEvents()
    assert not combo.is_open()
    wait_until(lambda: combo._turn.value == 0)


def test_dropdown_lists_keep_their_slim_scroll_bar_through_garbage_collection(combo):
    # Handed over through combo.view()'s temporary wrapper, the bar's Python
    # half once got collected and the list painted Qt's default bar again.
    gc.collect()

    assert isinstance(combo.view().verticalScrollBar(), ModernScrollBar)


def test_dropdown_lists_get_rounded_rows_and_a_slim_scroll_bar(combo):
    view = combo.view()

    assert DROPDOWN_STYLE in combo.styleSheet()
    assert view.styleSheet() == POPUP_LIST_STYLE
    # Style-sheet rows only apply through the styled delegate.
    assert type(view.itemDelegate()) is QStyledItemDelegate
    assert isinstance(view.verticalScrollBar(), ModernScrollBar)
