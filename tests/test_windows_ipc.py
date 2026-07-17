from __future__ import annotations

import os
import struct
import threading
import time
import uuid
from queue import Queue

import pytest

import windows_ipc as ipc
from app_cli import AppRequest


WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe IPC")


def unique_app_id() -> str:
    return f"SnipDoTranslateTest-{uuid.uuid4().hex}"


def test_json_frame_has_uint32_network_order_length_and_unicode_payload():
    value = {"message": "hello 世界"}
    frame = ipc.encode_json_frame(value)
    (payload_length,) = struct.unpack("!I", frame[:4])
    assert payload_length == len(frame) - 4
    assert ipc.decode_json_payload(frame[4:]) == value


def test_json_decoder_rejects_duplicate_keys_non_finite_numbers_and_non_objects():
    with pytest.raises(ipc.IpcProtocolError):
        ipc.decode_json_payload(b'{"id":"one","id":"two"}')
    with pytest.raises(ipc.IpcProtocolError):
        ipc.decode_json_payload(b'{"number":NaN}')
    with pytest.raises(ipc.IpcProtocolError):
        ipc.decode_json_payload(b"[]")


def test_frame_size_limit_is_enforced_before_transport():
    with pytest.raises(ipc.IpcProtocolError):
        ipc.encode_json_frame({"value": "x" * 64}, max_frame_bytes=32)
    with pytest.raises(ipc.IpcProtocolError):
        ipc.decode_json_payload(b"{" + b" " * 32 + b"}", max_frame_bytes=32)


@pytest.mark.parametrize(
    "value",
    [
        {"version": True, "id": "id", "action": "show", "payload": {}},
        {
            "version": 1,
            "id": "id",
            "action": "show",
            "payload": {},
            "extra": False,
        },
        {"version": 1, "id": "id", "action": "show", "payload": {"x": 1}},
        {
            "version": 1,
            "id": "id",
            "action": "translate_text",
            "payload": {"text": 42},
        },
        {
            "version": 1,
            "id": "id",
            "action": "ocr_image",
            "payload": {"path": "bad\x00path"},
        },
    ],
)
def test_request_schema_and_payload_types_are_strict(value):
    with pytest.raises(ipc.IpcProtocolError):
        ipc.validate_request_dict(value)


def test_ack_schema_distinguishes_accepted_and_rejected():
    accepted = ipc.IpcAck.from_dict(
        {"version": 1, "id": "request-1", "status": "accepted"},
        expected_request_id="request-1",
    )
    rejected = ipc.IpcAck.from_dict(
        {
            "version": 1,
            "id": "request-2",
            "status": "rejected",
            "reason": "busy",
        }
    )
    assert accepted.accepted is True
    assert accepted.reason is None
    assert rejected.accepted is False
    assert rejected.reason is ipc.RejectionReason.BUSY

    with pytest.raises(ipc.IpcProtocolError):
        ipc.IpcAck.from_dict(
            {"version": 1, "id": "wrong", "status": "accepted"},
            expected_request_id="expected",
        )
    with pytest.raises(ipc.IpcProtocolError):
        ipc.IpcAck.from_dict(
            {
                "version": 1,
                "id": "request-3",
                "status": "accepted",
                "reason": "busy",
            }
        )


def test_receive_result_requires_explicit_safe_ownership_decision():
    assert ipc.ReceiveResult.accept().accepted is True
    assert ipc.ReceiveResult.reject(ipc.RejectionReason.NOT_OWNED).accepted is False
    with pytest.raises(ValueError):
        ipc.ReceiveResult(False)
    with pytest.raises(ValueError):
        ipc.ReceiveResult(True, ipc.RejectionReason.BUSY)


@WINDOWS_ONLY
def test_named_pipe_round_trip_returns_explicit_accept_and_reject_ack():
    app_id = unique_app_id()
    received: list[AppRequest] = []

    def handler(request: AppRequest) -> ipc.ReceiveResult:
        received.append(request)
        if request.action == "show":
            return ipc.ReceiveResult.accept()
        return ipc.ReceiveResult.reject(ipc.RejectionReason.BUSY)

    server = ipc.SingleInstanceServer(app_id, handler)
    assert server.start() is True
    try:
        show = AppRequest("show")
        text = AppRequest("translate_text", {"text": "offline only"})
        accepted = ipc.send_request(app_id, show, timeout=2.0)
        rejected = ipc.send_request(app_id, text, timeout=2.0)
    finally:
        server.stop()

    assert accepted.request_id == show.request_id
    assert accepted.accepted is True
    assert rejected.request_id == text.request_id
    assert rejected.accepted is False
    assert rejected.reason is ipc.RejectionReason.BUSY
    assert received == [show, text]


@WINDOWS_ONLY
def test_named_mutex_allows_one_current_user_instance_and_releases_cleanly():
    app_id = unique_app_id()
    handler = lambda _request: ipc.ReceiveResult.accept()
    primary = ipc.SingleInstanceServer(app_id, handler)
    contender = ipc.SingleInstanceServer(app_id, handler)

    assert primary.start() is True
    try:
        assert contender.start() is False
    finally:
        primary.stop()

    assert contender.start() is True
    contender.stop()


@WINDOWS_ONLY
def test_handler_exception_and_plain_truthy_value_never_produce_acceptance():
    secret_exception_text = "sensitive text C:\\private\\source.png"
    app_id = unique_app_id()
    calls = 0

    def handler(_request: AppRequest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(secret_exception_text)
        return True

    server = ipc.SingleInstanceServer(app_id, handler)
    assert server.start() is True
    try:
        exception_ack = ipc.send_request(app_id, AppRequest("show"), timeout=2.0)
        truthy_ack = ipc.send_request(app_id, AppRequest("show"), timeout=2.0)
    finally:
        server.stop()

    assert exception_ack.accepted is False
    assert exception_ack.reason is ipc.RejectionReason.HANDLER_FAILED
    assert truthy_ack.accepted is False
    assert truthy_ack.reason is ipc.RejectionReason.HANDLER_FAILED
    assert secret_exception_text not in repr(exception_ack)


@WINDOWS_ONLY
def test_invalid_but_identifiable_request_receives_rejected_ack():
    app_id = unique_app_id()
    server = ipc.SingleInstanceServer(
        app_id, lambda _request: ipc.ReceiveResult.accept()
    )
    assert server.start() is True
    handle = None
    try:
        handle = ipc._connect_pipe(server.names.pipe, ipc._deadline(2.0))
        invalid_request = {
            "version": 1,
            "id": "invalid-request-1",
            "action": "show",
            "payload": {"unexpected": True},
        }
        ipc._write_json_frame(
            handle,
            invalid_request,
            ipc._deadline(2.0),
            ipc.DEFAULT_MAX_FRAME_BYTES,
        )
        raw_ack = ipc._read_json_frame(
            handle,
            ipc._deadline(2.0),
            ipc.DEFAULT_MAX_FRAME_BYTES,
        )
        ack = ipc.IpcAck.from_dict(
            raw_ack, expected_request_id="invalid-request-1"
        )
    finally:
        ipc._close_handle(handle)
        server.stop()

    assert ack.accepted is False
    assert ack.reason is ipc.RejectionReason.INVALID_REQUEST


@WINDOWS_ONLY
def test_client_retries_startup_race_until_primary_pipe_exists():
    app_id = unique_app_id()
    request = AppRequest("show")
    results: Queue[object] = Queue()

    def sender() -> None:
        try:
            results.put(ipc.send_request(app_id, request, timeout=2.0))
        except Exception as exc:  # the assertion below reports a normalized failure
            results.put(exc)

    thread = threading.Thread(target=sender)
    thread.start()
    time.sleep(0.1)
    server = ipc.SingleInstanceServer(
        app_id, lambda _request: ipc.ReceiveResult.accept()
    )
    assert server.start() is True
    try:
        thread.join(2.0)
        assert not thread.is_alive()
        result = results.get_nowait()
    finally:
        server.stop()

    assert isinstance(result, ipc.IpcAck)
    assert result.accepted is True


@WINDOWS_ONLY
def test_stalled_partial_frame_times_out_without_poisoning_next_connection():
    app_id = unique_app_id()
    server = ipc.SingleInstanceServer(
        app_id,
        lambda _request: ipc.ReceiveResult.accept(),
        io_timeout=0.1,
    )
    assert server.start() is True
    stalled_handle = None
    try:
        stalled_handle = ipc._connect_pipe(server.names.pipe, ipc._deadline(1.0))
        ipc._write_all(
            stalled_handle,
            struct.pack("!I", 64),
            ipc._deadline(1.0),
        )
        time.sleep(0.2)
        ipc._close_handle(stalled_handle)
        stalled_handle = None

        ack = ipc.send_request(app_id, AppRequest("show"), timeout=2.0)
    finally:
        ipc._close_handle(stalled_handle)
        server.stop()

    assert ack.accepted is True


@WINDOWS_ONLY
def test_missing_primary_times_out_and_never_synthesizes_ack():
    with pytest.raises(ipc.IpcTimeoutError):
        ipc.send_request(unique_app_id(), AppRequest("show"), timeout=0.1)
