import re
import threading
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QTextEdit

import latex_renderer
from latex_renderer import (
    LatexMathFragment,
    prepare_latex_math_for_document,
    render_latex_fragment_png,
    render_math_fragments_in_document,
    show_math_fragment_sources,
    warm_up_math_rendering,
)


def test_math_warm_up_runs_once_off_the_calling_thread(monkeypatch):
    ran = threading.Event()
    threads = []

    def fake_warm_up():
        threads.append(threading.get_ident())
        ran.set()

    monkeypatch.setattr(latex_renderer, "_warm_up_started", False)
    monkeypatch.setattr(latex_renderer, "_warm_up", fake_warm_up)

    assert warm_up_math_rendering()
    assert ran.wait(5)
    assert not warm_up_math_rendering()
    assert threads != [threading.get_ident()]
    assert len(threads) == 1


def test_math_warm_up_fills_the_result_view_render_cache(monkeypatch):
    calls = []
    original = latex_renderer._render_math_row

    def recording_render(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(latex_renderer, "_render_math_row", recording_render)

    latex_renderer._warm_up()

    assert calls == [("x", 15, "#2c3e50", 192)]


def test_prepare_latex_math_recognizes_common_delimiters_and_environments():
    source = r"""Inline \(a^2+b^2=c^2\).

\[
  \frac{-b \pm \sqrt{b^2-4ac}}{2a}
\]

\begin{align}
  F &= ma \\
  E &= mc^2
\end{align}
"""

    prepared, fragments = prepare_latex_math_for_document(source)

    assert len(fragments) == 3
    assert [fragment.display for fragment in fragments] == [False, True, True]
    assert fragments[2].environment == "align"
    assert all(fragment.placeholder in prepared for fragment in fragments)
    assert r"\frac" not in prepared


def test_prepare_latex_math_ignores_currency_and_code_literals():
    source = """Prices range from $5 to $10.

`$not_math$`

```python
price = "$also_not_math$"
```

But $x+y$ is math.
"""

    prepared, fragments = prepare_latex_math_for_document(source)

    assert len(fragments) == 1
    assert fragments[0].source == "$x+y$"
    assert "$5 to $10" in prepared
    assert "$not_math$" in prepared
    assert "$also_not_math$" in prepared


def test_display_formulas_get_exactly_one_paragraph_break_around_them():
    source = "Intro\n\n$$a$$\n\n  $$b$$  \n\nAfter $c$.\n\n$$d$$ then\n"

    plain, fragments = prepare_latex_math_for_document(source, paragraph_break="\n")
    markdown, _ = prepare_latex_math_for_document(source)

    a, b, c, d = (fragment.placeholder for fragment in fragments)
    assert plain == f"Intro\n{a}\n{b}\nAfter {c}.\n{d}\nthen\n"
    assert markdown == f"Intro\n\n{a}\n\n{b}\n\nAfter {c}.\n\n{d}\n\nthen\n"


def test_display_style_enlarges_only_top_level_fractions():
    expression = (
        r"\frac{a}{b} + x^{\frac{1}{2}} + \frac{\frac{c}{d}}{\frac{e}{f}}"
        r" + \sqrt{\frac{g}{h}}"
    )

    assert latex_renderer._use_display_fractions(expression) == (
        r"\dfrac{a}{b} + x^{\frac{1}{2}} + \dfrac{\frac{c}{d}}{\frac{e}{f}}"
        r" + \sqrt{\dfrac{g}{h}}"
    )


def test_display_formulas_draw_fractions_larger_than_inline_ones():
    body = r"I_s = \frac{12\ \text{V}}{2\ \text{k}\Omega}"
    inline = render_latex_fragment_png(
        LatexMathFragment("TOKEN", f"${body}$", body, display=False)
    )
    display = render_latex_fragment_png(
        LatexMathFragment("TOKEN", f"$${body}$$", body, display=True)
    )

    assert display.logical_height > inline.logical_height * 1.2


def test_scripts_shrink_twice_for_result_text_and_then_stop():
    math_size = 15 * latex_renderer._MATH_FONT_SCALE
    levels = latex_renderer._script_size_levels(math_size)
    smallest = math_size * latex_renderer._SCRIPT_SHRINK_FACTOR ** levels

    # 16.7px formula text, 13px scripts, and 10px scripts on scripts, where
    # TeX's sizes would reach 8px and keep going.
    assert levels == 2
    assert round(smallest) == 10


def test_row_render_applies_script_sizes_and_restores_matplotlib(monkeypatch):
    rc_context, font_properties, math_to_image, mathtext = (
        latex_renderer._matplotlib_api()
    )
    defaults = (mathtext.SHRINK_FACTOR, mathtext.NUM_SIZE_LEVELS)
    seen = []

    def recording_math_to_image(*args, **kwargs):
        seen.append((mathtext.SHRINK_FACTOR, mathtext.NUM_SIZE_LEVELS))
        return math_to_image(*args, **kwargs)

    monkeypatch.setattr(
        latex_renderer,
        "_matplotlib_api",
        lambda: (rc_context, font_properties, recording_math_to_image, mathtext),
    )

    png, depth = latex_renderer._render_math_row("x_{1_{2_{3}}}", 15, "#123456", 96)

    assert seen == [(latex_renderer._SCRIPT_SHRINK_FACTOR, 3)]
    assert (mathtext.SHRINK_FACTOR, mathtext.NUM_SIZE_LEVELS) == defaults
    assert Image.open(BytesIO(png)).height > 0
    assert depth > 0


def test_wide_display_formula_wraps_at_wide_spaces_instead_of_shrinking():
    body = r"v_C(t) = 9\left(1 - e^{-t/\tau}\right), \qquad \tau = R_{th}C = 15\ \text{ms}"
    fragment = LatexMathFragment("TOKEN", f"$${body}$$", body, display=True)

    single = render_latex_fragment_png(fragment)
    wrapped = render_latex_fragment_png(fragment, max_width=single.logical_width * 0.7)

    assert wrapped.logical_width <= single.logical_width * 0.7
    assert wrapped.logical_height > single.logical_height * 1.5


def test_formulas_are_only_split_at_top_level_wide_spaces():
    expression = r"\left(a \qquad b\right) \quad \text{if } {c \quad d}"

    assert latex_renderer._split_at_wide_spaces(expression) == [
        r"\left(a \qquad b\right)",
        r"\text{if } {c \quad d}",
    ]


@pytest.fixture
def text_font(qapp):
    # Offscreen tests have no system fonts, and the fallback has no descent.
    from matplotlib import get_data_path

    font_path = Path(get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    assert font_id >= 0
    font = QFont(QFontDatabase.applicationFontFamilies(font_id)[0])
    font.setPixelSize(15)
    yield font
    QFontDatabase.removeApplicationFont(font_id)


@pytest.mark.parametrize(
    "formula",
    [r"$H$", r"$H(0^-)$", r"$H_{y_{j}}$", r"$H\frac{a}{b}$"],
)
def test_inline_formula_sits_on_the_text_baseline(qapp, text_font, formula):
    widget = QTextEdit()
    widget.resize(400, 120)
    widget.setStyleSheet("background: white; border: none;")
    widget.document().setDefaultFont(text_font)
    prepared, fragments = prepare_latex_math_for_document(f"xH {formula} Hx")
    widget.setPlainText(prepared)

    stats = render_math_fragments_in_document(widget, fragments, color="#ff0000")
    widget.show()
    qapp.processEvents()
    try:
        layout = widget.document().firstBlock().layout()
        line = layout.lineAt(0)
        baseline = layout.position().y() + line.y() + line.ascent()
        image = widget.viewport().grab().toImage()
        red_columns = {}
        for x in range(image.width()):
            for y in range(image.height()):
                color = QColor(image.pixel(x, y))
                if color.red() > 150 and color.green() < 100 and color.blue() < 100:
                    red_columns.setdefault(x, []).append(y)
    finally:
        widget.close()

    assert stats.rendered == 1
    # The left stem of the formula's H ends on its baseline.
    stem = [y for x in sorted(red_columns)[:3] for y in red_columns[x]]
    assert abs(max(stem) + 1 - baseline) <= 1


def test_formula_renderer_outputs_transparent_high_resolution_png():
    fragment = LatexMathFragment(
        placeholder="TOKEN",
        source=r"\[\frac{-b \pm \sqrt{b^2-4ac}}{2a}\]",
        body=r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}",
        display=True,
    )

    rendered = render_latex_fragment_png(fragment, font_pixel_size=15)
    image = Image.open(BytesIO(rendered.png)).convert("RGBA")
    alpha_minimum, alpha_maximum = image.getchannel("A").getextrema()

    assert image.width > rendered.logical_width
    assert image.height > rendered.logical_height
    assert alpha_minimum == 0
    assert alpha_maximum > 0


def test_document_renderer_replaces_formulas_with_images(qapp):
    source = "能量为 $E=mc^2$。\n\n" + r"\[x^2+y^2=z^2\]"
    prepared, fragments = prepare_latex_math_for_document(source)
    widget = QTextEdit()
    widget.resize(500, 300)
    widget.setPlainText(prepared)

    stats = render_math_fragments_in_document(widget, fragments)

    assert stats.found == 2
    assert stats.rendered == 2
    assert stats.fallback == 0
    assert widget.toPlainText().count("\ufffc") == 2
    assert "SNIPDOMATHPLACEHOLDER" not in widget.toPlainText()
    assert widget.toHtml().count("<img") == 2


def test_document_renderer_shares_one_image_for_a_repeated_formula(qapp):
    source = "设 $x$ 为整数，$x$ 大于零，且 $y$ 为偶数。"
    prepared, fragments = prepare_latex_math_for_document(source)
    widget = QTextEdit()
    widget.resize(500, 300)
    widget.setPlainText(prepared)
    changes = []
    widget.document().contentsChange.connect(lambda *args: changes.append(args))

    stats = render_math_fragments_in_document(widget, fragments)

    assert stats.rendered == 3
    names = re.findall(r'<img src="([^"]+)"', widget.toHtml())
    assert len(names) == 3
    assert names[0] == names[1] != names[2]
    # The three replacements reach the layout as one change.
    assert len(changes) == 1


def test_formula_sources_are_found_out_of_document_order(qapp):
    source = "A $a$, B $b$, C $c$."
    prepared, fragments = prepare_latex_math_for_document(source)
    widget = QTextEdit()
    widget.setPlainText(prepared)

    show_math_fragment_sources(widget, list(reversed(fragments)))

    assert widget.toPlainText() == source
