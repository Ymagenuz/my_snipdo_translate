from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app_paths import AppPaths, ensure_app_directories

LOG_FILE_NAME = "SnipDoTranslate.log"
DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_BACKUP_COUNT = 3


class AppEvent(str, Enum):
    APP_STARTING = "app.starting"
    APP_READY = "app.ready"
    APP_STOPPED = "app.stopped"
    SELF_TEST_PASSED = "self_test.passed"
    SELF_TEST_FAILED = "self_test.failed"

    INSTANCE_PRIMARY = "instance.primary"
    INSTANCE_SECONDARY = "instance.secondary"

    IPC_LISTENING = "ipc.listening"
    IPC_REQUEST_ACCEPTED = "ipc.request_accepted"
    IPC_REQUEST_REJECTED = "ipc.request_rejected"
    IPC_FORWARD_ACCEPTED = "ipc.forward_accepted"
    IPC_FORWARD_REJECTED = "ipc.forward_rejected"
    IPC_PROTOCOL_ERROR = "ipc.protocol_error"
    IPC_UNAVAILABLE = "ipc.unavailable"

    HISTORY_LOADED = "history.loaded"
    HISTORY_MIGRATED = "history.migrated"
    HISTORY_UNAVAILABLE = "history.unavailable"

    CREDENTIAL_AVAILABLE = "credential.available"
    CREDENTIAL_MIGRATED = "credential.migrated"
    CREDENTIAL_MISSING = "credential.missing"
    CREDENTIAL_UNAVAILABLE = "credential.unavailable"

    REQUEST_ACCEPTED = "request.accepted"
    REQUEST_REJECTED = "request.rejected"
    OCR_STARTED = "ocr.started"
    OCR_COMPLETED = "ocr.completed"
    OCR_BUSY = "ocr.busy"
    OCR_FAILED = "ocr.failed"
    TRANSLATION_STARTED = "translation.started"
    TRANSLATION_COMPLETED = "translation.completed"
    TRANSLATION_CANCELLED = "translation.cancelled"
    TRANSLATION_FAILED = "translation.failed"

    SOURCE_DELETE_COMPLETED = "source_delete.completed"
    SOURCE_DELETE_SKIPPED = "source_delete.skipped"
    SOURCE_DELETE_FAILED = "source_delete.failed"


_WARNING_EVENTS = frozenset(
    {
        AppEvent.IPC_REQUEST_REJECTED,
        AppEvent.IPC_FORWARD_REJECTED,
        AppEvent.HISTORY_UNAVAILABLE,
        AppEvent.CREDENTIAL_MISSING,
        AppEvent.REQUEST_REJECTED,
        AppEvent.OCR_BUSY,
        AppEvent.TRANSLATION_CANCELLED,
        AppEvent.SOURCE_DELETE_SKIPPED,
    }
)
_ERROR_EVENTS = frozenset(
    {
        AppEvent.SELF_TEST_FAILED,
        AppEvent.IPC_PROTOCOL_ERROR,
        AppEvent.IPC_UNAVAILABLE,
        AppEvent.CREDENTIAL_UNAVAILABLE,
        AppEvent.OCR_FAILED,
        AppEvent.TRANSLATION_FAILED,
        AppEvent.SOURCE_DELETE_FAILED,
    }
)


def _event_level(event: AppEvent) -> int:
    if event in _ERROR_EVENTS:
        return logging.ERROR
    if event in _WARNING_EVENTS:
        return logging.WARNING
    return logging.INFO


class _StructuredEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "app_event", None)
        if not isinstance(event, AppEvent):
            raise ValueError("only fixed application events may be logged")
        timestamp = datetime.fromtimestamp(record.created, timezone.utc)
        value = {
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "level": logging.getLevelName(_event_level(event)),
            "event": event.value,
        }
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class _SilentRotatingFileHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # A GUI executable commonly has no stderr. Logging failures must neither
        # open a console nor expose an OS error containing a filesystem path.
        return None


class PrivacyEventLogger:
    """Write allow-listed events without accepting any caller-provided text."""

    __slots__ = ("__handler", "__logger", "__closed")

    def __init__(self, handler: RotatingFileHandler):
        logger = logging.Logger("SnipDoTranslate.privacy", level=logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        self.__handler = handler
        self.__logger = logger
        self.__closed = False

    def event(self, event: AppEvent) -> None:
        if not isinstance(event, AppEvent):
            raise TypeError("event must be an AppEvent")
        if self.__closed:
            raise RuntimeError("logger is closed")
        self.__logger.log(
            _event_level(event),
            event.value,
            extra={"app_event": event},
        )

    def flush(self) -> None:
        if not self.__closed:
            self.__handler.flush()

    def close(self) -> None:
        if self.__closed:
            return
        self.__closed = True
        self.__logger.removeHandler(self.__handler)
        self.__handler.close()

    def __enter__(self) -> "PrivacyEventLogger":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def configure_app_logging(
    paths: AppPaths,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> PrivacyEventLogger:
    if not isinstance(paths, AppPaths):
        raise TypeError("paths must be an AppPaths instance")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if (
        not isinstance(backup_count, int)
        or isinstance(backup_count, bool)
        or backup_count <= 0
    ):
        raise ValueError("backup_count must be a positive integer")

    ensure_app_directories(paths)
    log_path = Path(paths.log_dir) / LOG_FILE_NAME
    handler = _SilentRotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(_StructuredEventFormatter())
    return PrivacyEventLogger(handler)
