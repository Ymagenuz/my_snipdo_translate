from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app_logging import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    LOG_FILE_NAME,
    AppEvent,
    configure_app_logging,
)
from app_paths import resolve_app_paths

SYNTHETIC_SECRET = "codex-test-sensitive-value-that-must-never-be-logged"
SYNTHETIC_PATH = r"C:\Users\Synthetic\private-image.png"


def test_events_are_structured_and_stored_under_app_log_dir(tmp_path: Path):
    paths = resolve_app_paths(
        tmp_path / "bundle" / "snipdo_translate.pyw",
        tmp_path / "bundle" / "pythonw.exe",
        False,
        tmp_path / "LocalAppData",
    )

    with configure_app_logging(paths) as event_log:
        event_log.event(AppEvent.APP_STARTING)
        event_log.event(AppEvent.APP_READY)
        event_log.flush()

    log_path = paths.log_dir / LOG_FILE_NAME
    assert log_path.is_file()
    assert log_path.parent == paths.data_dir / "logs"
    records = [json.loads(line) for line in log_path.read_text("utf-8").splitlines()]
    assert [record["event"] for record in records] == [
        "app.starting",
        "app.ready",
    ]
    assert all(set(record) == {"timestamp", "level", "event"} for record in records)
    assert all(record["level"] == "INFO" for record in records)
    assert all(record["timestamp"].endswith("Z") for record in records)


def test_public_interface_rejects_arbitrary_messages_and_sensitive_values(
    tmp_path: Path,
):
    paths = resolve_app_paths(
        tmp_path / "snipdo_translate.pyw",
        tmp_path / "pythonw.exe",
        False,
        tmp_path / "LocalAppData",
    )
    event_log = configure_app_logging(paths)
    try:
        with pytest.raises(TypeError):
            event_log.event(SYNTHETIC_SECRET)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            event_log.event(  # type: ignore[call-arg]
                AppEvent.OCR_FAILED,
                message=f"{SYNTHETIC_SECRET} {SYNTHETIC_PATH}",
            )
        event_log.event(AppEvent.OCR_FAILED)
        event_log.flush()
    finally:
        event_log.close()

    contents = (paths.log_dir / LOG_FILE_NAME).read_text("utf-8")
    assert SYNTHETIC_SECRET not in contents
    assert SYNTHETIC_PATH not in contents
    assert "ocr.failed" in contents
    assert "ERROR" in contents
    assert not hasattr(event_log, "info")
    assert not hasattr(event_log, "exception")


def test_rotation_keeps_only_configured_backups(tmp_path: Path):
    paths = resolve_app_paths(
        tmp_path / "snipdo_translate.pyw",
        tmp_path / "pythonw.exe",
        False,
        tmp_path / "LocalAppData",
    )
    with configure_app_logging(paths, max_bytes=240, backup_count=2) as event_log:
        for _ in range(40):
            event_log.event(AppEvent.TRANSLATION_COMPLETED)

    files = sorted(paths.log_dir.glob(f"{LOG_FILE_NAME}*"))
    assert [path.name for path in files] == [
        LOG_FILE_NAME,
        f"{LOG_FILE_NAME}.1",
        f"{LOG_FILE_NAME}.2",
    ]
    for path in files:
        lines = path.read_text("utf-8").splitlines()
        assert lines
        assert all(json.loads(line)["event"] == "translation.completed" for line in lines)


def test_logging_does_not_touch_root_logger_or_write_to_console(
    tmp_path: Path, capsys
):
    paths = resolve_app_paths(
        tmp_path / "snipdo_translate.pyw",
        tmp_path / "pythonw.exe",
        False,
        tmp_path / "LocalAppData",
    )
    root_handlers = tuple(logging.getLogger().handlers)

    with configure_app_logging(paths) as event_log:
        event_log.event(AppEvent.APP_READY)

    assert tuple(logging.getLogger().handlers) == root_handlers
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    ("event", "expected_level"),
    [
        (AppEvent.APP_READY, "INFO"),
        (AppEvent.OCR_BUSY, "WARNING"),
        (AppEvent.OCR_FAILED, "ERROR"),
    ],
)
def test_event_severity_is_fixed_by_event(
    tmp_path: Path, event: AppEvent, expected_level: str
):
    paths = resolve_app_paths(
        tmp_path / event.name / "snipdo_translate.pyw",
        tmp_path / event.name / "pythonw.exe",
        False,
        tmp_path / "LocalAppData" / event.name,
    )
    with configure_app_logging(paths) as event_log:
        event_log.event(event)

    record = json.loads((paths.log_dir / LOG_FILE_NAME).read_text("utf-8"))
    assert record["level"] == expected_level


@pytest.mark.parametrize(
    ("max_bytes", "backup_count"),
    [(0, 1), (-1, 1), (True, 1), (1, 0), (1, -1), (1, False)],
)
def test_invalid_rotation_settings_are_rejected(
    tmp_path: Path, max_bytes: int, backup_count: int
):
    paths = resolve_app_paths(
        tmp_path / "snipdo_translate.pyw",
        tmp_path / "pythonw.exe",
        False,
        tmp_path / "LocalAppData",
    )
    with pytest.raises(ValueError):
        configure_app_logging(
            paths, max_bytes=max_bytes, backup_count=backup_count
        )


def test_rotation_defaults_are_bounded():
    assert DEFAULT_MAX_BYTES == 512 * 1024
    assert DEFAULT_BACKUP_COUNT == 3
    assert DEFAULT_MAX_BYTES * (DEFAULT_BACKUP_COUNT + 1) == 2 * 1024 * 1024
