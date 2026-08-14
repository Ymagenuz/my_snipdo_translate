from io import BytesIO

from PIL import Image
from PyQt6.QtWidgets import QTextEdit

from latex_renderer import (
    LatexMathFragment,
    prepare_latex_math_for_document,
    render_latex_fragment_png,
    render_math_fragments_in_document,
)


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
