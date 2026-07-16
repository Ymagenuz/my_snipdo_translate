from __future__ import annotations

import ctypes
import unicodedata
from contextlib import contextmanager, nullcontext
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import ContextManager, Protocol

from app_paths import LEGACY_KEY_FILE_NAME

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
TARGET_NAME = "SnipDoTranslate/GPTSAPI"
MIGRATION_MUTEX_NAME = r"Local\SnipDoTranslate.CredentialMigration.GPTSAPI"
MAX_CREDENTIAL_BLOB_BYTES = 2560
MAX_LEGACY_KEY_FILE_BYTES = 16 * 1024

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_HANDLE_EOF = 38

WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
MIGRATION_LOCK_TIMEOUT_MS = 30_000

LPBYTE = ctypes.POINTER(wintypes.BYTE)


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", LPBYTE),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class CredentialStoreError(OSError):
    """A redacted failure at the native credential-store boundary."""


@dataclass(frozen=True)
class CredentialEntry:
    exists: bool
    value: str = field(repr=False)


class CredentialStore(Protocol):
    def read(self) -> str: ...

    def write(self, secret: str) -> bool: ...


class _CredentialNativeApi(Protocol):
    def read(self, target_name: str) -> PCREDENTIALW | None: ...

    def free(self, pointer: PCREDENTIALW) -> None: ...

    def write(self, credential: CREDENTIALW) -> None: ...

    def delete(self, target_name: str) -> bool: ...

    def migration_lock(
        self, mutex_name: str, timeout_ms: int
    ) -> ContextManager[None]: ...


class _LegacyFileApi(Protocol):
    def open(self, path: Path) -> object: ...

    def info(self, handle: object) -> tuple[int, int]: ...

    def read(self, handle: object, maximum_bytes: int) -> bytes: ...

    def close(self, handle: object) -> None: ...


def _native_call_error(operation: str) -> OSError:
    return OSError(ctypes.get_last_error(), f"{operation} failed")


class _WindowsCredentialApi:
    def __init__(self) -> None:
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)

        self._cred_read = self._advapi32.CredReadW
        self._cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(PCREDENTIALW),
        ]
        self._cred_read.restype = wintypes.BOOL

        self._cred_write = self._advapi32.CredWriteW
        self._cred_write.argtypes = [PCREDENTIALW, wintypes.DWORD]
        self._cred_write.restype = wintypes.BOOL

        self._cred_free = self._advapi32.CredFree
        self._cred_free.argtypes = [wintypes.LPVOID]
        self._cred_free.restype = None

        self._cred_delete = self._advapi32.CredDeleteW
        self._cred_delete.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._cred_delete.restype = wintypes.BOOL

        self._create_mutex = self._kernel32.CreateMutexW
        self._create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self._create_mutex.restype = wintypes.HANDLE

        self._wait_for_single_object = self._kernel32.WaitForSingleObject
        self._wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._wait_for_single_object.restype = wintypes.DWORD

        self._release_mutex = self._kernel32.ReleaseMutex
        self._release_mutex.argtypes = [wintypes.HANDLE]
        self._release_mutex.restype = wintypes.BOOL

        self._close_handle = self._kernel32.CloseHandle
        self._close_handle.argtypes = [wintypes.HANDLE]
        self._close_handle.restype = wintypes.BOOL

    def read(self, target_name: str) -> PCREDENTIALW | None:
        pointer = PCREDENTIALW()
        if self._cred_read(
            target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            return pointer
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise OSError(error, "CredReadW failed")

    def free(self, pointer: PCREDENTIALW) -> None:
        self._cred_free(pointer)

    def write(self, credential: CREDENTIALW) -> None:
        if not self._cred_write(ctypes.byref(credential), 0):
            raise _native_call_error("CredWriteW")

    def delete(self, target_name: str) -> bool:
        if self._cred_delete(target_name, CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return True
        raise OSError(error, "CredDeleteW failed")

    @contextmanager
    def migration_lock(self, mutex_name: str, timeout_ms: int):
        handle = self._create_mutex(None, False, mutex_name)
        if not handle:
            raise _native_call_error("CreateMutexW")
        acquired = False
        try:
            wait_result = self._wait_for_single_object(handle, timeout_ms)
            if wait_result in (WAIT_OBJECT_0, WAIT_ABANDONED):
                acquired = True
            elif wait_result == WAIT_TIMEOUT:
                raise TimeoutError("credential migration lock timed out")
            elif wait_result == WAIT_FAILED:
                raise _native_call_error("WaitForSingleObject")
            else:
                raise OSError("unexpected credential migration lock result")
            yield
        finally:
            try:
                if acquired and not self._release_mutex(handle):
                    raise _native_call_error("ReleaseMutex")
            finally:
                if not self._close_handle(handle):
                    raise _native_call_error("CloseHandle")


class _WindowsLegacyFileApi:
    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)

        self._create_file = kernel32.CreateFileW
        self._create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._create_file.restype = wintypes.HANDLE

        self._get_file_information = kernel32.GetFileInformationByHandle
        self._get_file_information.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
        ]
        self._get_file_information.restype = wintypes.BOOL

        self._read_file = kernel32.ReadFile
        self._read_file.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._read_file.restype = wintypes.BOOL

        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = [wintypes.HANDLE]
        self._close_handle.restype = wintypes.BOOL

    def open(self, path: Path) -> object:
        handle = self._create_file(
            str(path),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle is None or handle == INVALID_HANDLE_VALUE:
            raise _native_call_error("CreateFileW")
        return handle

    def info(self, handle: object) -> tuple[int, int]:
        information = BY_HANDLE_FILE_INFORMATION()
        if not self._get_file_information(handle, ctypes.byref(information)):
            raise _native_call_error("GetFileInformationByHandle")
        size = (int(information.nFileSizeHigh) << 32) | int(
            information.nFileSizeLow
        )
        return int(information.dwFileAttributes), size

    def read(self, handle: object, maximum_bytes: int) -> bytes:
        chunks: list[bytes] = []
        remaining = maximum_bytes
        while remaining:
            chunk_size = min(4096, remaining)
            buffer = (wintypes.BYTE * chunk_size)()
            bytes_read = wintypes.DWORD()
            if not self._read_file(
                handle,
                buffer,
                chunk_size,
                ctypes.byref(bytes_read),
                None,
            ):
                error = ctypes.get_last_error()
                if error == ERROR_HANDLE_EOF:
                    break
                raise OSError(error, "ReadFile failed")
            count = int(bytes_read.value)
            if not count:
                break
            chunks.append(ctypes.string_at(buffer, count))
            remaining -= count
        return b"".join(chunks)

    def close(self, handle: object) -> None:
        if not self._close_handle(handle):
            raise _native_call_error("CloseHandle")


class WindowsCredentialStore:
    def __init__(
        self,
        target_name: str = TARGET_NAME,
        *,
        native_api: _CredentialNativeApi | None = None,
        migration_mutex_name: str = MIGRATION_MUTEX_NAME,
        migration_lock_timeout_ms: int = MIGRATION_LOCK_TIMEOUT_MS,
    ) -> None:
        if not isinstance(target_name, str) or not target_name or "\x00" in target_name:
            raise ValueError("invalid credential target")
        self.target_name = target_name
        if (
            not isinstance(migration_mutex_name, str)
            or not migration_mutex_name
            or "\x00" in migration_mutex_name
        ):
            raise ValueError("invalid credential migration mutex")
        if (
            not isinstance(migration_lock_timeout_ms, int)
            or not 0 <= migration_lock_timeout_ms <= 0xFFFFFFFF
        ):
            raise ValueError("invalid credential migration lock timeout")
        self._migration_mutex_name = migration_mutex_name
        self._migration_lock_timeout_ms = migration_lock_timeout_ms
        self._native_api = native_api or _WindowsCredentialApi()

    def read_entry(self) -> CredentialEntry:
        try:
            pointer = self._native_api.read(self.target_name)
        except Exception:
            raise CredentialStoreError("credential read failed") from None
        if pointer is None:
            return CredentialEntry(False, "")

        try:
            try:
                credential = pointer.contents
                blob_size = int(credential.CredentialBlobSize)
                if blob_size > MAX_CREDENTIAL_BLOB_BYTES or blob_size % 2:
                    raise CredentialStoreError("credential decode failed")
                if blob_size:
                    if not credential.CredentialBlob:
                        raise CredentialStoreError("credential decode failed")
                    blob = ctypes.string_at(credential.CredentialBlob, blob_size)
                else:
                    blob = b""
                value = blob.decode("utf-16-le").strip()
                return CredentialEntry(True, value)
            except CredentialStoreError:
                raise
            except Exception:
                raise CredentialStoreError("credential decode failed") from None
        finally:
            try:
                self._native_api.free(pointer)
            except Exception:
                raise CredentialStoreError("credential read cleanup failed") from None

    def read(self) -> str:
        return self.read_entry().value

    def write(self, secret: str) -> bool:
        prepared = _prepare_secret_for_write(secret)
        if prepared is None:
            return False
        normalized, blob = prepared
        buffer = (wintypes.BYTE * len(blob)).from_buffer_copy(blob)
        credential = CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = self.target_name
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, LPBYTE)
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "SnipDoTranslate"

        try:
            self._native_api.write(credential)
        except Exception:
            raise CredentialStoreError("credential write failed") from None

        readback = self.read_entry()
        if not readback.exists or readback.value != normalized:
            raise CredentialStoreError("credential write verification failed")
        return True

    def delete(self) -> bool:
        try:
            return self._native_api.delete(self.target_name)
        except Exception:
            raise CredentialStoreError("credential delete failed") from None

    def migration_lock(self) -> ContextManager[None]:
        return self._native_api.migration_lock(
            self._migration_mutex_name, self._migration_lock_timeout_ms
        )


def _prepare_secret_for_write(secret: str) -> tuple[str, bytes] | None:
    if not isinstance(secret, str):
        return None
    normalized = secret.strip()
    if not normalized:
        return None
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        return None
    try:
        blob = normalized.encode("utf-16-le")
    except UnicodeEncodeError:
        return None
    if len(blob) > MAX_CREDENTIAL_BLOB_BYTES:
        return None
    return normalized, blob


@dataclass(frozen=True)
class KeyResolution:
    key: str = field(repr=False)
    source: str
    persisted: bool
    migrated: bool


def _normalize_placeholder(value: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return " ".join(normalized.split())


_KNOWN_PLACEHOLDERS = frozenset(
    {
        "",
        "your_api_key",
        "your-api-key",
        "your api key",
        "填这里",
        "填 这里",
        "请填这里",
        "请填 这里",
        "在这里填写",
        "请在这里填写",
    }
)


def is_placeholder_api_key(value: str) -> bool:
    return _normalize_placeholder(value) in _KNOWN_PLACEHOLDERS


def _read_store_entry(store: CredentialStore) -> CredentialEntry:
    read_entry = getattr(store, "read_entry", None)
    if callable(read_entry):
        result = read_entry()
        if isinstance(result, CredentialEntry):
            return CredentialEntry(result.exists, result.value.strip())
        raise CredentialStoreError("credential read returned invalid data")

    value = store.read()
    if not isinstance(value, str):
        raise CredentialStoreError("credential read returned invalid data")
    return CredentialEntry(bool(value), value.strip())


def _migration_lock(store: CredentialStore) -> ContextManager[None]:
    lock = getattr(store, "migration_lock", None)
    if callable(lock):
        return lock()
    return nullcontext()


def _read_legacy_key(
    path: Path,
    *,
    file_api: _LegacyFileApi | None = None,
) -> str:
    handle: object | None = None
    try:
        api = file_api or _WindowsLegacyFileApi()
        handle = api.open(path)
        attributes, file_size = api.info(handle)
        if attributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY):
            return ""
        if file_size > MAX_LEGACY_KEY_FILE_BYTES:
            return ""
        contents = api.read(handle, MAX_LEGACY_KEY_FILE_BYTES + 1)
        if len(contents) > MAX_LEGACY_KEY_FILE_BYTES:
            return ""
        return contents.decode("utf-8-sig").strip()
    except (OSError, UnicodeError):
        return ""
    finally:
        if handle is not None:
            try:
                api.close(handle)
            except OSError:
                pass


def resolve_api_key(
    env_value: str,
    store: CredentialStore,
    legacy_dirs: tuple[Path, ...],
) -> KeyResolution:
    env_key = env_value.strip() if isinstance(env_value, str) else ""
    if not is_placeholder_api_key(env_key):
        return KeyResolution(env_key, "environment", False, False)

    initial_entry = _read_store_entry(store)
    if initial_entry.exists and not is_placeholder_api_key(initial_entry.value):
        return KeyResolution(
            initial_entry.value, "credential_manager", True, False
        )

    legacy_key = ""
    for directory in legacy_dirs:
        candidate_key = _read_legacy_key(Path(directory) / LEGACY_KEY_FILE_NAME)
        if not is_placeholder_api_key(candidate_key):
            legacy_key = candidate_key
            break
    if not legacy_key:
        return KeyResolution("", "missing", False, False)

    session_only = KeyResolution(legacy_key, "legacy_file", False, False)
    if initial_entry.exists:
        return session_only

    try:
        with _migration_lock(store):
            if _read_store_entry(store).exists:
                return session_only
            try:
                persisted = bool(store.write(legacy_key))
            except Exception:
                persisted = False
    except Exception:
        return session_only

    return KeyResolution(
        legacy_key,
        "legacy_file",
        persisted,
        persisted,
    )
