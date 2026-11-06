"""Release history shown on the About page in Settings.

CHANGELOG.md and its translations ship next to the app (see
SnipDoTranslate.spec); every release adds its entry to each of them.
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtGui import (
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)

from app_i18n import DEFAULT_UI_LANGUAGE, tr
from app_updates import ReleaseInfo


CHANGELOG_FILES = {"en": "CHANGELOG.md", "zh-CN": "CHANGELOG.zh-CN.md"}
MAX_CHANGELOG_BYTES = 256 * 1024
_FIRST_ENTRY = re.compile(r"^## ", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,5})(?=[ \t])", re.MULTILINE)
# Notes may come from GitHub, so any HTML in them is shown as plain text.
_MARKDOWN = (
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub
    | QTextDocument.MarkdownFeature.MarkdownNoHTML
)
_SIZE = QTextFormat.Property.FontSizeAdjustment
_TEXT_COLOR = "#606266"
_HEADING_COLOR = "#303133"
_MUTED_COLOR = "#A8ABB2"
_SECTION_COLOR = "#909399"
# Badge label key, text color and background for a release heading.
_INSTALLED_BADGE = ("settings.about.changelog.installed", "#0B7A6F", "#E6F6F4")
_AVAILABLE_BADGE = ("settings.about.changelog.available", "#2F62D6", "#EAF1FE")


def load_changelog(directory: Path, language: str) -> str:
    """Return the release entries in language, falling back to English.

    The title and introduction above the first entry are left out, since the
    About page has its own heading. A missing or unreadable file gives "".
    """
    names = dict.fromkeys(
        (CHANGELOG_FILES.get(language, ""), CHANGELOG_FILES[DEFAULT_UI_LANGUAGE])
    )
    for name in filter(None, names):
        try:
            with (directory / name).open("rb") as handle:
                raw = handle.read(MAX_CHANGELOG_BYTES + 1)
            if len(raw) > MAX_CHANGELOG_BYTES:
                continue
            text = raw.decode("utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            continue
        first_entry = _FIRST_ENTRY.search(text)
        if first_entry is not None:
            return text[first_entry.start():].strip()
    return ""


def compose_changelog(entries: str, release: ReleaseInfo | None = None) -> str:
    """Put an available release's notes above the installed releases' entries."""
    if release is None or not release.notes:
        return entries
    heading = f"## {release.version}"
    if release.published:
        heading += f" — {release.published}"
    # One level down, so the notes' own sections sit under the version heading.
    notes = _HEADING.sub(r"#\1", release.notes)
    return "\n\n".join(part for part in (heading, notes, entries) if part)


def render_changelog(
    document: QTextDocument,
    markdown: str,
    *,
    installed_version: str,
    available_version: str = "",
) -> None:
    """Lay out changelog Markdown as an easy-to-scan list of releases.

    Headings stay close to the body size, dates are muted, and the installed
    and available releases get a badge.
    """
    document.setMarkdown(markdown, _MARKDOWN)
    document.setDocumentMargin(14)
    document.setIndentWidth(18)
    body = QTextCharFormat()
    body.setForeground(QColor(_TEXT_COLOR))
    everything = QTextCursor(document)
    everything.select(QTextCursor.SelectionType.Document)
    everything.mergeCharFormat(body)

    block = document.begin()
    first = True
    while block.isValid():
        level = block.blockFormat().headingLevel()
        block_format = block.blockFormat()
        block_format.setLineHeight(
            140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        if level == 1 or level == 2:
            block_format.setTopMargin(0 if first else 20)
            block_format.setBottomMargin(6)
        elif level:
            block_format.setTopMargin(8)
            block_format.setBottomMargin(2)
        QTextCursor(block).setBlockFormat(block_format)
        if level == 1 or level == 2:
            version = block.text().partition(" ")[0]
            if available_version and version == available_version:
                badge = _AVAILABLE_BADGE
            elif version == installed_version:
                badge = _INSTALLED_BADGE
            else:
                badge = None
            _style_release_heading(block, version, badge)
        elif level:
            _merge_block(block, _char_format(_SECTION_COLOR, bold=True))
        first = False
        block = block.next()


def _char_format(color: str, *, bold: bool, size: int = 0) -> QTextCharFormat:
    char_format = QTextCharFormat()
    char_format.setForeground(QColor(color))
    char_format.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    char_format.setProperty(_SIZE, size)
    return char_format


def _merge_block(block, char_format: QTextCharFormat, start: int = 0) -> None:
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + start)
    cursor.movePosition(
        QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
    )
    cursor.mergeCharFormat(char_format)


def _style_release_heading(block, version: str, badge) -> None:
    _merge_block(block, _char_format(_HEADING_COLOR, bold=True, size=1))
    # The date after the version reads as secondary information.
    _merge_block(block, _char_format(_MUTED_COLOR, bold=False), len(version))
    if badge is None:
        return
    key, color, background = badge
    cursor = QTextCursor(block)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertText("   ", _char_format(_MUTED_COLOR, bold=False))
    tag = _char_format(color, bold=True, size=-1)
    tag.setBackground(QColor(background))
    cursor.insertText(f" {tr(key)} ", tag)
