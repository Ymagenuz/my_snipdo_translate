from latex_support import (
    LATEX_PLACEHOLDER_PREFIX,
    LatexStreamRestorer,
    is_latex_text,
    latex_prose_for_language_detection,
    protect_latex_fragments,
    restore_latex_placeholders,
)


def test_latex_detection_covers_documents_and_math():
    assert is_latex_text(r"\section{Introduction}") is True
    assert is_latex_text(r"The result is $E = mc^2$.") is True
    assert is_latex_text(r"\[x^2 + y^2 = z^2\]") is True
    assert is_latex_text("A plain translation sentence.") is False
    assert is_latex_text("Prices range from $5 to $10.") is False
    assert is_latex_text(r"C:\Users\example\notes.txt") is False


def test_protected_fragments_round_trip_without_changing_latex():
    source = r"""\section{Energy}
Einstein wrote $E = mc^2$ in Equation~\ref{eq:mass}.
\begin{align}
  F &= ma \\
  E &= mc^2
\end{align}
% Keep this source comment unchanged.
""".strip()

    protected, replacements = protect_latex_fragments(source)

    assert LATEX_PLACEHOLDER_PREFIX in protected
    assert "$E = mc^2$" not in protected
    assert r"\ref{eq:mass}" not in protected
    assert r"\section{Energy}" in protected
    assert len(replacements) == 4
    assert restore_latex_placeholders(protected, replacements) == source


def test_stream_restorer_handles_placeholders_split_across_chunks():
    source = r"The relation $E = mc^2$ is fundamental."
    protected, replacements = protect_latex_fragments(source)
    restorer = LatexStreamRestorer(replacements)

    restored_chunks = [restorer.feed(character) for character in protected]
    restored_chunks.append(restorer.finish())

    assert "".join(restored_chunks) == source


def test_language_detection_text_excludes_commands_formulas_and_comments():
    source = r"""\section{实验说明}
\begin{equation}
abcdefghijklmnopqrstuvwxyz = E + mc^2
\end{equation}
这是用于验证语言方向的中文正文。
% English source-only comment.
"""

    prose = latex_prose_for_language_detection(source)

    assert "实验说明" in prose
    assert "中文正文" in prose
    assert "abcdefghijklmnopqrstuvwxyz" not in prose
    assert "English source-only comment" not in prose
