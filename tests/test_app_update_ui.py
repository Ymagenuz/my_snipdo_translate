import json
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

import app_update_ui as ui
from app_updates import ReleaseInfo, is_newer_version
from app_version import APP_VERSION


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
        self.failed.emit("Can't check for updates right now.")

    def stop(self):
        self.stopped = True
        self.is_checking = False


class FakeUpdater(QObject):
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.is_active = False
        self.releases = []
        self.shown = 0
        self.stopped = False

    def start(self, value):
        self.releases.append(value)
        self.is_active = True

    def show_progress(self):
        self.shown += 1

    def stop(self):
        self.stopped = True
        self.is_active = False


@pytest.fixture
def notifications(qapp, tmp_path):
    window = QWidget()
    window.app_paths = SimpleNamespace(data_dir=tmp_path)
    clock = [1_000_000.0]
    checker = FakeChecker()
    controller = ui.UpdateNotifications(
        window, checker=checker, updater=FakeUpdater(), clock=lambda: clock[0]
    )
    changes = []
    controller.changed.connect(lambda: changes.append(controller.available_release))
    controller.changes = changes
    yield controller, checker, clock
    controller.stop()
    window.close()
    window.deleteLater()


def release(version="12.9.0"):
    return ReleaseInfo(
        version=version,
        url=f"https://github.com/Ymagenuz/my_snipdo_translate/releases/tag/v{version}",
    )


def visible_message_boxes():
    return [
        widget for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QMessageBox) and widget.isVisible()
    ]


def test_automatic_checks_are_delayed_and_throttled_across_restarts(notifications):
    controller, checker, clock = notifications
    assert checker.calls == 0
    controller.start()
    assert checker.calls == 0
    assert controller._schedule.interval() == 5000
    controller._check_automatically()
    assert checker.calls == 1
    assert controller._schedule.interval() == 6 * 60 * 60 * 1000
    checker.complete(None)
    assert controller._status_dialog is None
    assert ui.load_update_state(controller._path) == (clock[0], "")
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


def test_found_release_is_kept_without_any_pop_up(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    assert controller.is_checking
    checker.complete(release())

    assert controller.available_release == release()
    assert controller.changes[-1] == release()
    assert controller.last_result == ""
    assert controller.updater.releases == []
    assert controller._status_dialog is None
    assert visible_message_boxes() == []


def test_manual_check_reports_on_the_about_page_instead_of_a_dialog(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    controller.check_manually()
    controller.check_manually()
    assert checker.calls == 1
    checker.complete(None)

    assert controller.available_release is None
    assert controller.last_result == "current"
    assert controller._status_dialog is None
    assert visible_message_boxes() == []


def test_automatic_failure_is_quiet_but_manual_failure_is_reported(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    checker.fail()
    assert controller.last_result == ""
    controller.check_manually()
    checker.fail()
    assert controller.last_result == "failed"
    assert controller._status_dialog is None


def test_failed_check_keeps_a_release_found_earlier(notifications):
    controller, checker, clock = notifications
    controller._check_automatically()
    checker.complete(release())
    clock[0] += ui.CHECK_INTERVAL_SECONDS
    controller.check_manually()
    checker.fail()

    assert controller.available_release == release()


def test_newer_check_result_replaces_the_shown_release(notifications):
    controller, checker, clock = notifications
    controller._check_automatically()
    checker.complete(release())
    clock[0] += ui.CHECK_INTERVAL_SECONDS
    controller._check_automatically()
    checker.complete(release("13.0.0"))
    assert controller.available_release == release("13.0.0")
    controller.check_manually()
    checker.complete(None)
    assert controller.available_release is None


def test_release_found_before_a_restart_is_looked_up_again_at_once(notifications):
    controller, checker, clock = notifications
    controller._check_automatically()
    checker.complete(release())
    assert ui.load_update_state(controller._path) == (clock[0], "12.9.0")

    second_checker = FakeChecker()
    restarted = ui.UpdateNotifications(
        controller.window, checker=second_checker, clock=lambda: clock[0] + 60
    )
    restarted._check_automatically()
    assert second_checker.calls == 1
    second_checker.complete(None)
    restarted.stop()
    assert ui.load_update_state(controller._path)[1] == ""


def test_found_version_no_newer_than_this_one_keeps_the_throttle(tmp_path, qapp):
    window = QWidget()
    window.app_paths = SimpleNamespace(data_dir=tmp_path)
    (tmp_path / ui.STATE_FILE_NAME).write_text(
        json.dumps({"last_check": 1_000_000.0, "available_version": APP_VERSION}),
        encoding="utf-8",
    )
    checker = FakeChecker()
    controller = ui.UpdateNotifications(
        window, checker=checker, updater=FakeUpdater(), clock=lambda: 1_000_060.0
    )
    controller._check_automatically()
    assert checker.calls == 0
    controller.stop()
    window.deleteLater()


def test_install_starts_only_when_asked(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    checker.complete(release())
    assert controller.updater.releases == []

    controller.install(release())

    assert controller.updater.releases == [release()]
    assert controller.is_installing


def test_manual_check_during_download_reuses_progress_without_another_check(notifications):
    controller, checker, _clock = notifications
    controller.updater.is_active = True
    controller.check_manually()
    assert controller.updater.shown == 1
    assert checker.calls == 0
    controller.stop()
    assert controller.updater.stopped


def test_install_error_is_shown_and_can_be_retried(notifications):
    controller, checker, _clock = notifications
    controller.updater.failed.emit("Install failed; the current version was kept.")
    assert "current version was kept" in controller._status_dialog.text()
    controller.check_manually()
    checker.complete(release())
    assert controller.available_release == release()


def test_shutdown_ignores_late_results(notifications):
    controller, checker, _clock = notifications
    controller._check_automatically()
    controller.stop()
    assert checker.stopped
    checker.complete(release("13.0.0"))
    checker.fail()
    assert controller.available_release is None
    assert controller._status_dialog is None
    assert not controller._schedule.isActive()
    controller.install(release())
    assert controller.updater.releases == []


@pytest.mark.parametrize("raw", [
    b"not json", b"[]", b'{"last_check": true}', b'{"last_check": NaN}',
    b'{"last_check": 1' + b'0' * 400 + b'}', b'{"available_version": 12}', b"x" * 4097,
])
def test_invalid_state_falls_back_without_disabling_checks(tmp_path, raw):
    path = tmp_path / "update_state.json"
    path.write_bytes(raw)
    assert ui.load_update_state(path) == (0.0, "")


def test_state_from_older_versions_keeps_its_last_check(tmp_path):
    path = tmp_path / "update_state.json"
    path.write_text(
        json.dumps({"last_check": 5.0, "skipped_version": "12.1.0"}), encoding="utf-8"
    )
    assert ui.load_update_state(path) == (5.0, "")


def test_release_page_reports_whether_a_browser_opened(notifications, monkeypatch):
    controller, _checker, _clock = notifications
    opened = []
    monkeypatch.setattr(
        ui,
        "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url.toString()) or True),
    )
    assert controller.open_release_page(release())
    assert opened == [release().url]
    assert controller.updater.releases == []

    monkeypatch.setattr(ui, "QDesktopServices", SimpleNamespace(openUrl=lambda _url: False))
    assert not controller.open_release_page(release())


@pytest.mark.parametrize(("version", "newer"), [
    ("99.0.0", True), ("v99.0.0", True), (APP_VERSION, False),
    ("0.0.1", False), ("", False), ("12.x", False), (None, False),
])
def test_is_newer_version(version, newer):
    assert is_newer_version(version) is newer
