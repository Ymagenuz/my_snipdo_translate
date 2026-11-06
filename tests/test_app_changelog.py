import re
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtGui import QTextCursor, QTextDocument, QTextFormat

from app_changelog import (
    CHANGELOG_FILES,
    MAX_CHANGELOG_BYTES,
    compose_changelog,
    load_changelog,
    render_changelog,
)
from app_i18n import UI_LANGUAGE_CODES, set_ui_language
from app_updates import ReleaseInfo
from app_version import APP_VERSION


_ENTRY = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", re.MULTILINE)


def _write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_bytes(text.encode("utf-8"))


def test_every_ui_language_has_a_changelog():
    assert set(CHANGELOG_FILES) == set(UI_LANGUAGE_CODES)


@pytest.mark.parametrize("language", sorted(CHANGELOG_FILES))
def test_shipped_changelog_starts_with_this_version(project_root, language):
    # A release must add its entry to every changelog; the self-test checks it too.
    entries = load_changelog(project_root, language)
    assert entries.startswith(f"## {APP_VERSION} — ")
    assert (project_root / CHANGELOG_FILES[language]).is_file()


def test_changelogs_list_the_same_releases(project_root):
    listed = {
        language: _ENTRY.findall(
            (project_root / name).read_text(encoding="utf-8")
        )
        for language, name in CHANGELOG_FILES.items()
    }
    english = listed["en"]
    assert len(english) >= 2
    assert all(entries == english for entries in listed.values())
    versions = [tuple(map(int, version.split("."))) for version, _date in english]
    assert versions == sorted(versions, reverse=True)


def test_title_and_introduction_are_left_out(tmp_path):
    _write(tmp_path, "CHANGELOG.md", "# Changelog\r\n\r\nIntro.\r\n\r\n## 1.1.0\r\n\r\n- New.\r\n")
    assert load_changelog(tmp_path, "en") == "## 1.1.0\n\n- New."


def test_language_falls_back_to_english(tmp_path):
    _write(tmp_path, "CHANGELOG.md", "## 1.0.0\n\n- English.\n")
    assert load_changelog(tmp_path, "zh-CN") == "## 1.0.0\n\n- English."
    _write(tmp_path, "CHANGELOG.zh-CN.md", "## 1.0.0\n\n- 中文。\n")
    assert load_changelog(tmp_path, "zh-CN") == "## 1.0.0\n\n- 中文。"
    assert load_changelog(tmp_path, "xx") == "## 1.0.0\n\n- English."


@pytest.mark.parametrize("content", [
    b"\xff\xfe not utf-8",
    b"no entries here",
    b"## 1.0.0\n" + b"x" * MAX_CHANGELOG_BYTES,
], ids=["not-utf-8", "no-entries", "oversized"])
def test_unusable_changelog_gives_nothing(tmp_path, content):
    (tmp_path / "CHANGELOG.md").write_bytes(content)
    assert load_changelog(tmp_path, "en") == ""
    assert load_changelog(tmp_path / "missing", "en") == ""


def test_available_release_notes_come_first_one_level_down():
    release = ReleaseInfo(
        "13.0.0", "https://example.invalid",
        notes="## What's changed\n\n- Faster.\n\n### Details\n\nMore.",
        published="2026-11-20",
    )
    composed = compose_changelog("## 12.6.0 — 2026-11-05\n\n- Old.", release)
    assert composed == (
        "## 13.0.0 — 2026-11-20\n\n"
        "### What's changed\n\n- Faster.\n\n#### Details\n\nMore.\n\n"
        "## 12.6.0 — 2026-11-05\n\n- Old."
    )
    undated = replace(release, published="")
    assert compose_changelog("", undated).startswith("## 13.0.0\n\n### What's changed")


def test_release_without_notes_leaves_the_changelog_alone():
    entries = "## 12.6.0 — 2026-11-05\n\n- Old."
    assert compose_changelog(entries) == entries
    assert compose_changelog(entries, ReleaseInfo("13.0.0", "https://example.invalid")) == entries


def _headings(document):
    headings = []
    block = document.begin()
    while block.isValid():
        if block.blockFormat().headingLevel():
            headings.append(block)
        block = block.next()
    return headings


def _format_at(block, offset):
    # A cursor reports the format of the character just before it.
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + offset + 1)
    return cursor.charFormat()


def test_rendered_changelog_marks_installed_and_available_releases(qapp):
    document = QTextDocument()
    markdown = (
        "## 13.0.0 — 2026-11-20\n\n### What's changed\n\n- Faster.\n\n"
        "## 12.6.0 — 2026-11-05\n\n- Icon.\n\n## 12.5.0 — 2026-11-04\n\n- English."
    )

    render_changelog(
        document, markdown, installed_version="12.6.0", available_version="13.0.0"
    )

    headings = _headings(document)
    assert [block.text() for block in headings] == [
        "13.0.0 — 2026-11-20    Available ",
        "What's changed",
        "12.6.0 — 2026-11-05    Installed ",
        "12.5.0 — 2026-11-04",
    ]
    size = QTextFormat.Property.FontSizeAdjustment
    version_format = _format_at(headings[0], 0)
    assert version_format.property(size) == 1
    assert version_format.foreground().color().name() == "#303133"
    assert _format_at(headings[0], len("13.0.0 — ")).foreground().color().name() == "#a8abb2"
    available_badge = _format_at(headings[0], len(headings[0].text()) - 3)
    assert available_badge.background().color().name() == "#eaf1fe"
    installed_badge = _format_at(headings[2], len(headings[2].text()) - 3)
    assert installed_badge.foreground().color().name() == "#0b7a6f"
    assert _format_at(headings[1], 0).property(size) == 0
    assert headings[0].blockFormat().topMargin() == 0
    assert headings[2].blockFormat().topMargin() > 0


def test_rendered_changelog_shows_html_as_text(qapp):
    document = QTextDocument()
    render_changelog(
        document, "## 13.0.0\n\n- <img src=x> <b>bold</b>", installed_version="12.6.0"
    )
    assert "<img src=x> <b>bold</b>" in document.toPlainText()


def test_badges_follow_the_interface_language(qapp):
    set_ui_language("zh-CN")
    document = QTextDocument()
    render_changelog(
        document, "## 13.0.0\n\n- New.\n\n## 12.6.0\n\n- Old.",
        installed_version="12.6.0", available_version="13.0.0",
    )
    text = document.toPlainText()
    assert "13.0.0    可安装 " in text
    assert "12.6.0    当前版本 " in text
