"""Windows-only single-instance coordination and local request IPC.

The protocol is deliberately small: every message is UTF-8 JSON preceded by a
four-byte, unsigned, network-byte-order payload length.  A server acknowledges a
request only after its receive callback explicitly reports that it owns the
request data.  Merely parsing or validating a request never produces an
``accepted`` acknowledgement.

Named kernel objects are scoped to the current logon session and current user.
The pipe DACL grants access only to that user and LocalSystem and rejects remote
clients.  This module uses only the Python standard library and ctypes so it can
be frozen by PyInstaller without an additional runtime dependency.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any, Callable, Iterator, Literal, Mapping

from app_cli import AppRequest, CliError


PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_BYTES = 8 * 1024 * 1024
DEFAULT_CLIENT_TIMEOUT = 5.0
DEFAULT_SERVER_IO_TIMEOUT = 5.0

_FRAME_HEADER = struct.Struct("!I")
_APP_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_MAX_REQUEST_ID_CHARS = 128
_MAX_PIPE_CHUNK = 64 * 1024


class IpcError(RuntimeError):
    """Base class for normalized, privacy-safe IPC failures."""


class UnsupportedPlatformError(IpcError):
    """Raised when Windows IPC is requested on another platform."""


class SingleInstanceError(IpcError):
    """Raised when the single-instance guard cannot be created."""


class IpcUnavailableError(IpcError):
    """Raised when the primary instance cannot be reached."""


class IpcTimeoutError(IpcError):
    """Raised when an IPC operation exceeds its deadline."""


class IpcProtocolError(IpcError):
    """Raised for malformed, oversized, or unexpected protocol data."""


class RejectionReason(str, Enum):
    """Safe, non-sensitive reason codes returned to a secondary instance."""

    BUSY = "busy"
    MISSING_KEY = "missing_key"
    STARTUP_FAILED = "startup_failed"
    NOT_READY = "not_ready"
    INVALID_REQUEST = "invalid_request"
    HANDLER_FAILED = "handler_failed"
    NOT_OWNED = "not_owned"
    SHUTTING_DOWN = "shutting_down"


@dataclass(frozen=True)
class ReceiveResult:
    """The primary's explicit ownership decision for one request.

    The callback must return :meth:`accept` only after the primary has copied,
    queued, or otherwise taken durable ownership of every datum it needs.  A
    plain truthy value is intentionally not accepted by the server.
    """

    accepted: bool
    reason: RejectionReason | None = None

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool:
            raise TypeError("accepted must be a bool")
        if self.accepted and self.reason is not None:
            raise ValueError("an accepted result cannot have a rejection reason")
        if not self.accepted and not isinstance(self.reason, RejectionReason):
            raise ValueError("a rejected result requires a safe rejection reason")

    @classmethod
    def accept(cls) -> "ReceiveResult":
        return cls(True)

    @classmethod
    def reject(cls, reason: RejectionReason) -> "ReceiveResult":
        return cls(False, reason)


AckStatus = Literal["accepted", "rejected"]


@dataclass(frozen=True)
class IpcAck:
    request_id: str
    status: AckStatus
    reason: RejectionReason | None = None

    def __post_init__(self) -> None:
        _validate_request_id(self.request_id)
        if self.status not in {"accepted", "rejected"}:
            raise ValueError("invalid acknowledgement status")
        if self.status == "accepted" and self.reason is not None:
            raise ValueError("an accepted acknowledgement cannot have a reason")
        if self.status == "rejected" and not isinstance(self.reason, RejectionReason):
            raise ValueError("a rejected acknowledgement requires a reason")

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @classmethod
    def accept(cls, request_id: str) -> "IpcAck":
        return cls(request_id, "accepted")

    @classmethod
    def reject(cls, request_id: str, reason: RejectionReason) -> "IpcAck":
        return cls(request_id, "rejected", reason)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "version": PROTOCOL_VERSION,
            "id": self.request_id,
            "status": self.status,
        }
        if self.reason is not None:
            value["reason"] = self.reason.value
        return value

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_request_id: str | None = None,
    ) -> "IpcAck":
        if type(value) is not dict:
            raise IpcProtocolError("acknowledgement must be a JSON object")
        status = value.get("status")
        expected_keys = (
            {"version", "id", "status"}
            if status == "accepted"
            else {"version", "id", "status", "reason"}
        )
        if set(value) != expected_keys:
            raise IpcProtocolError("acknowledgement schema is invalid")
        if type(value.get("version")) is not int or value["version"] != PROTOCOL_VERSION:
            raise IpcProtocolError("unsupported acknowledgement version")
        request_id = value.get("id")
        try:
            _validate_request_id(request_id)
        except (TypeError, ValueError):
            raise IpcProtocolError("acknowledgement id is invalid") from None
        if expected_request_id is not None and request_id != expected_request_id:
            raise IpcProtocolError("acknowledgement id does not match the request")
        if status == "accepted":
            return cls.accept(request_id)
        if status != "rejected":
            raise IpcProtocolError("acknowledgement status is invalid")
        reason_value = value.get("reason")
        if not isinstance(reason_value, str):
            raise IpcProtocolError("acknowledgement reason is invalid")
        try:
            reason = RejectionReason(reason_value)
        except ValueError:
            raise IpcProtocolError("acknowledgement reason is unsupported") from None
        return cls.reject(request_id, reason)


@dataclass(frozen=True)
class EndpointNames:
    mutex: str
    pipe: str


def _validate_app_id(app_id: str) -> str:
    if not isinstance(app_id, str) or _APP_ID_RE.fullmatch(app_id) is None:
        raise ValueError("app_id must contain 1-64 safe ASCII characters")
    return app_id


def _validate_request_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_REQUEST_ID_CHARS:
        raise ValueError("request id is invalid")
    if any(ord(character) < 0x20 for character in value):
        raise ValueError("request id is invalid")
    return value


def validate_request_dict(value: Mapping[str, Any]) -> AppRequest:
    """Validate the complete v1 request schema and return an ``AppRequest``."""

    if type(value) is not dict:
        raise IpcProtocolError("request must be a JSON object")
    if set(value) != {"version", "id", "action", "payload"}:
        raise IpcProtocolError("request schema is invalid")
    if type(value.get("version")) is not int or value["version"] != PROTOCOL_VERSION:
        raise IpcProtocolError("unsupported request version")
    try:
        _validate_request_id(value.get("id"))
    except (TypeError, ValueError):
        raise IpcProtocolError("request id is invalid") from None

    action = value.get("action")
    payload = value.get("payload")
    if type(payload) is not dict:
        raise IpcProtocolError("request payload must be an object")
    if action == "show":
        if payload:
            raise IpcProtocolError("show payload must be empty")
    elif action == "translate_text":
        if set(payload) != {"text"}:
            raise IpcProtocolError("text request payload schema is invalid")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise IpcProtocolError("text request payload is invalid")
    elif action == "ocr_image":
        if set(payload) != {"path"}:
            raise IpcProtocolError("image request payload schema is invalid")
        path = payload.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            raise IpcProtocolError("image request payload is invalid")
    else:
        raise IpcProtocolError("request action is unsupported")

    try:
        return AppRequest.from_dict(dict(value))
    except CliError:
        raise IpcProtocolError("request is invalid") from None


def validate_request(request: AppRequest) -> AppRequest:
    if not isinstance(request, AppRequest):
        raise TypeError("request must be an AppRequest")
    return validate_request_dict(request.to_dict())


def _validate_max_frame_bytes(max_frame_bytes: int) -> int:
    if type(max_frame_bytes) is not int or not 1 <= max_frame_bytes <= 0xFFFFFFFF:
        raise ValueError("max_frame_bytes is outside the uint32 range")
    return max_frame_bytes


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def encode_json_frame(
    value: Mapping[str, Any],
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> bytes:
    """Serialize one canonical JSON object with its four-byte length prefix."""

    max_frame_bytes = _validate_max_frame_bytes(max_frame_bytes)
    if type(value) is not dict:
        raise IpcProtocolError("frame value must be a JSON object")
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise IpcProtocolError("frame is not valid JSON") from None
    if not payload or len(payload) > max_frame_bytes:
        raise IpcProtocolError("frame exceeds the configured size limit")
    return _FRAME_HEADER.pack(len(payload)) + payload


def decode_json_payload(
    payload: bytes,
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> dict[str, Any]:
    """Decode and strictly validate an unprefixed JSON frame payload."""

    max_frame_bytes = _validate_max_frame_bytes(max_frame_bytes)
    if not isinstance(payload, bytes) or not payload or len(payload) > max_frame_bytes:
        raise IpcProtocolError("frame size is invalid")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise IpcProtocolError("frame contains invalid JSON") from None
    if type(value) is not dict:
        raise IpcProtocolError("frame JSON must be an object")
    return value


def _deadline(timeout: float) -> float:
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise ValueError("timeout must be a positive finite number")
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    return time.monotonic() + timeout


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IpcTimeoutError("IPC operation timed out")
    return remaining


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _ERROR_ALREADY_EXISTS = 183
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_ACCESS_DENIED = 5
    _ERROR_INSUFFICIENT_BUFFER = 122
    _ERROR_SEM_TIMEOUT = 121
    _ERROR_BROKEN_PIPE = 109
    _ERROR_PIPE_BUSY = 231
    _ERROR_NO_DATA = 232
    _ERROR_PIPE_NOT_CONNECTED = 233
    _ERROR_IO_PENDING = 997
    _ERROR_OPERATION_ABORTED = 995
    _ERROR_PIPE_CONNECTED = 535
    _ERROR_NOT_FOUND = 1168

    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _INFINITE = 0xFFFFFFFF

    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _SDDL_REVISION_1 = 1

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _OPEN_EXISTING = 3
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000

    _PIPE_ACCESS_DUPLEX = 0x00000003
    _PIPE_TYPE_BYTE = 0x00000000
    _PIPE_READMODE_BYTE = 0x00000000
    _PIPE_WAIT = 0x00000000
    _PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    _PIPE_UNLIMITED_INSTANCES = 255

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Sid", wintypes.LPVOID),
            ("Attributes", wintypes.DWORD),
        ]

    class _TOKEN_USER_STRUCT(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcessId.argtypes = []
    _kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    _kernel32.ProcessIdToSessionId.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateMutexW.argtypes = [
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    ]
    _kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    _kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    _kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    _kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
    _kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED),
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED),
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL


def _require_windows() -> None:
    if os.name != "nt":
        raise UnsupportedPlatformError("Windows IPC is unavailable on this platform")


def _is_invalid_handle(handle: object) -> bool:
    return handle is None or (os.name == "nt" and handle == _INVALID_HANDLE_VALUE)


def _close_handle(handle: object) -> None:
    if os.name == "nt" and not _is_invalid_handle(handle):
        _kernel32.CloseHandle(handle)


@lru_cache(maxsize=1)
def _current_user_sid() -> str:
    _require_windows()
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise SingleInstanceError("current-user identity is unavailable")
    try:
        size = wintypes.DWORD()
        _advapi32.GetTokenInformation(
            token, _TOKEN_USER, None, 0, ctypes.byref(size)
        )
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or not size.value:
            raise SingleInstanceError("current-user identity is unavailable")
        buffer = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
            token,
            _TOKEN_USER,
            buffer,
            size.value,
            ctypes.byref(size),
        ):
            raise SingleInstanceError("current-user identity is unavailable")
        token_user = ctypes.cast(
            buffer, ctypes.POINTER(_TOKEN_USER_STRUCT)
        ).contents
        sid_pointer = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(
            token_user.User.Sid, ctypes.byref(sid_pointer)
        ):
            raise SingleInstanceError("current-user identity is unavailable")
        try:
            return ctypes.wstring_at(sid_pointer)
        finally:
            _kernel32.LocalFree(ctypes.cast(sid_pointer, wintypes.HLOCAL))
    finally:
        _close_handle(token)


@lru_cache(maxsize=1)
def _current_session_id() -> int:
    _require_windows()
    session_id = wintypes.DWORD()
    if not _kernel32.ProcessIdToSessionId(
        _kernel32.GetCurrentProcessId(), ctypes.byref(session_id)
    ):
        raise SingleInstanceError("current logon session is unavailable")
    return session_id.value


@contextmanager
def _user_only_security_attributes() -> Iterator[Any]:
    _require_windows()
    descriptor = wintypes.LPVOID()
    # Protected DACL: current user and LocalSystem only, with full access.
    sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{_current_user_sid()})"
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        None,
    ):
        raise SingleInstanceError("current-user object security could not be created")
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    try:
        yield ctypes.byref(attributes)
    finally:
        _kernel32.LocalFree(ctypes.cast(descriptor, wintypes.HLOCAL))


def endpoint_names(app_id: str) -> EndpointNames:
    """Return stable, non-sensitive kernel object names for this user."""

    _require_windows()
    app_id = _validate_app_id(app_id)
    identity = f"{_current_user_sid()}:{_current_session_id()}".encode("ascii")
    user_scope = hashlib.sha256(identity).hexdigest()[:16]
    stem = f"{app_id}.{user_scope}.v1"
    return EndpointNames(
        mutex=f"Local\\{stem}.mutex",
        pipe=f"\\\\.\\pipe\\{stem}",
    )


class _NamedMutexLease:
    """Lifetime lease backed by a named Win32 mutex object.

    Ownership is represented by being the creator and retaining the handle.  We
    intentionally do not take thread-affine mutex ownership, allowing orderly
    shutdown from a different thread while retaining atomic CreateMutexW
    first-creator semantics.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._handle: object | None = None
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        _require_windows()
        with self._lock:
            if self._handle is not None:
                raise SingleInstanceError("single-instance lease is already open")
            with _user_only_security_attributes() as attributes:
                ctypes.set_last_error(0)
                handle = _kernel32.CreateMutexW(attributes, False, self._name)
                error = ctypes.get_last_error()
            if _is_invalid_handle(handle):
                raise SingleInstanceError("single-instance lease could not be created")
            if error == _ERROR_ALREADY_EXISTS:
                _close_handle(handle)
                return False
            self._handle = handle
            return True

    def close(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
        _close_handle(handle)


class _ServerStopping(Exception):
    pass


def _new_overlapped() -> tuple[Any, Any]:
    event = _kernel32.CreateEventW(None, True, False, None)
    if _is_invalid_handle(event):
        raise IpcUnavailableError("IPC event could not be created")
    return _OVERLAPPED(hEvent=event), event


def _cancel_and_drain(handle: object, overlapped: Any, event: object) -> None:
    if not _kernel32.CancelIoEx(handle, ctypes.byref(overlapped)):
        if ctypes.get_last_error() not in {_ERROR_NOT_FOUND, _ERROR_OPERATION_ABORTED}:
            return
    _kernel32.WaitForSingleObject(event, _INFINITE)
    transferred = wintypes.DWORD()
    _kernel32.GetOverlappedResult(
        handle, ctypes.byref(overlapped), ctypes.byref(transferred), False
    )


def _wait_overlapped(
    handle: object,
    overlapped: Any,
    event: object,
    deadline: float | None,
    stop_event: threading.Event | None = None,
) -> int:
    while True:
        if stop_event is not None and stop_event.is_set():
            _cancel_and_drain(handle, overlapped, event)
            raise _ServerStopping
        if deadline is None:
            wait_ms = 50
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _cancel_and_drain(handle, overlapped, event)
                raise IpcTimeoutError("IPC operation timed out")
            wait_ms = max(1, min(50, math.ceil(remaining * 1000)))
        result = _kernel32.WaitForSingleObject(event, wait_ms)
        if result == _WAIT_OBJECT_0:
            transferred = wintypes.DWORD()
            if not _kernel32.GetOverlappedResult(
                handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                False,
            ):
                error = ctypes.get_last_error()
                if error == _ERROR_OPERATION_ABORTED and stop_event is not None and stop_event.is_set():
                    raise _ServerStopping
                if error in {
                    _ERROR_BROKEN_PIPE,
                    _ERROR_NO_DATA,
                    _ERROR_PIPE_NOT_CONNECTED,
                    _ERROR_OPERATION_ABORTED,
                }:
                    raise IpcUnavailableError("IPC peer disconnected")
                raise IpcUnavailableError("IPC operation failed")
            return transferred.value
        if result != _WAIT_TIMEOUT:
            _cancel_and_drain(handle, overlapped, event)
            raise IpcUnavailableError("IPC wait failed")
        if deadline is not None and time.monotonic() >= deadline:
            _cancel_and_drain(handle, overlapped, event)
            raise IpcTimeoutError("IPC operation timed out")


def _read_some(
    handle: object,
    count: int,
    deadline: float,
    stop_event: threading.Event | None = None,
) -> bytes:
    buffer = ctypes.create_string_buffer(count)
    transferred = wintypes.DWORD()
    overlapped, event = _new_overlapped()
    try:
        success = _kernel32.ReadFile(
            handle,
            buffer,
            count,
            ctypes.byref(transferred),
            ctypes.byref(overlapped),
        )
        if success:
            amount = transferred.value
        else:
            error = ctypes.get_last_error()
            if error != _ERROR_IO_PENDING:
                if error in {
                    _ERROR_BROKEN_PIPE,
                    _ERROR_NO_DATA,
                    _ERROR_PIPE_NOT_CONNECTED,
                }:
                    raise IpcUnavailableError("IPC peer disconnected")
                raise IpcUnavailableError("IPC read failed")
            amount = _wait_overlapped(handle, overlapped, event, deadline, stop_event)
        if amount == 0:
            raise IpcUnavailableError("IPC peer disconnected")
        return buffer.raw[:amount]
    finally:
        _close_handle(event)


def _read_exact(
    handle: object,
    count: int,
    deadline: float,
    stop_event: threading.Event | None = None,
) -> bytes:
    parts: list[bytes] = []
    remaining = count
    while remaining:
        part = _read_some(
            handle, min(remaining, _MAX_PIPE_CHUNK), deadline, stop_event
        )
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


def _write_all(
    handle: object,
    value: bytes,
    deadline: float,
    stop_event: threading.Event | None = None,
) -> None:
    offset = 0
    while offset < len(value):
        chunk = value[offset : offset + _MAX_PIPE_CHUNK]
        buffer = ctypes.create_string_buffer(chunk)
        transferred = wintypes.DWORD()
        overlapped, event = _new_overlapped()
        try:
            success = _kernel32.WriteFile(
                handle,
                buffer,
                len(chunk),
                ctypes.byref(transferred),
                ctypes.byref(overlapped),
            )
            if success:
                amount = transferred.value
            else:
                error = ctypes.get_last_error()
                if error != _ERROR_IO_PENDING:
                    if error in {
                        _ERROR_BROKEN_PIPE,
                        _ERROR_NO_DATA,
                        _ERROR_PIPE_NOT_CONNECTED,
                    }:
                        raise IpcUnavailableError("IPC peer disconnected")
                    raise IpcUnavailableError("IPC write failed")
                amount = _wait_overlapped(
                    handle, overlapped, event, deadline, stop_event
                )
            if amount <= 0:
                raise IpcUnavailableError("IPC write made no progress")
            offset += amount
        finally:
            _close_handle(event)


def _read_json_frame(
    handle: object,
    deadline: float,
    max_frame_bytes: int,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    header = _read_exact(handle, _FRAME_HEADER.size, deadline, stop_event)
    (length,) = _FRAME_HEADER.unpack(header)
    if length == 0 or length > max_frame_bytes:
        raise IpcProtocolError("frame size is invalid")
    payload = _read_exact(handle, length, deadline, stop_event)
    return decode_json_payload(payload, max_frame_bytes=max_frame_bytes)


def _write_json_frame(
    handle: object,
    value: Mapping[str, Any],
    deadline: float,
    max_frame_bytes: int,
    stop_event: threading.Event | None = None,
) -> None:
    frame = encode_json_frame(value, max_frame_bytes=max_frame_bytes)
    _write_all(handle, frame, deadline, stop_event)


def _connect_pipe(pipe_name: str, deadline: float) -> object:
    while True:
        remaining = _remaining(deadline)
        wait_ms = max(1, min(100, math.ceil(remaining * 1000)))
        if _kernel32.WaitNamedPipeW(pipe_name, wait_ms):
            handle = _kernel32.CreateFileW(
                pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OVERLAPPED,
                None,
            )
            if not _is_invalid_handle(handle):
                return handle
            error = ctypes.get_last_error()
            if error not in {_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY}:
                raise IpcUnavailableError("primary instance IPC is unavailable")
        else:
            error = ctypes.get_last_error()
            if error not in {
                _ERROR_FILE_NOT_FOUND,
                _ERROR_PIPE_BUSY,
                _ERROR_SEM_TIMEOUT,
            }:
                raise IpcUnavailableError("primary instance IPC is unavailable")
        time.sleep(min(0.02, max(0.0, remaining)))


def send_request(
    app_id: str,
    request: AppRequest,
    *,
    timeout: float = DEFAULT_CLIENT_TIMEOUT,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> IpcAck:
    """Send one request to the primary and return its validated explicit ACK.

    Connection attempts are retried until the deadline, covering the normal race
    where a new primary owns the mutex but has not started accepting yet.  Every
    failure raises; no error path synthesizes an accepted acknowledgement.
    """

    _require_windows()
    max_frame_bytes = _validate_max_frame_bytes(max_frame_bytes)
    request = validate_request(request)
    deadline = _deadline(timeout)
    names = endpoint_names(app_id)
    handle = _connect_pipe(names.pipe, deadline)
    try:
        _write_json_frame(
            handle, request.to_dict(), deadline, max_frame_bytes
        )
        response = _read_json_frame(handle, deadline, max_frame_bytes)
        return IpcAck.from_dict(
            response, expected_request_id=request.request_id
        )
    finally:
        _close_handle(handle)


RequestHandler = Callable[[AppRequest], ReceiveResult]


class SingleInstanceServer:
    """Current-user named-mutex guard and framed named-pipe server.

    ``handler`` runs on the server thread.  GUI integrations should synchronously
    hand the request to the UI thread and return :meth:`ReceiveResult.accept`
    only after that handoff has copied or queued all required data successfully.
    """

    def __init__(
        self,
        app_id: str,
        handler: RequestHandler,
        *,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        io_timeout: float = DEFAULT_SERVER_IO_TIMEOUT,
    ) -> None:
        _require_windows()
        if not callable(handler):
            raise TypeError("handler must be callable")
        self.names = endpoint_names(app_id)
        self._handler = handler
        self._max_frame_bytes = _validate_max_frame_bytes(max_frame_bytes)
        # Validate now, while storing the duration rather than an absolute time.
        self._io_timeout = _deadline(io_timeout) - time.monotonic()
        self._mutex = _NamedMutexLease(self.names.mutex)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._pipe_handle: object | None = None
        self._failed = threading.Event()

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    def _create_pipe(self, *, first_instance: bool) -> object:
        open_mode = _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED
        if first_instance:
            open_mode |= _FILE_FLAG_FIRST_PIPE_INSTANCE
        pipe_mode = (
            _PIPE_TYPE_BYTE
            | _PIPE_READMODE_BYTE
            | _PIPE_WAIT
            | _PIPE_REJECT_REMOTE_CLIENTS
        )
        with _user_only_security_attributes() as attributes:
            handle = _kernel32.CreateNamedPipeW(
                self.names.pipe,
                open_mode,
                pipe_mode,
                _PIPE_UNLIMITED_INSTANCES,
                _MAX_PIPE_CHUNK,
                _MAX_PIPE_CHUNK,
                0,
                attributes,
            )
        if _is_invalid_handle(handle):
            raise SingleInstanceError("local IPC endpoint could not be created")
        return handle

    def start(self) -> bool:
        """Start as primary, returning ``False`` if one already exists."""

        with self._state_lock:
            if self._thread is not None:
                raise SingleInstanceError("single-instance server was already started")
        if not self._mutex.acquire():
            return False
        try:
            pipe_handle = self._create_pipe(first_instance=True)
        except Exception:
            self._mutex.close()
            raise
        self._stop_event.clear()
        self._failed.clear()
        with self._state_lock:
            self._pipe_handle = pipe_handle
            self._thread = threading.Thread(
                target=self._run,
                name="SnipDoTranslate-IPC",
                daemon=True,
            )
            thread = self._thread
        try:
            thread.start()
        except Exception:
            with self._state_lock:
                self._pipe_handle = None
                self._thread = None
            _close_handle(pipe_handle)
            self._mutex.close()
            raise SingleInstanceError("IPC server thread could not be started")
        return True

    def _connect_client(self, handle: object) -> None:
        overlapped, event = _new_overlapped()
        try:
            success = _kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped))
            if success:
                return
            error = ctypes.get_last_error()
            if error == _ERROR_PIPE_CONNECTED:
                return
            if error != _ERROR_IO_PENDING:
                if error == _ERROR_OPERATION_ABORTED and self._stop_event.is_set():
                    raise _ServerStopping
                raise IpcUnavailableError("IPC accept failed")
            _wait_overlapped(handle, overlapped, event, None, self._stop_event)
        finally:
            _close_handle(event)

    @staticmethod
    def _candidate_request_id(value: object) -> str | None:
        if type(value) is not dict:
            return None
        request_id = value.get("id")
        try:
            return _validate_request_id(request_id)
        except (TypeError, ValueError):
            return None

    def _best_effort_ack(self, handle: object, ack: IpcAck) -> bool:
        try:
            _write_json_frame(
                handle,
                ack.to_dict(),
                _deadline(self._io_timeout),
                self._max_frame_bytes,
                self._stop_event,
            )
        except (IpcError, _ServerStopping):
            return False
        return True

    def _wait_for_ack_consumption(self, handle: object) -> None:
        """Keep the pipe connected until the client consumes the ACK and closes.

        DisconnectNamedPipe discards unread pipe data.  A bounded read provides a
        lightweight close handshake without the unbounded blocking behavior of
        FlushFileBuffers.  Receiving an unexpected extra byte also ends the
        connection, as one request per connection is the complete protocol.
        """

        try:
            _read_some(
                handle,
                1,
                _deadline(self._io_timeout),
                self._stop_event,
            )
        except (IpcError, _ServerStopping):
            return

    def _serve_client(self, handle: object) -> None:
        try:
            raw_request = _read_json_frame(
                handle,
                _deadline(self._io_timeout),
                self._max_frame_bytes,
                self._stop_event,
            )
        except (IpcError, _ServerStopping):
            return

        request_id = self._candidate_request_id(raw_request)
        try:
            request = validate_request_dict(raw_request)
        except IpcProtocolError:
            if request_id is not None:
                wrote_ack = self._best_effort_ack(
                    handle,
                    IpcAck.reject(request_id, RejectionReason.INVALID_REQUEST),
                )
                if wrote_ack:
                    self._wait_for_ack_consumption(handle)
            return

        if self._stop_event.is_set():
            result = ReceiveResult.reject(RejectionReason.SHUTTING_DOWN)
        else:
            try:
                result = self._handler(request)
            except Exception:
                result = ReceiveResult.reject(RejectionReason.HANDLER_FAILED)
            if not isinstance(result, ReceiveResult):
                result = ReceiveResult.reject(RejectionReason.HANDLER_FAILED)

        ack = (
            IpcAck.accept(request.request_id)
            if result.accepted
            else IpcAck.reject(request.request_id, result.reason)
        )
        if self._best_effort_ack(handle, ack):
            self._wait_for_ack_consumption(handle)

    def _close_current_pipe(self, handle: object) -> None:
        with self._state_lock:
            if self._pipe_handle == handle:
                self._pipe_handle = None
            _kernel32.DisconnectNamedPipe(handle)
            _close_handle(handle)

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._state_lock:
                    handle = self._pipe_handle
                if handle is None:
                    handle = self._create_pipe(first_instance=False)
                    with self._state_lock:
                        if self._stop_event.is_set():
                            _close_handle(handle)
                            break
                        self._pipe_handle = handle
                try:
                    self._connect_client(handle)
                    self._serve_client(handle)
                except _ServerStopping:
                    break
                except IpcError:
                    # A broken or stalled client is isolated to its pipe instance.
                    pass
                finally:
                    self._close_current_pipe(handle)
        except Exception:
            self._failed.set()
        finally:
            with self._state_lock:
                handle, self._pipe_handle = self._pipe_handle, None
            if handle is not None:
                _kernel32.DisconnectNamedPipe(handle)
                _close_handle(handle)
            self._mutex.close()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop accepting and release the single-instance lease."""

        deadline = _deadline(timeout)
        self._stop_event.set()
        with self._state_lock:
            handle = self._pipe_handle
            if handle is not None:
                # The server thread closes the handle under the same state lock.
                _kernel32.CancelIoEx(handle, None)
            thread = self._thread
        if thread is None:
            self._mutex.close()
            return
        thread.join(_remaining(deadline))
        if thread.is_alive():
            raise IpcTimeoutError("IPC server did not stop before the deadline")

    close = stop

    def __enter__(self) -> "SingleInstanceServer":
        if not self.start():
            raise SingleInstanceError("another primary instance already exists")
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.stop()


__all__ = [
    "DEFAULT_CLIENT_TIMEOUT",
    "DEFAULT_MAX_FRAME_BYTES",
    "DEFAULT_SERVER_IO_TIMEOUT",
    "EndpointNames",
    "IpcAck",
    "IpcError",
    "IpcProtocolError",
    "IpcTimeoutError",
    "IpcUnavailableError",
    "ReceiveResult",
    "RejectionReason",
    "SingleInstanceError",
    "SingleInstanceServer",
    "UnsupportedPlatformError",
    "decode_json_payload",
    "encode_json_frame",
    "endpoint_names",
    "send_request",
    "validate_request",
    "validate_request_dict",
]
