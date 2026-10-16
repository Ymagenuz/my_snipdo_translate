from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

import app_update_download as download
from app_updates import ReleaseInfo, REPOSITORY_URL


EXECUTABLE = b"MZ" + bytes(range(256)) * 1024
DIGEST = hashlib.sha256(EXECUTABLE).hexdigest()
CHECKSUM = f"{DIGEST}  SnipDoTranslate.exe\r\n".encode("ascii")


def release_info(*, executable=EXECUTABLE, checksum=CHECKSUM):
    prefix = f"{REPOSITORY_URL}/releases/download/v99.0.0"
    return ReleaseInfo(
        "99.0.0", f"{REPOSITORY_URL}/releases/tag/v99.0.0",
        f"{prefix}/SnipDoTranslate.exe", len(executable),
        f"{prefix}/SnipDoTranslate.exe.sha256", len(checksum),
    )


class FakeReply(QObject):
    readyRead = pyqtSignal()
    metaDataChanged = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, url):
        super().__init__()
        self._url = url
        self.pending = bytearray()
        self.status = 200
        self.length = None
        self.encoding = b""
        self.redirect = None
        self.network_error = QNetworkReply.NetworkError.NoError
        self.buffer_size = None
        self.read_sizes = []
        self.aborted = False
        self.delete_scheduled = False

    def url(self):
        return self._url

    def setReadBufferSize(self, value):
        self.buffer_size = value

    def read(self, amount):
        self.read_sizes.append(amount)
        if not self.pending:
            return None
        chunk = bytes(self.pending[:amount])
        del self.pending[:amount]
        return chunk

    def header(self, _header):
        return self.length

    def rawHeader(self, name):
        assert name == b"Content-Encoding"
        return self.encoding

    def attribute(self, attribute):
        if attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self.status
        if attribute == QNetworkRequest.Attribute.RedirectionTargetAttribute:
            return self.redirect
        raise AssertionError(attribute)

    def error(self):
        return self.network_error

    def abort(self):
        self.aborted = True
        self.finished.emit()

    def deleteLater(self):
        self.delete_scheduled = True


@pytest.fixture
def downloader(monkeypatch, qapp, tmp_path):
    class FakeManager(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.requests = []
            self.replies = []

        def get(self, request):
            self.requests.append(request)
            reply = FakeReply(request.url())
            self.replies.append(reply)
            return reply

    monkeypatch.setattr(download, "QNetworkAccessManager", FakeManager)
    instance = download.UpdateDownloader()
    instance.destination = tmp_path / "download.exe"
    instance.results = []
    instance.failures = []
    instance.progress_values = []
    instance.completed.connect(lambda path, digest: instance.results.append((path, digest)))
    instance.failed.connect(instance.failures.append)
    instance.progress.connect(lambda value, total: instance.progress_values.append((value, total)))
    yield instance
    instance.stop()


def finish_body(reply, body):
    reply.pending.extend(body)
    reply.finished.emit()


def begin_executable(downloader, *, release=None, checksum=CHECKSUM):
    assert downloader.start(release or release_info(checksum=checksum), downloader.destination)
    finish_body(downloader._reply, checksum)
    assert downloader._phase == "executable"
    return downloader._reply


def assert_failed(downloader):
    assert downloader.failures == [download.DOWNLOAD_FAILED_MESSAGE]
    assert downloader.results == []
    assert not downloader.is_downloading
    assert not downloader.destination.exists()


def test_start_is_async_exclusive_and_requests_are_credential_free(downloader):
    assert not downloader.is_downloading
    assert downloader._manager.requests == []
    assert downloader.start(release_info(), downloader.destination)
    assert downloader.is_downloading
    assert not downloader.start(release_info(), downloader.destination)
    assert len(downloader._manager.requests) == 1
    request = downloader._manager.requests[0]
    assert request.url().toString() == release_info().checksum_url
    assert not request.hasRawHeader(b"Authorization")
    assert not request.hasRawHeader(b"Cookie")
    assert request.rawHeader(b"Accept-Encoding") == b"identity"
    assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    for attribute in (
        QNetworkRequest.Attribute.AuthenticationReuseAttribute,
        QNetworkRequest.Attribute.CookieLoadControlAttribute,
        QNetworkRequest.Attribute.CookieSaveControlAttribute,
    ):
        assert request.attribute(attribute) == QNetworkRequest.LoadControl.Manual
    assert downloader._reply.buffer_size == download.CHUNK_BYTES
    assert downloader._inactivity_timer.interval() == download.INACTIVITY_TIMEOUT_MS
    assert downloader._overall_timer.interval() == download.OVERALL_TIMEOUT_MS


def test_streams_to_disk_verifies_and_handles_none_at_eof(downloader):
    assert downloader.start(release_info(), downloader.destination)
    checksum_reply = downloader._reply
    checksum_reply.length = len(CHECKSUM)
    checksum_reply.pending.extend(CHECKSUM)
    checksum_reply.readyRead.emit()
    assert checksum_reply.read(1) is None
    checksum_reply.finished.emit()
    reply = downloader._reply
    assert reply is not checksum_reply
    assert downloader._manager.requests[-1].url().toString() == release_info().download_url
    reply.length = len(EXECUTABLE)
    for offset in range(0, len(EXECUTABLE), 21_111):
        reply.pending.extend(EXECUTABLE[offset:offset + 21_111])
        reply.readyRead.emit()
    assert reply.read(1) is None
    reply.finished.emit()

    assert downloader.results == [(downloader.destination.resolve(), DIGEST)]
    assert downloader.failures == []
    assert downloader.destination.read_bytes() == EXECUTABLE
    assert not downloader.is_downloading
    assert downloader.progress_values[-1] == (len(EXECUTABLE), len(EXECUTABLE))
    assert max(reply.read_sizes) <= download.CHUNK_BYTES
    downloader.stop()
    assert downloader.destination.exists(), "verified file ownership belongs to the caller"


@pytest.mark.parametrize("change", [
    {"size": 0}, {"size": True}, {"size": download.MAX_EXECUTABLE_BYTES + 1},
    {"checksum_size": 0}, {"checksum_size": download.MAX_CHECKSUM_BYTES + 1},
    {"version": "98.0.0"},
    {"url": f"{REPOSITORY_URL}/releases/tag/../v99.0.0"},
    {"download_url": "https://github.com/other/project/releases/download/v99.0.0/SnipDoTranslate.exe"},
    {"checksum_url": f"{REPOSITORY_URL}/releases/download/v98.0.0/SnipDoTranslate.exe.sha256"},
])
def test_invalid_release_metadata_never_starts_network_or_creates_file(downloader, change):
    assert not downloader.start(replace(release_info(), **change), downloader.destination)
    assert downloader._manager.requests == []
    assert_failed(downloader)


def test_existing_destination_is_never_truncated_or_deleted(downloader):
    downloader.destination.write_bytes(b"existing file")
    assert not downloader.start(release_info(), downloader.destination)
    downloader.stop()
    assert downloader.destination.read_bytes() == b"existing file"
    assert downloader._manager.requests == []
    assert downloader.failures == [download.DOWNLOAD_FAILED_MESSAGE]


@pytest.mark.parametrize("body", [
    b"invalid", f"{DIGEST}\n".encode(),
    f"{DIGEST}  Other.exe\r\n".encode(),
    f"{DIGEST} *SnipDoTranslate.exe\n".encode(),
    f"{DIGEST}  ../SnipDoTranslate.exe\n".encode(),
    CHECKSUM + b"extra text", b"\xef\xbb\xbf" + CHECKSUM,
])
def test_invalid_checksum_prevents_executable_request(downloader, body):
    assert downloader.start(release_info(checksum=body), downloader.destination)
    finish_body(downloader._reply, body)
    assert len(downloader._manager.requests) == 1
    assert_failed(downloader)


def test_uppercase_checksum_is_normalized(downloader):
    checksum = f"{DIGEST.upper()}  SnipDoTranslate.exe\n".encode("ascii")
    reply = begin_executable(downloader, checksum=checksum)
    finish_body(reply, EXECUTABLE)
    assert downloader.results == [(downloader.destination.resolve(), DIGEST)]


@pytest.mark.parametrize("phase", ["checksum", "executable"])
@pytest.mark.parametrize("mode", ["header", "oversized", "truncated"])
def test_both_assets_require_exact_bounded_sizes(downloader, phase, mode):
    if phase == "checksum":
        assert downloader.start(release_info(), downloader.destination)
        reply, body = downloader._reply, CHECKSUM
    else:
        reply, body = begin_executable(downloader), EXECUTABLE
    if mode == "header":
        reply.length = len(body) + 1
        reply.metaDataChanged.emit()
    elif mode == "oversized":
        reply.pending.extend(body + b"x")
        reply.readyRead.emit()
    else:
        finish_body(reply, body[:-1])
    assert_failed(downloader)


def test_wrong_executable_hash_removes_partial_file(downloader):
    reply = begin_executable(downloader)
    finish_body(reply, b"x" * len(EXECUTABLE))
    assert_failed(downloader)


@pytest.mark.parametrize("host", ["release-assets.githubusercontent.com", "objects.githubusercontent.com"])
def test_manual_redirect_to_documented_asset_host_is_followed(downloader, host):
    assert downloader.start(release_info(), downloader.destination)
    old_reply = downloader._reply
    old_reply.status = 302
    old_reply.redirect = QUrl(f"https://{host}/github-production-release-asset/123?signature=public-download")
    old_reply.metaDataChanged.emit()

    assert old_reply.aborted
    assert old_reply.delete_scheduled
    assert downloader._reply is not old_reply
    assert downloader._manager.requests[-1].url() == old_reply.redirect
    finish_body(downloader._reply, CHECKSUM)
    finish_body(downloader._reply, EXECUTABLE)
    assert downloader.results == [(downloader.destination.resolve(), DIGEST)]


@pytest.mark.parametrize("url", [
    "http://release-assets.githubusercontent.com/file",
    "https://release-assets.githubusercontent.com.evil.example/file",
    "https://evil.example/file", "file:///tmp/update.exe",
    "https://user:password@release-assets.githubusercontent.com/file",
    "https://release-assets.githubusercontent.com:443/file",
    "https://release-assets.githubusercontent.com/file#fragment",
    "https://github.com/other/repo/releases/download/v99.0.0/file",
])
def test_unsafe_redirects_fail_without_following(downloader, url):
    assert downloader.start(release_info(), downloader.destination)
    reply = downloader._reply
    reply.status = 302
    reply.redirect = QUrl(url)
    reply.finished.emit()
    assert len(downloader._manager.requests) == 1
    assert_failed(downloader)


def test_redirect_loop_is_bounded(downloader):
    assert downloader.start(release_info(), downloader.destination)
    for _ in range(download.MAX_REDIRECTS + 1):
        reply = downloader._reply
        reply.status = 302
        reply.redirect = QUrl("https://release-assets.githubusercontent.com/loop")
        reply.finished.emit()
    assert len(downloader._manager.requests) == download.MAX_REDIRECTS + 1
    assert_failed(downloader)


@pytest.mark.parametrize("status,error", [
    (404, QNetworkReply.NetworkError.ContentNotFoundError),
    (500, QNetworkReply.NetworkError.InternalServerError),
    (200, QNetworkReply.NetworkError.RemoteHostClosedError),
    (None, QNetworkReply.NetworkError.SslHandshakeFailedError),
])
def test_http_and_network_errors_are_sanitized(downloader, status, error):
    assert downloader.start(release_info(), downloader.destination)
    reply = downloader._reply
    reply.status, reply.network_error = status, error
    finish_body(reply, b"private server error details")
    assert_failed(downloader)


@pytest.mark.parametrize("name", ["_inactivity_timer", "_overall_timer"])
def test_timeouts_clean_up_and_old_timer_cannot_cancel_next_download(downloader, name):
    assert downloader.start(release_info(), downloader.destination)
    old_timer = getattr(downloader, name)
    old_reply = downloader._reply
    old_timer.timeout.emit()
    assert old_reply.aborted
    assert_failed(downloader)
    assert downloader.start(release_info(), downloader.destination)
    new_reply = downloader._reply
    old_timer.timeout.emit()
    finish_body(old_reply, CHECKSUM)
    assert downloader._reply is new_reply
    assert downloader.failures == [download.DOWNLOAD_FAILED_MESSAGE]


def test_stop_during_stream_is_quiet_and_late_callbacks_are_harmless(downloader):
    reply = begin_executable(downloader)
    reply.pending.extend(EXECUTABLE[:100])
    reply.readyRead.emit()
    downloader.stop()
    downloader.stop()
    finish_body(reply, EXECUTABLE[100:])
    reply.readyRead.emit()
    reply.metaDataChanged.emit()
    assert downloader.results == []
    assert downloader.failures == []
    assert not downloader.destination.exists()
    assert not downloader.is_downloading
    assert reply.aborted


def test_progress_callback_can_cancel_before_completion(downloader):
    reply = begin_executable(downloader)
    downloader.progress.connect(lambda *_args: downloader.stop())
    finish_body(reply, EXECUTABLE)
    assert downloader.results == []
    assert downloader.failures == []
    assert not downloader.destination.exists()


def test_native_read_exception_is_caught_and_cleans_up(downloader):
    reply = begin_executable(downloader)

    def broken_read(_amount):
        raise OSError("synthetic native read failure")

    reply.read = broken_read
    reply.readyRead.emit()
    assert_failed(downloader)


@pytest.mark.parametrize("mode", ["invalid_length", "compressed_body", "empty_redirect"])
def test_unusable_headers_fail_without_slot_exceptions(downloader, mode):
    assert downloader.start(release_info(), downloader.destination)
    reply = downloader._reply
    if mode == "invalid_length":
        reply.length = "invalid Content-Length"
    elif mode == "compressed_body":
        reply.encoding = b"gzip"
    else:
        reply.status = 302
        reply.redirect = QUrl()
    reply.metaDataChanged.emit()
    assert_failed(downloader)


def test_disk_write_failure_removes_partial_update(downloader):
    reply = begin_executable(downloader)
    handle = downloader._file

    def broken_write(_chunk):
        raise OSError("disk full")

    downloader._file = SimpleNamespace(write=broken_write, close=handle.close)
    reply.pending.extend(EXECUTABLE[:100])
    reply.readyRead.emit()
    assert handle.closed
    assert_failed(downloader)


def test_failed_second_request_cleans_up_after_checksum_reply_detached(downloader):
    assert downloader.start(release_info(), downloader.destination)

    def broken_get(_request):
        raise RuntimeError("synthetic request creation failure")

    downloader._manager.get = broken_get
    finish_body(downloader._reply, CHECKSUM)
    assert_failed(downloader)


def test_real_qt_stream_redirects_and_eof_lifecycle(project_root, tmp_path):
    """Exercise native QNetworkReply callbacks in a subprocess (slot errors abort)."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/checksum", "/exe"):
                self.send_response(302)
                self.send_header("Location", self.path + "-data")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = CHECKSUM if self.path == "/checksum-data" else EXECUTABLE
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for offset in range(0, len(body), 8191):
                self.wfile.write(body[offset:offset + 8191])
                self.wfile.flush()

        def log_message(self, *_args):
            pass

    script = textwrap.dedent("""
        import hashlib, json, sys
        from pathlib import Path
        from PyQt6.QtCore import QCoreApplication, QTimer
        from PyQt6.QtNetwork import QNetworkProxy
        import app_update_download as download
        from app_updates import ReleaseInfo

        app = QCoreApplication([])
        base, output, size, checksum_size, expected_hash = sys.argv[1:]
        # Test-only loopback policy; production validation stays strict.
        download._validate_release = lambda release: True
        download._url_allowed = lambda url, initial: url.scheme() == 'http' and url.host() == '127.0.0.1'
        downloader = download.UpdateDownloader()
        downloader._manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        results, failures, progress = [], [], []
        downloader.completed.connect(lambda path, digest: (results.append((path, digest)), app.quit()))
        downloader.failed.connect(lambda message: (failures.append(message), app.quit()))
        downloader.progress.connect(lambda value, total: progress.append((value, total)))
        release = ReleaseInfo('99.0.0', '', base+'/exe', int(size), base+'/checksum', int(checksum_size))
        assert downloader.start(release, Path(output))
        QTimer.singleShot(5000, lambda: app.exit(2))
        result = app.exec()
        downloader.stop()
        assert result == 0, 'download timed out'
        assert not failures, failures
        assert len(results) == 1 and results[0][1] == expected_hash, results
        assert hashlib.sha256(Path(output).read_bytes()).hexdigest() == expected_hash
        assert progress and progress[-1] == (int(size), int(size)), progress
        print(json.dumps({'verified': True, 'bytes': int(size)}))
    """)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, f"http://127.0.0.1:{server.server_port}",
                 str(tmp_path / "verified.exe"), str(len(EXECUTABLE)), str(len(CHECKSUM)), DIGEST],
                cwd=project_root, capture_output=True, text=True, timeout=10,
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert json.loads(result.stdout) == {"verified": True, "bytes": len(EXECUTABLE)}
