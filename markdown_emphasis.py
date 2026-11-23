"""Bold and italic beside Chinese, Japanese and Korean text, as the writer meant.

CommonMark takes ** as bold only at a word's edge, and finds that edge by
spaces and punctuation. Chinese, Japanese and Korean put no spaces between
words, so "**注意：**请勿关闭" and "这是**“引号”**文字" keep their marks as
text, and a later pair can take the wrong partner: Qt shows
"这是**“引号”**文字和**粗体**" with "文字和" in bold. Qt reads Markdown by
those rules, so the text is prepared for it here.

Emphasis is paired again with these characters counting as a word's edge, as
markdown-cjk-friendly proposes. Where that pairing differs from CommonMark's,
an invisible space on the outer side of a mark tells Qt which way the mark
faces, and marks left without a partner are escaped so they stay text. The
caller takes the spaces out again once Qt has read the text.
"""
from __future__ import annotations

import re
import unicodedata

# Spaces Qt counts as whitespace; the first one the text lacks is the marker.
_MARKER_CANDIDATES = tuple(chr(code) for code in (0x2008, 0x2007, 0x2006, 0x2005, 0x2004))
_DELIMITERS = "*_~"
_ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_CJK_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x2FDF),  # CJK and Kangxi radicals
    (0x2FF0, 0x303F),  # ideographic description, CJK symbols and punctuation
    (0x3040, 0x33FF),  # kana, Bopomofo, Hangul compatibility Jamo, enclosed CJK
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xA960, 0xA97F),  # Hangul Jamo extended A
    (0xAC00, 0xD7FF),  # Hangul syllables, Jamo extended B
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFE10, 0xFE1F),  # vertical forms
    (0xFE30, 0xFE6F),  # CJK compatibility forms, small form variants
    (0xFF00, 0xFFEF),  # halfwidth and fullwidth forms
    (0x1B000, 0x1B16F),  # kana supplement and extended
    (0x20000, 0x3FFFF),  # CJK extensions B onwards
)
_CJK_RE = re.compile(
    "[" + "".join(f"{chr(low)}-{chr(high)}" for low, high in _CJK_RANGES) + "]"
)

# Block structure, enough to tell which lines are read together as one paragraph.
_QUOTE_RE = re.compile(r"[ ]{0,3}>[ ]?")
_FENCE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")
_HEADING_RE = re.compile(r"[ ]{0,3}#{1,6}(?:[ \t]+|$)")
_THEMATIC_BREAK_RE = re.compile(r"[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})")
_SETEXT_RE = re.compile(r"[ ]{0,3}(?:=+|-+)[ \t]*")
_LIST_ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+(?:\[[ xX]\][ \t]+)?|$)")
_TABLE_DELIMITER_RE = re.compile(r"[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")

# Inline text whose * _ ~ are not emphasis.
_AUTOLINK_RE = re.compile(
    r"<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>"
    r"|<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*>"
)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>|<!--.*?-->", re.DOTALL)
_BARE_LINK_RE = re.compile(
    r"(?:https?://|ftp://|www\.)[A-Za-z0-9-][A-Za-z0-9\-._~:/?#@!$&'()*+,;=%]*"
    r"|[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)
# As in GitHub's Markdown, these end a bare link without being part of it.
_LINK_TRAILING = "?!.,:*_~"
_BACKTICK_CLOSERS: dict[int, re.Pattern] = {}


def prepare_cjk_emphasis(text: str) -> tuple[str, str]:
    """Markdown for Qt to read with its emphasis beside CJK text intact.

    Returns the text to read and the marker space put into it, which the
    caller removes from what Qt made of it; the marker is "" when the text
    needed no change.
    """
    if not text or not _CJK_RE.search(text) or not any(char in text for char in _DELIMITERS):
        return text, ""
    marker = next((char for char in _MARKER_CANDIDATES if char not in text), "")
    if not marker:
        return text, ""
    inserts = []
    for segments in _paragraphs(text):
        unit = "\n".join(text[start:end] for start, end in segments)
        positions = []
        for start, end in segments:
            if positions:
                positions.append(-1)
            positions.extend(range(start, end))
        inserts.extend(
            (positions[offset], order, piece)
            for offset, order, piece in _paragraph_inserts(unit, marker)
        )
    if not inserts:
        return text, ""
    return _insert(text, inserts), marker


# ---------- Characters ----------
def _is_space(char) -> bool:
    return char is None or char in " \t\n\r\f\v" or unicodedata.category(char) == "Zs"


def _is_punctuation(char) -> bool:
    if char is None:
        return False
    return char in _ASCII_PUNCTUATION or unicodedata.category(char)[0] in "PS"


def _is_cjk(char) -> bool:
    if char is None:
        return False
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _insert(text: str, inserts) -> str:
    """Text with each (offset, order, piece) put before the character at offset."""
    output = []
    last = 0
    for offset, _order, piece in sorted(inserts):
        output.append(text[last:offset])
        output.append(piece)
        last = offset
    output.append(text[last:])
    return "".join(output)


# ---------- Paragraphs ----------
def _strip_quotes(line: str) -> tuple[int, int]:
    """Where a line's text starts after its "> " marks, and how many there are."""
    position = depth = 0
    while True:
        found = _QUOTE_RE.match(line, position)
        if found is None:
            return position, depth
        position = found.end()
        depth += 1


def _indent(line: str, position: int) -> int:
    width = 0
    for char in line[position:]:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4 - width % 4
        else:
            break
    return width


def _table_cells(line: str, start: int) -> list:
    cells = []
    cell_start = index = start
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if line[index] == "|":
            cells.append((cell_start, index))
            cell_start = index + 1
        index += 1
    cells.append((cell_start, len(line)))
    return [(begin, end) for begin, end in cells if line[begin:end].strip()]


def _is_table_delimiter(line: str) -> bool:
    position, _depth = _strip_quotes(line)
    rest = line[position:]
    return "|" in rest and "-" in rest and _TABLE_DELIMITER_RE.fullmatch(rest) is not None


def _paragraphs(text: str) -> list:
    """The (start, end) line pieces of each run of inline text: a paragraph,
    heading, list item's text or table cell. Code is left out."""
    lines = []
    offset = 0
    for line in text.split("\n"):
        lines.append((offset, line[:-1] if line.endswith("\r") else line))
        offset += len(line) + 1

    paragraphs = []
    segments = []
    quote_depth = 0
    fence = None
    in_table = False
    in_list = False
    previous_blank = True
    skip = -1

    def flush():
        if segments:
            paragraphs.append(list(segments))
            segments.clear()

    for index, (offset, line) in enumerate(lines):
        if index == skip:
            continue
        position, depth = _strip_quotes(line)
        if fence is not None:
            closing = _FENCE_RE.match(line, position)
            if (
                closing is not None
                and closing.group(1)[0] == fence[0]
                and len(closing.group(1)) >= fence[1]
                and not closing.group(2).strip()
            ):
                fence = None
            continue
        if not line[position:].strip():
            flush()
            in_table = False
            previous_blank = True
            continue
        blank_before = previous_blank
        previous_blank = False
        item = _LIST_ITEM_RE.match(line, position)
        if item is not None and _THEMATIC_BREAK_RE.fullmatch(line, position):
            item = None
        body = item.end() if item is not None else position
        opening = _FENCE_RE.match(line, body)
        if opening is not None and not (
            opening.group(1)[0] == "`" and "`" in opening.group(2)
        ):
            flush()
            fence = (opening.group(1)[0], len(opening.group(1)))
            in_table = False
            continue
        indent = _indent(line, position)
        if not depth and indent >= 4 and not segments and not in_list and item is None:
            continue  # an indented code line
        if in_table:
            if "|" in line[position:]:
                paragraphs.extend(
                    [(offset + begin, offset + end)]
                    for begin, end in _table_cells(line, position)
                )
                continue
            in_table = False
        elif (
            "|" in line[position:]
            and index + 1 < len(lines)
            and _is_table_delimiter(lines[index + 1][1])
        ):
            flush()
            in_table = True
            skip = index + 1
            paragraphs.extend(
                [(offset + begin, offset + end)]
                for begin, end in _table_cells(line, position)
            )
            continue
        if segments and _SETEXT_RE.fullmatch(line, position):
            flush()
            continue
        if _THEMATIC_BREAK_RE.fullmatch(line, position):
            flush()
            continue
        heading = _HEADING_RE.match(line, position)
        if heading is not None:
            flush()
            paragraphs.append([(offset + heading.end(), offset + len(line))])
            continue
        if item is not None:
            flush()
            in_list = True
            quote_depth = depth
            segments.append((offset + body, offset + len(line)))
            continue
        if blank_before and not depth and not indent:
            in_list = False
        if segments and depth > quote_depth:
            flush()
        if not segments:
            quote_depth = depth
        segments.append((offset + position, offset + len(line)))
    flush()
    return paragraphs


# ---------- Delimiter runs ----------
class _Run:
    """A run of *, _ or ~, with what CommonMark and the CJK reading let it do."""

    __slots__ = ("char", "start", "end", "before", "after", "standard", "cjk", "wide")

    def __init__(self, text: str, start: int, end: int):
        self.char = text[start]
        self.start = start
        self.end = end
        self.before = text[start - 1] if start else None
        self.after = text[end] if end < len(text) else None
        self.wide = _is_cjk(self.before) or _is_cjk(self.after)
        self.standard = self._flags(cjk=False)
        self.cjk = self._flags(cjk=True) if self.wide else self.standard

    @property
    def length(self) -> int:
        return self.end - self.start

    def _flags(self, *, cjk: bool) -> tuple[bool, bool]:
        """(can open, can close), as Qt's reader decides it, or with CJK
        characters counting as a word's edge."""
        space_before = _is_space(self.before)
        space_after = _is_space(self.after)
        punct_before = _is_punctuation(self.before)
        punct_after = _is_punctuation(self.after)
        if self.char == "~":
            can_open = space_before or punct_before
            can_close = space_after or punct_after
            # A single ~ is a range in Chinese, as in 周一~周五.
            if cjk and self.length == 2:
                can_open = can_open or not space_after
                can_close = can_close or not space_before
            return can_open, can_close
        left = not space_after and (
            not punct_after or space_before or punct_before or cjk
        )
        right = not space_before and (
            not punct_before or space_after or punct_after or cjk
        )
        if self.char == "*":
            return left, right
        return left and (not right or punct_before), right and (not left or punct_after)


def _closing_backticks(length: int) -> re.Pattern:
    pattern = _BACKTICK_CLOSERS.get(length)
    if pattern is None:
        pattern = re.compile("(?<!`)" + "`" * length + "(?!`)")
        _BACKTICK_CLOSERS[length] = pattern
    return pattern


def _link_end(text: str, index: int):
    """Where an inline link's "(destination "title")" starting at index ends."""
    depth = 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if not depth:
                return index + 1
        index += 1
    return None


def _bare_links(text: str) -> dict:
    links = {}
    for found in _BARE_LINK_RE.finditer(text):
        end = found.end()
        while end > found.start() and text[end - 1] in _LINK_TRAILING:
            end -= 1
        links[found.start()] = end
    return links


def _runs(text: str) -> tuple[list, list]:
    """A paragraph's delimiter runs, and the groups they pair within: each
    link's text, and the rest. Code, links' addresses and HTML are skipped."""
    runs = []
    groups = []
    outside = []
    brackets = []
    links = _bare_links(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            escaped = index + 1 < len(text) and text[index + 1] in _ASCII_PUNCTUATION
            index += 2 if escaped else 1
        elif index in links:
            index = max(links[index], index + 1)
        elif char == "`":
            end = index
            while end < len(text) and text[end] == "`":
                end += 1
            closing = _closing_backticks(end - index).search(text, end)
            index = closing.end() if closing is not None else end
        elif char == "<":
            found = _AUTOLINK_RE.match(text, index) or _HTML_TAG_RE.match(text, index)
            index = found.end() if found is not None else index + 1
        elif char == "[":
            brackets.append(len(outside))
            index += 1
        elif char == "]":
            index += 1
            if not brackets:
                continue
            first = brackets.pop()
            if index < len(text) and text[index] == "(":
                end = _link_end(text, index + 1)
                if end is not None:
                    groups.append(outside[first:])
                    del outside[first:]
                    index = end
        elif char in _DELIMITERS:
            end = index
            while end < len(text) and text[end] == char:
                end += 1
            if char != "~" or end - index <= 2:
                outside.append(len(runs))
                runs.append(_Run(text, index, end))
            index = end
        else:
            index += 1
    groups.append(outside)
    return runs, groups


def _pair_group(runs, group, flags) -> list:
    """CommonMark's emphasis pairing over one group: (opener, closer, used)."""
    count = {index: runs[index].length for index in group}
    alive = dict.fromkeys(group, True)
    pairs = []
    for position, closer in enumerate(group):
        can_open, can_close = flags[closer]
        if not can_close:
            continue
        run = runs[closer]
        while count[closer]:
            found = None
            for back in range(position - 1, -1, -1):
                opener = group[back]
                other = runs[opener]
                if (
                    not alive[opener]
                    or not count[opener]
                    or other.char != run.char
                    or not flags[opener][0]
                ):
                    continue
                if run.char == "~":
                    if other.length != run.length:
                        continue
                elif (
                    (flags[opener][1] or can_open)
                    and (other.length + run.length) % 3 == 0
                    and (other.length % 3 or run.length % 3)
                ):
                    continue
                found = back
                break
            if found is None:
                break
            opener = group[found]
            if run.char == "~":
                used = count[closer]
            else:
                used = 2 if count[opener] >= 2 and count[closer] >= 2 else 1
            count[opener] -= used
            count[closer] -= used
            pairs.append((opener, closer, used))
            for between in group[found + 1:position]:
                alive[between] = False
            if not count[opener]:
                alive[opener] = False
        if not count[closer] or not can_open:
            alive[closer] = False
    return pairs


def _pairs(runs, groups, flags) -> list:
    return sorted(pair for group in groups for pair in _pair_group(runs, group, flags))


def _paragraph_inserts(text: str, marker: str) -> list:
    """(offset, order, piece) to put into a paragraph so Qt pairs its emphasis
    as the CJK reading does; none when the readings agree or Qt can't be told."""
    runs, groups = _runs(text)
    if not any(run.wide for run in runs):
        return []
    standard = [run.standard for run in runs]
    cjk = [run.cjk for run in runs]
    wanted = _pairs(runs, groups, cjk)
    if standard == cjk or wanted == _pairs(runs, groups, standard):
        return []
    openers = {opener for opener, _closer, _used in wanted}
    closers = {closer for _opener, closer, _used in wanted}
    if openers & closers:
        return []

    def needs_space(char) -> bool:
        return not _is_space(char) and char not in _DELIMITERS

    inserts = []
    kept = []
    for index, run in enumerate(runs):
        if index in openers:
            kept.append(index)
            if run.standard != (True, False) and needs_space(run.before):
                inserts.append((run.start, 1, marker))
        elif index in closers:
            kept.append(index)
            if run.standard != (False, True) and needs_space(run.after):
                inserts.append((run.end, 0, marker))
        elif any(run.standard):
            inserts.extend((run.start + offset, 2, "\\") for offset in range(run.length))
        else:
            kept.append(index)

    # Check that Qt, reading the changed text by its own rules, pairs as wanted.
    changed_runs, changed_groups = _runs(_insert(text, inserts))
    if len(changed_runs) != len(kept):
        return []
    renumber = {old: new for new, old in enumerate(kept)}
    expected = sorted((renumber[opener], renumber[closer], used) for opener, closer, used in wanted)
    if _pairs(changed_runs, changed_groups, [run.standard for run in changed_runs]) != expected:
        return []
    return inserts
