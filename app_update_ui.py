"""Update scheduling and state, separate from translation workflows.

Checks never interrupt with a prompt. A newer release is kept as
available_release; the Settings button and the About page in Settings show it
and offer to install it.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from app_i18n import tr
from app_updates import ReleaseInfo, UpdateChecker, is_newer_version
from app_update_flow import UpdateFlow
from ui_dialogs import fit_message_box


CHECK_INTERVAL_SECONDS = 6 * 60 * 60
STATE_FILE_NAME = "update_state.json"


def load_update_state(path: Path) -> tuple[float, str]:
    """Return when updates were last checked and the newer version found then."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            return 0.0, ""
        payload = json.loads(raw)
        last_check = payload.get("last_check", 0.0)
        available_version = payload.get("available_version", "")
        if (
            isinstance(last_check, bool)
            or not isinstance(last_check, (int, float))
            or not math.isfinite(last_check)
            or last_check < 0
            or not isinstance(available_version, str)
            or len(available_version) > 64
        ):
            return 0.0, ""
        return float(last_check), available_version
    except (OSError, ValueError, AttributeError, TypeError, OverflowError):
        return 0.0, ""


class UpdateNotifications(QObject):
    # Emitted whenever available_release, is_checking, is_installing or
    # last_result may have changed.
    changed = pyqtSignal()

    def __init__(self, window, *, checker=None, updater=None, clock=time.time):
        super().__init__(window)
        self.window = window
        self.checker = checker if checker is not None else UpdateChecker(self)
        self.updater = updater if updater is not None else UpdateFlow(window)
        self.updater.failed.connect(self._install_failed)
        self._clock = clock
        self._path = window.app_paths.data_dir / STATE_FILE_NAME
        self._last_check, self._found_version = load_update_state(self._path)
        self._manual_requested = False
        self._stopped = False
        self._status_dialog: QMessageBox | None = None
        self.available_release: ReleaseInfo | None = None
        # "current" or "failed" after a check that found no release, so the
        # About page can word it in whatever language is active; "" before.
        self.last_result = ""

        self._schedule = QTimer(self)
        self._schedule.setSingleShot(True)
        self._schedule.timeout.connect(self._check_automatically)
        self.checker.completed.connect(self._completed)
        self.checker.failed.connect(self._failed)

    @property
    def is_checking(self) -> bool:
        return self.checker.is_checking

    @property
    def is_installing(self) -> bool:
        return self.updater.is_active

    def start(self) -> None:
        """Call only for the primary GUI instance, after startup succeeds."""
        if not self._stopped and not self._schedule.isActive():
            self._schedule.start(5000)

    def stop(self) -> None:
        self._stopped = True
        self._schedule.stop()
        self.checker.stop()
        self.updater.stop()
        if self._status_dialog is not None:
            self._status_dialog.close()

    def _save_state(self) -> None:
        temporary_path = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix="update_state.", suffix=".tmp", dir=self._path.parent
            )
            temporary_path = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {"last_check": self._last_check,
                     "available_version": self._found_version},
                    handle,
                )
            os.replace(temporary_path, self._path)
        except OSError:
            # An unwritable preference file must not interrupt translation.
            pass
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _check_automatically(self) -> None:
        if self._stopped:
            return
        elapsed = self._clock() - self._last_check
        # A release found before a restart is looked up again straight away,
        # so the Settings button shows it without waiting out the interval.
        if (
            self._last_check
            and 0 <= elapsed < CHECK_INTERVAL_SECONDS
            and not is_newer_version(self._found_version)
        ):
            self._schedule.start(max(1000, int((CHECK_INTERVAL_SECONDS - elapsed) * 1000)))
            return
        self._begin_check(manual=False)

    def check_manually(self) -> None:
        if not self._stopped:
            self._begin_check(manual=True)

    def _begin_check(self, *, manual: bool) -> None:
        if self.updater.is_active:
            if manual:
                self.updater.show_progress()
            return
        self._manual_requested = self._manual_requested or manual
        if self.checker.is_checking:
            return
        self._schedule.stop()
        self._last_check = self._clock()
        self._save_state()
        self._schedule.start(CHECK_INTERVAL_SECONDS * 1000)
        self.checker.check()
        self.changed.emit()

    def _completed(self, release: ReleaseInfo | None) -> None:
        if self._stopped:
            return
        self._manual_requested = False
        self.available_release = release
        self.last_result = "" if release is not None else "current"
        found_version = release.version if release is not None else ""
        if found_version != self._found_version:
            self._found_version = found_version
            self._save_state()
        self.changed.emit()

    def _failed(self, _message: str) -> None:
        if self._stopped:
            return
        manual = self._manual_requested
        self._manual_requested = False
        # Automatic failures stay quiet; a release found earlier stays usable.
        if manual:
            self.last_result = "failed"
        self.changed.emit()

    def install(self, release: ReleaseInfo) -> None:
        """Download and install release; only ever called after the user asks."""
        if not self._stopped:
            self.updater.start(release)
            self.changed.emit()

    def open_release_page(self, release: ReleaseInfo) -> bool:
        return bool(QDesktopServices.openUrl(QUrl(release.url)))

    def _install_failed(self, message: str) -> None:
        self._show_status(message)
        self.changed.emit()

    def _show_status(self, message: str) -> None:
        if self._status_dialog is not None:
            self._status_dialog.close()
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle(tr("update.status.title"))
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        fit_message_box(dialog)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        self._status_dialog = dialog
        dialog.finished.connect(lambda _result: self._release_status(dialog))
        dialog.show()

    def _release_status(self, dialog) -> None:
        if self._status_dialog is dialog:
            self._status_dialog = None
        dialog.deleteLater()
