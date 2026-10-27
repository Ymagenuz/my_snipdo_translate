from __future__ import annotations

import threading
import time
import weakref
from typing import Callable, Protocol


class _HeadClient(Protocol):
    def head(self, url: str, **kwargs: object) -> object: ...


class ConnectionWarmer:
    """Keep a pooled HTTPS connection to the API host ready between requests.

    A new connection costs DNS, TCP and TLS round trips before the first token.
    A contentless HEAD to the API origin opens or refreshes the pooled
    connection that the next API call reuses. It never carries the API key,
    because the SDK adds credentials per request, not to the shared client.
    """

    def __init__(
        self,
        http_client: _HeadClient,
        url: str,
        *,
        refresh_after: float,
        keep_warm_for: float,
        timeout: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http_client = http_client
        self._url = url
        self._refresh_after = refresh_after
        self._keep_warm_for = keep_warm_for
        self._timeout = timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._last_used: float | None = None
        self._last_request: float | None = None

    def warm(self) -> bool:
        """Open the connection in the background unless it is recent or opening."""
        with self._lock:
            if not self._idle.is_set():
                return False
            if (
                self._last_used is not None
                and self._clock() - self._last_used < self._refresh_after
            ):
                return False
            self._idle.clear()
        try:
            threading.Thread(
                target=self._ping,
                name="SnipDoTranslate-ConnectionWarmup",
                daemon=True,
            ).start()
        except RuntimeError:
            self._idle.set()
            return False
        return True

    def keep_warm(self) -> bool:
        """Refresh an idle connection; return False once the keep-warm window ends."""
        with self._lock:
            if (
                self._last_request is None
                or self._clock() - self._last_request >= self._keep_warm_for
            ):
                return False
        self.warm()
        return True

    def wait(self, timeout: float) -> bool:
        """Wait for an in-flight warm-up; return False if it is still running."""
        return self._idle.wait(timeout)

    def before_request(self, wait_seconds: float) -> None:
        """Let an in-flight warm-up finish so the request reuses its connection."""
        self.wait(wait_seconds)
        self._mark_request()

    def after_request(self) -> None:
        self._mark_request()

    def _mark_request(self) -> None:
        with self._lock:
            now = self._clock()
            self._last_used = now
            self._last_request = now

    def _ping(self) -> None:
        try:
            self._http_client.head(
                self._url,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except Exception:
            # A failed warm-up only means the next request connects itself.
            pass
        finally:
            with self._lock:
                # Failed attempts also count, so an offline network is retried
                # at the refresh interval rather than on every check.
                self._last_used = self._clock()
            self._idle.set()


_WARMERS: "weakref.WeakKeyDictionary[object, ConnectionWarmer]" = (
    weakref.WeakKeyDictionary()
)


def attach_connection_warmer(api_client: object, warmer: ConnectionWarmer) -> None:
    try:
        _WARMERS[api_client] = warmer
    except TypeError:
        # Clients without weak-reference support simply run without warm-up.
        pass


def connection_warmer(api_client: object | None) -> ConnectionWarmer | None:
    if api_client is None:
        return None
    try:
        return _WARMERS.get(api_client)
    except TypeError:
        return None


__all__ = [
    "ConnectionWarmer",
    "attach_connection_warmer",
    "connection_warmer",
]
