import gc
from types import SimpleNamespace

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QEnterEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QTextBrowser,
    QTextEdit,
    QWidget,
)
import pytest

import ui_controls
from ui_controls import (
    DROPDOWN_STYLE,
    TOOLTIP_WIDTH,
    MENU_STYLE,
    BusyPlaceholder,
    DropdownComboBox,
    ModernCheckBox,
    ModernScrollBar,
    POPUP_LIST_STYLE,
    style_menu,
    style_popup_list,
    use_modern_scroll_bar,
    wrapped_tooltip,
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


@pytest.mark.parametrize(
    ("view_type", "style_sheet", "background"),
    [
        # The source and back-translation boxes: a bare rule with a side bar,
        # which once drew a stray line beside the scroll bar.
        (QTextEdit, "background-color: #FAFAFA; border-left: 2px solid #D7BDE2;", "#fafafa"),
        # The changelog: a scoped rule, which once left the scroll bar on a
        # strip of the window's grey.
        (
            QTextBrowser,
            "QTextBrowser { background-color: #FFFFFF; border: 1px solid #E4E7ED;"
            " border-radius: 8px; }",
            "#ffffff",
        ),
    ],
)
def test_scroll_bar_floats_on_the_text_box_background(
    qapp, view_type, style_sheet, background
):
    window = QWidget()
    # The main window's bare rule reaches every widget inside it.
    window.setStyleSheet("background-color: #F5F7FA;")
    view = view_type(window)
    view.setStyleSheet(style_sheet)
    bar = use_modern_scroll_bar(view)
    view.setPlainText("\n".join(f"Line {number}" for number in range(300)))
    view.setGeometry(10, 10, 300, 200)
    window.resize(320, 220)
    window.show()
    qapp.processEvents()

    # A row below the handle, across the column the scroll bar sits in.
    image = view.grab().toImage()
    left = bar.mapTo(view, QPoint(0, 0)).x()
    row = bar.mapTo(view, QPoint(0, bar.height() - 12)).y()
    colours = {image.pixelColor(x, row).name() for x in range(left, left + bar.width())}
    assert colours == {background}
    window.deleteLater()
    qapp.processEvents()


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


def _see_through_list_window(window):
    """Mark a list window the way Qt 6.10's Windows 11 style does."""
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    window.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)


def _has_windows_border_and_shadow(window):
    flags = window.windowFlags()
    return not (
        window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        or flags & Qt.WindowType.FramelessWindowHint
        or flags & Qt.WindowType.NoDropShadowWindowHint
    )


def test_dropdown_lists_keep_the_windows_border_and_shadow(combo, qapp):
    # Qt 6.10 made the list window see-through so the style could paint a
    # border, which the list's white rows covered: no border, no shadow.
    window = combo.view().window()
    _see_through_list_window(window)

    combo.showPopup()
    qapp.processEvents()
    assert _has_windows_border_and_shadow(window)
    combo.hidePopup()

    # The style marks it again whenever it re-polishes the combo box.
    _see_through_list_window(window)
    combo.showPopup()
    qapp.processEvents()
    assert _has_windows_border_and_shadow(window)


@pytest.fixture
def corner_requests(monkeypatch):
    requests = []

    def set_window_attribute(hwnd, attribute, value, size):
        requests.append((hwnd, attribute, value._obj.value, size))
        return 0

    monkeypatch.setattr(ui_controls, "_dwm_set_window_attribute", lambda: set_window_attribute)
    monkeypatch.setattr(
        ui_controls, "QGuiApplication", SimpleNamespace(platformName=lambda: "windows")
    )
    return requests


def test_dropdown_lists_ask_windows_for_rounded_corners(combo, corner_requests):
    combo.showPopup()

    window = combo.view().window()
    assert corner_requests == [(int(window.winId()), 33, 2, 4)]


def test_completer_lists_ask_windows_for_rounded_corners(qapp, corner_requests):
    view = QListView()
    view.setWindowFlags(Qt.WindowType.Popup)

    style_popup_list(view)

    assert corner_requests == [(int(view.winId()), 33, 2, 4)]
    assert _has_windows_border_and_shadow(view)
    view.deleteLater()


def test_dropdown_lists_get_rounded_rows_and_a_slim_scroll_bar(combo):
    view = combo.view()

    assert DROPDOWN_STYLE in combo.styleSheet()
    assert view.styleSheet() == POPUP_LIST_STYLE
    # Style-sheet rows only apply through the styled delegate.
    assert type(view.itemDelegate()) is QStyledItemDelegate
    assert isinstance(view.verticalScrollBar(), ModernScrollBar)


@pytest.fixture
def check_box(qapp):
    # The Settings dialog's rule, under which Qt drew a ticked box as a bare tick.
    window = QWidget()
    window.setStyleSheet("QCheckBox { color: #303133; font-size: 13px; spacing: 8px; }")
    box = ModernCheckBox("Enable global selection translation", window)
    box.resize(box.sizeHint())
    window.resize(box.sizeHint())
    window.show()
    qapp.processEvents()
    # Wherever the pointer happens to be, test the resting look.
    box.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
    yield box
    window.deleteLater()
    qapp.processEvents()


def _box_colours(check_box):
    image = check_box.grab().toImage()
    box = check_box.indicator_rect().toAlignedRect()
    middle = box.center().y()
    return (
        # The left edge, halfway down: border or fill, never the tick.
        image.pixelColor(box.left(), middle),
        # Inside the box, near its top-left corner, clear of the tick.
        image.pixelColor(box.left() + 3, box.top() + 3),
    )


def test_ticked_check_box_fills_its_box_with_teal(check_box):
    check_box.setChecked(True)

    edge, inside = _box_colours(check_box)

    assert edge.name() == ModernCheckBox.TICKED_COLOR.name() == "#0e9a8b"
    assert inside.name() == ModernCheckBox.TICKED_COLOR.name()


def test_unticked_check_box_shows_an_empty_box_with_the_same_shape(check_box):
    check_box.setChecked(False)

    edge, inside = _box_colours(check_box)

    assert edge.name() == ModernCheckBox.BORDER_COLOR.name()
    assert inside.name() == "#ffffff"


def test_short_tooltips_stay_as_they_are():
    assert wrapped_tooltip("Source language") == "Source language"
    assert wrapped_tooltip("Provider: A\nModel: b") == "Provider: A\nModel: b"


def test_long_tooltips_wrap_at_a_readable_width():
    text = (
        "When on, each finished translation is translated back into the source "
        "language so you can check the meaning. <Each> request & more."
    )

    wrapped = wrapped_tooltip(text)

    assert wrapped.startswith(f"<table width='{TOOLTIP_WIDTH}'>")
    assert "&lt;Each&gt; request &amp; more." in wrapped


@pytest.fixture
def busy_view(qapp):
    view = QTextEdit()
    busy = BusyPlaceholder(view)
    busy.set_idle_text("Press Ctrl+Enter to translate")
    yield view, busy
    view.deleteLater()
    qapp.processEvents()


def test_busy_placeholder_counts_its_dots_while_motion_is_on(busy_view, motion_on):
    view, busy = busy_view

    busy.start("Translating...")
    seen = {view.placeholderText()}
    for _ in range(3):
        QTest.qWait(BusyPlaceholder.INTERVAL_MS + 30)
        seen.add(view.placeholderText())

    assert seen == {"Translating.", "Translating..", "Translating..."}
    busy.stop()
    assert view.placeholderText() == "Press Ctrl+Enter to translate"


def test_busy_placeholder_holds_still_without_motion(busy_view):
    view, busy = busy_view

    busy.start("Translating...")
    QTest.qWait(BusyPlaceholder.INTERVAL_MS + 30)

    assert view.placeholderText() == "Translating..."
    assert busy.is_active()
    busy.stop()
    assert not busy.is_active()


def test_text_menus_look_like_the_history_menu(qapp):
    view = QTextEdit()
    view.setPlainText("Some text")
    view.selectAll()
    menu = view.createStandardContextMenu()

    style_menu(menu)

    assert menu.styleSheet() == MENU_STYLE
    assert menu.actions()
    assert all(action.icon().isNull() for action in menu.actions())
    menu.deleteLater()
    view.deleteLater()
