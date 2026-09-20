"""Update scheduling and user prompts, separate from translation workflows."""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox

from app_updates import ReleaseInfo, UpdateChecker
from app_version import APP_VERSION


CHECK_INTERVAL_SECONDS = 24 * 60 * 60
STATE_FILE_NAME = "update_state.json"


def load_update_state(path: Path) -> tuple[float, str]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            return 0.0, ""
        payload = json.loads(raw)
        last_check = payload.get("last_check", 0.0)
        skipped_version = payload.get("skipped_version", "")
        if (
            isinstance(last_check, bool)
            or not isinstance(last_check, (int, float))
            or not math.isfinite(last_check)
            or last_check < 0
            or not isinstance(skipped_version, str)
            or len(skipped_version) > 64
        ):
            return 0.0, ""
        return float(last_check), skipped_version
    except (OSError, ValueError, AttributeError, TypeError, OverflowError):
        return 0.0, ""


class UpdateNotifications(QObject):
    def __init__(self, window, *, checker=None, clock=time.time):
        super().__init__(window)
        self.window = window
        self.checker = checker if checker is not None else UpdateChecker(self)
        self._clock = clock
        self._path = window.app_paths.data_dir / STATE_FILE_NAME
        self._last_check, self._skipped_version = load_update_state(self._path)
        self._manual_requested = False
        self._stopped = False
        self._shown_versions: set[str] = set()
        self._pending_release: ReleaseInfo | None = None
        self._pending_manual = False
        self._dialog: QMessageBox | None = None
        self._displayed_version: str | None = None
        self._status_dialog: QMessageBox | None = None

        self._schedule = QTimer(self)
        self._schedule.setSingleShot(True)
        self._schedule.timeout.connect(self._check_automatically)
        self._prompt_retry = QTimer(self)
        self._prompt_retry.setSingleShot(True)
        self._prompt_retry.timeout.connect(self._show_pending_release)
        self.checker.completed.connect(self._completed)
        self.checker.failed.connect(self._failed)

    def start(self) -> None:
        """Call only for the primary GUI instance, after startup succeeds."""
        if not self._stopped and not self._schedule.isActive():
            self._schedule.start(5000)

    def stop(self) -> None:
        self._stopped = True
        self._schedule.stop()
        self._prompt_retry.stop()
        self._pending_release = None
        self._pending_manual = False
        self.checker.stop()
        for dialog in (self._dialog, self._status_dialog):
            if dialog is not None:
                dialog.close()

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
                     "skipped_version": self._skipped_version},
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
        if self._last_check and 0 <= elapsed < CHECK_INTERVAL_SECONDS:
            self._schedule.start(max(1000, int((CHECK_INTERVAL_SECONDS - elapsed) * 1000)))
            return
        self._begin_check(manual=False)

    def check_manually(self) -> None:
        if not self._stopped:
            self._begin_check(manual=True)

    def _begin_check(self, *, manual: bool) -> None:
        self._manual_requested = self._manual_requested or manual
        if self.checker.is_checking:
            return
        self._schedule.stop()
        self._last_check = self._clock()
        self._save_state()
        self._schedule.start(CHECK_INTERVAL_SECONDS * 1000)
        self.checker.check()

    def _completed(self, release: ReleaseInfo | None) -> None:
        if self._stopped:
            return
        manual = self._manual_requested
        self._manual_requested = False
        if release is None:
            if manual:
                self._show_status(f"当前版本为 {APP_VERSION}，未发现可用的更新。")
            return
        if not manual and (
            release.version == self._skipped_version
            or release.version in self._shown_versions
        ):
            return
        if self._dialog is not None and self._displayed_version == release.version:
            if manual:
                self._dialog.raise_()
                self._dialog.activateWindow()
            return
        self._pending_release = release
        self._pending_manual = manual
        self._show_pending_release(manual=manual)

    def _failed(self, message: str) -> None:
        if self._stopped:
            return
        manual = self._manual_requested
        self._manual_requested = False
        if manual:
            self._show_status(message)

    def _show_status(self, message: str) -> None:
        if self._status_dialog is not None:
            self._status_dialog.close()
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle("检查更新")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        self._status_dialog = dialog
        dialog.finished.connect(lambda _result: self._release_status(dialog))
        dialog.show()

    def _release_status(self, dialog) -> None:
        if self._status_dialog is dialog:
            self._status_dialog = None
        dialog.deleteLater()

    def _show_pending_release(self, *, manual: bool = False) -> None:
        release = self._pending_release
        manual = manual or self._pending_manual
        if self._stopped or release is None:
            return
        if self._dialog is not None or QApplication.activeModalWidget() is not None:
            self._prompt_retry.start(30000)
            return

        self._pending_release = None
        self._pending_manual = False
        self._shown_versions.add(release.version)
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle("SnipDo Translate 有新版本")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"发现新版本 {release.version}\n当前版本：{APP_VERSION}")
        dialog.setInformativeText(
            "打开下载页面可查看发行说明并下载新版。\n"
            "下载后，请从托盘彻底退出，再用新版 EXE 替换旧文件。"
            "历史记录和 API Key 会保留。"
        )
        open_button = dialog.addButton("打开下载页面", QMessageBox.ButtonRole.AcceptRole)
        later_button = dialog.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        skip_button = dialog.addButton("跳过此版本", QMessageBox.ButtonRole.ActionRole)
        dialog.setDefaultButton(later_button)
        dialog.setEscapeButton(later_button)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not manual)
        self._dialog = dialog
        self._displayed_version = release.version

        def finish(_result):
            clicked = dialog.clickedButton()
            if not self._stopped:
                if clicked is open_button:
                    if not QDesktopServices.openUrl(QUrl(release.url)):
                        self._show_status(f"无法打开浏览器，请访问：\n{release.url}")
                elif clicked is skip_button:
                    self._skipped_version = release.version
                    self._save_state()
            if self._dialog is dialog:
                self._dialog = None
                self._displayed_version = None
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.show()
