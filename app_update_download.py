"""Bounded, asynchronous download and verification of this app's release assets."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app_updates import (
    MAX_CHECKSUM_BYTES, MAX_EXECUTABLE_BYTES, REPOSITORY_URL,
    UPDATE_CHECKSUM, UPDATE_EXECUTABLE, ReleaseInfo, _version_parts,
)
from app_version import APP_VERSION


CHUNK_BYTES = 64 * 1024
INACTIVITY_TIMEOUT_MS = 30_000
OVERALL_TIMEOUT_MS = 30 * 60 * 1000
MAX_REDIRECTS = 5
DOWNLOAD_FAILED_MESSAGE = "更新下载失败或校验未通过，请检查网络后重试。"
# GitHub's documented release-asset hosts; never allow arbitrary subdomains.
_ASSET_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})
_CHECKSUM_PATTERN = re.compile(rb"([0-9a-fA-F]{64})  SnipDoTranslate\.exe(?:\r?\n)?")


def _validate_release(release: ReleaseInfo) -> bool:
    if not isinstance(release, ReleaseInfo):
        return False
    prefix = f"{REPOSITORY_URL}/releases/tag/"
    if not isinstance(release.url, str) or not release.url.startswith(prefix):
        return False
    tag = release.url[len(prefix):]
    try:
        if _version_parts(tag) != _version_parts(release.version):
            return False
    except ValueError:
        return False
    return bool(
        type(release.size) is int and 0 < release.size <= MAX_EXECUTABLE_BYTES
        and type(release.checksum_size) is int and 0 < release.checksum_size <= MAX_CHECKSUM_BYTES
        and release.download_url == f"{REPOSITORY_URL}/releases/download/{tag}/{UPDATE_EXECUTABLE}"
        and release.checksum_url == f"{REPOSITORY_URL}/releases/download/{tag}/{UPDATE_CHECKSUM}"
    )


def _url_allowed(url: QUrl, initial_url: str) -> bool:
    if (
        not url.isValid() or url.scheme() != "https"
        or url.userInfo() or url.port() != -1 or url.hasFragment()
    ):
        return False
    if url.host() == "github.com":
        return url.toString(QUrl.ComponentFormattingOption.FullyEncoded) == initial_url
    return url.host() in _ASSET_HOSTS


def _parse_checksum(body: bytes) -> str:
    match = _CHECKSUM_PATTERN.fullmatch(body)
    if match is None:
        raise ValueError("Invalid checksum file")
    return match.group(1).decode("ascii").lower()


class UpdateDownloader(QObject):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(object, str)  # Verified Path and lowercase SHA-256.
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply = None
        self._release = None
        self._destination = None
        self._file = None
        self._generation = 0
        self._inactivity_timer = None
        self._overall_timer = None
        self._phase = ""
        self._initial_url = ""
        self._redirects = 0
        self._received = 0
        self._checksum_body = bytearray()
        self._expected_hash = ""
        self._hash = None

    @property
    def is_downloading(self) -> bool:
        return self._release is not None

    def start(self, release: ReleaseInfo, destination: Path) -> bool:
        """Create a new destination exclusively, then fetch checksum and executable.

        The caller owns a verified destination after completed; cancellation or
        failure removes only the partial file created by this downloader.
        """
        if self.is_downloading:
            return False
        if not _validate_release(release):
            self.failed.emit(DOWNLOAD_FAILED_MESSAGE)
            return False
        try:
            # Preserve the final component: resolving a pre-existing dangling
            # symlink would bypass exclusive creation and write to its target.
            path = Path(destination).absolute()
            handle = path.open("xb")
        except (OSError, ValueError, TypeError):
            self.failed.emit(DOWNLOAD_FAILED_MESSAGE)
            return False
        self._release = release
        self._destination = path
        self._file = handle
        self._generation += 1
        generation = self._generation
        self._checksum_body.clear()
        self._expected_hash = ""
        self._hash = hashlib.sha256()
        for name, timeout in (
            ("_inactivity_timer", INACTIVITY_TIMEOUT_MS),
            ("_overall_timer", OVERALL_TIMEOUT_MS),
        ):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda g=generation: self._timeout(g))
            setattr(self, name, timer)
            timer.start(timeout)
        try:
            self._begin_phase("checksum", release.checksum_url)
        except Exception:
            self._fail()
            return False
        return self.is_downloading

    def stop(self) -> None:
        """Cancel quietly and make every old network/timer callback harmless."""
        self._dispose(abort=True)

    def _begin_phase(self, phase: str, url: str) -> None:
        self._phase = phase
        self._initial_url = url
        self._redirects = 0
        self._received = 0
        self._request(QUrl(url))

    def _request(self, url: QUrl) -> None:
        if not _url_allowed(url, self._initial_url):
            self._fail()
            return
        request = QNetworkRequest(url)
        request.setRawHeader(b"Accept", b"application/octet-stream")
        request.setRawHeader(b"Accept-Encoding", b"identity")
        request.setRawHeader(b"User-Agent", f"SnipDoTranslate/{APP_VERSION}".encode("ascii"))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        for attribute in (
            QNetworkRequest.Attribute.AuthenticationReuseAttribute,
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
        ):
            request.setAttribute(attribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        request.setAttribute(
            QNetworkRequest.Attribute.CacheLoadControlAttribute,
            QNetworkRequest.CacheLoadControl.AlwaysNetwork,
        )
        request.setTransferTimeout(INACTIVITY_TIMEOUT_MS)
        reply = self._manager.get(request)
        self._reply = reply
        reply.setReadBufferSize(CHUNK_BYTES)
        reply.metaDataChanged.connect(lambda: self._invoke(reply, self._check_headers))
        reply.readyRead.connect(lambda: self._invoke(reply, self._consume))
        reply.finished.connect(lambda: self._invoke(reply, self._finish))
        self._inactivity_timer.start(INACTIVITY_TIMEOUT_MS)

    def _invoke(self, reply, callback) -> None:
        if reply is not self._reply:
            return
        generation = self._generation
        try:
            callback(reply)
        except Exception:
            # Exceptions escaping PyQt slots terminate the process. Keep errors
            # sanitized and clean up even when disk writes or native reads fail.
            if generation == self._generation and self.is_downloading:
                self._fail()

    def _expected_size(self) -> int:
        return self._release.checksum_size if self._phase == "checksum" else self._release.size

    def _check_headers(self, reply) -> bool:
        if reply is not self._reply:
            return False
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status is None:
            return False
        if status in (301, 302, 303, 307, 308):
            target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            if not isinstance(target, QUrl) or target.isEmpty():
                self._fail()
                return False
            target = reply.url().resolved(target)
            if self._redirects >= MAX_REDIRECTS or not _url_allowed(target, self._initial_url):
                self._fail()
                return False
            self._redirects += 1
            self._detach_reply(abort=True)
            self._request(target)
            return False
        if status != 200:
            self._fail()
            return False
        length = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        if length is not None and int(length) != self._expected_size():
            self._fail()
            return False
        encoding = bytes(reply.rawHeader(b"Content-Encoding")).strip().lower()
        if encoding not in (b"", b"identity"):
            self._fail()
            return False
        return True

    def _consume(self, reply) -> None:
        if not self._check_headers(reply):
            return
        while reply is self._reply:
            # read() can return None after readyRead drained the response.
            chunk = reply.read(CHUNK_BYTES)
            if not chunk:
                return
            chunk = bytes(chunk)
            self._received += len(chunk)
            if self._received > self._expected_size():
                self._fail()
                return
            self._inactivity_timer.start(INACTIVITY_TIMEOUT_MS)
            if self._phase == "checksum":
                self._checksum_body.extend(chunk)
            else:
                written = self._file.write(chunk)
                if written != len(chunk):
                    raise OSError("Incomplete update write")
                self._hash.update(chunk)
                self.progress.emit(self._received, self._release.size)

    def _finish(self, reply) -> None:
        if not self._check_headers(reply):
            # A finished response without HTTP headers is a network failure.
            if reply is self._reply:
                self._fail()
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._fail()
            return
        self._consume(reply)
        if reply is not self._reply:
            return
        if self._received != self._expected_size():
            self._fail()
            return
        if self._phase == "checksum":
            self._expected_hash = _parse_checksum(bytes(self._checksum_body))
            self._checksum_body.clear()
            self._detach_reply()
            self._begin_phase("executable", self._release.download_url)
            return
        digest = self._hash.hexdigest()
        if digest != self._expected_hash:
            self._fail()
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._file = None
        destination = self._destination
        self._dispose(keep_file=True)
        self.completed.emit(destination, digest)

    def _timeout(self, generation: int) -> None:
        if generation == self._generation and self.is_downloading:
            self._fail()

    def _fail(self) -> None:
        if not self.is_downloading:
            return
        self._dispose(abort=True)
        self.failed.emit(DOWNLOAD_FAILED_MESSAGE)

    def _detach_reply(self, *, abort=False) -> None:
        reply, self._reply = self._reply, None
        if reply is not None:
            if abort:
                reply.abort()
            reply.deleteLater()

    def _dispose(self, *, abort=False, keep_file=False) -> None:
        self._generation += 1
        self._release = None
        for name in ("_inactivity_timer", "_overall_timer"):
            timer = getattr(self, name)
            setattr(self, name, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
        self._detach_reply(abort=abort)
        handle, self._file = self._file, None
        destination, self._destination = self._destination, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if destination is not None and not keep_file:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        self._checksum_body.clear()
        self._hash = None
        self._expected_hash = ""
