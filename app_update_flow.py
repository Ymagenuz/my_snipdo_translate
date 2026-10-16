"""Download progress and the handoff to the detached Windows update helper."""
from __future__ import annotations

from pathlib import Path
import tempfile

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QProgressDialog

from app_update_download import UpdateDownloader
from app_update_install import (
    UpdateInstallError,
    install_unavailable_reason,
    prepare_install,
)


class _PrepareInstall(QThread):
    """Keep executable copies and hashing off the GUI thread."""

    def __init__(self, path, digest, version, parent=None):
        super().__init__(parent)
        self.path, self.digest, self.version = path, digest, version
        self.job = None
        self.error = ""

    def run(self):
        try:
            self.job = prepare_install(self.path, self.digest, self.version)
            if self.isInterruptionRequested():
                self.job.cancel()
                return
        except Exception:
            self.error = "无法准备更新。请确认程序目录可写、磁盘空间充足，然后重试。"
            if self.job is not None:
                self.job.cancel()


class UpdateFlow(QObject):
    failed = pyqtSignal(str)

    def __init__(self, window, *, downloader=None):
        super().__init__(window)
        self.window = window
        self.downloader = downloader if downloader is not None else UpdateDownloader(self)
        self.downloader.progress.connect(self._progress)
        self.downloader.completed.connect(self._downloaded)
        self.downloader.failed.connect(self._fail)
        self._poll = QTimer(self)
        self._poll.setInterval(200)
        self._poll.timeout.connect(self._try_restart)
        self._dialog = None
        self._temporary = None
        self._worker = None
        self._job = None
        self._release = None
        self._active = False
        self._cancelled = False
        self._stopped = False
        self._handed_off = False
        self._helper_started = False

    @property
    def is_active(self):
        return self._active

    def show_progress(self):
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()

    def start(self, release):
        if self._stopped:
            return
        if self._active:
            self.show_progress()
            return
        reason = install_unavailable_reason()
        if reason:
            self.failed.emit(reason)
            return
        self._active = True
        self._cancelled = False
        self._release = release
        dialog = QProgressDialog("正在下载更新校验信息…", "取消", 0, 0, self.window)
        dialog.setWindowTitle(f"更新至 {release.version}")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(420)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self.cancel)
        self._dialog = dialog
        dialog.show()
        try:
            directory = self.window.app_paths.data_dir / "updates"
            directory.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix="download-", dir=directory)
            destination = Path(self._temporary.name) / "SnipDoTranslate.exe"
            if not self.downloader.start(release, destination) and self._active:
                self._fail("无法开始下载更新，请稍后重试。")
        except OSError:
            self._fail("无法保存更新文件。请检查磁盘空间与目录权限后重试。")

    def _progress(self, received, total):
        if not self._active or self._cancelled or self._dialog is None:
            return
        self._dialog.setRange(0, max(total, 1))
        # QProgressDialog resets when it reaches maximum; keep the final step
        # for verification and helper preparation instead.
        self._dialog.setValue(min(received, max(total - 1, 0)))
        self._dialog.setLabelText(
            f"正在下载更新：{received / 1048576:.1f} / {total / 1048576:.1f} MB\n"
            "校验完成后将自动重启。设置、历史记录和 API Key 会保留。"
        )

    def _downloaded(self, path, digest):
        if not self._active or self._cancelled or self._stopped:
            return
        self._dialog.setRange(0, 0)
        self._dialog.setLabelText("下载校验完成，正在准备更新…")
        worker = _PrepareInstall(path, digest, self._release.version, self)
        self._worker = worker
        worker.finished.connect(self._prepared)
        worker.start()

    def _prepared(self):
        worker, self._worker = self._worker, None
        if worker is None:
            return
        self._job = worker.job
        error = worker.error
        worker.deleteLater()
        if self._cancelled or self._stopped:
            self._reset()
        elif error:
            self._fail(error)
        else:
            self._dialog.setLabelText("正在准备重启…")
            self._poll.start()

    def _busy(self):
        if getattr(self.window, "selection_capture_busy", False):
            return True
        if QApplication.activeModalWidget() is not None:
            return True
        workers = [getattr(self.window, name, None) for name in
                   ("trans_thread", "ocr_thread", "align_thread")]
        workers.extend(getattr(self.window, "translation_threads", ()))
        return any(worker is not None and worker.isRunning() for worker in workers)

    def _try_restart(self):
        if not self._active or self._cancelled or self._stopped or self._job is None:
            return
        try:
            if self._busy():
                self._dialog.setLabelText("更新已就绪，等待翻译、OCR 或设置完成后重启…")
                return
            if not self._helper_started:
                self._job.start()
                self._helper_started = True
                return
            if not self._job.is_ready():
                return
            self._job.commit()
            self._handed_off = True
            self._poll.stop()
            self._dialog.setCancelButton(None)
            self._dialog.setLabelText("正在安装更新并重启…")
            self.window.quit_app()
        except (UpdateInstallError, OSError, ValueError, RuntimeError):
            self._handed_off = False
            self._fail("无法启动更新安装，当前版本已保留。请稍后重试。")

    def cancel(self):
        if self._handed_off:
            return
        self._cancelled = True
        self.downloader.stop()
        self._poll.stop()
        if self._worker is not None:
            self._worker.requestInterruption()
            # Preparation owns the files until its thread finishes. The UI may
            # hide immediately, but cancellation must not delete files in use.
            if self._dialog is not None:
                self._dialog.hide()
        else:
            self._reset()

    def stop(self):
        self._stopped = True
        if not self._handed_off:
            self.cancel()
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait()
            self._prepared()
        self._reset()

    def _fail(self, message):
        if not self._active or self._stopped or self._cancelled:
            return
        self.downloader.stop()
        self._reset()
        self.failed.emit(message)

    def _reset(self):
        self._poll.stop()
        if self._job is not None and not self._handed_off:
            self._job.cancel()
        self._job = None
        if self._temporary is not None:
            try:
                self._temporary.cleanup()
            except OSError:
                pass
            self._temporary = None
        if self._dialog is not None:
            dialog, self._dialog = self._dialog, None
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()
        self._release = None
        self._active = False
        self._helper_started = False
