import copy
import json

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

import app_updates
from app_updates import ReleaseInfo, UpdateChecker, parse_release


def release_payload(tag="v13.0.0"):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "html_url": "https://untrusted.example/release",
        "assets": [{
            "name": "SnipDoTranslate.exe",
            "state": "uploaded",
            "size": 1024,
            "browser_download_url": (
                f"{app_updates.REPOSITORY_URL}/releases/download/{tag}/SnipDoTranslate.exe"
            ),
        }],
    }


def test_numeric_version_comparison_and_trusted_release_url():
    result = parse_release(release_payload("v1.10.0"), current_version="1.9.9")
    assert result == ReleaseInfo(
        version="1.10.0",
        url=f"{app_updates.REPOSITORY_URL}/releases/tag/v1.10.0",
    )
    with pytest.raises(AttributeError):
        result.version = "99.0.0"


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2.3"])
def test_versions_with_and_without_prefix(tag):
    assert parse_release(release_payload(tag), current_version="v1.2.2").version == "1.2.3"


@pytest.mark.parametrize("tag", ["1.9.9", "v1.10.0", "0.99.999"])
def test_equal_or_older_release_has_no_update(tag):
    assert parse_release(release_payload(tag), current_version="1.10.0") is None


@pytest.mark.parametrize("tag", [
    "v1.2", "1.2.3.4", "1.02.3", "1.2.3-rc.1", "1.2.3+metadata", "1.2.3\n",
    "../../redirect", "https://untrusted.example", "v１.2.3", None, 1300,
])
def test_invalid_tags_are_rejected(tag):
    with pytest.raises(ValueError):
        parse_release(release_payload(tag), current_version="1.0.0")


@pytest.mark.parametrize("payload", [None, [], {}, {"draft": "false", "prerelease": False}])
def test_invalid_metadata_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_release(payload, current_version="12.0.0")


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_draft_and_prerelease_are_ignored(field):
    payload = release_payload()
    payload[field] = True
    assert parse_release(payload, current_version="12.0.0") is None


@pytest.mark.parametrize("asset_change", [
    {"name": "other.exe"}, {"name": "SnipDoTranslate.exe.zip"},
    {"state": "new"}, {"size": 0}, {"size": -1}, {"size": "1024"}, {"size": True},
    {"browser_download_url": "https://untrusted.example/SnipDoTranslate.exe"},
    {"browser_download_url": (
        "http://github.com/Ymagenuz/my_snipdo_translate/releases/download/v13.0.0/SnipDoTranslate.exe"
    )},
    {"browser_download_url": (
        "https://github.com/other/my_snipdo_translate/releases/download/v13.0.0/SnipDoTranslate.exe"
    )},
    {"browser_download_url": (
        "https://github.com/Ymagenuz/my_snipdo_translate/releases/download/v12.0.0/SnipDoTranslate.exe"
    )},
])
def test_only_finished_matching_executable_uploads_are_usable(asset_change):
    payload = release_payload()
    payload["assets"][0].update(asset_change)
    assert parse_release(payload, current_version="12.0.0") is None


def test_source_only_release_has_no_update():
    payload = release_payload()
    payload["assets"] = []
    assert parse_release(payload, current_version="12.0.0") is None


def test_unrelated_assets_do_not_hide_a_valid_executable():
    payload = release_payload()
    payload["assets"] = [None, "invalid", {"name": "source.zip"}, *payload["assets"]]
    assert parse_release(payload, current_version="12.0.0").version == "13.0.0"


class FakeReply(QObject):
    readyRead = pyqtSignal()
    metaDataChanged = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, body=b"", status=200, error=QNetworkReply.NetworkError.NoError):
        super().__init__()
        self.pending = bytearray(body)
        self.status = status
        self.network_error = error
        self.content_length = None
        self.read_buffer_size = None
        self.aborted = False
        self.delete_scheduled = False

    def setReadBufferSize(self, value):
        self.read_buffer_size = value

    def read(self, amount):
        chunk = self.pending[:amount]
        del self.pending[:amount]
        return bytes(chunk)

    def header(self, header):
        assert header == QNetworkRequest.KnownHeaders.ContentLengthHeader
        return self.content_length

    def attribute(self, attribute):
        assert attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute
        return self.status

    def error(self):
        return self.network_error

    def abort(self):
        self.aborted = True
        self.finished.emit()

    def deleteLater(self):
        self.delete_scheduled = True


@pytest.fixture
def checker(monkeypatch, qapp):
    class FakeManager(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.requests = []
            self.replies = []

        def get(self, request):
            self.requests.append(request)
            reply = FakeReply()
            self.replies.append(reply)
            return reply

    monkeypatch.setattr(app_updates, "QNetworkAccessManager", FakeManager)
    # Fix both sides of the comparison so future application version bumps do
    # not turn these transport/lifecycle checks into older-release checks.
    monkeypatch.setattr(
        app_updates,
        "parse_release",
        lambda payload: parse_release(payload, current_version="12.0.0"),
    )
    instance = UpdateChecker()
    instance.results = []
    instance.failures = []
    instance.completed.connect(instance.results.append)
    instance.failed.connect(instance.failures.append)
    yield instance
    instance.stop()


def complete(reply, payload=None):
    reply.pending.extend(json.dumps(payload or release_payload()).encode("utf-8"))
    reply.finished.emit()


def test_initialization_has_no_network_activity_and_request_is_credential_free(checker):
    assert not checker.is_checking
    assert checker._manager.requests == []
    assert checker.check()
    request = checker._manager.requests[0]
    assert request.url().toString() == app_updates.LATEST_RELEASE_API
    assert bytes(request.rawHeader(b"Accept")) == b"application/vnd.github+json"
    assert bytes(request.rawHeader(b"User-Agent")) == f"SnipDoTranslate/{app_updates.APP_VERSION}".encode()
    assert not request.hasRawHeader(b"Authorization")
    assert not request.hasRawHeader(b"Cookie")
    assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == (
        QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    )
    for attribute in (
        QNetworkRequest.Attribute.AuthenticationReuseAttribute,
        QNetworkRequest.Attribute.CookieLoadControlAttribute,
        QNetworkRequest.Attribute.CookieSaveControlAttribute,
    ):
        assert request.attribute(attribute) == QNetworkRequest.LoadControl.Manual
    assert checker._reply.read_buffer_size == app_updates.MAX_RESPONSE_BYTES + 1
    assert checker._timer.interval() == app_updates.CHECK_TIMEOUT_MS


def test_check_is_async_and_prevents_concurrent_requests(checker):
    assert checker.check()
    assert checker.results == []
    assert checker.is_checking
    assert checker.check() is False
    assert len(checker._manager.requests) == 1
    reply = checker._reply
    complete(reply)
    assert checker.results == [ReleaseInfo(
        "13.0.0", f"{app_updates.REPOSITORY_URL}/releases/tag/v13.0.0"
    )]
    assert not checker.is_checking
    assert reply.delete_scheduled
    assert checker.failures == []


def test_streamed_response_is_assembled(checker):
    checker.check()
    reply = checker._reply
    body = json.dumps(release_payload()).encode()
    for chunk in (body[:10], body[10:33], body[33:]):
        reply.pending.extend(chunk)
        reply.readyRead.emit()
    reply.finished.emit()
    assert checker.results[0].version == "13.0.0"


def test_404_without_any_releases_is_not_an_error(checker):
    checker.check()
    reply = checker._reply
    reply.status = 404
    reply.network_error = QNetworkReply.NetworkError.ContentNotFoundError
    reply.finished.emit()
    assert checker.results == [None]
    assert checker.failures == []


@pytest.mark.parametrize("status,error", [
    (200, QNetworkReply.NetworkError.RemoteHostClosedError),
    (None, QNetworkReply.NetworkError.HostNotFoundError),
    (None, QNetworkReply.NetworkError.SslHandshakeFailedError),
    (403, QNetworkReply.NetworkError.ContentAccessDenied),
    (429, QNetworkReply.NetworkError.UnknownContentError),
    (500, QNetworkReply.NetworkError.InternalServerError),
    (302, QNetworkReply.NetworkError.NoError),
])
def test_network_and_http_failures_are_sanitized(checker, status, error):
    checker.check()
    reply = checker._reply
    reply.status = status
    reply.network_error = error
    reply.pending.extend(b"private server error details")
    reply.finished.emit()
    assert checker.failures == [app_updates.CHECK_FAILED_MESSAGE]
    assert checker.results == []
    assert not checker.is_checking
    assert reply.delete_scheduled


@pytest.mark.parametrize("body", [b"invalid JSON", b"\xff", b"[]", b'{}'])
def test_invalid_json_or_metadata_fails_cleanly(checker, body):
    checker.check()
    reply = checker._reply
    reply.pending.extend(body)
    reply.finished.emit()
    assert checker.failures == [app_updates.CHECK_FAILED_MESSAGE]
    assert checker.results == []


@pytest.mark.parametrize("mode", ["header", "stream", "finished"])
def test_response_size_is_bounded(checker, mode):
    checker.check()
    reply = checker._reply
    if mode == "header":
        reply.content_length = app_updates.MAX_RESPONSE_BYTES + 1
        reply.metaDataChanged.emit()
    else:
        reply.pending.extend(b"x" * (app_updates.MAX_RESPONSE_BYTES + 1))
        if mode == "stream":
            reply.readyRead.emit()
        else:
            reply.finished.emit()
    assert checker.failures == [app_updates.CHECK_FAILED_MESSAGE]
    assert checker.results == []
    assert reply.aborted
    assert not checker.is_checking


def test_timeout_aborts_once_and_late_events_do_not_affect_next_request(checker):
    checker.check()
    old_reply = checker._reply
    old_timer = checker._timer
    old_timer.timeout.emit()
    assert old_reply.aborted
    assert checker.failures == [app_updates.CHECK_FAILED_MESSAGE]
    assert checker.check()
    current_reply = checker._reply
    complete(old_reply)
    old_reply.readyRead.emit()
    old_reply.metaDataChanged.emit()
    old_timer.timeout.emit()
    assert checker._reply is current_reply
    assert checker.results == []
    assert checker.failures == [app_updates.CHECK_FAILED_MESSAGE]
    complete(current_reply)
    assert len(checker.results) == 1


def test_stop_is_quiet_reentrant_safe_and_stops_timer(checker):
    checker.stop()
    checker.check()
    reply = checker._reply
    timer = checker._timer
    checker.stop()
    checker.stop()
    assert reply.aborted
    assert reply.delete_scheduled
    assert not timer.isActive()
    assert not checker.is_checking
    complete(reply)
    assert checker.results == []
    assert checker.failures == []


def test_completed_slot_can_start_a_new_request(checker):
    checker.completed.connect(lambda _: checker.check())
    checker.check()
    complete(checker._reply)
    assert checker.is_checking
    assert len(checker._manager.requests) == 2


def test_missing_asset_completes_without_update(checker):
    checker.check()
    payload = copy.deepcopy(release_payload())
    payload["assets"] = []
    complete(checker._reply, payload)
    assert checker.results == [None]
    assert checker.failures == []
