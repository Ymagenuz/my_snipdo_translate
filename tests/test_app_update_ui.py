from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

import app_update_ui as ui
from app_updates import ReleaseInfo


class FakeChecker(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.is_checking = False
        self.calls = 0
        self.stopped = False

    def check(self):
        self.calls += 1
        self.is_checking = True
        return True

    def complete(self, result):
        self.is_checking = False
        self.completed.emit(result)

    def fail(self):
        self.is_checking = False
        self.failed.emit("暂时无法检查更新，请稍后重试。")

    def stop(self):
        self.stopped = True
        self.is_checking = False


@pytest.fixture
def notifications(qapp, tmp_path):
    window = QWidget()
    window.app_paths = SimpleNamespace(data_dir=tmp_path)
    clock = [1_000_000.0]
    checker = FakeChecker()
    controller = ui.UpdateNotifications(window, checker=checker, clock=lambda: clock[0])
    yield controller, checker, clock
    controller.stop()
    window.close()
    window.deleteLater()


def release(version="12.1.0"):
    return ReleaseInfo(
        version=version,
        url=f"https://github.com/Ymagenuz/my_snipdo_translate/releases/tag/v{version}",
    )


def test_automatic_checks_are_delayed_and_throttled_across_restarts(notifications):
    controller, checker, clock = notifications
    assert checker.calls == 0
    controller.start()
    assert checker.calls == 0
    assert controller._schedule.interval() == 5000
    controller._check_automatically()
    assert checker.calls == 1
    checker.complete(None)
    assert controller._status_dialog is None
    last_check, skipped = ui.load_update_state(controller._path)
    assert last_check == clock[0]
    assert skipped == ""
    second_checker = FakeChecker()
    restarted = ui.UpdateNotifications(
        controller.window, checker=second_checker, clock=lambda: clock[0] + 60
    )
    restarted._check_automatically()
    assert second_checker.calls == 0
    assert restarted._schedule.isActive()
    restarted.stop()
    clock[0] += ui.CHECK_INTERVAL_SECONDS
    controller._check_automatically()
    assert checker.calls == 2


def test_manual_request_joins_active_check_and_reports_result(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    controller.check_manually()
    controller.check_manually()
    assert checker.calls == 1
    checker.complete(None)
    assert controller._status_dialog is not None
    assert ui.APP_VERSION in controller._status_dialog.text()


def test_automatic_failure_is_quiet_but_manual_failure_is_visible(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    checker.fail()
    assert controller._status_dialog is None
    controller.check_manually()
    checker.fail()
    assert "暂时无法" in controller._status_dialog.text()


def test_prompt_opens_browser_only_after_user_clicks(notifications, monkeypatch):
    controller, checker, _clock = notifications
    opened = []
    monkeypatch.setattr(ui.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    controller._check_automatically()
    checker.complete(release())
    dialog = controller._dialog
    assert opened == []
    assert "12.1.0" in dialog.text()
    assert ui.APP_VERSION in dialog.text()
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    next(button for button in dialog.buttons() if button.text() == "打开下载页面").click()
    assert opened == [release().url]
    assert controller._dialog is None


def test_skip_persists_and_manual_checks_can_show_skipped_release(notifications):
    controller, checker, clock = notifications
    controller._check_automatically()
    checker.complete(release())
    dialog = controller._dialog
    next(button for button in dialog.buttons() if button.text() == "跳过此版本").click()
    assert ui.load_update_state(controller._path)[1] == "12.1.0"
    controller._shown_versions.clear()
    clock[0] += ui.CHECK_INTERVAL_SECONDS
    controller._check_automatically()
    checker.complete(release())
    assert controller._dialog is None
    controller.check_manually()
    checker.complete(release())
    assert controller._dialog is not None


def test_later_does_not_repeat_in_same_session_and_new_release_can_prompt(notifications):
    controller, checker, clock = notifications
    controller._check_automatically()
    checker.complete(release())
    controller._dialog.close()
    clock[0] += ui.CHECK_INTERVAL_SECONDS
    controller._check_automatically()
    checker.complete(release())
    assert controller._dialog is None
    clock[0] += ui.CHECK_INTERVAL_SECONDS
    controller._check_automatically()
    checker.complete(release("12.2.0"))
    assert controller._dialog is not None


def test_prompt_waits_for_modal_dialog_and_shutdown_ignores_late_results(notifications, monkeypatch):
    controller, checker, _clock = notifications
    monkeypatch.setattr(ui.QApplication, "activeModalWidget", lambda: object())
    controller._check_automatically()
    checker.complete(release())
    assert controller._dialog is None
    assert controller._prompt_retry.isActive()
    monkeypatch.setattr(ui.QApplication, "activeModalWidget", lambda: None)
    controller._show_pending_release()
    assert controller._dialog is not None
    controller.stop()
    assert checker.stopped
    checker.complete(release("13.0.0"))
    checker.fail()
    assert controller._dialog is None
    assert controller._status_dialog is None
    assert not controller._schedule.isActive()
    assert not controller._prompt_retry.isActive()


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"last_check": true}', b'{"last_check": NaN}', b'{"last_check": 1' + b'0' * 400 + b'}', b"x" * 4097])
def test_invalid_state_falls_back_without_disabling_checks(tmp_path, raw):
    path = tmp_path / "update_state.json"
    path.write_bytes(raw)
    assert ui.load_update_state(path) == (0.0, "")


def test_manual_check_reuses_visible_update_prompt(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    checker.complete(release())
    original_dialog = controller._dialog
    controller.check_manually()
    checker.complete(release())
    assert controller._dialog is original_dialog
    assert controller._pending_release is None
    assert not controller._prompt_retry.isActive()
