from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget

import app_update_flow as flow
from app_updates import ReleaseInfo


class Downloader(QObject):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.stopped = 0

    def start(self, release, path):
        assert not path.exists()
        self.calls.append((release, path))
        path.write_bytes(b"verified executable fixture")
        return True

    def stop(self):
        self.stopped += 1


class Job:
    def __init__(self):
        self.started = 0
        self.cancelled = 0
        self.committed = 0
        self.ready = False

    def start(self):
        self.started += 1

    def cancel(self):
        self.cancelled += 1

    def is_ready(self):
        return self.ready

    def commit(self):
        self.committed += 1


@pytest.fixture
def update(qapp, tmp_path, monkeypatch):
    window = QWidget()
    window.app_paths = SimpleNamespace(data_dir=tmp_path)
    window.quit_calls = 0

    def quit_app():
        window.quit_calls += 1

    window.quit_app = quit_app
    downloader = Downloader()
    controller = flow.UpdateFlow(window, downloader=downloader)
    controller.errors = []
    controller.failed.connect(controller.errors.append)
    controller.test_job = Job()
    monkeypatch.setattr(flow, "install_unavailable_reason", lambda: "")
    monkeypatch.setattr(flow, "prepare_install", lambda *_args: controller.test_job)
    yield controller
    controller.stop()
    window.close()
    window.deleteLater()


def release():
    return ReleaseInfo("99.0.0", "https://github.com/Ymagenuz/my_snipdo_translate/releases/tag/v99.0.0")


def prepare(update):
    update.start(release())
    update.downloader.completed.emit(update.downloader.calls[-1][1], "a" * 64)
    deadline = time.monotonic() + 5
    while update._worker is not None and time.monotonic() < deadline:
        QTest.qWait(10)
    assert update._worker is None


def test_source_install_explains_without_downloading(update, monkeypatch):
    monkeypatch.setattr(flow, "install_unavailable_reason", lambda: "源码运行不能替换 Python。")
    update.start(release())
    assert not update.is_active
    assert not update.downloader.calls
    assert "Python" in update.errors[0]


def test_progress_and_duplicate_start_keep_one_download(update):
    update.start(release())
    update.start(release())
    assert len(update.downloader.calls) == 1
    update.downloader.progress.emit(512, 1024)
    assert update._dialog.value() == 512
    assert "正在下载" in update._dialog.labelText()
    assert update.window.quit_calls == 0


def test_download_cancellation_discards_files_and_ignores_late_completion(update):
    update.start(release())
    path = update.downloader.calls[-1][1]
    update.cancel()
    update.downloader.completed.emit(path, "a" * 64)
    update.downloader.failed.emit("late failure")
    assert not update.is_active
    assert not path.exists()
    assert update._worker is None
    assert not update.errors
    assert update.window.quit_calls == 0
    update.start(release())
    assert len(update.downloader.calls) == 2


def test_close_progress_cancels_download(update):
    update.start(release())
    update._dialog.close()
    assert not update.is_active
    assert update.downloader.stopped == 1


def test_download_failure_keeps_app_running_and_allows_retry(update):
    update.start(release())
    path = update.downloader.calls[-1][1]
    update.downloader.failed.emit("校验失败")
    assert update.errors == ["校验失败"]
    assert not path.exists()
    assert update.window.quit_calls == 0
    update.start(release())
    assert update.is_active


def test_helper_handoff_waits_until_ready_and_survives_shutdown_cleanup(update):
    prepare(update)
    job = update.test_job
    update._try_restart()
    assert job.started == 1
    assert update.window.quit_calls == 0
    update._try_restart()
    assert not job.committed
    job.ready = True
    update._try_restart()
    assert job.committed == 1
    assert update.window.quit_calls == 1
    update.stop()
    assert not job.cancelled
    assert not update._poll.isActive()


@pytest.mark.parametrize("busy", ["trans_thread", "ocr_thread", "align_thread", "selection", "modal", "old_translation"])
def test_restart_waits_for_work_and_settings_before_starting_helper(update, monkeypatch, busy):
    prepare(update)
    running = SimpleNamespace(isRunning=lambda: True)
    if busy == "selection":
        update.window.selection_capture_busy = True
    elif busy == "modal":
        monkeypatch.setattr(flow.QApplication, "activeModalWidget", lambda: object())
    elif busy == "old_translation":
        update.window.translation_threads = [running]
    else:
        setattr(update.window, busy, running)
    update._try_restart()
    assert "等待" in update._dialog.labelText()
    assert update.test_job.started == 0
    assert update.window.quit_calls == 0
    update.cancel()
    assert update.test_job.cancelled == 1


def test_prepare_failure_is_reported_without_quitting(update, monkeypatch):
    def fail(*_args):
        raise OSError("fixture permission error")
    monkeypatch.setattr(flow, "prepare_install", fail)
    prepare(update)
    assert not update.is_active
    assert "无法准备" in update.errors[0]
    assert update.window.quit_calls == 0


@pytest.mark.parametrize("method", ["start", "is_ready", "commit"])
def test_helper_errors_preserve_running_app(update, monkeypatch, method):
    prepare(update)
    update.test_job.ready = True

    def fail():
        raise flow.UpdateInstallError("fixture failure")
    monkeypatch.setattr(update.test_job, method, fail)
    update._try_restart()
    if update.is_active:
        update._try_restart()
    assert not update.is_active
    assert update.errors
    assert update.window.quit_calls == 0
    assert update.test_job.cancelled == 1


def test_shutdown_joins_preparation_then_cancels_job(update, monkeypatch):
    def slow_prepare(*_args):
        time.sleep(.05)
        return update.test_job
    monkeypatch.setattr(flow, "prepare_install", slow_prepare)
    update.start(release())
    update.downloader.completed.emit(update.downloader.calls[-1][1], "a" * 64)
    update.stop()
    assert update._worker is None
    assert not update.is_active
    assert update.test_job.cancelled >= 1
    assert update.test_job.started == 0
    assert update.window.quit_calls == 0


def test_installer_dispatch_precedes_application_imports(project_root):
    script = """
import runpy, sys, types
stub = types.ModuleType('app_update_install')
def apply(path):
    assert path == 'fixture-manifest'
    assert 'PyQt6.QtWidgets' not in sys.modules
    assert 'openai' not in sys.modules
    return 33
stub.run_apply_update = apply
sys.modules['app_update_install'] = stub
sys.argv = ['SnipDoTranslate.exe', '--apply-update', 'fixture-manifest']
runpy.run_path('snipdo_translate.pyw', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=project_root, capture_output=True, timeout=10)
    assert result.returncode == 33, result.stderr
