from datetime import datetime

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget

from app_i18n import set_ui_language
from ui_motion import SmoothScroller
from ui_history import (
    SEARCH_MARK,
    HistoryPanel,
    beside_frame,
    history_marks,
    history_clock,
    history_day_label,
    history_groups,
    history_matches,
    history_preview,
)

NOW = datetime(2026, 11, 12, 18, 30)


def _entry(time, source, result, mode="en2zh"):
    return {"time": time, "mode": mode, "source": source, "result": result}


ENTRIES = [
    _entry("2026-11-12 14:02:11", "Attention cost grows with length.", "注意力开销随长度增长。"),
    _entry("2026-11-12 13:47:00", "ubiquitous", "*adj.* 无处不在的", mode="dictionary"),
    _entry("2026-11-11 22:18:40", "请把合同发给法务部门。", "Please send the contract to legal.", mode="zh2en"),
    _entry("2026-11-09 08:05:00", "Disconnect the power first.", "请先断开电源。"),
]


def test_previews_read_as_plain_text():
    source = "# Release notes\n\n- [x] **Faster** start with `cache`\n> See [the docs](https://x.y)\n---\n```\ncode\n```"

    assert history_preview(source) == "Release notes Faster start with cache See the docs code"
    assert history_preview("*adj.* 无处不在的") == "adj. 无处不在的"
    # Stars that aren't emphasis stay.
    assert history_preview("2*3*4 and a * b") == "2*3*4 and a * b"
    assert history_preview("snake_case_name") == "snake_case_name"


def test_entries_are_listed_under_their_day():
    groups = history_groups(ENTRIES, now=NOW)

    assert groups == [("Today", [0, 1]), ("Yesterday", [2]), ("Nov 9", [3])]
    assert history_day_label("2025-12-31 23:59:00", NOW) == "2025-12-31"
    assert history_clock("2026-11-12 14:02:11") == "14:02"
    assert history_clock("not a time") == "not a time"
    set_ui_language("zh-CN")
    assert [day for day, _rows in history_groups(ENTRIES, now=NOW)] == ["今天", "昨天", "11月9日"]


def test_search_needs_every_word_in_the_source_or_translation():
    assert history_matches(ENTRIES[0], "attention LENGTH")
    assert history_matches(ENTRIES[0], "开销")
    assert not history_matches(ENTRIES[0], "attention power")
    assert history_groups(ENTRIES, "contract", now=NOW) == [("Yesterday", [2])]


@pytest.fixture
def panel(qapp):
    parent = QWidget()
    # Leaves room on its right for History on the offscreen screen.
    parent.setGeometry(10, 10, 300, 420)
    rendered = []

    def render(view, text, kind):
        rendered.append((kind, text))
        view.setPlainText(text)

    panel = HistoryPanel(parent, render)
    panel.rendered = rendered
    panel.set_entries(ENTRIES)
    parent.show()
    panel.open_panel()
    qapp.processEvents()
    yield panel
    panel.close_panel()
    parent.close()
    parent.deleteLater()


def _entry_items(panel):
    return [
        panel.list.item(row) for row in range(panel.list.count())
        if panel.list.item(row).data(Qt.ItemDataRole.UserRole) is not None
    ]


def _click(panel, item):
    rect = panel.list.visualItemRect(item)
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_every_entry_is_listed_not_just_the_latest_twelve(qapp):
    parent = QWidget()
    panel = HistoryPanel(parent, lambda *_args: None)
    entries = [_entry(f"2026-11-12 10:{minute:02d}:00", f"Text {minute}", f"译文 {minute}") for minute in range(50)]

    panel.set_entries(entries)

    assert len(_entry_items(panel)) == 50
    assert panel.count.text() == "50 entries"
    parent.deleteLater()


def test_searching_narrows_the_list_and_says_how_many_match(panel):
    panel.search.setText("contract")

    assert len(_entry_items(panel)) == 1
    assert panel.count.text() == "1 of 4 entries"
    # Enter reads the match without reaching for the mouse.
    QTest.keyClick(panel.search, Qt.Key.Key_Return)
    assert panel.shown_entry() == ENTRIES[2]

    panel.show_list()
    panel.search.setText("nothing like this")
    assert panel.list.isHidden()
    assert not panel.empty.isHidden()
    assert panel.empty.text() == "No entries match your search"


def test_an_entry_is_read_in_the_panel_without_asking_the_window(panel):
    opened = []
    panel.open_requested.connect(opened.append)

    _click(panel, _entry_items(panel)[0])

    assert panel.pages.currentIndex() == 1
    assert panel.source_view.toPlainText() == ENTRIES[0]["source"]
    assert panel.result_view.toPlainText() == ENTRIES[0]["result"]
    assert panel.rendered == [("origin", ENTRIES[0]["source"]), ("result", ENTRIES[0]["result"])]
    assert panel.tag.text() == "EN → ZH"
    assert panel.lbl_result.text() == "CHINESE TRANSLATION"
    assert opened == []


def test_copy_takes_the_translation_and_leaves_the_window_alone(panel):
    opened = []
    panel.open_requested.connect(opened.append)
    panel.show_entry(ENTRIES[2])
    width = panel.btn_copy.width()

    panel.btn_copy.click()

    assert QApplication.clipboard().text() == "Please send the contract to legal."
    assert panel.btn_copy.text() == "Copied"
    assert panel.btn_copy.width() == width
    assert opened == []


def test_open_asks_first_while_a_translation_is_running(panel):
    opened = []
    panel.open_requested.connect(opened.append)
    panel.set_translating(True)
    panel.show_entry(ENTRIES[0])

    panel.btn_open.click()

    assert opened == []
    assert not panel.open_bar.isHidden()
    assert panel.open_bar.label.text() == "Opening this entry stops the translation in progress."

    panel.open_bar.no.click()
    assert opened == []
    assert panel.open_bar.isHidden()

    panel.btn_open.click()
    panel.open_bar.yes.click()
    assert opened == [ENTRIES[0]]


def test_a_finished_translation_withdraws_the_question(panel):
    opened = []
    panel.open_requested.connect(opened.append)
    panel.set_translating(True)
    panel.show_entry(ENTRIES[0])
    panel.btn_open.click()

    panel.set_translating(False)

    assert panel.open_bar.isHidden()
    panel.btn_open.click()
    assert opened == [ENTRIES[0]]


def test_the_list_says_a_translation_is_running_and_when_it_lands(panel):
    assert panel.status.isHidden()

    panel.set_translating(True)
    assert not panel.status.isHidden()
    assert "Translation keeps running while you browse" in panel.status.text()

    newest = _entry("2026-11-12 18:29:00", "A new sentence.", "一个新句子。")
    panel.set_translating(False)
    panel.set_entries([newest, *ENTRIES], added=True)
    assert "Finished translation added to the top" in panel.status.text()
    assert _entry_items(panel)[0].data(Qt.ItemDataRole.UserRole) == 0
    assert panel.count.text() == "5 entries"

    # The next translation starts the note over.
    panel.set_translating(True)
    assert "Translation keeps running while you browse" in panel.status.text()


def test_clearing_asks_first(panel):
    cleared = []
    panel.clear_requested.connect(lambda: cleared.append(True))

    panel.btn_clear.click()
    assert cleared == []
    assert not panel.clear_bar.isHidden()

    panel.clear_bar.yes.click()
    assert cleared == [True]


def test_escape_steps_back_then_closes(panel):
    closed = []
    panel.closed.connect(lambda: closed.append(True))
    panel.show_entry(ENTRIES[1])

    QTest.keyClick(panel, Qt.Key.Key_Escape)
    assert panel.pages.currentIndex() == 0
    assert closed == []

    QTest.keyClick(panel.search, Qt.Key.Key_Escape)
    assert panel.isHidden()
    assert closed == [True]


def test_history_is_a_window_of_its_own_beside_the_main_one(panel):
    parent = panel.parentWidget()

    assert panel.isWindow()
    assert panel.windowFlags() & Qt.WindowType.Tool == Qt.WindowType.Tool
    assert panel.windowTitle() == "History"
    owner = parent.frameGeometry()
    frame = panel.frameGeometry()
    assert frame.left() > owner.right()
    assert frame.top() == owner.top()
    assert frame.height() == owner.height()


def test_typing_in_the_main_window_leaves_history_open(panel, qapp):
    parent = panel.parentWidget()
    field = QLineEdit(parent)
    field.setGeometry(10, 10, 200, 28)
    field.show()
    button = QPushButton("Translate", parent)
    button.setGeometry(10, 50, 100, 28)
    button.show()
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))

    QTest.mouseClick(field, Qt.MouseButton.LeftButton)
    QTest.keyClicks(field, "Still typing")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(parent, Qt.MouseButton.RightButton, pos=QPoint(250, 300))

    assert panel.isVisible()
    assert field.text() == "Still typing"
    assert clicks == [True]


def test_history_moves_with_the_main_window_until_moved_by_hand(panel, qapp):
    parent = panel.parentWidget()

    parent.move(parent.x(), parent.y() + 30)
    panel.follow()
    assert panel.frameGeometry().top() == parent.frameGeometry().top()
    assert panel.is_docked()

    # Moved by hand, it stays put, also when opened again.
    panel.move(panel.x() + 40, panel.y() + 20)
    moved = panel.frameGeometry()
    assert not panel.is_docked()
    parent.move(parent.x(), parent.y() - 20)
    panel.follow()
    assert panel.frameGeometry() == moved
    panel.close_panel()
    panel.open_panel()
    assert panel.frameGeometry() == moved


def test_dropped_near_its_place_history_snaps_back_and_follows_again(panel, qapp):
    parent = panel.parentWidget()
    docked = panel.frameGeometry()

    # Dragged away, it stays put...
    panel.move(panel.x() + 150, panel.y() + 90)
    assert not panel.snap_if_near()
    assert not panel.is_docked()

    # ...and dropped close to where it was, it clicks back into place.
    panel.move(docked.left() + 25, docked.top() - 30)
    assert panel.snap_if_near()
    assert panel.frameGeometry() == docked
    assert panel.is_docked()
    parent.move(parent.x(), parent.y() + 20)
    panel.follow()
    assert panel.frameGeometry().top() == parent.frameGeometry().top()


def test_history_snaps_to_whichever_side_it_is_dropped_by(qapp):
    parent = QWidget()
    # Narrow and central, so History fits on either side of it offscreen.
    parent.setGeometry(340, 10, 100, 420)
    panel = HistoryPanel(parent, lambda *_args: None)
    panel.resize(320, 400)
    parent.show()
    panel.open_panel()
    qapp.processEvents()
    owner = parent.frameGeometry()
    assert panel.frameGeometry().left() > owner.right()

    left_spot = owner.left() - 8 - panel.frameGeometry().width()
    panel.move(left_spot - 20, owner.top() + 15)
    assert panel.snap_if_near()
    assert panel.frameGeometry().right() < owner.left()

    # It keeps to that side as the main window moves.
    parent.move(parent.x() + 10, parent.y())
    panel.follow()
    assert panel.frameGeometry().right() == parent.frameGeometry().left() - 8 - 1
    panel.close_panel()
    parent.deleteLater()


def test_the_end_of_a_drag_by_the_title_bar_snaps_it_back(panel, qapp):
    import ctypes
    from ctypes import wintypes

    from PyQt6 import sip
    from PyQt6.QtCore import QByteArray

    docked = panel.frameGeometry()
    panel.move(docked.left() + 20, docked.top() + 20)
    message = wintypes.MSG()
    message.message = 0x0232  # WM_EXITSIZEMOVE

    panel.nativeEvent(QByteArray(b"windows_generic_MSG"), sip.voidptr(ctypes.addressof(message)))
    qapp.processEvents()

    assert panel.frameGeometry() == docked


@pytest.mark.parametrize(
    ("prefer", "expected_left"),
    [("right", 668), ("left", 172)],
    ids=["prefers-right", "prefers-left"],
)
def test_history_keeps_to_the_side_it_prefers_while_it_fits(prefer, expected_left):
    # A narrow main window in the middle, with room on both sides.
    area = QRect(0, 0, 1920, 1040)
    owner = QRect(600, 80, 60, 700)

    frame = beside_frame(owner, area, 420, prefer=prefer)

    assert frame.left() == expected_left


def test_the_close_button_closes_history(panel):
    closed = []
    panel.closed.connect(lambda: closed.append(True))

    panel.close()

    assert panel.isHidden()
    assert closed == [True]


@pytest.mark.parametrize(
    ("owner", "expected"),
    [
        # Room on the right: beside it there, a little apart, as tall as it.
        (QRect(100, 80, 560, 700), QRect(668, 80, 420, 700)),
        # Near the right edge: on its left instead.
        (QRect(1300, 80, 560, 700), QRect(872, 80, 420, 700)),
        # Maximized: over its edge on the side with more room.
        (QRect(-8, -8, 1936, 1056), QRect(1500, 0, 420, 1040)),
        # Taller than the screen, or partly off it: kept on it.
        (QRect(200, -40, 560, 1200), QRect(768, 0, 420, 1040)),
    ],
    ids=["right", "left", "maximized", "kept-on-screen"],
)
def test_history_goes_beside_the_main_window_where_there_is_room(owner, expected):
    area = QRect(0, 0, 1920, 1040)

    assert beside_frame(owner, area, 420) == expected


def test_the_list_and_entry_glide_like_the_window(qapp, motion_on, wait_until):
    parent = QWidget()
    parent.setGeometry(10, 10, 300, 420)
    panel = HistoryPanel(parent, lambda view, text, _kind: view.setPlainText(text))
    entries = [_entry(f"2026-11-12 10:{minute:02d}:00", f"Text {minute}", f"译文 {minute}") for minute in range(50)]
    panel.set_entries(entries)
    parent.show()
    panel.open_panel()
    qapp.processEvents()
    bar = panel.list.verticalScrollBar()
    assert bar.maximum() > 0

    viewport = panel.list.viewport()
    wheel = QWheelEvent(
        QPointF(10, 10), QPointF(viewport.mapToGlobal(QPoint(10, 10))),
        QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(viewport, wheel)

    # It sets off from where it was rather than jumping.
    assert panel.list_scroller.is_scrolling()
    assert bar.value() == 0
    wait_until(lambda: not panel.list_scroller.is_scrolling())
    assert bar.value() > 0

    for view in (panel.source_view, panel.result_view):
        assert isinstance(view.smooth_scroller, SmoothScroller)
        assert view.findChild(SmoothScroller) is view.smooth_scroller
    panel.close_panel()
    parent.deleteLater()


def test_search_words_are_found_wherever_they_show_ignoring_case():
    text = "Please send the Contract to legal; contracts too."

    assert history_marks(text, ["contract", "LEGAL"]) == [(16, 24), (28, 33), (35, 43)]
    assert history_marks(text, []) == []
    # The longer of two overlapping words wins.
    assert history_marks("contracting", ["contract", "contracting"]) == [(0, 11)]


def _yellow_pixels(widget):
    image = widget.grab().toImage()
    return sum(
        image.pixelColor(x, y).name() == SEARCH_MARK.lower()
        for x in range(0, image.width(), 2)
        for y in range(0, image.height(), 2)
    )


def test_search_words_are_marked_in_the_list(panel, qapp):
    assert _yellow_pixels(panel.list.viewport()) == 0

    panel.search.setText("contract")
    qapp.processEvents()

    assert _yellow_pixels(panel.list.viewport()) > 0


def test_search_words_are_marked_in_an_open_entry(panel):
    entry_changes = []
    panel.entry_changed.connect(lambda: entry_changes.append(True))
    panel.search.setText("contract legal")

    panel.show_entry(ENTRIES[2])

    marks = panel.result_view.extraSelections()
    assert [mark.cursor.selectedText() for mark in marks] == ["contract", "legal"]
    assert {mark.format.background().color().name() for mark in marks} == {SEARCH_MARK.lower()}
    assert panel.source_view.extraSelections() == []
    assert panel.result_view.textCursor().position() == ENTRIES[2]["result"].index("contract")
    assert entry_changes == [True]

    # Without a search, nothing is marked.
    panel.show_list()
    panel.search.clear()
    panel.show_entry(ENTRIES[2])
    assert panel.result_view.extraSelections() == []


def test_the_first_search_word_is_brought_into_view(panel):
    long_source = "\n".join(f"Line {number}" for number in range(200)) + "\nThe needle is here."
    entry = _entry("2026-11-12 09:00:00", long_source, "针在这里。")
    panel.search.setText("needle")

    panel.show_entry(entry)

    assert panel.source_view.verticalScrollBar().value() > 0


def test_locate_is_offered_for_translations_but_not_dictionary_entries(panel):
    panel.show_entry(ENTRIES[0])
    assert panel.locate_available()

    panel.show_entry(ENTRIES[1])
    assert not panel.locate_available()

    panel.show_list()
    assert not panel.locate_available()


def test_arrow_keys_choose_entries_from_the_search_box(panel):
    QTest.keyClick(panel.search, Qt.Key.Key_Down)
    QTest.keyClick(panel.search, Qt.Key.Key_Down)
    QTest.keyClick(panel.search, Qt.Key.Key_Return)

    assert panel.shown_entry() == ENTRIES[1]


def test_the_panel_follows_the_interface_language(panel):
    set_ui_language("zh-CN")
    panel.retranslate()

    assert panel.windowTitle() == "历史记录"
    assert panel.search.placeholderText() == "搜索原文或译文"
    assert panel.count.text() == "4 条记录"
    panel.show_entry(ENTRIES[1])
    assert panel.tag.text() == "词典"
