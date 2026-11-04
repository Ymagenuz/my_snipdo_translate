from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (
    QFont,
    QFontInfo,
    QFontMetrics,
    QImage,
    QPainter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
)


_MATH_OR_LITERAL_RE = re.compile(
    r"""
    (?P<fenced_code>
        ^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*\r?\n
        .*?
        ^[ \t]{0,3}(?P=fence)[ \t]*$
    )
    |
    (?P<inline_code>(?<!`)`(?!`)[^`\r\n]*`(?!`))
    |
    (?P<verbatim>
        \\begin\{(?P<verbatim_env>verbatim|Verbatim|lstlisting|minted)\}
        .*?
        \\end\{(?P=verbatim_env)\}
    )
    |
    \\begin\{(?P<math_env>
        equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|
        flalign\*?|displaymath|math|split|cases|matrix|pmatrix|bmatrix|
        Bmatrix|vmatrix|Vmatrix|smallmatrix|array
    )\}
    (?P<math_env_body>.*?)
    \\end\{(?P=math_env)\}
    |
    (?P<display_dollar>(?<!\\)\$\$.*?(?<!\\)\$\$)
    |
    (?P<display_bracket>\\\[.*?\\\])
    |
    (?P<inline_parenthesis>\\\(.*?\\\))
    |
    (?P<inline_dollar>(?<!\\)\$(?!\$)(?:\\.|[^$\\\r\n])+(?<!\\)\$(?!\$))
    """,
    re.VERBOSE | re.DOTALL | re.MULTILINE,
)

_DISPLAY_ENVIRONMENTS = {
    "equation",
    "equation*",
    "align",
    "align*",
    "alignat",
    "alignat*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "flalign",
    "flalign*",
    "displaymath",
    "split",
    "cases",
    "matrix",
    "pmatrix",
    "bmatrix",
    "Bmatrix",
    "vmatrix",
    "Vmatrix",
    "smallmatrix",
    "array",
}

_MULTILINE_ENVIRONMENTS = {
    "align",
    "align*",
    "alignat",
    "alignat*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "flalign",
    "flalign*",
    "split",
    "cases",
    "array",
}

_ROW_BREAK_RE = re.compile(r"(?<!\\)\\\\(?:\s*\[[^\]\r\n]*\])?")
_NESTED_LAYOUT_RE = re.compile(
    r"\\(?:begin|end)\{(?:aligned|alignedat|gathered|split|cases|array)\}"
    r"(?:\s*\{[^{}]*\})?"
)
_IGNORED_LAYOUT_COMMAND_RE = re.compile(
    r"\\(?:label|tag)\*?\s*\{(?:\\.|[^{}])*\}"
    r"|\\(?:nonumber|notag)\b"
)
_COMMAND_RE = re.compile(r"\\(?:[A-Za-z]+|.)?", re.DOTALL)
_FRACTION_COMMANDS = {r"\frac", r"\dfrac", r"\tfrac", r"\binom"}
_WIDE_SPACE_COMMANDS = {r"\quad", r"\qquad"}

# STIX has an x-height of 0.45 em against 0.5 em for Segoe UI, so formulas
# are drawn this much larger to look the same size as the text around them.
_MATH_FONT_SCALE = 0.5 / 0.45
# Each script level is this much smaller than the one it is attached to.
# TeX uses 0.7, which is hard to read at screen text sizes.
_SCRIPT_SHRINK_FACTOR = 0.78
# Scripts stop shrinking before they get smaller than this (logical pixels),
# so a script on a script, such as the "-" in \int_{0^-} or a subscript in
# an inline fraction, stays readable.
_MIN_SCRIPT_PIXEL_SIZE = 10.0
# What is left of a display formula's line, and any blank lines after it.
_LEADING_BLANK_LINES_RE = re.compile(r"\A[ \t]*(?:\r?\n(?:[ \t]*\r?\n)*)?")


@dataclass(frozen=True)
class LatexMathFragment:
    placeholder: str
    source: str
    body: str
    display: bool
    environment: str = ""


@dataclass(frozen=True)
class RenderedFormula:
    png: bytes
    logical_width: float
    logical_height: float
    # Distance from the bottom edge up to the baseline of the last row.
    logical_depth: float = 0.0


@dataclass(frozen=True)
class FormulaRenderStats:
    found: int = 0
    rendered: int = 0
    fallback: int = 0


def prepare_latex_math_for_document(
    text: str,
    *,
    paragraph_break: str = "\n\n",
) -> tuple[str, list[LatexMathFragment]]:
    """Replace renderable math with stable plain-text placeholders.

    Each display formula gets a paragraph of its own, separated from the text
    around it by exactly ``paragraph_break``: plain text needs a single line
    break, as any blank line there is an empty block above the formula's own
    margins.
    """
    source_text = text or ""
    if not source_text:
        return source_text, []

    placeholder_stem = "SNIPDOMATHPLACEHOLDER"
    while placeholder_stem in source_text:
        placeholder_stem += "Q"

    fragments: list[LatexMathFragment] = []
    output: list[str] = []
    previous_end = 0
    after_display = False

    for match in _MATH_OR_LITERAL_RE.finditer(source_text):
        if (
            match.group("fenced_code") is not None
            or match.group("inline_code") is not None
            or match.group("verbatim") is not None
        ):
            continue

        if match.group("inline_dollar") is not None:
            # "$5 to $10" is ordinary currency prose, not a formula pair.
            if match.end() < len(source_text) and source_text[match.end()].isdigit():
                continue

        raw = match.group(0)
        environment = match.group("math_env") or ""
        if environment:
            body = match.group("math_env_body") or ""
            display = environment in _DISPLAY_ENVIRONMENTS
        elif match.group("display_dollar") is not None:
            body = raw[2:-2]
            display = True
        elif match.group("display_bracket") is not None:
            body = raw[2:-2]
            display = True
        elif match.group("inline_parenthesis") is not None:
            body = raw[2:-2]
            display = False
        else:
            body = raw[1:-1]
            display = False

        if not body.strip():
            continue

        placeholder = f"{placeholder_stem}{len(fragments):04d}END"
        fragments.append(
            LatexMathFragment(
                placeholder=placeholder,
                source=raw,
                body=body,
                display=display,
                environment=environment,
            )
        )
        text_before = source_text[previous_end:match.start()]
        if after_display:
            text_before = _LEADING_BLANK_LINES_RE.sub("", text_before)
        if display:
            text_before = text_before.rstrip()
        if text_before:
            if after_display:
                output.append(paragraph_break)
            output.append(text_before)
        if output and (display or (after_display and not text_before)):
            output.append(paragraph_break)
        output.append(placeholder)
        after_display = display
        previous_end = match.end()

    if not fragments:
        return source_text, []

    text_after = source_text[previous_end:]
    if after_display:
        text_after = _LEADING_BLANK_LINES_RE.sub("", text_after)
        if text_after.strip():
            output.append(paragraph_break)
        else:
            text_after = ""
    output.append(text_after)
    return "".join(output), fragments


def _configure_matplotlib_cache() -> None:
    if os.environ.get("MPLCONFIGDIR"):
        return

    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "SnipDoTranslate" / "matplotlib-cache"
        )
    candidates.append(Path(tempfile.gettempdir()) / "SnipDoTranslate-matplotlib")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            continue
        os.environ["MPLCONFIGDIR"] = str(candidate)
        return


# matplotlib keeps global rc state, so the background warm-up and the UI thread
# must not render at the same time.
_MATPLOTLIB_LOCK = threading.Lock()
_WARM_UP_LOCK = threading.Lock()
_warm_up_started = False


@lru_cache(maxsize=1)
def _matplotlib_api():
    _configure_matplotlib_cache()
    from matplotlib import _mathtext, rc_context
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import math_to_image

    return rc_context, FontProperties, math_to_image, _mathtext


def _formula_rows(fragment: LatexMathFragment) -> list[str]:
    body = fragment.body.strip()
    body = _NESTED_LAYOUT_RE.sub("", body)
    body = _IGNORED_LAYOUT_COMMAND_RE.sub("", body)
    display_style = fragment.display or r"\displaystyle" in body
    body = body.replace(r"\displaystyle", "")

    if fragment.environment in _MULTILINE_ENVIRONMENTS or _ROW_BREAK_RE.search(body):
        rows = _ROW_BREAK_RE.split(body)
    else:
        rows = [body]

    normalized: list[str] = []
    for row in rows:
        row = row.strip()
        if not row:
            continue
        # Alignment markers are layout hints rather than mathematical tokens.
        row = re.sub(r"(?<!\\)&", r"\\;", row)
        if display_style:
            row = _use_display_fractions(row)
        normalized.append(row)
    return normalized or [body]


def _use_display_fractions(expression: str) -> str:
    r"""Draw top-level fractions at full size, as LaTeX does in display math.

    matplotlib always sets \frac in text style, which shrinks the numerator
    and denominator. Fractions inside scripts or inside another fraction keep
    that smaller style, as they would in LaTeX.
    """
    output: list[str] = []
    # For each open brace group: whether it is set in a smaller style, and
    # the argument styles still expected once it closes.
    groups: list[tuple[bool, list[bool]]] = []
    # Styles for the arguments that follow a script mark or fraction command.
    pending: list[bool] = []
    position = 0
    while position < len(expression):
        char = expression[position]
        if char == "\\":
            command = _COMMAND_RE.match(expression, position).group(0)
            position += len(command)
            reduced = bool(pending) or any(group[0] for group in groups)
            if command == r"\frac" and not reduced:
                command = r"\dfrac"
            pending = [True, True] if command in _FRACTION_COMMANDS else pending[1:]
            output.append(command)
            continue
        if char in "^_":
            pending = [True]
        elif char == "{":
            reduced = pending[0] if pending else False
            groups.append((reduced, pending[1:]))
            pending = []
        elif char == "}":
            if groups:
                pending = groups.pop()[1]
        elif not char.isspace():
            pending = pending[1:]
        output.append(char)
        position += 1
    return "".join(output)


def _split_at_wide_spaces(expression: str) -> list[str]:
    r"""Split a row at its top-level \quad and \qquad separators."""
    pieces: list[str] = []
    depth = 0
    start = 0
    position = 0
    while position < len(expression):
        char = expression[position]
        if char == "\\":
            command = _COMMAND_RE.match(expression, position).group(0)
            if command == r"\left":
                depth += 1
            elif command == r"\right":
                depth -= 1
            elif command in _WIDE_SPACE_COMMANDS and depth == 0:
                pieces.append(expression[start:position])
                start = position + len(command)
            position += len(command)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        position += 1
    pieces.append(expression[start:])
    return [piece.strip() for piece in pieces if piece.strip()]


def _script_size_levels(math_pixel_size: float) -> int:
    """Count how many times scripts may shrink before getting too small."""
    levels = 1
    size = math_pixel_size * _SCRIPT_SHRINK_FACTOR
    while levels < 5 and size * _SCRIPT_SHRINK_FACTOR >= _MIN_SCRIPT_PIXEL_SIZE:
        size *= _SCRIPT_SHRINK_FACTOR
        levels += 1
    return levels


@lru_cache(maxsize=256)
def _render_math_row(
    expression: str,
    font_pixel_size: int,
    color: str,
    dpi: int,
) -> tuple[bytes, float]:
    """Render one row to PNG; also return its depth below the baseline in pixels."""
    with _MATPLOTLIB_LOCK:
        rc_context, FontProperties, math_to_image, mathtext = _matplotlib_api()
        math_pixel_size = float(font_pixel_size) * _MATH_FONT_SCALE
        point_size = max(8.0, math_pixel_size * 72.0 / 96.0)
        output = BytesIO()
        # matplotlib reads its script sizes from these module globals while
        # it lays the formula out. Its parse cache does not include them, so
        # they must depend on nothing but the font size.
        default_sizes = (mathtext.SHRINK_FACTOR, mathtext.NUM_SIZE_LEVELS)
        mathtext.SHRINK_FACTOR = _SCRIPT_SHRINK_FACTOR
        mathtext.NUM_SIZE_LEVELS = _script_size_levels(math_pixel_size) + 1
        try:
            with rc_context(
                {
                    "figure.facecolor": "none",
                    "savefig.facecolor": "none",
                    "savefig.transparent": True,
                    "mathtext.fontset": "stix",
                }
            ):
                depth_points = math_to_image(
                    f"${expression}$",
                    output,
                    prop=FontProperties(size=point_size),
                    dpi=dpi,
                    format="png",
                    color=color,
                )
        finally:
            mathtext.SHRINK_FACTOR, mathtext.NUM_SIZE_LEVELS = default_sizes
        return output.getvalue(), float(depth_points or 0.0) * dpi / 72.0


def warm_up_math_rendering() -> bool:
    """Load the formula renderer off the UI thread before a formula is shown.

    The first render imports matplotlib and loads its fonts, which otherwise
    freezes the window for most of a second. Only the first call starts work.
    """
    global _warm_up_started

    with _WARM_UP_LOCK:
        if _warm_up_started:
            return False
        _warm_up_started = True
    try:
        threading.Thread(
            target=_warm_up,
            name="SnipDoTranslate-MathWarmup",
            daemon=True,
        ).start()
    except RuntimeError:
        with _WARM_UP_LOCK:
            _warm_up_started = False
        return False
    return True


def _warm_up() -> None:
    try:
        import PIL.Image

        # Same size, colour and DPI as the result view, so the parser, fonts
        # and caches it fills are the ones the first real formula needs.
        _render_math_row("x", 15, "#2c3e50", 192)
    except Exception:
        # The UI thread renders normally (with its own fallback) if this fails.
        pass


def render_latex_fragment_png(
    fragment: LatexMathFragment,
    *,
    font_pixel_size: int = 15,
    color: str = "#2c3e50",
    dpi: int = 192,
    max_width: float | None = None,
) -> RenderedFormula:
    r"""Render a common LaTeX math fragment to a transparent PNG.

    A display row wider than ``max_width`` (logical pixels) is broken at its
    top-level \quad and \qquad separators, one part per row, so it does not
    have to be shrunk until its scripts are unreadable.
    """
    from PIL import Image

    scale = max(1.0, float(dpi) / 96.0)

    def render_row(row: str):
        png, depth = _render_math_row(row, font_pixel_size, color, dpi)
        image = Image.open(BytesIO(png)).convert("RGBA")
        image.load()
        return image, depth

    images = []
    depth = 0.0
    for row in _formula_rows(fragment):
        image, depth = render_row(row)
        too_wide = (
            fragment.display
            and max_width is not None
            and image.width > max_width * scale
        )
        pieces = _split_at_wide_spaces(row) if too_wide else []
        if len(pieces) < 2:
            images.append(image)
            continue
        for piece in pieces:
            image, depth = render_row(piece)
            images.append(image)

    if len(images) == 1:
        image = images[0]
    else:
        gap = max(2, round(font_pixel_size * scale * 0.5))
        width = max(item.width for item in images)
        height = sum(item.height for item in images) + gap * (len(images) - 1)
        image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        y = 0
        for item in images:
            x = (width - item.width) // 2
            image.alpha_composite(item, (x, y))
            y += item.height + gap

    output = BytesIO()
    image.save(output, format="PNG")
    return RenderedFormula(
        png=output.getvalue(),
        logical_width=max(1.0, image.width / scale),
        logical_height=max(1.0, image.height / scale),
        logical_depth=depth / scale,
    )


def render_math_fragments_in_document(
    widget,
    fragments: list[LatexMathFragment],
    *,
    font_pixel_size: int = 15,
    color: str = "#2c3e50",
) -> FormulaRenderStats:
    """Replace prepared placeholders in a QTextEdit with formula images."""
    if not fragments:
        return FormulaRenderStats()

    document = widget.document()
    available_width = max(120.0, float(widget.viewport().width()) - 28.0)
    text_font = document.defaultFont()
    rendered_count = 0
    fallback_count = 0

    # Formulas appear in document order, so search on from the previous
    # one, and lay the document out once instead of after every edit.
    search_from = 0
    # A formula repeated through the text shares one image resource.
    resources: dict[tuple[str, str, bool], tuple] = {}
    edit_cursor = QTextCursor(document)
    edit_cursor.beginEditBlock()
    try:
        for fragment in fragments:
            cursor = _find_placeholder(document, fragment.placeholder, search_from)
            if cursor.isNull():
                fallback_count += 1
                continue

            try:
                resource_key = (fragment.body, fragment.environment, fragment.display)
                resource = resources.get(resource_key)
                if resource is None:
                    rendered = render_latex_fragment_png(
                        fragment,
                        font_pixel_size=font_pixel_size,
                        color=color,
                        max_width=available_width,
                    )
                    image = QImage.fromData(rendered.png, "PNG")
                    if image.isNull():
                        raise ValueError("formula renderer returned an invalid image")

                    logical_width = rendered.logical_width
                    logical_height = rendered.logical_height
                    logical_depth = rendered.logical_depth
                    if logical_width > available_width:
                        ratio = available_width / logical_width
                        logical_width *= ratio
                        logical_height *= ratio
                        logical_depth *= ratio
                    alignment = QTextCharFormat.VerticalAlignment.AlignMiddle
                    image_font = text_font
                    if not fragment.display:
                        alignment = QTextCharFormat.VerticalAlignment.AlignBaseline
                        image, logical_height, image_font = _align_inline_baseline(
                            image,
                            logical_height,
                            logical_depth,
                            text_font,
                        )

                    resource_url = QUrl(
                        f"snipdo-math:{uuid.uuid4().hex}/{rendered_count}"
                    )
                    document.addResource(
                        QTextDocument.ResourceType.ImageResource,
                        resource_url,
                        image,
                    )
                    resource = (
                        resource_url.toString(),
                        logical_width,
                        logical_height,
                        alignment,
                        image_font,
                    )
                    resources[resource_key] = resource
                (
                    resource_name,
                    logical_width,
                    logical_height,
                    alignment,
                    image_font,
                ) = resource

                image_format = QTextImageFormat()
                image_format.setName(resource_name)
                image_format.setWidth(logical_width)
                image_format.setHeight(logical_height)
                image_format.setVerticalAlignment(alignment)
                image_format.setFont(image_font)
                image_format.setProperty(
                    QTextFormat.Property.ImageAltText,
                    fragment.source,
                )
                image_format.setProperty(
                    QTextFormat.Property.ImageTitle,
                    fragment.source,
                )

                cursor.removeSelectedText()
                cursor.insertImage(image_format)

                if fragment.display:
                    block_format = cursor.blockFormat()
                    block_format.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                    # The text line above already ends in extra line spacing,
                    # so a smaller top margin leaves equal space on both sides.
                    block_format.setTopMargin(10)
                    block_format.setBottomMargin(15)
                    # Set explicitly, so the text's wider line spacing is not
                    # applied to a formula that may be several rows tall.
                    block_format.setLineHeight(
                        100,
                        QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
                    )
                    cursor.setBlockFormat(block_format)
                rendered_count += 1
            except Exception:
                _insert_formula_source(cursor, fragment)
                fallback_count += 1
            search_from = cursor.position()
    finally:
        edit_cursor.endEditBlock()

    return FormulaRenderStats(
        found=len(fragments),
        rendered=rendered_count,
        fallback=fallback_count,
    )


def show_math_fragment_sources(widget, fragments: list[LatexMathFragment]) -> None:
    """Show prepared formulas as LaTeX source until the final render."""
    document = widget.document()
    search_from = 0
    edit_cursor = QTextCursor(document)
    edit_cursor.beginEditBlock()
    try:
        for fragment in fragments:
            cursor = _find_placeholder(document, fragment.placeholder, search_from)
            if not cursor.isNull():
                _insert_formula_source(cursor, fragment)
                search_from = cursor.position()
    finally:
        edit_cursor.endEditBlock()


def _font_with_descent(font: QFont, depth: float) -> QFont:
    """Return ``font``, or a larger copy of it whose descent reaches ``depth``."""
    if QFontMetrics(font).descent() >= depth:
        return font
    pixel_size = QFontInfo(font).pixelSize()
    for size in range(pixel_size + 1, pixel_size * 6):
        larger = QFont(font)
        larger.setPixelSize(size)
        if QFontMetrics(larger).descent() >= depth:
            return larger
    return font


def _align_inline_baseline(
    image: QImage,
    logical_height: float,
    logical_depth: float,
    text_font: QFont,
) -> tuple[QImage, float, QFont]:
    """Pad an inline formula so its baseline sits on the text baseline.

    Qt ends an AlignBaseline image one descent of its format's font below the
    text baseline (other alignments depend on the rest of the line), so
    transparent rows under the formula fill that descent. A formula reaching
    deeper than the text gets a larger font for its format instead, which
    makes the line just deep enough for it. Returns that font.
    """
    image_font = _font_with_descent(text_font, logical_depth)
    padding = QFontMetrics(image_font).descent() - logical_depth
    pixels_per_unit = image.height() / logical_height
    padding_pixels = round(padding * pixels_per_unit)
    if padding_pixels <= 0:
        return image, logical_height, image_font

    padded = QImage(
        image.width(),
        image.height() + padding_pixels,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    padded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(padded)
    painter.drawImage(0, 0, image)
    painter.end()
    return padded, padded.height() / pixels_per_unit, image_font


def _find_placeholder(document, placeholder: str, position: int):
    cursor = document.find(placeholder, position)
    if cursor.isNull() and position:
        cursor = document.find(placeholder)
    return cursor


def _insert_formula_source(cursor, fragment: LatexMathFragment) -> None:
    source_format = QTextCharFormat()
    source_format.setFontFamilies(["Cascadia Mono", "Consolas", "monospace"])
    source_format.setForeground(Qt.GlobalColor.darkGray)
    cursor.removeSelectedText()
    cursor.insertText(fragment.source, source_format)
