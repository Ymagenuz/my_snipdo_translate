from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import uuid

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (
    QImage,
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


@dataclass(frozen=True)
class FormulaRenderStats:
    found: int = 0
    rendered: int = 0
    fallback: int = 0


def prepare_latex_math_for_document(
    text: str,
) -> tuple[str, list[LatexMathFragment]]:
    """Replace renderable math with stable plain-text placeholders."""
    source_text = text or ""
    if not source_text:
        return source_text, []

    placeholder_stem = "SNIPDOMATHPLACEHOLDER"
    while placeholder_stem in source_text:
        placeholder_stem += "Q"

    fragments: list[LatexMathFragment] = []
    output: list[str] = []
    previous_end = 0

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
        output.append(source_text[previous_end:match.start()])
        if display:
            output.append(f"\n\n{placeholder}\n\n")
        else:
            output.append(placeholder)
        previous_end = match.end()

    if not fragments:
        return source_text, []

    output.append(source_text[previous_end:])
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


@lru_cache(maxsize=1)
def _matplotlib_api():
    _configure_matplotlib_cache()
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import math_to_image

    return rc_context, FontProperties, math_to_image


def _formula_rows(fragment: LatexMathFragment) -> list[str]:
    body = fragment.body.strip()
    body = _NESTED_LAYOUT_RE.sub("", body)
    body = _IGNORED_LAYOUT_COMMAND_RE.sub("", body)
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
        normalized.append(row)
    return normalized or [body]


@lru_cache(maxsize=256)
def _render_math_row_png(
    expression: str,
    font_pixel_size: int,
    color: str,
    dpi: int,
) -> bytes:
    rc_context, FontProperties, math_to_image = _matplotlib_api()
    point_size = max(8.0, float(font_pixel_size) * 72.0 / 96.0)
    output = BytesIO()
    with rc_context(
        {
            "figure.facecolor": "none",
            "savefig.facecolor": "none",
            "savefig.transparent": True,
            "mathtext.fontset": "stix",
        }
    ):
        math_to_image(
            f"${expression}$",
            output,
            prop=FontProperties(size=point_size),
            dpi=dpi,
            format="png",
            color=color,
        )
    return output.getvalue()


def render_latex_fragment_png(
    fragment: LatexMathFragment,
    *,
    font_pixel_size: int = 15,
    color: str = "#2c3e50",
    dpi: int = 192,
) -> RenderedFormula:
    """Render a common LaTeX math fragment to a transparent PNG."""
    from PIL import Image

    rows = _formula_rows(fragment)
    images = []
    for row in rows:
        png = _render_math_row_png(row, font_pixel_size, color, dpi)
        image = Image.open(BytesIO(png)).convert("RGBA")
        image.load()
        images.append(image)

    scale = max(1.0, float(dpi) / 96.0)
    if len(images) == 1:
        image = images[0]
    else:
        gap = max(2, round(font_pixel_size * scale * 0.35))
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
    rendered_count = 0
    fallback_count = 0

    for fragment in fragments:
        cursor = document.find(fragment.placeholder)
        if cursor.isNull():
            fallback_count += 1
            continue

        try:
            rendered = render_latex_fragment_png(
                fragment,
                font_pixel_size=font_pixel_size,
                color=color,
            )
            image = QImage.fromData(rendered.png, "PNG")
            if image.isNull():
                raise ValueError("formula renderer returned an invalid image")

            resource_url = QUrl(
                f"snipdo-math:{uuid.uuid4().hex}/{rendered_count}"
            )
            document.addResource(
                QTextDocument.ResourceType.ImageResource,
                resource_url,
                image,
            )

            logical_width = rendered.logical_width
            logical_height = rendered.logical_height
            if logical_width > available_width:
                ratio = available_width / logical_width
                logical_width *= ratio
                logical_height *= ratio

            image_format = QTextImageFormat()
            image_format.setName(resource_url.toString())
            image_format.setWidth(logical_width)
            image_format.setHeight(logical_height)
            image_format.setVerticalAlignment(
                QTextCharFormat.VerticalAlignment.AlignMiddle
            )
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
                block_format.setTopMargin(8)
                block_format.setBottomMargin(10)
                cursor.setBlockFormat(block_format)
            rendered_count += 1
        except Exception:
            fallback_format = QTextCharFormat()
            fallback_format.setFontFamilies(["Cascadia Mono", "Consolas", "monospace"])
            fallback_format.setForeground(Qt.GlobalColor.darkGray)
            cursor.removeSelectedText()
            cursor.insertText(fragment.source, fallback_format)
            fallback_count += 1

    return FormulaRenderStats(
        found=len(fragments),
        rendered=rendered_count,
        fallback=fallback_count,
    )
