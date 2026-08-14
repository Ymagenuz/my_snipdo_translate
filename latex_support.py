from __future__ import annotations

import re


LATEX_PLACEHOLDER_PREFIX = "[[SNIPDO_LATEX_"
LATEX_PLACEHOLDER_SUFFIX = "]]"

_LATEX_SIGNAL_RE = re.compile(
    r"""
    \\(?:
        documentclass|usepackage|begin|end|chapter|section|subsection|
        subsubsection|paragraph|title|author|maketitle|item|caption|
        textbf|textit|emph|label|ref|eqref|pageref|autoref|cref|Cref|
        cite|citep|citet|frac|dfrac|tfrac|sqrt|sum|prod|int|lim|
        alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|
        mathrm|mathbf|mathit|mathbb|mathcal|operatorname|text
    )\*?(?=\s*[\[{]|\b)
    |\\[\[(]
    """,
    re.VERBOSE,
)

_INLINE_MATH_RE = re.compile(
    r"(?<!\\)\$(?!\$)(?:\\.|[^$\\\r\n])+(?<!\\)\$(?!\$)"
)

_DISPLAY_MATH_RE = re.compile(
    r"(?<!\\)\$\$.*?(?<!\\)\$\$|\\\[.*?\\\]",
    re.DOTALL,
)

_PROTECTED_LATEX_RE = re.compile(
    r"""
    \\begin\{(?P<math_env>
        equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|
        flalign\*?|displaymath|math|split|cases|matrix|pmatrix|bmatrix|
        Bmatrix|vmatrix|Vmatrix|smallmatrix|array|subequations|
        tikzpicture|lstlisting|minted|verbatim|Verbatim
    )\}
    .*?
    \\end\{(?P=math_env)\}
    |
    (?<!\\)\$\$.*?(?<!\\)\$\$
    |
    \\\[.*?\\\]
    |
    \\\(.*?\\\)
    |
    (?<!\\)\$(?!\$)(?:\\.|[^$\\\r\n])+(?<!\\)\$(?!\$)
    |
    \\(?:
        label|ref|eqref|pageref|autoref|cref|Cref|cite|citep|citet|
        nocite|bibliography|bibliographystyle|includegraphics|url|path|
        href|input|include
    )\*?(?:\s*\[[^\]\r\n]*\])*\s*\{(?:\\.|[^{}\r\n])*\}
    |
    \\verb\*?(?P<verb_delimiter>[^\w\s]).*?(?P=verb_delimiter)
    |
    (?<!\\)%[^\r\n]*
    """,
    re.VERBOSE | re.DOTALL,
)


def is_latex_text(text: str) -> bool:
    candidate = text or ""
    if not candidate:
        return False
    return bool(
        _LATEX_SIGNAL_RE.search(candidate)
        or _DISPLAY_MATH_RE.search(candidate)
        or _has_inline_math(candidate)
    )


def _has_inline_math(text: str) -> bool:
    for match in _INLINE_MATH_RE.finditer(text):
        # In ordinary prose, "$5 to $10" is two currency prefixes rather
        # than a LaTeX pair.  A digit immediately after the apparent closing
        # delimiter distinguishes that common case without rejecting $2$.
        if match.end() < len(text) and text[match.end()].isdigit():
            continue
        return True
    return False


def protect_latex_fragments(text: str) -> tuple[str, dict[str, str]]:
    """Replace non-translatable LaTeX fragments with stable placeholders."""
    if not text or not is_latex_text(text):
        return text or "", {}

    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = (
            f"{LATEX_PLACEHOLDER_PREFIX}"
            f"{len(replacements):04d}"
            f"{LATEX_PLACEHOLDER_SUFFIX}"
        )
        replacements[token] = match.group(0)
        return token

    return _PROTECTED_LATEX_RE.sub(replace, text), replacements


def restore_latex_placeholders(
    text: str,
    replacements: dict[str, str],
) -> str:
    restored = text or ""
    for token, original in replacements.items():
        restored = restored.replace(token, original)
    return restored


def latex_prose_for_language_detection(text: str) -> str:
    """Remove formula/control noise while retaining translatable prose."""
    candidate = text or ""
    if not is_latex_text(candidate):
        return candidate

    protected, replacements = protect_latex_fragments(candidate)
    for token in replacements:
        protected = protected.replace(token, " ")

    protected = re.sub(
        r"\\(?:begin|end)\s*\{[^{}]*\}",
        " ",
        protected,
    )
    protected = re.sub(
        r"\\(?:documentclass|usepackage|input|include)"
        r"(?:\s*\[[^\]]*\])?\s*\{[^{}]*\}",
        " ",
        protected,
    )
    protected = re.sub(r"\\[A-Za-z@]+\*?", " ", protected)
    protected = re.sub(r"\\.", " ", protected)
    protected = re.sub(r"[{}\[\]&~_^]", " ", protected)
    return re.sub(r"\s+", " ", protected).strip()


class LatexStreamRestorer:
    """Restore placeholders even when a streamed token spans API chunks."""

    def __init__(self, replacements: dict[str, str] | None = None):
        self.replacements = dict(replacements or {})
        self.buffer = ""

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        if not self.replacements:
            return chunk

        self.buffer += chunk
        output: list[str] = []

        while self.buffer:
            start = self.buffer.find(LATEX_PLACEHOLDER_PREFIX)
            if start < 0:
                retained = self._possible_prefix_suffix_length(self.buffer)
                if retained:
                    output.append(self.buffer[:-retained])
                    self.buffer = self.buffer[-retained:]
                else:
                    output.append(self.buffer)
                    self.buffer = ""
                break

            if start:
                output.append(self.buffer[:start])
                self.buffer = self.buffer[start:]

            end = self.buffer.find(
                LATEX_PLACEHOLDER_SUFFIX,
                len(LATEX_PLACEHOLDER_PREFIX),
            )
            if end < 0:
                break

            token_end = end + len(LATEX_PLACEHOLDER_SUFFIX)
            token = self.buffer[:token_end]
            output.append(self.replacements.get(token, token))
            self.buffer = self.buffer[token_end:]

        return "".join(output)

    def finish(self) -> str:
        remaining = restore_latex_placeholders(
            self.buffer,
            self.replacements,
        )
        self.buffer = ""
        return remaining

    @staticmethod
    def _possible_prefix_suffix_length(text: str) -> int:
        maximum = min(len(text), len(LATEX_PLACEHOLDER_PREFIX) - 1)
        for length in range(maximum, 0, -1):
            if LATEX_PLACEHOLDER_PREFIX.startswith(text[-length:]):
                return length
        return 0
