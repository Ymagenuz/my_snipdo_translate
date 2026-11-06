"""Markdown-aware rich-text editing for the input box, in the manner of MarkText.

The box shows Markdown as formatting rather than as marks: typing "# " starts
a heading and "**bold**" turns bold, MarkText's shortcuts and the Paragraph
and Format menus reformat any text, and pasted web pages, documents and
Markdown keep their headings, lists, quotes, code, emphasis, links and tables
but take the box's own fonts, sizes and colours. Translation reads the box
back as Markdown, so the structure reaches the model and the History.

As in MarkText, the formatting at the caret shows its marks in grey, as text
that can be edited: deleting "**" makes bold text plain, and deleting a "#"
lowers a heading. The caret's line shows its "- ", "1. " or "> " in place of
the bullet, number or quote bar they draw. The marks hide again once the
caret leaves, and Undo skips their showing and hiding.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextFormat,
    QTextFrameFormat,
    QTextLength,
    QTextListFormat,
    QTextTable,
)

from app_i18n import tr
from latex_renderer import prepare_latex_math_for_document
from latex_support import is_latex_text


HEADING_PIXEL_SIZES = {1: 20, 2: 18, 3: 16, 4: 15, 5: 15, 6: 15}
CODE_FAMILIES = ["Consolas", "Cascadia Mono", "monospace"]
CODE_BACKGROUND = "#EEF1F6"
LINK_COLOR = "#2F62D6"
MATH_COLOR = "#606266"
MARK_COLOR = "#A8ABB2"
QUOTE_BAR_COLOR = "#D5DCE6"
TABLE_BORDER_COLOR = "#DCDFE6"
QUOTE_INDENT = 14
LIST_INDENT = 20
CODE_INDENT = 6

# Marks LaTeX shown as source; it is written back exactly as it reads.
MATH_SOURCE_PROPERTY = QTextFormat.Property.UserProperty.value + 1
# Copies from the box carry their Markdown, so pasting them back loses nothing.
MARKDOWN_MIME_TYPE = "text/markdown"
# Marks shown at the caret: every character of a shown span carries the span's
# number, the "> ", "- ", "1. " or "## " shown before a line the line mark, and
# the line's own format the shown-line mark while they show.
_SPAN_PROPERTY = QTextFormat.Property.UserProperty.value + 2
_LINE_MARK_PROPERTY = QTextFormat.Property.UserProperty.value + 3
_SHOWN_LINE_PROPERTY = QTextFormat.Property.UserProperty.value + 4
# GitHub's Markdown, with _x_ and __x__ as emphasis: Qt's dialect adds md4c's
# underline extension (0x4000), which reads them as underlined text.
_MARKDOWN_FEATURES = QTextDocument.MarkdownFeature(
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub.value & ~0x4000
)

_P = QTextFormat.Property
_DEMIBOLD = QFont.Weight.DemiBold.value
_CODE_FONT_PROPERTIES = (_P.FontFamilies, _P.FontFixedPitch, _P.BackgroundBrush)
_LINE_SEPARATOR = chr(0x2028)
_NO_BREAK_SPACE = chr(0x00A0)
_OBJECT_REPLACEMENT = chr(0xFFFC)
# Stands for the caret while a span is written out as Markdown.
_CARET = chr(0xE000)
_ORDERED_STYLES = {
    QTextListFormat.Style.ListDecimal,
    QTextListFormat.Style.ListLowerAlpha,
    QTextListFormat.Style.ListUpperAlpha,
    QTextListFormat.Style.ListLowerRoman,
    QTextListFormat.Style.ListUpperRoman,
}
_TASK_MARKS = {
    QTextBlockFormat.MarkerType.Unchecked: "[ ] ",
    QTextBlockFormat.MarkerType.Checked: "[x] ",
}
# Pasted code arrives in whatever monospace font its page used.
_MONOSPACE_FAMILIES = frozenset({
    "cascadia code", "cascadia mono", "consolas", "courier", "courier new",
    "dejavu sans mono", "fira code", "fira mono", "ibm plex mono",
    "jetbrains mono", "liberation mono", "lucida console", "menlo", "monaco",
    "monospace", "noto sans mono", "roboto mono", "sf mono", "sfmono-regular",
    "source code pro", "ubuntu mono",
})

_MARKDOWN_BLOCK_RE = re.compile(
    r'(?m)^\s*(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+|```|\|.*\|)'
)
_MARKDOWN_INLINE_RE = re.compile(
    r'\*\*[^*\n]+\*\*|__[^_\n]+__|~~[^~\n]+~~|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)'
)

# Marks typed at the start of a paragraph, completed by a space.
_BLOCK_SHORTCUTS = (
    (re.compile(r"(#{1,6}) "), "heading"),
    (re.compile(r"[-*+] "), "bullet"),
    (re.compile(r"(\d{1,9})[.)] "), "numbered"),
    (re.compile(r"> "), "quote"),
    (re.compile(r"\[([ xX])\] "), "task"),
)
# Emphasis completed by typing its closing mark, by the mark typed.
_INLINE_SHORTCUTS = {
    "`": ((re.compile(r"(?<![`\\])`([^`]+)`$"), "code"),),
    "*": (
        (re.compile(r"(?<![*\\])\*\*(?![\s*])([^*]*?[^\s*\\])\*\*$"), "bold"),
        (re.compile(r"(?<![*\\])\*(?![\s*])([^*]*?[^\s*\\])\*$"), "italic"),
    ),
    "_": (
        (re.compile(r"(?<![\w\\])__(?![\s_])([^_]*?[^\s_\\])__$"), "bold"),
        (re.compile(r"(?<![\w\\])_(?![\s_])([^_]*?[^\s_\\])_$"), "italic"),
    ),
    "~": ((re.compile(r"(?<![~\\])~~(?![\s~])([^~]*?[^\s~\\])~~$"), "strike"),),
}
_MODIFIER_KEYS = frozenset(
    key.value
    for key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta)
)
_CODE_FENCE_RE = re.compile(r"(?:`{3,}|~{3,})[ \t]*([\w#+.-]*)[ \t]*")
_RULE_RE = re.compile(r"(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}")
# A line's edited marks, read in their Markdown order.
_QUOTE_MARKS_RE = re.compile(r"(?:>[ ]?)*")
_LIST_MARK_RE = re.compile(r"([-*+])[ ]|([0-9]{1,9})[.)][ ]")
_TASK_MARK_RE = re.compile(re.escape("[") + "([ xX])" + re.escape("]") + " ")
_HEADING_MARK_RE = re.compile(r"(#{1,6})(?:[ ]|$)")


def is_markdown_structured_text(text: str) -> bool:
    if not text:
        return False

    return bool(_MARKDOWN_BLOCK_RE.search(text))


def shows_as_markdown(text: str) -> bool:
    """Whether the input box shows text as formatting instead of marks.

    LaTeX without Markdown blocks stays source, so its * and _ keep meaning.
    """
    if is_markdown_structured_text(text):
        return True
    return (
        bool(text)
        and not is_latex_text(text)
        and bool(_MARKDOWN_INLINE_RE.search(text))
    )


def markdown_fragment(text: str) -> QTextDocumentFragment:
    """Markdown as formatting, with its LaTeX kept as source."""
    formulas = []
    document_text = text
    if is_latex_text(text):
        document_text, formulas = prepare_latex_math_for_document(text)
    scratch = QTextDocument()
    scratch.setMarkdown(document_text, _MARKDOWN_FEATURES)
    search_from = 0
    for formula in formulas:
        found = scratch.find(formula.placeholder, search_from)
        if found.isNull():
            found = scratch.find(formula.placeholder)
        if found.isNull():
            continue
        found.insertText(formula.source, _math_format())
        search_from = found.position()
    return QTextDocumentFragment(scratch)


# ---------- Reading the document back as Markdown ----------
def quote_level(block_format) -> int:
    level = block_format.property(_P.BlockQuoteLevel)
    return level if isinstance(level, int) else 0


def is_code_block(block) -> bool:
    block_format = block.blockFormat()
    if (
        block_format.hasProperty(_P.BlockCodeFence)
        or block_format.hasProperty(_P.BlockCodeLanguage)
    ):
        return True
    if not block_format.nonBreakableLines():
        return False
    # Pasted <pre> text: unwrapped lines in a monospace font.
    formats = [fragment.charFormat() for fragment in _fragments(block)]
    return all(_is_code_format(fmt) for fmt in formats or [block.charFormat()])


def _is_rule(block_format) -> bool:
    return block_format.hasProperty(_P.BlockTrailingHorizontalRulerWidth)


def _fragments(block):
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            yield fragment
        iterator += 1


def _is_code_format(fmt) -> bool:
    if fmt.hasProperty(MATH_SOURCE_PROPERTY):
        return False
    if fmt.fontFixedPitch():
        return True
    for family in fmt.fontFamilies() or []:
        for name in str(family).split(","):
            if name.strip().strip("'\"").lower() in _MONOSPACE_FAMILIES:
                return True
    return False


def _inline_marks(fmt, *, ignore_bold=False) -> tuple:
    """The Markdown marks a run of text needs, outermost first."""
    if fmt.hasProperty(MATH_SOURCE_PROPERTY) or fmt.isImageFormat():
        return ()
    marks = []
    if fmt.isAnchor() and fmt.anchorHref():
        marks.append(("link", fmt.anchorHref()))
    if fmt.fontStrikeOut():
        marks.append("~~")
    code = _is_code_format(fmt)
    if not ignore_bold and not code and fmt.fontWeight() >= _DEMIBOLD:
        marks.append("**")
    if fmt.fontItalic() and not code:
        marks.append("*")
    if code:
        marks.append("`")
    return tuple(marks)


def _has_block_structure(block) -> bool:
    block_format = block.blockFormat()
    return bool(
        block_format.headingLevel()
        or block.textList() is not None
        or quote_level(block_format)
        or _is_rule(block_format)
        or is_code_block(block)
        or QTextCursor(block).currentTable() is not None
    )


def document_has_formatting(document) -> bool:
    """Whether any of the document needs Markdown to be written down."""
    block = document.begin()
    while block.isValid():
        if _has_block_structure(block):
            return True
        if any(_inline_marks(fragment.charFormat()) for fragment in _fragments(block)):
            return True
        block = block.next()
    return False


def document_to_markdown(document) -> str:
    """The document as Markdown; without formatting it reads as typed."""
    if not document_has_formatting(document):
        return document.toPlainText()
    return _MarkdownWriter().write(document)


def _link_target(href: str) -> str:
    return f"<{href}>" if re.search(r"[\s()<>]", href) else href


def _join_runs(runs) -> str:
    """Write runs of text with their marks, keeping spaces outside the marks."""
    output = []
    open_marks = []
    pending = ""
    for text, marks in runs:
        core = text.strip()
        if not core:
            pending += text
            continue
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(lead) + len(core):]
        pending += lead
        if marks and marks[-1] == "`":
            fence = "`" * (max((len(run) for run in re.findall(r"`+", core)), default=0) + 1)
            if core.startswith("`") or core.endswith("`"):
                core = f" {core} "
            marks = (*marks[:-1], ("code", fence))
        keep = 0
        while (
            keep < len(open_marks)
            and keep < len(marks)
            and open_marks[keep] == marks[keep]
        ):
            keep += 1
        for mark in reversed(open_marks[keep:]):
            output.append(_closing_mark(mark))
        output.append(pending)
        for mark in marks[keep:]:
            output.append(_opening_mark(mark))
        output.append(core)
        open_marks = list(marks)
        pending = trail
    for mark in reversed(open_marks):
        output.append(_closing_mark(mark))
    output.append(pending)
    return "".join(output)


def _opening_mark(mark) -> str:
    if isinstance(mark, tuple):
        return "[" if mark[0] == "link" else mark[1]
    return mark


def _closing_mark(mark) -> str:
    if isinstance(mark, tuple):
        return f"]({_link_target(mark[1])})" if mark[0] == "link" else mark[1]
    return mark


class _MarkdownWriter:
    def __init__(self):
        self.parts = []
        self.previous_kind = ""
        self.code_lines = []
        self.code_language = ""
        # Width of the last list marker at each depth, for nested items.
        self.list_widths = {}
        # Where the outermost list being written starts.
        self.list_root = None

    def write(self, document) -> str:
        self.frame(document.rootFrame())
        self.flush_code()
        return "".join(self.parts)

    def emit(self, kind: str, text: str, *, tight: bool = False):
        if self.parts:
            if tight:
                self.parts.append("\n")
            elif kind == self.previous_kind and kind.startswith("quote"):
                self.parts.append("\n" + ">" * int(kind[5:]) + "\n")
            else:
                self.parts.append("\n\n")
        self.parts.append(text)
        self.previous_kind = kind

    def frame(self, frame):
        iterator = frame.begin()
        while not iterator.atEnd():
            child = iterator.currentFrame()
            if child is not None:
                self.flush_code()
                self.list_widths = {}
                if isinstance(child, QTextTable):
                    self.table(child)
                else:
                    self.frame(child)
            else:
                block = iterator.currentBlock()
                if block.isValid():
                    self.block(block)
            iterator += 1

    def block(self, block):
        block_format = block.blockFormat()
        if is_code_block(block):
            language = block_format.property(_P.BlockCodeLanguage) or ""
            if self.code_lines and language != self.code_language:
                self.flush_code()
            self.code_language = language
            self.code_lines.append(_plain_block_text(block))
            return
        self.flush_code()

        quote = "> " * quote_level(block_format)
        kind = f"quote{quote_level(block_format)}" if quote else "paragraph"
        text_list = block.textList()
        if text_list is None:
            self.list_widths = {}
        else:
            list_format = text_list.format()
            depth = max(1, list_format.indent())
            if list_format.style() in _ORDERED_STYLES:
                start = (
                    list_format.start()
                    if list_format.hasProperty(_P.ListStart)
                    else 1
                )
                marker = f"{start + text_list.itemNumber(block)}. "
            else:
                marker = "- "
            padding = " " * sum(
                self.list_widths.get(level, 2) for level in range(1, depth)
            )
            self.list_widths = {
                level: width
                for level, width in self.list_widths.items()
                if level < depth
            }
            self.list_widths[depth] = len(marker)
            task = _TASK_MARKS.get(block_format.marker(), "")
            text = self.inline(
                block, continuation=quote + " " * (len(padding) + len(marker))
            )
            # A blank line ends one list before the next begins.
            root = text_list.item(0).position() if depth == 1 else self.list_root
            tight = self.previous_kind == "list" and root == self.list_root
            self.list_root = root
            self.emit("list", quote + padding + marker + task + text, tight=tight)
            return

        heading = block_format.headingLevel()
        if _is_rule(block_format) and not block.text().strip():
            self.emit(kind, quote + "---")
            return
        text = self.inline(block, ignore_bold=bool(heading), continuation=quote)
        if not text.strip():
            return
        if heading:
            text = "#" * heading + " " + text.strip()
        self.emit(kind, quote + text)
        if _is_rule(block_format):
            self.emit(kind, quote + "---")

    def inline(self, block, *, ignore_bold=False, continuation="") -> str:
        runs = []
        for fragment in _fragments(block):
            fmt = fragment.charFormat()
            if fmt.isImageFormat():
                continue
            text = (
                fragment.text()
                .replace(_NO_BREAK_SPACE, " ")
                .replace(_OBJECT_REPLACEMENT, "")
            )
            marks = _inline_marks(fmt, ignore_bold=ignore_bold)
            if runs and runs[-1][1] == marks:
                runs[-1][0] += text
            else:
                runs.append([text, marks])
        # A line break inside a paragraph is a hard break, so it stays one.
        text = _join_runs(runs).rstrip(" " + _LINE_SEPARATOR)
        return text.replace(_LINE_SEPARATOR, "  \n" + continuation)

    def table(self, table):
        rows = []
        for row in range(table.rows()):
            cells = []
            for column in range(table.columns()):
                cell = table.cellAt(row, column)
                if cell.row() != row or cell.column() != column:
                    cells.append("")
                    continue
                parts = []
                block = cell.firstCursorPosition().block()
                last = cell.lastCursorPosition().block()
                while block.isValid():
                    # Header cells are bold by their look, not by choice.
                    parts.append(self.inline(block, ignore_bold=row == 0).strip())
                    if block == last:
                        break
                    block = block.next()
                text = " ".join(part for part in parts if part)
                cells.append(text.replace("\n", " ").replace("|", "\\|"))
            rows.append(cells)
        if not rows:
            return
        lines = [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join(["---"] * table.columns()) + " |",
            *("| " + " | ".join(cells) + " |" for cells in rows[1:]),
        ]
        self.emit("table", "\n".join(lines))

    def flush_code(self):
        if not self.code_lines:
            return
        body = "\n".join(self.code_lines)
        longest = max((len(run) for run in re.findall(r"`{3,}", body)), default=0)
        fence = "`" * max(3, longest + 1)
        self.emit("code", f"{fence}{self.code_language}\n{body}\n{fence}")
        self.code_lines = []
        self.code_language = ""


def _plain_block_text(block) -> str:
    return (
        block.text()
        .replace(_LINE_SEPARATOR, "\n")
        .replace(_NO_BREAK_SPACE, " ")
        .replace(_OBJECT_REPLACEMENT, "")
    )


# ---------- The box's own look ----------
def _math_format() -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setProperty(MATH_SOURCE_PROPERTY, True)
    fmt.setFontFamilies(CODE_FAMILIES)
    fmt.setForeground(QColor(MATH_COLOR))
    return fmt


def _set_code_font(fmt, *, background: bool):
    fmt.setFontFamilies(CODE_FAMILIES)
    fmt.setFontFixedPitch(True)
    if background:
        fmt.setBackground(QColor(CODE_BACKGROUND))


def _clear_code_font(fmt):
    for prop in _CODE_FONT_PROPERTIES:
        fmt.clearProperty(prop)


def _set_block_char_format(block, fmt):
    """The format an empty line types in."""
    QTextCursor(block).setBlockCharFormat(fmt)
    # Qt leaves the document's first block unlaid and unpainted after this.
    if not block.previous().isValid():
        block.document().markContentsDirty(block.position(), block.length())


def _clean_char_format(fmt, *, heading=0, code=False) -> QTextCharFormat:
    """Only what Markdown can say about a run of text, in the box's look."""
    if fmt.hasProperty(MATH_SOURCE_PROPERTY):
        return _math_format()
    clean = QTextCharFormat()
    if code:
        _set_code_font(clean, background=False)
        return clean
    code_span = _is_code_format(fmt)
    if heading:
        clean.setFontWeight(QFont.Weight.DemiBold)
        clean.setProperty(_P.FontPixelSize, HEADING_PIXEL_SIZES.get(heading, 15))
    elif fmt.fontWeight() >= _DEMIBOLD and not code_span:
        clean.setFontWeight(QFont.Weight.Bold)
    if fmt.fontItalic():
        clean.setFontItalic(True)
    if fmt.fontStrikeOut():
        clean.setFontStrikeOut(True)
    if code_span:
        _set_code_font(clean, background=True)
    if fmt.isAnchor() and fmt.anchorHref():
        clean.setAnchor(True)
        clean.setAnchorHref(fmt.anchorHref())
        clean.setForeground(QColor(LINK_COLOR))
        clean.setFontUnderline(True)
    return clean


def _normalize_block(block):
    document = block.document()
    # A read-only view may have set its own list indent on this document.
    if document.indentWidth() != LIST_INDENT:
        document.setIndentWidth(LIST_INDENT)
    old = block.blockFormat()
    code = is_code_block(block)
    heading = 0 if code else old.headingLevel()
    quote = quote_level(old)

    clean = QTextBlockFormat()
    if block.textList() is not None:
        clean.setObjectIndex(old.objectIndex())
        if old.marker() != QTextBlockFormat.MarkerType.NoMarker:
            clean.setMarker(old.marker())
    if code:
        clean.setProperty(_P.BlockCodeFence, "`")
        language = old.property(_P.BlockCodeLanguage)
        if language:
            clean.setProperty(_P.BlockCodeLanguage, language)
        clean.setNonBreakableLines(True)
        clean.setBackground(QColor(CODE_BACKGROUND))
        # Each code line is a block, so this indents every line.
        clean.setTextIndent(CODE_INDENT)
    elif heading:
        clean.setHeadingLevel(heading)
        clean.setTopMargin(6 if block.previous().isValid() else 0)
        clean.setBottomMargin(2)
    if quote:
        clean.setProperty(_P.BlockQuoteLevel, quote)
        clean.setLeftMargin(QUOTE_INDENT * quote)
    if _is_rule(old):
        clean.setProperty(
            _P.BlockTrailingHorizontalRulerWidth,
            QTextLength(QTextLength.Type.PercentageLength, 100),
        )
    cursor = QTextCursor(block)
    cursor.setBlockFormat(clean)

    spans = [
        (fragment.position(), fragment.length(), QTextCharFormat(fragment.charFormat()))
        for fragment in _fragments(block)
    ]
    # From the end, so dropping an image leaves earlier positions alone.
    for position, length, fmt in reversed(spans):
        span = QTextCursor(document)
        span.setPosition(position)
        span.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        if fmt.isImageFormat():
            span.removeSelectedText()
        else:
            span.setCharFormat(_clean_char_format(fmt, heading=heading, code=code))
    _set_block_char_format(
        block, _clean_char_format(block.charFormat(), heading=heading, code=code)
    )


def _normalize_table(table):
    table_format = table.format()
    table_format.setBorder(1)
    table_format.setBorderBrush(QColor(TABLE_BORDER_COLOR))
    table_format.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
    table_format.setBorderCollapse(True)
    table_format.setCellSpacing(0)
    table_format.setCellPadding(4)
    table_format.setMargin(0)
    table_format.setTopMargin(4)
    table_format.setBottomMargin(4)
    table_format.setWidth(QTextLength())
    table_format.clearColumnWidthConstraints()
    table_format.clearBackground()
    table.setFormat(table_format)
    for row in range(table.rows()):
        for column in range(table.columns()):
            cell = table.cellAt(row, column)
            cell_format = cell.format()
            cell_format.clearBackground()
            cell.setFormat(cell_format)


def normalize_formatting(document, start: int = 0, end: int | None = None):
    """Keep only what Markdown can say in [start, end], in the box's look."""
    if end is None:
        end = document.characterCount() - 1
    block = document.findBlock(start)
    last = document.findBlock(max(start, end))
    tables = {}
    while block.isValid():
        table = QTextCursor(block).currentTable()
        if table is not None:
            tables[table.firstPosition()] = table
        _normalize_block(block)
        if block == last:
            break
        block = block.next()
    for table in tables.values():
        _normalize_table(table)


# ---------- Changing blocks and runs ----------
def _selected_blocks(cursor):
    document = cursor.document()
    block = document.findBlock(cursor.selectionStart())
    last = document.findBlock(cursor.selectionEnd())
    if (
        cursor.hasSelection()
        and cursor.selectionEnd() == last.position()
        and last != block
    ):
        last = last.previous()
    blocks = []
    while block.isValid():
        blocks.append(block)
        if block == last:
            break
        block = block.next()
    return blocks


def _update_block_format(block, change):
    block_format = block.blockFormat()
    change(block_format)
    QTextCursor(block).setBlockFormat(block_format)


def _change_char_formats(block, change):
    document = block.document()
    spans = [
        (fragment.position(), fragment.length(), QTextCharFormat(fragment.charFormat()))
        for fragment in _fragments(block)
    ]
    for position, length, fmt in spans:
        change(fmt)
        span = QTextCursor(document)
        span.setPosition(position)
        span.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        span.setCharFormat(fmt)
    # An empty line types in its block's format.
    block_char_format = QTextCharFormat(block.charFormat())
    change(block_char_format)
    _set_block_char_format(block, block_char_format)


def _clear_char_properties(block, properties):
    def clear(fmt):
        for prop in properties:
            fmt.clearProperty(prop)

    _change_char_formats(block, clear)


def _leave_heading(block):
    if not block.blockFormat().headingLevel():
        return

    def leave(fmt):
        fmt.setHeadingLevel(0)
        # A heading's spacing goes with it.
        fmt.clearProperty(_P.BlockTopMargin)
        fmt.clearProperty(_P.BlockBottomMargin)

    _update_block_format(block, leave)
    # The heading's weight and size were its look, not bold text.
    _clear_char_properties(
        block, (_P.FontWeight, _P.FontPixelSize, _P.FontSizeAdjustment)
    )


def _leave_code(block):
    if not is_code_block(block):
        return

    def change(fmt):
        for prop in (_P.BlockCodeFence, _P.BlockCodeLanguage, _P.BackgroundBrush):
            fmt.clearProperty(prop)
        fmt.setNonBreakableLines(False)

    _update_block_format(block, change)
    _clear_char_properties(block, _CODE_FONT_PROPERTIES)


def _leave_list(block):
    text_list = block.textList()
    if text_list is not None:
        text_list.remove(block)
        _update_block_format(block, lambda fmt: fmt.setIndent(0))


def _leave_quote(block):
    _update_block_format(block, lambda fmt: fmt.clearProperty(_P.BlockQuoteLevel))


def _outdent_quote(block):
    level = quote_level(block.blockFormat())
    if level > 1:
        _update_block_format(
            block, lambda fmt: fmt.setProperty(_P.BlockQuoteLevel, level - 1)
        )
    else:
        _leave_quote(block)


def _leave_rule(block):
    _update_block_format(
        block, lambda fmt: fmt.clearProperty(_P.BlockTrailingHorizontalRulerWidth)
    )


def _make_paragraph(block):
    _leave_heading(block)
    _leave_code(block)
    _leave_list(block)
    _leave_quote(block)
    _leave_rule(block)
    _update_block_format(
        block, lambda fmt: fmt.setMarker(QTextBlockFormat.MarkerType.NoMarker)
    )


def _make_heading(block, level: int):
    _leave_code(block)
    _leave_list(block)
    _leave_rule(block)
    _update_block_format(block, lambda fmt: fmt.setHeadingLevel(level))


def _make_code(block, language: str = ""):
    _leave_heading(block)
    _leave_list(block)
    _leave_quote(block)
    _leave_rule(block)

    def change(fmt):
        fmt.setProperty(_P.BlockCodeFence, "`")
        if language:
            fmt.setProperty(_P.BlockCodeLanguage, language)

    _update_block_format(block, change)


def _add_to_list(block, style, start: int = 1):
    """Put a block in a list, continuing one of the same kind just above it."""
    previous = block.previous()
    previous_list = previous.textList() if previous.isValid() else None
    if (
        previous_list is not None
        and previous_list.format().style() == style
        and previous_list.format().indent() == 1
    ):
        previous_list.add(block)
        return
    list_format = QTextListFormat()
    list_format.setStyle(style)
    list_format.setIndent(1)
    if start != 1:
        list_format.setStart(start)
    QTextCursor(block).createList(list_format)


def _has_inline(fmt, style: str) -> bool:
    if style == "bold":
        return fmt.fontWeight() >= _DEMIBOLD
    if style == "italic":
        return fmt.fontItalic()
    if style == "strike":
        return fmt.fontStrikeOut()
    return _is_code_format(fmt)


def _set_inline(fmt, style: str, on: bool):
    if style == "bold":
        fmt.setFontWeight(QFont.Weight.Bold if on else QFont.Weight.Normal)
    elif style == "italic":
        fmt.setFontItalic(on)
    elif style == "strike":
        fmt.setFontStrikeOut(on)
    elif on:
        _set_code_font(fmt, background=True)
    else:
        _clear_code_font(fmt)


def _spans(document, start: int, end: int):
    """(start, end, format, text) for each run of text in [start, end)."""
    spans = []
    block = document.findBlock(start)
    while block.isValid() and block.position() < end:
        for fragment in _fragments(block):
            span_start = max(start, fragment.position())
            span_end = min(end, fragment.position() + fragment.length())
            if span_start < span_end:
                offset = span_start - fragment.position()
                spans.append((
                    span_start,
                    span_end,
                    QTextCharFormat(fragment.charFormat()),
                    fragment.text()[offset:offset + span_end - span_start],
                ))
        block = block.next()
    return spans


def _inside_math(text: str) -> bool:
    return len(re.findall(r"(?<!\\)\$", text)) % 2 == 1


# ---------- Marks shown at the caret ----------
def _context_format(heading: int) -> QTextCharFormat:
    """Plain text in a block: a heading's look, or none."""
    return _clean_char_format(QTextCharFormat(), heading=heading)


def _mark_format(heading: int) -> QTextCharFormat:
    fmt = _context_format(heading)
    fmt.setForeground(QColor(MARK_COLOR))
    return fmt


def _source_formats(source: str, heading: int) -> list:
    """How each character of a span's Markdown shows: a format, or None for a mark."""
    if _LINE_SEPARATOR in source:
        formats = []
        for index, line in enumerate(source.split(_LINE_SEPARATOR)):
            if index:
                formats.append(_context_format(heading))
            formats.extend(_source_formats(line, heading))
        return formats
    core = source.rstrip()
    plain = _context_format(heading)
    scratch = QTextDocument()
    # The "." keeps a "# " or "- " in the text from starting a heading or list.
    scratch.setMarkdown("." + core, _MARKDOWN_FEATURES)
    shown = []
    if scratch.blockCount() == 1:
        for fragment in _fragments(scratch.begin()):
            fmt = _clean_char_format(fragment.charFormat(), heading=heading)
            shown.extend((char, fmt) for char in fragment.text())
    if not shown or shown[0][0] != ".":
        return [plain] * len(source)
    formats = []
    index = 1
    for char in core:
        if index < len(shown) and shown[index][0] == char:
            formats.append(shown[index][1])
            index += 1
        else:
            formats.append(None)
    if index < len(shown):
        # Markdown that shows text of its own, such as an image, stays as typed.
        return [plain] * len(source)
    return formats + [plain] * (len(source) - len(core))


def _shown_formats(source: str, heading: int, number: int) -> list:
    """A shown span's formats: its marks grey, its text formatted, all numbered."""
    mark = _mark_format(heading)
    mark.setProperty(_SPAN_PROPERTY, number)
    numbered = {}
    formats = []
    for fmt in _source_formats(source, heading):
        if fmt is None:
            formats.append(mark)
            continue
        if id(fmt) not in numbered:
            numbered[id(fmt)] = QTextCharFormat(fmt)
            numbered[id(fmt)].setProperty(_SPAN_PROPERTY, number)
        formats.append(numbered[id(fmt)])
    return formats


def _insert_runs(cursor, text: str, formats):
    """Insert text with a format for each of its characters."""
    start = 0
    for index in range(1, len(text) + 1):
        if index == len(text) or formats[index] != formats[start]:
            cursor.insertText(text[start:index], formats[start])
            start = index


def _restyle_ranges(document, start: int, formats) -> list:
    """(start, end, format) for the runs from start whose format is not yet given."""
    current = []
    for span_start, span_end, fmt, _text in _spans(document, start, start + len(formats)):
        current.extend([fmt] * (span_end - span_start))
    ranges = []
    index = 0
    while index < len(formats):
        if index < len(current) and current[index] == formats[index]:
            index += 1
            continue
        end = index + 1
        while (
            end < len(formats)
            and formats[end] == formats[index]
            and not (end < len(current) and current[end] == formats[end])
        ):
            end += 1
        ranges.append((start + index, start + end, formats[index]))
        index = end
    return ranges


def _set_char_formats(document, ranges):
    for start, end, fmt in ranges:
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(fmt)


def _marked_range(block, prop):
    """(start, end, value) of the characters of a block that carry prop, or None."""
    found = None
    for fragment in _fragments(block):
        fmt = fragment.charFormat()
        if fmt.hasProperty(prop):
            end = fragment.position() + fragment.length()
            if found is None:
                found = (fragment.position(), end, fmt.property(prop))
            else:
                found = (found[0], end, found[2])
    return found


def _has_shown_marks(block) -> bool:
    if block.blockFormat().hasProperty(_SHOWN_LINE_PROPERTY):
        return True
    return any(
        fragment.charFormat().hasProperty(_SPAN_PROPERTY)
        or fragment.charFormat().hasProperty(_LINE_MARK_PROPERTY)
        for fragment in _fragments(block)
    )


def _block_slice(block, start: int, end: int) -> str:
    return block.text()[start - block.position():end - block.position()]


def _map_position(position: int, start: int, end: int, length: int, inside) -> int:
    """Where a position goes once [start, end) becomes length characters."""
    if position < start:
        return position
    if position >= end:
        return position + length - (end - start)
    return start + min(length, inside(position - start))


def _can_show_marks(block) -> bool:
    return (
        block.isValid()
        and not is_code_block(block)
        and QTextCursor(block).currentTable() is None
        and _CARET not in block.text()
    )


def _line_marks(block) -> str:
    """The marks a line's Markdown starts with: quote, list item, task, heading or rule."""
    fmt = block.blockFormat()
    marks = "> " * quote_level(fmt)
    text_list = block.textList()
    if text_list is not None:
        list_format = text_list.format()
        if list_format.style() in _ORDERED_STYLES:
            start = list_format.start() if list_format.hasProperty(_P.ListStart) else 1
            marks += f"{start + text_list.itemNumber(block)}. "
        else:
            marks += "- "
        return marks + _TASK_MARKS.get(fmt.marker(), "")
    if fmt.headingLevel():
        return marks + "#" * fmt.headingLevel() + " "
    if _is_rule(fmt) and not block.text():
        return marks + "---"
    return marks


def _shown_line_marks(block) -> str:
    found = _marked_range(block, _LINE_MARK_PROPERTY)
    return _block_slice(block, found[0], found[1]) if found else ""


def _read_line_marks(marks: str, *, alone: bool) -> dict:
    """What a line's edited marks make it. "used" counts the characters that
    read as marks, "drawn" those a bullet, number or quote bar stands for."""
    read = {"quote": 0, "list": None, "number": 1, "task": None, "heading": 0, "rule": False}
    quote = _QUOTE_MARKS_RE.match(marks)
    read["quote"] = quote.group().count(">")
    used = drawn = quote.end()
    rest = marks[used:]
    item = _LIST_MARK_RE.match(rest)
    if alone and rest.strip() and _RULE_RE.fullmatch(rest.strip()):
        read["rule"] = True
        used = len(marks)
    elif item is not None:
        read["list"] = "ordered" if item.group(2) else "bullet"
        if item.group(2):
            read["number"] = int(item.group(2))
        used += item.end()
        task = _TASK_MARK_RE.match(marks, used)
        if task is not None:
            read["task"] = (
                QTextBlockFormat.MarkerType.Unchecked
                if task.group(1) == " "
                else QTextBlockFormat.MarkerType.Checked
            )
            used = task.end()
        drawn = used
    else:
        heading = _HEADING_MARK_RE.match(marks, used)
        if heading is not None:
            read["heading"] = len(heading.group(1))
            used = heading.end()
    read["used"] = used
    read["drawn"] = drawn
    return read


def _set_heading_level(block, level: int):
    if level == block.blockFormat().headingLevel():
        return
    if not level:
        _leave_heading(block)
        return
    size = HEADING_PIXEL_SIZES.get(level, 15)
    _update_block_format(block, lambda fmt: fmt.setHeadingLevel(level))

    def resize(fmt):
        fmt.setFontWeight(QFont.Weight.DemiBold)
        fmt.setProperty(_P.FontPixelSize, size)

    _change_char_formats(block, resize)


def _apply_line_marks(block, read: dict):
    """Give a line the quote, list and heading its edited marks say."""
    level = read["quote"]
    if level != quote_level(block.blockFormat()):

        def requote(fmt):
            if level:
                fmt.setProperty(_P.BlockQuoteLevel, level)
                fmt.setLeftMargin(QUOTE_INDENT * level)
            else:
                fmt.clearProperty(_P.BlockQuoteLevel)
                fmt.clearProperty(_P.BlockLeftMargin)

        _update_block_format(block, requote)
    text_list = block.textList()
    kind = None
    if text_list is not None:
        kind = "ordered" if text_list.format().style() in _ORDERED_STYLES else "bullet"
    if read["list"] != kind:
        if text_list is not None:
            _leave_list(block)
        if read["list"] is not None:
            _leave_heading(block)
            ordered = read["list"] == "ordered"
            style = QTextListFormat.Style.ListDecimal if ordered else QTextListFormat.Style.ListDisc
            _add_to_list(block, style, read["number"])
    elif kind == "ordered" and text_list.itemNumber(block) == 0:
        list_format = text_list.format()
        start = list_format.start() if list_format.hasProperty(_P.ListStart) else 1
        if start != read["number"]:
            list_format.setStart(read["number"])
            text_list.setFormat(list_format)
    if read["list"] is None:
        _set_heading_level(block, read["heading"])


def _fit_line_marks(block):
    """Put a shown line's marks where its bullet, number or quote bar was drawn,
    and hide those, or the line shows both."""
    document = block.document()
    fmt = block.blockFormat()
    drawn = _shown_line_marks(block)
    drawn = drawn[:_read_line_marks(drawn, alone=False)["drawn"]]
    text_list = block.textList()
    room = fmt.leftMargin() + fmt.indent() * document.indentWidth()
    if text_list is not None:
        room += text_list.format().indent() * document.indentWidth()
    font = _mark_format(fmt.headingLevel()).font().resolve(document.defaultFont())
    indent = min(QFontMetricsF(font).horizontalAdvance(drawn), room)

    def shift(block_format):
        if drawn:
            block_format.setTextIndent(-indent)
        else:
            block_format.clearProperty(_P.TextIndent)

    _update_block_format(block, shift)
    # Qt draws a bullet or number in the colour of the line's own format.
    char_format = QTextCharFormat(block.charFormat())
    if text_list is not None:
        char_format.setForeground(QColor(0, 0, 0, 0))
    else:
        char_format.clearProperty(_P.ForegroundBrush)
    _set_block_char_format(block, char_format)


def _show_line_marks(block) -> int:
    """Show a line's marks before its text; returns how many there are."""
    marks = _line_marks(block)
    mark = _mark_format(block.blockFormat().headingLevel())
    mark.setProperty(_LINE_MARK_PROPERTY, True)

    def show(fmt):
        # While the marks show, they stand for the rule and checkbox.
        fmt.clearProperty(_P.BlockTrailingHorizontalRulerWidth)
        fmt.clearProperty(_P.BlockMarker)
        fmt.setProperty(_SHOWN_LINE_PROPERTY, True)

    _update_block_format(block, show)
    QTextCursor(block).insertText(marks, mark)
    _fit_line_marks(block)
    return len(marks)


def _hide_line_marks(block, positions: list) -> list:
    """Make a shown line what its marks say, with its bullet, number or bar back."""
    found = _marked_range(block, _LINE_MARK_PROPERTY)
    marks = _shown_line_marks(block)
    read = _read_line_marks(marks, alone=len(block.text()) == len(marks))
    _apply_line_marks(block, read)

    def settle(fmt):
        fmt.clearProperty(_SHOWN_LINE_PROPERTY)
        fmt.clearProperty(_P.TextIndent)
        if read["task"] is not None and block.textList() is not None:
            fmt.setMarker(read["task"])
        if read["rule"]:
            fmt.setProperty(
                _P.BlockTrailingHorizontalRulerWidth,
                QTextLength(QTextLength.Type.PercentageLength, 100),
            )

    _update_block_format(block, settle)
    char_format = QTextCharFormat(block.charFormat())
    char_format.clearProperty(_P.ForegroundBrush)
    _set_block_char_format(block, char_format)
    if found is None:
        return positions
    start, end, _value = found
    used = read["used"]
    cursor = QTextCursor(block.document())
    cursor.setPosition(start + used)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    kept = 0
    if marks[used:].strip():
        # What did not read as marks, such as "-x", is text now.
        cursor.setCharFormat(_context_format(block.blockFormat().headingLevel()))
        kept = end - start - used
    else:
        cursor.removeSelectedText()
    cursor.setPosition(start)
    cursor.setPosition(start + used, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    return [
        _map_position(position, start, end, kept, lambda offset: max(0, offset - used) if kept else 0)
        for position in positions
    ]


def _block_runs(block, heading: int) -> list:
    """[start, end, marks, text] for each run of a block with the same marks."""
    runs = []
    for fragment in _fragments(block):
        fmt = fragment.charFormat()
        marks = () if fmt.isImageFormat() else _inline_marks(fmt, ignore_bold=bool(heading))
        position = fragment.position()
        # Spans end at a line break, as their Markdown does.
        for piece in re.split(f"({_LINE_SEPARATOR})", fragment.text()):
            if not piece:
                continue
            piece_marks = () if piece == _LINE_SEPARATOR else marks
            if runs and runs[-1][2] == piece_marks:
                runs[-1][1] += len(piece)
                runs[-1][3] += piece
            else:
                runs.append([position, position + len(piece), piece_marks, piece])
            position += len(piece)
    return runs


def _span_at(block, caret: int, heading: int):
    """The runs of the outermost formatting the caret is in or beside, or None."""
    runs = _block_runs(block, heading)
    touching = [
        index
        for index, (start, end, marks, text) in enumerate(runs)
        if marks and text.strip() and start <= caret <= end
    ]
    if not touching:
        return None

    def closeness(index):
        start, end = runs[index][0], runs[index][1]
        return 0 if start < caret < end else 1 if end == caret else 2

    index = min(touching, key=closeness)
    outer = runs[index][2][:1]
    low = high = index
    while low > 0 and runs[low - 1][2][:1] == outer:
        low -= 1
    while high + 1 < len(runs) and runs[high + 1][2][:1] == outer:
        high += 1
    return runs[low:high + 1]


def _common_marks(first, second) -> tuple:
    """The outer marks two runs share."""
    shared = 0
    while shared < min(len(first), len(second)) and first[shared] == second[shared]:
        shared += 1
    return tuple(first[:shared])


def _show_span_marks(block, runs, caret: int, typing_marks, heading: int, number: int):
    """Write a span out as its Markdown; returns the caret's place and what it was.

    The caret goes where typing would go: inside the marks when what is typed
    there takes the span's formatting, as within it, and outside them when not.
    """
    document = block.document()
    start, end = runs[0][0], runs[-1][1]
    pieces = []
    placed = False
    for index, (run_start, run_end, marks, text) in enumerate(runs):
        if not placed and run_start <= caret <= run_end:
            offset = caret - run_start
            caret_marks = marks
            if offset in (0, len(text)):
                following = runs[index + 1][2] if offset and index + 1 < len(runs) else ()
                caret_marks = _common_marks(typing_marks, marks)
                # Between two runs of the span, the caret stays inside it.
                within = _common_marks(marks, following)
                if len(caret_marks) < len(within):
                    caret_marks = within
            pieces += [[text[:offset], marks], [_CARET, caret_marks], [text[offset:], marks]]
            placed = True
        else:
            pieces.append([text, marks])
    source = _join_runs([piece for piece in pieces if piece[0]])
    offset = source.index(_CARET)
    source = source.replace(_CARET, "")
    was = (source, [(text, fmt) for _start, _end, fmt, text in _spans(document, start, end)])
    cursor = QTextCursor(document)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    _insert_runs(cursor, source, _shown_formats(source, heading, number))
    return start + offset, was


def _hide_span_marks(block, shown_spans: dict, positions: list) -> list:
    """Put a shown span back as formatting: as it was, or as its edited Markdown says."""
    found = _marked_range(block, _SPAN_PROPERTY)
    if found is None:
        return positions
    start, end, number = found
    source = _block_slice(block, start, end)
    formats = _source_formats(source, block.blockFormat().headingLevel())
    was = shown_spans.get(number)
    cursor = QTextCursor(block.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    if was is not None and was[0] == source:
        for text, fmt in was[1]:
            cursor.insertText(text, fmt)
        length = sum(len(text) for text, _fmt in was[1])
    else:
        kept = [(char, fmt) for char, fmt in zip(source, formats) if fmt is not None]
        _insert_runs(cursor, "".join(char for char, _fmt in kept), [fmt for _char, fmt in kept])
        length = len(kept)

    def inside(offset):
        return sum(1 for fmt in formats[:offset] if fmt is not None)

    return [_map_position(position, start, end, length, inside) for position in positions]


def _hide_all_marks(document, shown_spans: dict) -> bool:
    """Hide every shown mark in a copy of the box's text; returns whether there were any."""
    hid = False
    block = document.begin()
    while block.isValid():
        if _has_shown_marks(block):
            hid = True
            if block.blockFormat().hasProperty(_SHOWN_LINE_PROPERTY):
                _hide_line_marks(block, [])
            _hide_span_marks(block, shown_spans, [])
        block = block.next()
    return hid


class MarkdownEditing:
    """Mix into a QTextEdit, before it, to edit Markdown as formatting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # (Markdown as first written, text given) for the text last loaded.
        self._loaded_source = None
        # Selection changes read the text back; only edits change it.
        self._markdown = None
        self._forget_marks()
        # Marks follow the caret once Qt has finished moving it.
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self._sync_marks)
        self._syncing = False
        self._view_edit = None
        self._closing_view_edit = False
        # The hint the box shows while it is blank.
        self._placeholder = super().placeholderText()
        document = self.document()
        document.contentsChanged.connect(self._forget_markdown)
        document.contentsChanged.connect(self._update_placeholder)
        document.undoCommandAdded.connect(self._note_undo_step)
        self.cursorPositionChanged.connect(self._caret_moved)

    def _forget_markdown(self):
        self._markdown = None

    def placeholderText(self) -> str:
        return self._placeholder

    def setPlaceholderText(self, text):
        self._placeholder = text or ""
        self._update_placeholder()

    def _update_placeholder(self):
        """Qt shows the hint until there is text; a heading, list, quote or code
        block started on the empty line hides it too, or the two overlap."""
        document = self.document()
        started = document.isEmpty() and _has_block_structure(document.begin())
        shown = "" if started else self._placeholder
        if super().placeholderText() != shown:
            super().setPlaceholderText(shown)

    def _forget_marks(self):
        """Start over: no marks shown, and no undo history to tell apart."""
        # What each shown span was, by its number, to put it back as it was.
        self._shown_spans = {}
        self._span_number = 0
        # The block whose marks show, and its line marks as last applied.
        self._marks_at = None
        self._line_marks_seen = None
        # Undo and Redo may bring back marks anywhere.
        self._marks_unknown = False
        # Each undo step's end, and whether it only showed or hid marks.
        self._step_kinds = {}
        self._undo_top = 0
        # Edits undone and not yet redone: marks stay put, or Redo would lose them.
        self._redo_steps = 0
        # Emphasis turned on for what is typed next waits for that text.
        self._hold_marks = False

    def _current_markdown(self) -> str:
        if self._markdown is None:
            document = self.document()
            if self._marked_block() is not None:
                # Read as if no marks showed.
                document = document.clone()
                _hide_all_marks(document, self._shown_spans)
            self._markdown = document_to_markdown(document)
        return self._markdown

    # ---------- Replacing the text ----------
    def clear(self):
        self._forget_marks()
        super().clear()

    def setPlainText(self, text):
        self._forget_marks()
        super().setPlainText(text)

    def setHtml(self, text):
        self._forget_marks()
        super().setHtml(text)

    def setMarkdown(self, markdown):
        self._forget_marks()
        super().setMarkdown(markdown)

    def setText(self, text):
        self._forget_marks()
        super().setText(text)

    def setReadOnly(self, read_only):
        if read_only:
            self._hide_marks()
        super().setReadOnly(read_only)

    # ---------- Text in and out ----------
    def set_source_text(self, text: str):
        """Show text, its Markdown as formatting; unedited, it reads back as given."""
        text = text or ""
        if shows_as_markdown(text):
            self.clear()
            # One edit block, or Qt lays out the document after every block.
            cursor = QTextCursor(self.document())
            cursor.beginEditBlock()
            cursor.insertFragment(markdown_fragment(text))
            normalize_formatting(self.document())
            cursor.endEditBlock()
            self.document().clearUndoRedoStacks()
            self.moveCursor(QTextCursor.MoveOperation.Start)
        else:
            self.setPlainText(text)
        self._forget_marks()
        self._loaded_source = (self._current_markdown(), text)

    def source_text(self) -> str:
        """The box's text as Markdown, or as plain text without formatting."""
        markdown = self._current_markdown()
        if self._loaded_source is not None and self._loaded_source[0] == markdown:
            return self._loaded_source[1]
        return markdown

    def insertFromMimeData(self, source):
        if self.isReadOnly() or source is None:
            super().insertFromMimeData(source)
            return
        self._hide_marks()
        plain = source.text() if source.hasText() else ""
        # LaTeX copied from an editor reads best as its own source.
        latex_source = (
            bool(plain)
            and is_latex_text(plain)
            and not is_markdown_structured_text(plain)
        )
        if source.hasFormat(MARKDOWN_MIME_TYPE):
            markdown = bytes(source.data(MARKDOWN_MIME_TYPE)).decode("utf-8", "replace")
            self._insert_fragment(markdown_fragment(markdown))
        elif source.hasHtml() and not latex_source:
            self._insert_fragment(QTextDocumentFragment.fromHtml(source.html()))
        elif plain and shows_as_markdown(plain):
            self._insert_fragment(markdown_fragment(plain))
        elif plain:
            text_only = QMimeData()
            text_only.setText(plain)
            super().insertFromMimeData(text_only)
        else:
            super().insertFromMimeData(source)

    def createMimeDataFromSelection(self):
        if self.isReadOnly():
            return super().createMimeDataFromSelection()
        fragment = self.textCursor().selection()
        selection = QTextDocument()
        QTextCursor(selection).insertFragment(fragment)
        if _hide_all_marks(selection, self._shown_spans):
            fragment = QTextDocumentFragment(selection)
            if not document_has_formatting(selection):
                mime = QMimeData()
                mime.setText(selection.toPlainText())
                return mime
        if not document_has_formatting(selection):
            return super().createMimeDataFromSelection()
        # Plain-text apps get the Markdown, rich ones the formatting. Qt's own
        # data would replace the plain text when it is first read.
        markdown = document_to_markdown(selection)
        mime = QMimeData()
        mime.setHtml(fragment.toHtml())
        mime.setText(markdown)
        mime.setData(MARKDOWN_MIME_TYPE, markdown.encode("utf-8"))
        return mime

    def _insert_fragment(self, fragment):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        start = cursor.selectionStart()
        cursor.insertFragment(fragment)
        normalize_formatting(self.document(), start, cursor.position())
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    # ---------- Paragraph commands ----------
    def _change_blocks(self, change):
        self._hide_marks()
        cursor = self.textCursor()
        cursor.beginEditBlock()
        for block in _selected_blocks(cursor):
            change(block)
            _normalize_block(block)
        cursor.endEditBlock()
        # Typing picks up the block's new look.
        self.setTextCursor(cursor)

    def set_heading(self, level: int):
        self._hide_marks()
        blocks = _selected_blocks(self.textCursor())
        if all(block.blockFormat().headingLevel() == level for block in blocks):
            self.make_paragraph()
            return

        def change(block):
            _make_heading(block, level)

        self._change_blocks(change)

    def make_paragraph(self):
        self._change_blocks(_make_paragraph)

    def toggle_quote(self):
        self._hide_marks()
        blocks = _selected_blocks(self.textCursor())
        if all(quote_level(block.blockFormat()) for block in blocks):
            self._change_blocks(_leave_quote)
            return

        def change(block):
            _leave_code(block)
            _leave_rule(block)
            _update_block_format(
                block, lambda fmt: fmt.setProperty(_P.BlockQuoteLevel, 1)
            )

        self._change_blocks(change)

    def toggle_code_block(self):
        self._hide_marks()
        blocks = _selected_blocks(self.textCursor())
        if all(is_code_block(block) for block in blocks):
            self._change_blocks(_leave_code)
            return
        self._change_blocks(_make_code)

    def toggle_bullet_list(self):
        self._toggle_list(QTextListFormat.Style.ListDisc)

    def toggle_numbered_list(self):
        self._toggle_list(QTextListFormat.Style.ListDecimal)

    def _toggle_list(self, style):
        self._hide_marks()
        ordered = style in _ORDERED_STYLES
        blocks = _selected_blocks(self.textCursor())
        lists = [block.textList() for block in blocks]
        if all(text_list is not None for text_list in lists):
            if all((text_list.format().style() in _ORDERED_STYLES) == ordered for text_list in lists):
                self._change_blocks(_leave_list)
                return
            # Switch the lists' kind and keep their nesting.
            cursor = self.textCursor()
            cursor.beginEditBlock()
            for text_list in {id(text_list): text_list for text_list in lists}.values():
                list_format = text_list.format()
                list_format.setStyle(style)
                text_list.setFormat(list_format)
            cursor.endEditBlock()
            return

        def change(block):
            _leave_heading(block)
            _leave_code(block)
            _leave_rule(block)
            if block.textList() is None:
                _add_to_list(block, style)

        self._change_blocks(change)

    # ---------- Format commands ----------
    def toggle_bold(self):
        self._toggle_inline("bold")

    def toggle_italic(self):
        self._toggle_inline("italic")

    def toggle_strikethrough(self):
        self._toggle_inline("strike")

    def toggle_inline_code(self):
        self._toggle_inline("code")

    def _toggle_inline(self, style: str):
        self._hide_marks()
        cursor = self.textCursor()
        if not cursor.hasSelection():
            fmt = QTextCharFormat(self.currentCharFormat())
            _set_inline(fmt, style, not _has_inline(fmt, style))
            self.setCurrentCharFormat(fmt)
            # It is for what is typed next, so no marks show until then.
            self._hold_marks = True
            return
        spans = _spans(self.document(), cursor.selectionStart(), cursor.selectionEnd())
        visible = [span for span in spans if span[3].strip()] or spans
        turn_on = not all(_has_inline(span[2], style) for span in visible)
        cursor.beginEditBlock()
        for start, end, fmt, _text in spans:
            _set_inline(fmt, style, turn_on)
            span = QTextCursor(self.document())
            span.setPosition(start)
            span.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            span.setCharFormat(fmt)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def clear_formatting(self):
        """Plain paragraphs and plain text for the selection or current block."""
        self._hide_marks()
        cursor = self.textCursor()
        blocks = _selected_blocks(cursor)
        if cursor.hasSelection():
            start, end = cursor.selectionStart(), cursor.selectionEnd()
        else:
            start = blocks[0].position()
            end = blocks[-1].position() + blocks[-1].length() - 1
        cursor.beginEditBlock()
        for block in blocks:
            _make_paragraph(block)
        span = QTextCursor(self.document())
        span.setPosition(start)
        span.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        span.setCharFormat(QTextCharFormat())
        for block in blocks:
            _normalize_block(block)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        if not cursor.hasSelection():
            self.setCurrentCharFormat(QTextCharFormat())

    # ---------- Keys ----------
    def keyPressEvent(self, event):
        if self.isReadOnly():
            super().keyPressEvent(event)
            return
        if self._sync_timer.isActive():
            # The marks of where the caret went show before the key acts there.
            self._sync_timer.stop()
            self._sync_marks()
        if event.key() not in _MODIFIER_KEYS:
            self._hold_marks = False
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            event.accept()
            return
        command = self._shortcut_command(event)
        if command is not None:
            command()
            event.accept()
            self._sync_marks()
            return

        key = event.key()
        plain_key = (
            event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            == Qt.KeyboardModifier.NoModifier
        )
        returned = plain_key and key in (Qt.Key.Key_Return.value, Qt.Key.Key_Enter.value)
        if returned:
            self._hide_marks()
            if self._handle_return():
                event.accept()
                self._sync_marks()
                return
        if plain_key and key == Qt.Key.Key_Backspace.value:
            cursor = self.textCursor()
            if cursor.atBlockStart() and not cursor.hasSelection():
                self._hide_marks()
            if self._handle_backspace():
                event.accept()
                self._sync_marks()
                # Before the line's marks, Backspace goes on stepping out.
                self.moveCursor(QTextCursor.MoveOperation.StartOfBlock)
                return

        super().keyPressEvent(event)

        if returned:
            self._after_return()
        typed = event.text()
        if typed == " " or typed in _INLINE_SHORTCUTS:
            # Marks the caret has just left hide first, so the rules read text.
            self._sync_marks()
            if typed == " ":
                self._apply_block_shortcut()
            elif not self._caret_in_shown_span():
                # Inside shown marks the text is Markdown already.
                self._apply_inline_shortcut(typed)
        self._sync_marks()

    def inputMethodEvent(self, event):
        super().inputMethodEvent(event)
        if not self.isReadOnly():
            self._sync_marks()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._sync_timer.start()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._sync_timer.start()

    def _shortcut_command(self, event):
        """MarkText's paragraph and format shortcuts."""
        ctrl = Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        modifiers = event.modifiers() & (
            ctrl | shift | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        )
        key = event.key()
        if modifiers == ctrl:
            if Qt.Key.Key_1.value <= key <= Qt.Key.Key_6.value:
                level = key - Qt.Key.Key_1.value + 1
                return lambda: self.set_heading(level)
            return {
                Qt.Key.Key_0.value: self.make_paragraph,
                Qt.Key.Key_B.value: self.toggle_bold,
                Qt.Key.Key_I.value: self.toggle_italic,
                Qt.Key.Key_D.value: self.toggle_strikethrough,
                Qt.Key.Key_QuoteLeft.value: self.toggle_inline_code,
                Qt.Key.Key_G.value: self.toggle_numbered_list,
                Qt.Key.Key_H.value: self.toggle_bullet_list,
            }.get(key)
        if modifiers == ctrl | shift:
            return {
                Qt.Key.Key_K.value: self.toggle_code_block,
                Qt.Key.Key_Q.value: self.toggle_quote,
                Qt.Key.Key_R.value: self.clear_formatting,
            }.get(key)
        return None

    def _apply_block_shortcut(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            return
        block = cursor.block()
        block_format = block.blockFormat()
        if (
            is_code_block(block)
            or block_format.headingLevel()
            or QTextCursor(block).currentTable() is not None
        ):
            return
        typed = block.text()[:cursor.positionInBlock()]
        shown = _marked_range(block, _LINE_MARK_PROPERTY)
        if shown is not None:
            # After a line's shown "> " or "- ", its text starts.
            typed = typed[shown[1] - block.position():]
        in_list = block.textList() is not None
        for pattern, kind in _BLOCK_SHORTCUTS:
            match = pattern.fullmatch(typed)
            if match is None or (kind == "task") != in_list:
                continue
            if kind == "task" and block_format.marker() != QTextBlockFormat.MarkerType.NoMarker:
                return
            if _has_shown_marks(block):
                # The new heading or list makes its line afresh.
                self._hide_marks()
                if not _has_shown_marks(self.textCursor().block()):
                    self._apply_block_shortcut()
                return
            edit = QTextCursor(block)
            edit.beginEditBlock()
            edit.setPosition(block.position() + len(typed), QTextCursor.MoveMode.KeepAnchor)
            edit.removeSelectedText()
            if kind == "heading":
                _make_heading(block, len(match.group(1)))
            elif kind == "bullet":
                _add_to_list(block, QTextListFormat.Style.ListDisc)
            elif kind == "numbered":
                _add_to_list(block, QTextListFormat.Style.ListDecimal, int(match.group(1)))
            elif kind == "quote":
                level = quote_level(block_format) + 1
                _update_block_format(
                    block, lambda fmt: fmt.setProperty(_P.BlockQuoteLevel, level)
                )
            else:
                marker = (
                    QTextBlockFormat.MarkerType.Unchecked
                    if match.group(1) == " "
                    else QTextBlockFormat.MarkerType.Checked
                )
                _update_block_format(block, lambda fmt: fmt.setMarker(marker))
            _normalize_block(block)
            edit.endEditBlock()
            self.setTextCursor(self.textCursor())
            return

    def _apply_inline_shortcut(self, mark: str):
        cursor = self.textCursor()
        block = cursor.block()
        typed_format = cursor.charFormat()
        if (
            is_code_block(block)
            or _is_code_format(typed_format)
            or typed_format.hasProperty(MATH_SOURCE_PROPERTY)
        ):
            return
        before = block.text()[:cursor.positionInBlock()]
        for pattern, style in _INLINE_SHORTCUTS[mark]:
            match = pattern.search(before)
            if match is None:
                continue
            ahead = before[:match.start()]
            if _inside_math(ahead) or (style != "code" and ahead.count("`") % 2):
                return
            document = self.document()
            block_start = block.position()
            start = block_start + match.start()
            inner_length = match.end(1) - match.start(1)
            edit = QTextCursor(document)
            # Undo puts the caret back where the edit began.
            edit.setPosition(cursor.position())
            edit.beginEditBlock()
            edit.setPosition(block_start + match.end(1))
            edit.setPosition(block_start + match.end(), QTextCursor.MoveMode.KeepAnchor)
            edit.removeSelectedText()
            edit.setPosition(start)
            edit.setPosition(block_start + match.start(1), QTextCursor.MoveMode.KeepAnchor)
            edit.removeSelectedText()
            for span_start, span_end, fmt, _text in _spans(
                document, start, start + inner_length
            ):
                _set_inline(fmt, style, True)
                span = QTextCursor(document)
                span.setPosition(span_start)
                span.setPosition(span_end, QTextCursor.MoveMode.KeepAnchor)
                span.setCharFormat(fmt)
            edit.endEditBlock()
            # What is typed next goes on without the new emphasis.
            typing = QTextCharFormat(self.textCursor().charFormat())
            _set_inline(typing, style, False)
            self.setCurrentCharFormat(typing)
            return

    def _handle_return(self) -> bool:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        block = cursor.block()
        block_format = block.blockFormat()
        text = block.text()
        if not _has_block_structure(block) and cursor.atBlockEnd():
            fence = _CODE_FENCE_RE.fullmatch(text)
            rule = _RULE_RE.fullmatch(text.strip()) if text.strip() else None
            if fence is not None or rule is not None:
                cursor.beginEditBlock()
                cursor.movePosition(
                    QTextCursor.MoveOperation.StartOfBlock,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                cursor.removeSelectedText()
                if fence is not None:
                    _make_code(block, fence.group(1))
                    _normalize_block(block)
                else:
                    _update_block_format(
                        block,
                        lambda fmt: fmt.setProperty(
                            _P.BlockTrailingHorizontalRulerWidth,
                            QTextLength(QTextLength.Type.PercentageLength, 100),
                        ),
                    )
                    _normalize_block(block)
                    cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
                cursor.endEditBlock()
                self.setTextCursor(cursor)
                return True
        if text:
            return False
        # Return on an empty quote or last code line leaves it, as in a list.
        leave = None
        if quote_level(block_format) and block.textList() is None:
            leave = _leave_quote
        elif is_code_block(block):
            previous, following = block.previous(), block.next()
            if (
                previous.isValid()
                and is_code_block(previous)
                and not (following.isValid() and is_code_block(following))
            ):
                leave = _leave_code
        if leave is None:
            return False
        cursor.beginEditBlock()
        leave(block)
        _normalize_block(block)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _after_return(self):
        # A new line after a rule is text, not another rule.
        block = self.textCursor().block()
        if _is_rule(block.blockFormat()):
            cursor = self.textCursor()
            cursor.beginEditBlock()
            _leave_rule(block)
            _normalize_block(block)
            cursor.endEditBlock()
            self.setTextCursor(cursor)

    def _handle_backspace(self) -> bool:
        """Backspace at a heading, quote or code line's start makes it text."""
        cursor = self.textCursor()
        if cursor.hasSelection() or not cursor.atBlockStart():
            return False
        block = cursor.block()
        if block.textList() is not None or QTextCursor(block).currentTable() is not None:
            return False
        block_format = block.blockFormat()
        change = None
        if block_format.headingLevel():
            change = _leave_heading
        elif is_code_block(block):
            change = _leave_code
        elif _is_rule(block_format):
            change = _leave_rule
        elif quote_level(block_format):
            change = _outdent_quote
        previous = block.previous()
        if change is None:
            if not (
                previous.isValid()
                and _is_rule(previous.blockFormat())
                and not previous.text()
            ):
                return False
            # Remove the rule above rather than joining the text to it.
            cursor.beginEditBlock()
            cursor.setPosition(previous.position(), QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.setBlockFormat(block_format)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        cursor.beginEditBlock()
        change(block)
        _normalize_block(block)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    # ---------- Menu ----------
    def createStandardContextMenu(self, position=None):
        if position is None:
            menu = super().createStandardContextMenu()
        else:
            menu = super().createStandardContextMenu(position)
        if self.isReadOnly() or menu is None:
            return menu
        if position is not None and not self.textCursor().hasSelection():
            # The commands act where the user right-clicked.
            hit = self.document().documentLayout().hitTest(
                QPointF(position), Qt.HitTestAccuracy.FuzzyHit
            )
            if hit >= 0:
                cursor = self.textCursor()
                cursor.setPosition(hit)
                self.setTextCursor(cursor)
        self._hide_marks()
        document = self.document()
        for action in menu.actions():
            # Qt's own Undo and Redo would stop at shown and hidden marks.
            if action.objectName() == "edit-undo":
                self._take_over(action, self.undo, document.isUndoAvailable() and self._has_steps(redo=False))
            elif action.objectName() == "edit-redo":
                self._take_over(action, self.redo, document.isRedoAvailable() and self._has_steps(redo=True))

        menu.addSeparator()
        paragraph = menu.addMenu(tr("editor.menu.paragraph"))
        headings = (
            (tr("editor.paragraph.heading", level=level), f"Ctrl+{level}", level)
            for level in range(1, 7)
        )
        for label, shortcut, level in headings:
            self._add_command(paragraph, label, shortcut, lambda _c=False, lv=level: self.set_heading(lv))
        self._add_command(paragraph, tr("editor.paragraph.body"), "Ctrl+0", self.make_paragraph)
        paragraph.addSeparator()
        self._add_command(paragraph, tr("editor.paragraph.quote"), "Ctrl+Shift+Q", self.toggle_quote)
        self._add_command(paragraph, tr("editor.paragraph.code_block"), "Ctrl+Shift+K", self.toggle_code_block)
        self._add_command(paragraph, tr("editor.paragraph.bullet_list"), "Ctrl+H", self.toggle_bullet_list)
        self._add_command(paragraph, tr("editor.paragraph.numbered_list"), "Ctrl+G", self.toggle_numbered_list)

        formatting = menu.addMenu(tr("editor.menu.format"))
        self._add_command(formatting, tr("editor.format.bold"), "Ctrl+B", self.toggle_bold)
        self._add_command(formatting, tr("editor.format.italic"), "Ctrl+I", self.toggle_italic)
        self._add_command(formatting, tr("editor.format.strikethrough"), "Ctrl+D", self.toggle_strikethrough)
        self._add_command(formatting, tr("editor.format.inline_code"), "Ctrl+`", self.toggle_inline_code)
        formatting.addSeparator()
        self._add_command(formatting, tr("editor.format.clear"), "Ctrl+Shift+R", self.clear_formatting)
        return menu

    @staticmethod
    def _add_command(menu, label: str, shortcut: str, handler):
        action = menu.addAction(label)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: handler())
        return action

    @staticmethod
    def _take_over(action, handler, enabled: bool):
        try:
            action.triggered.disconnect()
        except TypeError:
            pass
        action.triggered.connect(lambda _checked=False: handler())
        action.setEnabled(enabled)

    # ---------- Undo ----------
    def _note_undo_step(self):
        top = self.document().availableUndoSteps()
        if top <= self._undo_top:
            # The history was cleared since the last step.
            self._step_kinds = {}
        # A new step drops the ones there were to redo.
        self._step_kinds = {
            end: view for end, view in self._step_kinds.items() if end <= self._undo_top
        }
        self._step_kinds[top] = self._closing_view_edit
        if not self._closing_view_edit:
            self._redo_steps = 0
        self._undo_top = top

    def _has_steps(self, *, redo: bool) -> bool:
        """Whether there is an edit, not just marks shown or hidden, to undo or redo."""
        top = self.document().availableUndoSteps()
        return any(
            not view and (end > top if redo else end <= top)
            for end, view in self._step_kinds.items()
        )

    def undo(self):
        """Undo the last edit, and the marks shown and hidden since."""
        document = self.document()
        cursor = self.textCursor()
        steps = []
        while document.isUndoAvailable():
            view = self._step_kinds.get(document.availableUndoSteps(), False)
            document.undo(cursor)
            steps.append(view)
            if not view:
                break
        if steps and steps[-1]:
            # Only marks were left to undo, so they stay.
            spare = QTextCursor(document)
            for _step in steps:
                document.redo(spare)
            steps = []
        self._undo_top = document.availableUndoSteps()
        if steps:
            self._redo_steps += 1
            self._marks_moved()
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def redo(self):
        """Redo the last edit undone, with the marks shown or hidden before it."""
        document = self.document()
        cursor = self.textCursor()
        steps = []
        while document.isRedoAvailable():
            document.redo(cursor)
            view = self._step_kinds.get(document.availableUndoSteps(), False)
            steps.append(view)
            if not view:
                break
        if steps and steps[-1]:
            spare = QTextCursor(document)
            for _step in steps:
                document.undo(spare)
            steps = []
        self._undo_top = document.availableUndoSteps()
        if steps:
            self._redo_steps = max(0, self._redo_steps - 1)
            self._marks_moved()
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    # ---------- Marks at the caret ----------
    def _marks_moved(self):
        self._marks_at = None
        self._line_marks_seen = None
        self._marks_unknown = True

    def _caret_moved(self):
        if self._syncing:
            return
        self._hold_marks = False
        self._sync_timer.start()

    def _marks_caret(self):
        """Where marks show: at the caret, while the box has focus and no selection."""
        cursor = self.textCursor()
        if self.isReadOnly() or not self.hasFocus() or cursor.hasSelection():
            return None
        return cursor.position()

    def _caret_in_shown_span(self) -> bool:
        cursor = self.textCursor()
        found = _marked_range(cursor.block(), _SPAN_PROPERTY)
        return found is not None and found[0] <= cursor.position() <= found[1]

    def _marked_block(self):
        """The block whose marks show, or None."""
        if self._marks_at is not None:
            block = self._marks_at.block()
            if block.isValid() and _has_shown_marks(block):
                return block
            self._marks_at = None
        if self._marks_unknown:
            self._marks_unknown = False
            block = self.document().begin()
            while block.isValid():
                if _has_shown_marks(block):
                    self._marks_at = QTextCursor(block)
                    return block
                block = block.next()
        return None

    def _redo_waits(self) -> bool:
        """After Undo the marks stay as they are, so Redo keeps its steps."""
        return self._redo_steps > 0 and self.document().isRedoAvailable()

    def _composing(self) -> bool:
        layout = self.textCursor().block().layout()
        return layout is not None and bool(layout.preeditAreaText())

    def _open_view_edit(self):
        """Showing and hiding marks is an undo step of its own, which Undo skips."""
        if self._view_edit is None:
            self._view_edit = QTextCursor(self.document())
            self._view_edit.setPosition(self.textCursor().position())
            self._view_edit.beginEditBlock()

    def _close_view_edit(self):
        edit = self._view_edit
        if edit is None:
            return
        self._view_edit = None
        # Typed text would join the step if it ended inserting text, as Qt
        # merges typing into an insert just before it.
        cursor = self.textCursor()
        block = cursor.block()
        if block.length() > 1:
            position = min(cursor.position(), block.position() + block.length() - 2)
            spans = _spans(self.document(), position, position + 1)
            if spans:
                _set_char_formats(self.document(), [(position, position + 1, spans[0][2])])
        self._closing_view_edit = True
        try:
            edit.endEditBlock()
        finally:
            self._closing_view_edit = False

    def _set_caret(self, anchor: int, position: int):
        last = self.document().characterCount() - 1
        cursor = self.textCursor()
        cursor.setPosition(max(0, min(anchor, last)))
        cursor.setPosition(max(0, min(position, last)), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def _sync_marks(self):
        """Show the marks of the formatting at the caret, and hide the others."""
        if self._syncing or self._redo_waits() or self._composing():
            return
        self._syncing = True
        try:
            block = self._marked_block()
            if block is not None:
                self._refresh_marks(block)
                caret = self._marks_caret()
                if caret is None or self.document().findBlock(caret) != block:
                    self._hide_block_marks(block)
                else:
                    span = _marked_range(block, _SPAN_PROPERTY)
                    if span is not None and not span[0] <= caret <= span[1]:
                        self._hide_block_marks(block, line=False)
            caret = self._marks_caret()
            if caret is not None and not self._hold_marks:
                self._show_marks_at(caret)
                self._type_outside_marks()
        finally:
            self._close_view_edit()
            self._syncing = False

    def _hide_marks(self):
        """Hide the shown marks, so a command acts on the formatting itself."""
        if self._syncing:
            return
        block = self._marked_block()
        if block is None:
            return
        self._syncing = True
        try:
            self._refresh_marks(block)
            self._hide_block_marks(block)
        finally:
            self._close_view_edit()
            self._syncing = False

    def _refresh_marks(self, block):
        """Show at once what typing into shown marks did."""
        if block.blockFormat().hasProperty(_SHOWN_LINE_PROPERTY):
            marks = _shown_line_marks(block)
            if marks != self._line_marks_seen:
                self._line_marks_seen = marks
                self._open_view_edit()
                _apply_line_marks(block, _read_line_marks(marks, alone=False))
                _fit_line_marks(block)
        found = _marked_range(block, _SPAN_PROPERTY)
        if found is not None:
            start, end, number = found
            source = _block_slice(block, start, end)
            heading = block.blockFormat().headingLevel()
            ranges = _restyle_ranges(
                self.document(), start, _shown_formats(source, heading, number)
            )
            if ranges:
                self._open_view_edit()
                _set_char_formats(self.document(), ranges)

    def _hide_block_marks(self, block, *, line: bool = True):
        if not _has_shown_marks(block):
            self._marks_at = None
            return
        cursor = self.textCursor()
        positions = [cursor.anchor(), cursor.position()]
        self._open_view_edit()
        if line and block.blockFormat().hasProperty(_SHOWN_LINE_PROPERTY):
            positions = _hide_line_marks(block, positions)
            self._line_marks_seen = None
        positions = _hide_span_marks(block, self._shown_spans, positions)
        if not _has_shown_marks(block):
            self._marks_at = None
        self._set_caret(*positions)

    def _show_marks_at(self, caret: int):
        block = self.document().findBlock(caret)
        if not _can_show_marks(block):
            return
        if _line_marks(block) and not block.blockFormat().hasProperty(_SHOWN_LINE_PROPERTY):
            self._open_view_edit()
            caret += _show_line_marks(block)
            self._line_marks_seen = _shown_line_marks(block)
            self._marks_at = QTextCursor(block)
            self._set_caret(caret, caret)
        heading = block.blockFormat().headingLevel()
        if _marked_range(block, _SPAN_PROPERTY) is not None:
            return
        runs = _span_at(block, caret, heading)
        if runs is None:
            return
        typing_marks = _inline_marks(self.currentCharFormat(), ignore_bold=bool(heading))
        self._open_view_edit()
        self._span_number += 1
        caret, was = _show_span_marks(
            block, runs, caret, typing_marks, heading, self._span_number
        )
        self._shown_spans[self._span_number] = was
        self._marks_at = QTextCursor(block)
        self._set_caret(caret, caret)

    def _type_outside_marks(self):
        """Text typed just before or after shown marks goes outside them."""
        cursor = self.textCursor()
        block = cursor.block()
        caret = cursor.position()
        span = _marked_range(block, _SPAN_PROPERTY)
        line_marks = _marked_range(block, _LINE_MARK_PROPERTY)
        if (
            (span is not None and caret in (span[0], span[1]))
            or (line_marks is not None and caret == line_marks[1])
        ):
            self.setCurrentCharFormat(_context_format(block.blockFormat().headingLevel()))

    # ---------- Painting ----------
    def paintEvent(self, event):
        super().paintEvent(event)
        self._paint_quote_bars()

    def _paint_quote_bars(self):
        """A bar beside each quote level, as MarkText draws them."""
        viewport = self.viewport()
        layout = self.document().documentLayout()
        dx = -self.horizontalScrollBar().value()
        dy = -self.verticalScrollBar().value()
        painter = None
        block = self.cursorForPosition(QPoint(0, 0)).block()
        while block.isValid():
            # Qt crashes measuring a table cell's block before the table is
            # laid out, and quote bars never sit in tables. A line showing its
            # "> " needs no bar.
            if (
                QTextCursor(block).currentTable() is not None
                or block.blockFormat().hasProperty(_SHOWN_LINE_PROPERTY)
            ):
                block = block.next()
                continue
            rect = layout.blockBoundingRect(block).translated(dx, dy)
            if rect.top() > viewport.height():
                break
            level = quote_level(block.blockFormat())
            if level and rect.bottom() >= 0:
                if painter is None:
                    painter = QPainter(viewport)
                for depth in range(level):
                    painter.fillRect(
                        QRectF(rect.left() + depth * QUOTE_INDENT + 2, rect.top(), 3, rect.height()),
                        QColor(QUOTE_BAR_COLOR),
                    )
            block = block.next()
        if painter is not None:
            painter.end()
