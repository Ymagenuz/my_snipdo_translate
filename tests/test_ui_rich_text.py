from PyQt6.QtCore import QMimeData, Qt
from PyQt6.QtGui import QFont, QTextBlockFormat, QTextCursor, QTextFormat
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QTextEdit
import pytest

from ui_rich_text import (
    CODE_FAMILIES,
    HEADING_PIXEL_SIZES,
    MARK_COLOR,
    MARKDOWN_MIME_TYPE,
    MarkdownEditing,
    quote_level,
    shows_as_markdown,
)


CTRL = Qt.KeyboardModifier.ControlModifier
CTRL_SHIFT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier

# Written the way the editor writes Markdown, so it reads back unchanged.
MARKDOWN = (
    "# Title\n\n"
    "Some **bold**, *italic*, ~~gone~~, `code` and [a link](https://example.com).\n\n"
    "- one\n"
    "- **two**\n"
    "  - nested\n\n"
    "1. first\n"
    "2. second\n\n"
    "> quoted\n\n"
    "```python\n"
    "x = a*b*c\n"
    "```\n\n"
    "---\n\n"
    "| A | B |\n"
    "| --- | --- |\n"
    "| 1 | 2 |\n\n"
    "Energy: $E = m*c^2$ and $a_1 * b_2$."
)


class Editor(MarkdownEditing, QTextEdit):
    pass


@pytest.fixture
def editor(qapp):
    view = Editor()
    view.resize(420, 320)
    view.show()
    view.setFocus()
    qapp.processEvents()
    yield view
    view.deleteLater()
    qapp.processEvents()


def _paste(view, *, html=None, text=None):
    mime = QMimeData()
    if html is not None:
        mime.setHtml(html)
    if text is not None:
        mime.setText(text)
    view.insertFromMimeData(mime)


def _select(view, text):
    start = view.toPlainText().index(text)
    cursor = view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(start + len(text), QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)


def _type_lines(view, *lines):
    for index, line in enumerate(lines):
        if index:
            QTest.keyClick(view, Qt.Key.Key_Return)
        QTest.keyClicks(view, line)


def _move_to(view, qapp, text, offset=0):
    """Put the caret in the text as it shows, and let its marks show."""
    cursor = view.textCursor()
    cursor.setPosition(view.toPlainText().index(text) + offset)
    view.setTextCursor(cursor)
    qapp.processEvents()


def _shown_without_marks(view, qapp):
    """The text as it shows once the box loses focus and its marks hide."""
    view.clearFocus()
    qapp.processEvents()
    text = view.toPlainText()
    view.setFocus()
    qapp.processEvents()
    return text


def _heading_level(view):
    return view.document().begin().blockFormat().headingLevel()


def _runs(view):
    runs = []
    block = view.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            runs.append((block, fragment.text(), fragment.charFormat()))
            iterator += 1
        block = block.next()
    return runs


@pytest.mark.parametrize(
    ("text", "formatted"),
    [
        ("Just a sentence.", False),
        ("# Heading", True),
        ("- item", True),
        ("Some **bold** words", True),
        ("Read [the docs](https://example.com)", True),
        (r"Let $x_1 * y$ be **bold**", False),
        ("\\section{Intro}\n\nText with $a_1$.", False),
        ("## Notes\n\nEnergy $E=mc^2$.", True),
    ],
)
def test_markdown_shows_as_formatting_but_latex_alone_stays_source(text, formatted):
    assert shows_as_markdown(text) == formatted


def test_typed_plain_text_reads_back_exactly_as_typed(editor):
    _type_lines(editor, "First line with a_b and 2 * 3", "", "Second line")

    assert editor.source_text() == "First line with a_b and 2 * 3\n\nSecond line"
    assert editor.source_text() == editor.toPlainText()


def test_loaded_markdown_shows_as_formatting_and_reads_back_unchanged(editor):
    editor.set_source_text(MARKDOWN)

    shown = editor.toPlainText()
    for mark in ("#", "**", "~~", "`", "](", "> ", "- one"):
        assert mark not in shown
    assert editor.document().begin().blockFormat().headingLevel() == 1
    assert editor.source_text() == MARKDOWN

    # Once edited, the box writes its own Markdown, which matches the source.
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClicks(editor, " Done.")
    assert editor.source_text() == MARKDOWN + " Done."


def test_loaded_source_comes_back_as_given_until_it_is_edited(editor):
    source = "* starred item\n* another\n\n__strong__ words"
    editor.set_source_text(source)

    assert editor.document().begin().textList() is not None
    assert editor.source_text() == source

    QTest.keyClicks(editor, "x")
    assert editor.source_text() == "- xstarred item\n- another\n\n**strong** words"


def test_latex_without_markdown_loads_as_plain_source(editor):
    source = r"\section{Intro} Let $x_1 * y$ be **this**."
    editor.set_source_text(source)

    assert editor.toPlainText() == source
    assert editor.source_text() == source


def test_formulas_in_markdown_keep_their_source(editor):
    editor.set_source_text("## Energy\n\nIt is $a_1 * b_2 * c$ here.")

    assert editor.toPlainText().splitlines() == ["Energy", "It is $a_1 * b_2 * c$ here."]
    QTest.keyClicks(editor, "The ")
    assert editor.source_text() == "## The Energy\n\nIt is $a_1 * b_2 * c$ here."


def test_pasted_html_keeps_its_structure_but_not_its_look(editor):
    _paste(
        editor,
        html=(
            '<h2 style="color:red;font-size:30px;font-family:Georgia">Heading</h2>'
            '<p style="font-family:Georgia;font-size:22px;color:#333;background:#ff0">'
            'Text <span style="font-weight:700">bold span</span> '
            '<b style="font-weight:normal">plain</b> <em>em</em> '
            '<s>gone</s> <code>inline</code> <a href="https://a.b">a link</a></p>'
            "<blockquote><p>A quote</p></blockquote>"
            "<pre><code>code line 1\ncode line 2</code></pre>"
            "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul>"
            '<ol start="3"><li>three</li><li>four</li></ol>'
            "<table><tr><th>H1</th><th>H2</th></tr><tr><td>c1</td><td>c2</td></tr></table>"
        ),
        text="Heading Text bold span ...",
    )

    assert editor.source_text() == (
        "## Heading\n\n"
        "Text **bold span** plain *em* ~~gone~~ `inline` [a link](https://a.b)\n\n"
        "> A quote\n\n"
        "```\ncode line 1\ncode line 2\n```\n\n"
        "- one\n- two\n  - nested\n\n"
        "3. three\n4. four\n\n"
        "| H1 | H2 |\n| --- | --- |\n| c1 | c2 |"
    )
    for block, text, fmt in _runs(editor):
        heading = block.blockFormat().headingLevel()
        size = fmt.property(QTextFormat.Property.FontPixelSize)
        assert size == (HEADING_PIXEL_SIZES[heading] if heading else None), text
        assert not fmt.hasProperty(QTextFormat.Property.FontPointSize), text
        assert fmt.fontFamilies() in (None, [], CODE_FAMILIES), text
        if not fmt.isAnchor():
            assert not fmt.hasProperty(QTextFormat.Property.ForegroundBrush), text
        assert not block.blockFormat().hasProperty(
            QTextFormat.Property.BlockTopMargin
        ) or heading, text
    # Painting the quote bars must not measure the table before it is laid out.
    assert not editor.viewport().grab().isNull()


def test_pasted_latex_source_is_kept_as_written(editor):
    source = r"\section{Intro} Let $x_1 * y$ be **this**."
    _paste(editor, html="<b>rendered</b>", text=source)

    assert editor.source_text() == source


def test_pasted_markdown_text_is_formatted(editor):
    _paste(editor, text="## Notes\n\n- a\n- **b**")

    assert editor.document().begin().blockFormat().headingLevel() == 2
    assert editor.toPlainText() == "Notes\na\nb"
    assert editor.source_text() == "## Notes\n\n- a\n- **b**"


def test_pasted_plain_text_goes_in_as_it_is(editor):
    _paste(editor, text="line one\nline two\n\nline * three")

    assert editor.source_text() == "line one\nline two\n\nline * three"


@pytest.mark.parametrize(
    ("typed", "shown"),
    [("# Head", "Head"), ("- item", "item"), ("1. item", "item"), ("> said", "said")],
)
def test_typed_markdown_marks_become_formatting(editor, qapp, typed, shown):
    QTest.keyClicks(editor, typed)

    assert _shown_without_marks(editor, qapp) == shown
    assert editor.source_text() == typed


def test_typed_closing_marks_format_the_text_and_undo_brings_them_back(editor, qapp):
    QTest.keyClicks(editor, "say **this**, _that_, ~~old~~ and `code`")

    assert editor.source_text() == "say **this**, *that*, ~~old~~ and `code`"
    # The caret is just past the code, so its marks show until it leaves.
    assert editor.toPlainText() == "say this, that, old and `code`"
    assert _shown_without_marks(editor, qapp) == "say this, that, old and code"

    QTest.keyClick(editor, Qt.Key.Key_Z, CTRL)
    assert editor.toPlainText().endswith("and `code`")
    *_, (_block, text, fmt) = _runs(editor)
    assert text.endswith("`code`") and fmt.fontFamilies() in (None, [])

    QTest.keyClick(editor, Qt.Key.Key_Y, CTRL)
    assert editor.source_text().endswith("and `code`")
    assert _shown_without_marks(editor, qapp).endswith("and code")


def test_marks_in_words_formulas_and_arithmetic_are_left_alone(editor):
    typed = "snake_case_name, file_name_, $a*b*c$ and 2 * 3 * 4"
    QTest.keyClicks(editor, typed)

    assert editor.toPlainText() == typed
    assert editor.source_text() == typed


def test_fences_and_rules_typed_on_their_own_line_start_code_and_rules(editor):
    _type_lines(editor, "```py", "x = a*b*c", "", "---", "after")

    assert editor.source_text() == "```py\nx = a*b*c\n```\n\n---\n\nafter"
    last = editor.document().lastBlock()
    assert last.charFormat().fontFamilies() in (None, [])


def test_return_on_an_empty_quote_line_leaves_the_quote(editor):
    _type_lines(editor, "> quoted", "", "plain")

    assert editor.source_text() == "> quoted\n\nplain"
    assert quote_level(editor.document().lastBlock().blockFormat()) == 0


def test_backspace_at_a_heading_start_makes_it_text(editor):
    QTest.keyClicks(editor, "# Title")
    editor.moveCursor(QTextCursor.MoveOperation.StartOfBlock)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)

    assert editor.source_text() == "Title"
    assert editor.textCursor().charFormat().fontWeight() == QFont.Weight.Normal.value


def test_marktext_shortcuts_change_paragraphs_and_emphasis(editor):
    QTest.keyClicks(editor, "plain words here")
    _select(editor, "words")

    steps = [
        (Qt.Key.Key_B, CTRL, "plain **words** here"),
        (Qt.Key.Key_I, CTRL, "plain ***words*** here"),
        (Qt.Key.Key_D, CTRL, "plain ~~***words***~~ here"),
        (Qt.Key.Key_R, CTRL_SHIFT, "plain words here"),
        (Qt.Key.Key_QuoteLeft, CTRL, "plain `words` here"),
        (Qt.Key.Key_QuoteLeft, CTRL, "plain words here"),
        (Qt.Key.Key_2, CTRL, "## plain words here"),
        (Qt.Key.Key_2, CTRL, "plain words here"),
        (Qt.Key.Key_3, CTRL, "### plain words here"),
        (Qt.Key.Key_0, CTRL, "plain words here"),
        (Qt.Key.Key_H, CTRL, "- plain words here"),
        (Qt.Key.Key_G, CTRL, "1. plain words here"),
        (Qt.Key.Key_G, CTRL, "plain words here"),
        (Qt.Key.Key_Q, CTRL_SHIFT, "> plain words here"),
        (Qt.Key.Key_K, CTRL_SHIFT, "```\nplain words here\n```"),
        (Qt.Key.Key_K, CTRL_SHIFT, "plain words here"),
    ]
    for key, modifiers, expected in steps:
        QTest.keyClick(editor, key, modifiers)
        assert editor.source_text() == expected, (key, modifiers)
    # Text that was a heading or code is plain again, not bold or monospace.
    for _block, text, fmt in _runs(editor):
        assert fmt.fontWeight() == QFont.Weight.Normal.value, text
        assert fmt.fontFamilies() in (None, []), text


def test_bold_without_a_selection_applies_to_what_is_typed_next(editor):
    QTest.keyClicks(editor, "a ")
    QTest.keyClick(editor, Qt.Key.Key_B, CTRL)
    QTest.keyClicks(editor, "bold")
    QTest.keyClick(editor, Qt.Key.Key_B, CTRL)
    QTest.keyClicks(editor, " word")

    assert editor.source_text() == "a **bold** word"


def test_copying_formatted_text_carries_its_markdown(editor, qapp):
    editor.set_source_text("# Title\n\n**Bold** text")
    editor.selectAll()

    mime = editor.createMimeDataFromSelection()
    assert mime.text() == "# Title\n\n**Bold** text"
    assert bytes(mime.data(MARKDOWN_MIME_TYPE)).decode() == "# Title\n\n**Bold** text"
    assert mime.hasHtml()

    other = Editor()
    other.insertFromMimeData(mime)
    assert other.source_text() == "# Title\n\n**Bold** text"
    other.deleteLater()


def test_copying_plain_text_keeps_qt_s_own_data(editor):
    QTest.keyClicks(editor, "plain words")
    editor.selectAll()

    assert editor.createMimeDataFromSelection().text() == "plain words"


def test_context_menu_offers_marktext_s_paragraph_and_format_commands(editor):
    QTest.keyClicks(editor, "menu text")
    menu = editor.createStandardContextMenu()
    submenus = {
        action.text(): action.menu() for action in menu.actions() if action.menu()
    }

    assert list(submenus) == ["Paragraph", "Format"]
    paragraph = {action.text(): action for action in submenus["Paragraph"].actions()}
    formatting = {action.text(): action for action in submenus["Format"].actions()}
    assert paragraph["Heading 2"].shortcut().toString() == "Ctrl+2"
    assert formatting["Clear formatting"].shortcut().toString() == "Ctrl+Shift+R"

    paragraph["Heading 2"].trigger()
    assert editor.source_text() == "## menu text"
    menu.deleteLater()

    editor.setReadOnly(True)
    read_only_menu = editor.createStandardContextMenu()
    assert not any(action.menu() for action in read_only_menu.actions())
    read_only_menu.deleteLater()


def test_read_only_view_ignores_markdown_shortcuts(editor):
    editor.setPlainText("text")
    editor.setReadOnly(True)
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_B, CTRL)

    assert editor.source_text() == "text"


def test_loaded_and_pasted_text_is_laid_out_and_painted(editor, qapp):
    # Qt skips laying out the first block after its block format changes.
    editor.set_source_text("**Bold** first line")
    qapp.processEvents()
    image = editor.viewport().grab().toImage()
    layout = editor.document().documentLayout()

    assert not layout.blockBoundingRect(editor.document().begin()).isEmpty()
    assert any(
        image.pixelColor(x, y).lightness() < 128
        for x in range(60)
        for y in range(24)
    )


def test_quote_bars_are_painted_beside_quotes(editor, qapp):
    editor.set_source_text("> quoted line")
    # With the caret on it, the line shows its "> " instead.
    editor.clearFocus()
    qapp.processEvents()
    image = editor.viewport().grab().toImage()
    layout = editor.document().documentLayout()
    rect = layout.blockBoundingRect(editor.document().begin())

    color = image.pixelColor(int(rect.left()) + 3, int(rect.center().y()))
    assert color.name() == "#d5dce6"


def test_backspace_at_a_nested_quote_start_steps_out_one_level(editor, qapp):
    editor.set_source_text("> > deep")
    qapp.processEvents()
    editor.moveCursor(QTextCursor.MoveOperation.StartOfBlock)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.source_text() == "> deep"

    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.source_text() == "deep"


def test_formatting_at_the_caret_shows_its_marks_until_the_caret_leaves(editor, qapp):
    source = "Some **bold** and [a link](https://example.com) here"
    editor.set_source_text(source)

    _move_to(editor, qapp, "bold", 2)
    assert editor.toPlainText() == "Some **bold** and a link here"
    marks = [fmt for _block, text, fmt in _runs(editor) if text == "**"]
    assert len(marks) == 2
    assert all(fmt.foreground().color().name() == MARK_COLOR.lower() for fmt in marks)
    assert editor.source_text() == source

    _move_to(editor, qapp, "link", 1)
    assert editor.toPlainText() == "Some bold and [a link](https://example.com) here"
    assert _shown_without_marks(editor, qapp) == "Some bold and a link here"
    assert editor.source_text() == source


def test_deleting_a_span_s_marks_makes_it_plain_text(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 4)
    QTest.keyClick(editor, Qt.Key.Key_Delete)
    QTest.keyClick(editor, Qt.Key.Key_Delete)
    for _ in range(4):
        QTest.keyClick(editor, Qt.Key.Key_Left)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)

    # Plain at once, before the caret leaves.
    assert editor.toPlainText() == "Some bold text"
    assert all(fmt.fontWeight() == QFont.Weight.Normal.value for _b, _t, fmt in _runs(editor))
    assert editor.source_text() == "Some bold text"


def test_a_mark_left_without_its_pair_stays_as_typed(editor, qapp):
    editor.set_source_text("an *idea* here")
    _move_to(editor, qapp, "idea", 4)
    QTest.keyClick(editor, Qt.Key.Key_Delete)

    assert _shown_without_marks(editor, qapp) == "an *idea here"
    assert not any(fmt.fontItalic() for _b, _t, fmt in _runs(editor))


def test_marks_typed_inside_shown_marks_format_as_markdown(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    QTest.keyClicks(editor, "*it*")

    assert editor.toPlainText() == "Some **bo*it*ld** text"
    assert editor.source_text() == "Some **bo*it*ld** text"
    italic = [text for _b, text, fmt in _runs(editor) if fmt.fontItalic()]
    assert italic == ["it"]


def test_a_heading_shows_its_marks_and_deleting_them_lowers_it(editor, qapp):
    editor.set_source_text("## Notes\n\nText")
    _move_to(editor, qapp, "Notes")
    assert editor.toPlainText() == "## Notes\nText"
    assert editor.source_text() == "## Notes\n\nText"

    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert _heading_level(editor) == 1
    assert editor.source_text() == "# Notes\n\nText"

    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert _heading_level(editor) == 0
    assert editor.toPlainText() == "Notes\nText"
    assert all(fmt.fontWeight() == QFont.Weight.Normal.value for _b, _t, fmt in _runs(editor))


def test_a_mark_typed_into_a_heading_s_marks_raises_it(editor, qapp):
    editor.set_source_text("# Title")
    qapp.processEvents()
    QTest.keyClick(editor, Qt.Key.Key_Home)
    QTest.keyClicks(editor, "#")

    assert editor.toPlainText() == "## Title"
    assert _heading_level(editor) == 2
    size = [fmt for _b, text, fmt in _runs(editor) if text == "Title"][0].property(
        QTextFormat.Property.FontPixelSize
    )
    assert size == HEADING_PIXEL_SIZES[2]
    assert editor.source_text() == "## Title"


def test_text_typed_just_past_a_span_s_marks_is_outside_it(editor, qapp):
    editor.set_source_text("Some **bold**")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    qapp.processEvents()
    QTest.keyClick(editor, Qt.Key.Key_End)
    QTest.keyClicks(editor, " more")

    assert editor.source_text() == "Some **bold** more"
    assert editor.toPlainText() == "Some bold more"


def test_undo_and_redo_skip_the_marks_shown_and_hidden(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    QTest.keyClicks(editor, "XX")
    _move_to(editor, qapp, "text")
    _move_to(editor, qapp, "Some")

    QTest.keyClick(editor, Qt.Key.Key_Z, CTRL)
    assert editor.source_text() == "Some **bold** text"
    QTest.keyClick(editor, Qt.Key.Key_Y, CTRL)
    assert editor.source_text() == "Some **boXXld** text"

    # With only marks to undo, Undo leaves the text alone.
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    _move_to(editor, qapp, "Some")
    QTest.keyClick(editor, Qt.Key.Key_Z, CTRL)
    assert editor.source_text() == "Some **bold** text"
    assert editor.toPlainText() == "Some bold text"


def test_the_menu_s_undo_skips_the_marks_too(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    QTest.keyClicks(editor, "XX")
    menu = editor.createStandardContextMenu()
    undo = next(action for action in menu.actions() if action.objectName() == "edit-undo")

    assert undo.isEnabled()
    undo.trigger()
    assert editor.source_text() == "Some **bold** text"
    menu.deleteLater()


def test_a_selection_hides_the_marks_and_keeps_its_text(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    QTest.keyClick(editor, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)

    assert editor.toPlainText() == "Some bold text"
    assert editor.textCursor().selectedText() == "l"
    QTest.keyClick(editor, Qt.Key.Key_I, CTRL)
    assert editor.source_text() == "Some **bo*l*d** text"


def test_commands_act_on_the_formatting_not_its_marks(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    QTest.keyClick(editor, Qt.Key.Key_Q, CTRL_SHIFT)

    assert editor.source_text() == "> Some **bold** text"
    assert editor.toPlainText() == "> Some **bold** text"
    assert _shown_without_marks(editor, qapp) == "Some bold text"


def test_marks_hide_when_the_box_is_read_only(editor, qapp):
    editor.set_source_text("Some **bold** text")
    _move_to(editor, qapp, "bold", 2)
    editor.setReadOnly(True)
    qapp.processEvents()

    assert editor.toPlainText() == "Some bold text"


def test_marks_shown_after_a_format_toggle_add_no_stray_marks(editor, qapp):
    editor.set_source_text("a **b** and *it* x")
    _move_to(editor, qapp, "it", 2)
    QTest.keyClick(editor, Qt.Key.Key_B, CTRL)
    QTest.keyClick(editor, Qt.Key.Key_Shift)
    assert editor.toPlainText() == "a b and it x"

    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert editor.toPlainText() == "a b and *it* x"
    assert editor.source_text() == "a **b** and *it* x"


@pytest.mark.parametrize(
    ("key", "modifiers"),
    [
        (Qt.Key.Key_H, CTRL),
        (Qt.Key.Key_G, CTRL),
        (Qt.Key.Key_Q, CTRL_SHIFT),
        (Qt.Key.Key_K, CTRL_SHIFT),
        (Qt.Key.Key_2, CTRL),
    ],
)
def test_the_hint_hides_once_a_format_starts_on_the_empty_line(editor, qapp, key, modifiers):
    editor.setPlaceholderText("Type here")
    assert QTextEdit.placeholderText(editor) == "Type here"

    QTest.keyClick(editor, key, modifiers)
    # Shown marks would hide it anyway; the hint must stay hidden without them.
    editor.clearFocus()
    qapp.processEvents()
    assert editor.toPlainText() == ""
    assert QTextEdit.placeholderText(editor) == ""
    assert editor.placeholderText() == "Type here"

    editor.setFocus()
    qapp.processEvents()
    QTest.keyClick(editor, key, modifiers)
    assert QTextEdit.placeholderText(editor) == "Type here"


@pytest.mark.parametrize("typed", ["- ", "1. ", "> "])
def test_the_hint_hides_once_a_typed_mark_starts_a_format(editor, qapp, typed):
    editor.setPlaceholderText("Type here")
    QTest.keyClicks(editor, typed)

    # The marks show while the caret is on the line, and the format without them.
    assert editor.toPlainText() == typed
    editor.clearFocus()
    qapp.processEvents()
    assert editor.toPlainText() == ""
    assert QTextEdit.placeholderText(editor) == ""

    editor.setFocus()
    qapp.processEvents()
    for _ in typed:
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == ""
    assert QTextEdit.placeholderText(editor) == "Type here"


def _line_format(view, index=0):
    block = view.document().findBlockByNumber(index)
    return block, block.blockFormat()


@pytest.mark.parametrize(
    ("source", "line", "shown", "hidden"),
    [
        ("- one\n- two", "two", "one\n- two", "one\ntwo"),
        ("1. first\n2. second", "second", "first\n2. second", "first\nsecond"),
        ("- [ ] open\n- [x] done", "done", "open\n- [x] done", "open\ndone"),
        ("> one\n\n> > two", "two", "one\n> > two", "one\ntwo"),
        ("> - quoted item", "quoted", "> - quoted item", "quoted item"),
    ],
)
def test_a_line_shows_its_typed_marks_instead_of_its_bullet_or_bar(
    editor, qapp, source, line, shown, hidden
):
    editor.set_source_text(source)
    _move_to(editor, qapp, line)

    assert editor.toPlainText() == shown
    assert editor.source_text() == source
    block = editor.textCursor().block()
    # The marks sit where the bullet, number or bar was, which no longer shows.
    assert block.blockFormat().textIndent() < 0
    if block.textList() is not None:
        assert block.charFormat().foreground().color().alpha() == 0
        assert block.blockFormat().marker() == QTextBlockFormat.MarkerType.NoMarker

    editor.clearFocus()
    qapp.processEvents()
    assert editor.toPlainText() == hidden
    assert not block.blockFormat().hasProperty(QTextFormat.Property.TextIndent)
    assert not block.charFormat().hasProperty(QTextFormat.Property.ForegroundBrush)
    assert editor.source_text() == source


def test_a_task_item_gets_its_checkbox_back(editor, qapp):
    editor.set_source_text("- [x] done\n- after")
    _move_to(editor, qapp, "done")
    _move_to(editor, qapp, "after")

    block, block_format = _line_format(editor)
    assert block_format.marker() == QTextBlockFormat.MarkerType.Checked
    assert not block.charFormat().hasProperty(QTextFormat.Property.ForegroundBrush)


def test_a_rule_shows_its_marks_and_editing_them_makes_text(editor, qapp):
    editor.set_source_text("Above **it**\n\n---\n\nBelow")
    cursor = editor.textCursor()
    cursor.setPosition(editor.document().findBlockByNumber(1).position())
    editor.setTextCursor(cursor)
    qapp.processEvents()
    assert editor.toPlainText() == "Above it\n---\nBelow"
    assert editor.source_text() == "Above **it**\n\n---\n\nBelow"

    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    _move_to(editor, qapp, "Below")
    assert editor.toPlainText() == "Above it\n--\nBelow"
    assert editor.source_text() == "Above **it**\n\n--\n\nBelow"


def test_deleting_a_list_mark_makes_the_item_text(editor, qapp):
    editor.set_source_text("- one\n- two\n- three")
    _move_to(editor, qapp, "two")
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)

    block = editor.textCursor().block()
    assert block.textList() is None
    # Out of the list, nothing hides its text.
    assert not block.charFormat().hasProperty(QTextFormat.Property.ForegroundBrush)
    assert editor.source_text() == "- one\n\ntwo\n\n- three"


def test_editing_a_list_mark_changes_the_list(editor, qapp):
    editor.set_source_text("- one\n- two")
    _move_to(editor, qapp, "one")
    QTest.keyClick(editor, Qt.Key.Key_Home)
    QTest.keyClick(editor, Qt.Key.Key_Delete)
    QTest.keyClicks(editor, "1.")

    assert editor.source_text() == "1. one\n\n- two"
    assert editor.toPlainText() == "1. one\ntwo"


def test_a_stray_letter_in_a_list_mark_leaves_it_as_text(editor, qapp):
    editor.set_source_text("Intro **x**\n\n- item")
    _move_to(editor, qapp, "item")
    QTest.keyClick(editor, Qt.Key.Key_Home)
    QTest.keyClick(editor, Qt.Key.Key_Right)
    QTest.keyClicks(editor, "x")
    _move_to(editor, qapp, "Intro")

    assert editor.toPlainText().splitlines()[1] == "-x item"
    assert editor.document().lastBlock().textList() is None


def test_typing_a_mark_after_a_shown_quote_mark_starts_a_list(editor, qapp):
    editor.set_source_text("> quoted")
    qapp.processEvents()
    QTest.keyClick(editor, Qt.Key.Key_End)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QTest.keyClicks(editor, "- item")

    assert editor.source_text() == "> quoted\n\n> - item"
    assert editor.toPlainText() == "quoted\n> - item"
