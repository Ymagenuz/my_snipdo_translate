from PyQt6.QtGui import QFont, QTextDocument
import pytest

from markdown_emphasis import prepare_cjk_emphasis
from ui_rich_text import set_document_markdown


def _shown(text):
    """The text as Qt shows it once read, each formatted run tagged:
    [B:...] bold, [I:...] italic, [S:...] struck through, [U:...] underlined."""
    document = QTextDocument()
    set_document_markdown(document, text)
    blocks = []
    block = document.begin()
    while block.isValid():
        pieces = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fmt = fragment.charFormat()
            tag = "".join(
                letter
                for letter, on in (
                    ("B", fmt.fontWeight() >= QFont.Weight.DemiBold.value),
                    ("I", fmt.fontItalic()),
                    ("S", fmt.fontStrikeOut()),
                    ("U", fmt.fontUnderline()),
                )
                if on
            )
            pieces.append(f"[{tag}:{fragment.text()}]" if tag else fragment.text())
            iterator += 1
        blocks.append("".join(pieces))
        block = block.next()
    return " / ".join(blocks)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("**注意：**请勿关闭", "[B:注意：]请勿关闭"),
        ("这是**“引号”**文字", "这是[B:“引号”]文字"),
        ("这是**粗体，**文字", "这是[B:粗体，]文字"),
        ("请**务必（重要）**保存", "请[B:务必（重要）]保存"),
        ("这是**《书名》**的内容", "这是[B:《书名》]的内容"),
        ("日本語の**「太字」**です", "日本語の[B:「太字」]です"),
        ("한국어 **「굵게」**입니다", "한국어 [B:「굵게」]입니다"),
        ("**Note：**Do this", "[B:Note：]Do this"),
        ("这是*“斜体”*文字", "这是[I:“斜体”]文字"),
        ("***“粗斜”***文字", "[BI:“粗斜”]文字"),
        ("这是~~删除~~文字", "这是[S:删除]文字"),
    ],
    ids=[
        "colon-inside", "quotes-inside", "comma-inside", "brackets-inside",
        "book-title", "japanese", "korean", "fullwidth-colon-in-english",
        "italic", "bold-italic", "strikethrough",
    ],
)
def test_emphasis_beside_cjk_punctuation_is_formatted(qapp, text, shown):
    assert _shown(text) == shown


def test_a_pair_beside_cjk_punctuation_no_longer_steals_the_next_one(qapp):
    # Qt alone bolded "文字和" and left both pairs as marks.
    assert _shown("这是**“引号”**文字和**粗体**") == "这是[B:“引号”]文字和[B:粗体]"
    assert _shown("**注意：**请勿关闭，**务必**保存") == "[B:注意：]请勿关闭，[B:务必]保存"


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("- **步骤一：**打开\n- **步骤二：**保存", "[B:步骤一：]打开 / [B:步骤二：]保存"),
        ("> **提示：**这是引用", "[B:提示：]这是引用"),
        (
            "| 名称 | 说明 |\n| --- | --- |\n| **键：**值 | 普通 |",
            # Qt puts an empty line before a table and after it.
            " / [B:名称] / [B:说明] / [B:键：]值 / 普通 / ",
        ),
        ("见[链接](https://a.com/x_y)和**“粗体”**", "见链接和[B:“粗体”]"),
        ("访问https://a.com/a_b_c，**注意：**大小写", "访问https://a.com/a_b_c，[B:注意：]大小写"),
    ],
    ids=["list", "quote", "table", "link", "bare-link"],
)
def test_emphasis_beside_cjk_punctuation_works_inside_blocks(qapp, text, shown):
    assert _shown(text) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("周一~周五，上午9点~下午6点", "周一~周五，上午9点~下午6点"),
        ("用__下划线__加粗", "用__下划线__加粗"),
        ("word**(bold)**word", "word**(bold)**word"),
        ("未闭合的**“引号", "未闭合的**“引号"),
        ("代码 `**“不是”**` 和**“是”**的", "代码 **“不是”** 和[B:“是”]的"),
        ("```\n**“代码”**中文\n```", "**“代码”**中文"),
        ("a **b** c *d* e", "a [B:b] c [I:d] e"),
        ("use __bold__ and _it_ here", "use [B:bold] and [I:it] here"),
    ],
    ids=[
        "tilde-ranges", "underscores-in-a-word", "english-in-a-word",
        "unclosed", "inline-code", "code-block", "english", "underscores",
    ],
)
def test_text_that_reads_the_same_either_way_is_left_alone(qapp, text, shown):
    assert _shown(text) == shown


def test_text_without_cjk_emphasis_is_given_to_qt_unchanged():
    for text in ("**bold** and *it*", "这是**粗体**文字", "没有标记", "2*3 和 4*5"):
        assert prepare_cjk_emphasis(text) == (text, "")


def test_marker_spaces_are_removed_once_qt_has_read_the_text(qapp):
    text = "这是**“引号”**文字"
    prepared, marker = prepare_cjk_emphasis(text)
    assert marker and marker in prepared

    document = QTextDocument()
    set_document_markdown(document, text)
    assert document.toPlainText() == "这是“引号”文字"


def test_text_that_already_has_every_marker_space_is_left_alone():
    markers = "".join(chr(code) for code in (0x2008, 0x2007, 0x2006, 0x2005, 0x2004))
    text = markers + "这是**“引号”**文字"
    assert prepare_cjk_emphasis(text) == (text, "")
