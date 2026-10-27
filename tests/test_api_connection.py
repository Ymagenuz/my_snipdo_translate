from __future__ import annotations

import threading

import httpx

from api_connection import (
    ConnectionWarmer,
    attach_connection_warmer,
    connection_warmer,
)


URL = "https://api.example.test/v1"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _HeadClient:
    def __init__(self, *, hold=None, error=None):
        self.calls = []
        self.hold = hold
        self.error = error

    def head(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.hold is not None:
            assert self.hold.wait(5)
        if self.error is not None:
            raise self.error
        return httpx.Response(404)


def _warmer(client, clock):
    return ConnectionWarmer(
        client,
        URL,
        refresh_after=90.0,
        keep_warm_for=900.0,
        timeout=5.0,
        clock=clock,
    )


def test_warm_up_sends_a_head_and_skips_a_recently_used_connection():
    clock = _Clock()
    client = _HeadClient()
    warmer = _warmer(client, clock)

    assert warmer.warm()
    assert warmer.wait(5)
    assert client.calls == [(URL, {"timeout": 5.0, "follow_redirects": False})]

    clock.now += 89.0
    assert not warmer.warm()
    clock.now += 1.0
    assert warmer.warm()
    assert warmer.wait(5)
    assert len(client.calls) == 2


def test_request_waits_for_an_in_flight_warm_up_instead_of_opening_another():
    hold = threading.Event()
    client = _HeadClient(hold=hold)
    warmer = _warmer(client, _Clock())
    request_started = threading.Event()

    assert warmer.warm()
    assert not warmer.warm()
    assert not warmer.wait(0.05)
    requester = threading.Thread(
        target=lambda: (warmer.before_request(5), request_started.set()),
    )
    requester.start()
    assert not request_started.wait(0.1)

    hold.set()
    requester.join(5)
    assert request_started.is_set()
    assert len(client.calls) == 1


def test_request_wait_is_bounded_when_a_warm_up_hangs():
    hold = threading.Event()
    warmer = _warmer(_HeadClient(hold=hold), _Clock())
    try:
        assert warmer.warm()
        warmer.before_request(0.05)
        # The request proceeds on its own connection after the bound.
        assert not warmer.wait(0)
    finally:
        hold.set()


def test_keep_warm_refreshes_idle_connections_only_after_a_recent_request():
    clock = _Clock()
    client = _HeadClient()
    warmer = _warmer(client, clock)

    assert not warmer.keep_warm()
    assert client.calls == []

    warmer.before_request(0)
    warmer.after_request()
    assert warmer.keep_warm()
    assert client.calls == []

    clock.now += 90.0
    assert warmer.keep_warm()
    assert warmer.wait(5)
    assert len(client.calls) == 1

    clock.now += 810.0
    assert not warmer.keep_warm()
    assert len(client.calls) == 1


def test_failed_warm_up_is_retried_at_the_refresh_interval():
    clock = _Clock()
    client = _HeadClient(error=httpx.ConnectError("offline"))
    warmer = _warmer(client, clock)

    assert warmer.warm()
    assert warmer.wait(5)
    assert not warmer.warm()
    clock.now += 90.0
    assert warmer.warm()
    assert warmer.wait(5)
    assert len(client.calls) == 2


def test_clients_without_weak_references_have_no_warmer():
    warmer = _warmer(_HeadClient(), _Clock())
    unsupported = object()

    attach_connection_warmer(unsupported, warmer)

    assert connection_warmer(unsupported) is None
    assert connection_warmer(None) is None
