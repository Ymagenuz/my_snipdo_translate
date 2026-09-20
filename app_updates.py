"""Optional, asynchronous checks for usable public GitHub releases.

This module checks release metadata only. It never downloads or runs an update.
"""

from dataclasses import dataclass
import json
import re

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app_version import APP_VERSION


REPOSITORY_URL = "https://github.com/Ymagenuz/my_snipdo_translate"
LATEST_RELEASE_API = "https://api.github.com/repos/Ymagenuz/my_snipdo_translate/releases/latest"
UPDATE_EXECUTABLE = "SnipDoTranslate.exe"
MAX_RESPONSE_BYTES = 256 * 1024
CHECK_TIMEOUT_MS = 10_000
CHECK_FAILED_MESSAGE = "暂时无法检查更新，请检查网络后重试。"
_VERSION_PATTERN = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str


def _version_parts(version: object) -> tuple[int, int, int]:
    if not isinstance(version, str) or len(version) > 64:
        raise ValueError("Invalid release version")
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError("Invalid release version")
    return tuple(int(part) for part in match.groups())


def parse_release(payload: object, current_version: str = APP_VERSION) -> ReleaseInfo | None:
    """Return a newer stable release with a finished Windows executable upload.

    Malformed metadata raises ValueError; a valid but unsuitable release returns
    None. All links are restricted to this repository, and release page links are
    built from validated tags instead of trusting a server-provided page URL.
    """
    current = _version_parts(current_version)
    if not isinstance(payload, dict):
        raise ValueError("Invalid release metadata")
    if type(payload.get("draft")) is not bool or type(payload.get("prerelease")) is not bool:
        raise ValueError("Invalid release metadata")
    if payload["draft"] or payload["prerelease"]:
        return None
    tag = payload.get("tag_name")
    version = _version_parts(tag)
    if version <= current:
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Invalid release assets")
    expected_download = f"{REPOSITORY_URL}/releases/download/{tag}/{UPDATE_EXECUTABLE}"
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if (
            asset.get("name") == UPDATE_EXECUTABLE
            and asset.get("state") == "uploaded"
            and type(asset.get("size")) is int
            and asset["size"] > 0
            and asset.get("browser_download_url") == expected_download
        ):
            return ReleaseInfo(".".join(map(str, version)), f"{REPOSITORY_URL}/releases/tag/{tag}")
    return None


class UpdateChecker(QObject):
    """Run at most one bounded network check without blocking the GUI thread."""

    completed = pyqtSignal(object)  # ReleaseInfo, or None when no usable update exists.
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply = None
        self._timer = None
        self._body = bytearray()

    @property
    def is_checking(self) -> bool:
        return self._reply is not None

    def check(self) -> bool:
        """Start a check, returning False if another check is already running."""
        if self.is_checking:
            return False
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"SnipDoTranslate/{APP_VERSION}".encode("ascii"))
        request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
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
        reply = self._manager.get(request)
        self._reply = reply
        self._body.clear()
        reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        reply.readyRead.connect(lambda: self._consume(reply))
        reply.metaDataChanged.connect(lambda: self._check_headers(reply))
        reply.finished.connect(lambda: self._finish(reply))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._fail(reply))
        self._timer = timer
        timer.start(CHECK_TIMEOUT_MS)
        return True

    def stop(self):
        """Cancel quietly; callbacks from the cancelled request become harmless."""
        self._dispose(abort=True)

    def _check_headers(self, reply):
        if reply is not self._reply:
            return
        length = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        try:
            oversized = int(length) > MAX_RESPONSE_BYTES
        except (TypeError, ValueError, OverflowError):
            oversized = False
        if oversized:
            self._fail(reply)

    def _consume(self, reply):
        if reply is not self._reply:
            return
        remaining = MAX_RESPONSE_BYTES - len(self._body)
        self._body.extend(bytes(reply.read(remaining + 1)))
        if len(self._body) > MAX_RESPONSE_BYTES:
            self._fail(reply)

    def _finish(self, reply):
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        # GitHub returns 404 when this public repository has no published release.
        if status == 404:
            self._dispose()
            self.completed.emit(None)
            return
        if status != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
            self._fail(reply)
            return
        self._check_headers(reply)
        self._consume(reply)
        if reply is not self._reply:
            return
        try:
            release = parse_release(json.loads(self._body.decode("utf-8")))
        except (ValueError, TypeError, RecursionError):
            self._fail(reply)
            return
        self._dispose()
        self.completed.emit(release)

    def _fail(self, reply):
        if reply is not self._reply:
            return
        self._dispose(abort=True)
        self.failed.emit(CHECK_FAILED_MESSAGE)

    def _dispose(self, *, abort=False):
        # Detach before abort(): Qt may emit finished synchronously while aborting.
        reply, self._reply = self._reply, None
        timer, self._timer = self._timer, None
        self._body.clear()
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if reply is not None:
            if abort:
                reply.abort()
            reply.deleteLater()
