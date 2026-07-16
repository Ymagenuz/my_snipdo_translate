from __future__ import annotations

import ctypes
import inspect
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from app_paths import LEGACY_KEY_FILE_NAME
from credential_store import (
    CRED_PERSIST_LOCAL_MACHINE,
    CREDENTIALW,
    FILE_ATTRIBUTE_REPARSE_POINT,
    LPBYTE,
    MAX_CREDENTIAL_BLOB_BYTES,
    TARGET_NAME,
    CredentialEntry,
    CredentialStoreError,
    KeyResolution,
    WindowsCredentialStore,
    _read_legacy_key,
    is_placeholder_api_key,
    resolve_api_key,
)

SYNTHETIC_ENV_KEY = "codex-test-environment-secret-not-a-real-key"
SYNTHETIC_STORED_KEY = "codex-test-stored-secret-not-a-real-key"
SYNTHETIC_LEGACY_KEY = "codex-test-legacy-secret-not-a-real-key"
SYNTHETIC_NATIVE_KEY = "codex-test-native-secret-not-a-real-key"


def synthetic_target() -> str:
    return str(uuid.uuid4())


class FakeStore:
    def __init__(
        self,
        value: str = "",
        *,
        present: bool | None = None,
        fail_write: bool = False,
        raise_write: bool = False,
        appear_on_locked_read: str | None = None,
    ):
        self.value = value
        self.present = bool(value) if present is None else present
        self.fail_write = fail_write
        self.raise_write = raise_write
        self.appear_on_locked_read = appear_on_locked_read
        self.read_calls = 0
        self.write_calls: list[str] = []
        self.write_lock_depths: list[int] = []
        self.lock_depth = 0

    def read(self) -> str:
        return self.value if self.present else ""

    def read_entry(self) -> CredentialEntry:
        self.read_calls += 1
        if (
            self.appear_on_locked_read is not None
            and self.lock_depth
            and self.read_calls >= 2
        ):
            self.value = self.appear_on_locked_read
            self.present = True
        return CredentialEntry(self.present, self.value if self.present else "")

    def write(self, secret: str) -> bool:
        self.write_calls.append(secret)
        self.write_lock_depths.append(self.lock_depth)
        if self.raise_write:
            raise CredentialStoreError("synthetic credential write failure")
        if self.fail_write:
            return False
        self.value = secret
        self.present = True
        return True

    @contextmanager
    def migration_lock(self):
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1


class FakeNativeApi:
    def __init__(self):
        self.entries: dict[str, bytes] = {}
        self.read_error: OSError | None = None
        self.write_error: OSError | None = None
        self.readback_error: OSError | None = None
        self.readback_blob: bytes | None = None
        self.write_calls: list[dict[str, object]] = []
        self.free_calls = 0
        self.mutex_calls: list[tuple[str, int]] = []
        self._keepalive: list[object] = []
        self._has_written = False

    def read(self, target_name: str):
        error = self.readback_error if self._has_written else self.read_error
        if error is not None:
            raise error
        if target_name not in self.entries:
            return None
        blob = self.entries[target_name]
        credential = CREDENTIALW()
        credential.CredentialBlobSize = len(blob)
        if blob:
            buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
            credential.CredentialBlob = ctypes.cast(buffer, LPBYTE)
        else:
            buffer = None
            credential.CredentialBlob = LPBYTE()
        pointer = ctypes.pointer(credential)
        self._keepalive.append((credential, buffer, pointer))
        return pointer

    def free(self, pointer) -> None:
        self.free_calls += 1

    def write(self, credential: CREDENTIALW) -> None:
        if self.write_error is not None:
            raise self.write_error
        size = int(credential.CredentialBlobSize)
        blob = ctypes.string_at(credential.CredentialBlob, size)
        target_name = str(credential.TargetName)
        self.write_calls.append(
            {
                "target_name": target_name,
                "blob": blob,
                "blob_size": size,
                "persist": int(credential.Persist),
                "username": str(credential.UserName),
            }
        )
        self.entries[target_name] = blob if self.readback_blob is None else self.readback_blob
        self._has_written = True

    def delete(self, target_name: str) -> bool:
        self.entries.pop(target_name, None)
        return True

    @contextmanager
    def migration_lock(self, mutex_name: str, timeout_ms: int):
        self.mutex_calls.append((mutex_name, timeout_ms))
        yield


class FakeLegacyFileApi:
    def __init__(
        self,
        contents: bytes,
        *,
        attributes: int = 0,
        fail_open: bool = False,
    ):
        self.contents = contents
        self.attributes = attributes
        self.fail_open = fail_open
        self.handle = object()
        self.info_handles: list[object] = []
        self.read_handles: list[object] = []
        self.close_handles: list[object] = []

    def open(self, path: Path):
        if self.fail_open:
            raise PermissionError("synthetic access denied")
        return self.handle

    def info(self, handle: object) -> tuple[int, int]:
        self.info_handles.append(handle)
        return self.attributes, len(self.contents)

    def read(self, handle: object, maximum_bytes: int) -> bytes:
        self.read_handles.append(handle)
        return self.contents[:maximum_bytes]

    def close(self, handle: object) -> None:
        self.close_handles.append(handle)


def legacy_file(directory: Path, contents: bytes) -> Path:
    path = directory / LEGACY_KEY_FILE_NAME
    path.write_bytes(contents)
    return path


def test_environment_key_wins_without_reading_or_persisting(
    tmp_path: Path, monkeypatch
):
    store = FakeStore(SYNTHETIC_STORED_KEY)
    synthetic_environment = {"CODEX_SYNTHETIC_EXISTING": "unchanged"}
    monkeypatch.setattr(os, "environ", synthetic_environment)

    result = resolve_api_key(SYNTHETIC_ENV_KEY, store, (tmp_path,))

    assert result == KeyResolution(SYNTHETIC_ENV_KEY, "environment", False, False)
    assert store.read_calls == 0
    assert store.write_calls == []
    assert os.environ == {"CODEX_SYNTHETIC_EXISTING": "unchanged"}


def test_stored_key_wins_over_legacy_file(tmp_path: Path):
    old = legacy_file(tmp_path, SYNTHETIC_LEGACY_KEY.encode("utf-8"))
    original = old.read_bytes()

    result = resolve_api_key("", FakeStore(SYNTHETIC_STORED_KEY), (tmp_path,))

    assert result == KeyResolution(
        SYNTHETIC_STORED_KEY, "credential_manager", True, False
    )
    assert old.read_bytes() == original


def test_legacy_key_is_copied_under_lock_and_source_remains(tmp_path: Path):
    old = legacy_file(tmp_path, f"\ufeff{SYNTHETIC_LEGACY_KEY}\r\n".encode("utf-8"))
    original = old.read_bytes()
    store = FakeStore(present=False)

    result = resolve_api_key("", store, (tmp_path,))

    assert result == KeyResolution(SYNTHETIC_LEGACY_KEY, "legacy_file", True, True)
    assert store.value == SYNTHETIC_LEGACY_KEY
    assert store.write_calls == [SYNTHETIC_LEGACY_KEY]
    assert store.write_lock_depths == [1]
    assert old.read_bytes() == original


@pytest.mark.parametrize("existing_value", ["", "your_api_key", "填这里"])
def test_existing_invalid_record_blocks_legacy_overwrite(
    tmp_path: Path, existing_value: str
):
    old = legacy_file(tmp_path, SYNTHETIC_LEGACY_KEY.encode("utf-8"))
    original = old.read_bytes()
    store = FakeStore(existing_value, present=True)

    result = resolve_api_key("", store, (tmp_path,))

    assert result == KeyResolution(SYNTHETIC_LEGACY_KEY, "legacy_file", False, False)
    assert store.value == existing_value
    assert store.write_calls == []
    assert old.read_bytes() == original


def test_record_appearing_before_locked_migration_is_not_overwritten(tmp_path: Path):
    old = legacy_file(tmp_path, SYNTHETIC_LEGACY_KEY.encode("utf-8"))
    concurrent_value = "codex-test-concurrent-entry-not-a-real-key"
    store = FakeStore(present=False, appear_on_locked_read=concurrent_value)

    result = resolve_api_key("", store, (tmp_path,))

    assert result == KeyResolution(SYNTHETIC_LEGACY_KEY, "legacy_file", False, False)
    assert store.value == concurrent_value
    assert store.write_calls == []
    assert old.exists()


@pytest.mark.parametrize("failure_mode", ["false", "exception"])
def test_failed_legacy_persistence_keeps_session_key_and_source(
    tmp_path: Path, failure_mode: str
):
    old = legacy_file(tmp_path, SYNTHETIC_LEGACY_KEY.encode("utf-8"))
    store = FakeStore(
        present=False,
        fail_write=failure_mode == "false",
        raise_write=failure_mode == "exception",
    )

    result = resolve_api_key("", store, (tmp_path,))

    assert result == KeyResolution(SYNTHETIC_LEGACY_KEY, "legacy_file", False, False)
    assert old.exists()


def test_missing_valid_key_returns_missing(tmp_path: Path):
    legacy_file(tmp_path, b"your-api-key\n")
    result = resolve_api_key("", FakeStore(present=False), (tmp_path,))
    assert result == KeyResolution("", "missing", False, False)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "YOUR_API_KEY",
        " your-api-key ",
        "your api key",
        "填这里",
        "填 这里",
        "请填这里",
        "在这里填写",
        "请在这里填写",
    ],
)
def test_known_placeholder_values_are_rejected(value: str):
    assert is_placeholder_api_key(value)


@pytest.mark.parametrize(
    "value",
    [
        "codex-test-value-containing-api_key-but-valid",
        "prefix-your_api_key-suffix",
        "your_api_key_2",
        "codex-test-填这里-near-match",
        "real-looking-test-value",
    ],
)
def test_placeholder_near_matches_remain_valid(value: str):
    assert not is_placeholder_api_key(value)


def test_key_resolution_repr_redacts_the_key():
    result = KeyResolution(SYNTHETIC_ENV_KEY, "environment", False, False)
    assert SYNTHETIC_ENV_KEY not in repr(result)
    assert "key=" not in repr(result)


def test_legacy_reader_skips_invalid_utf8_without_modifying_source(tmp_path: Path):
    old = legacy_file(tmp_path, b"codex-test-invalid-utf8-\xff\xfe")
    original = old.read_bytes()
    store = FakeStore(present=False)

    result = resolve_api_key("", store, (tmp_path,))

    assert result.source == "missing"
    assert store.write_calls == []
    assert old.read_bytes() == original


def test_legacy_reader_skips_oversized_file_without_modifying_source(tmp_path: Path):
    old = legacy_file(tmp_path, b"x" * (16 * 1024 + 1))
    original = old.read_bytes()
    store = FakeStore(present=False)

    result = resolve_api_key("", store, (tmp_path,))

    assert result.source == "missing"
    assert store.write_calls == []
    assert old.read_bytes() == original


def test_legacy_reader_rejects_reparse_handle_without_reading_after_path_race(
    tmp_path: Path,
):
    old = legacy_file(tmp_path, SYNTHETIC_LEGACY_KEY.encode("utf-8"))
    file_api = FakeLegacyFileApi(
        SYNTHETIC_LEGACY_KEY.encode("utf-8"),
        attributes=FILE_ATTRIBUTE_REPARSE_POINT,
    )

    result = _read_legacy_key(old, file_api=file_api)

    assert result == ""
    assert file_api.info_handles == [file_api.handle]
    assert file_api.read_handles == []
    assert file_api.close_handles == [file_api.handle]


def test_legacy_reader_skips_unreadable_source_without_exposing_contents(
    tmp_path: Path,
):
    old = legacy_file(tmp_path, SYNTHETIC_LEGACY_KEY.encode("utf-8"))
    file_api = FakeLegacyFileApi(
        SYNTHETIC_LEGACY_KEY.encode("utf-8"), fail_open=True
    )

    result = _read_legacy_key(old, file_api=file_api)

    assert result == ""
    assert file_api.info_handles == []
    assert file_api.read_handles == []
    assert file_api.close_handles == []


def test_native_read_distinguishes_missing_from_existing_empty_and_always_frees():
    target = synthetic_target()
    api = FakeNativeApi()
    store = WindowsCredentialStore(target, native_api=api)

    assert store.read_entry() == CredentialEntry(False, "")
    api.entries[target] = b""
    assert store.read_entry() == CredentialEntry(True, "")
    assert api.free_calls == 1


def test_native_read_decodes_utf16_and_frees_pointer():
    target = synthetic_target()
    api = FakeNativeApi()
    api.entries[target] = SYNTHETIC_NATIVE_KEY.encode("utf-16-le")
    store = WindowsCredentialStore(target, native_api=api)

    assert store.read() == SYNTHETIC_NATIVE_KEY
    assert api.free_calls == 1


def test_native_decode_failure_is_redacted_and_still_frees_pointer():
    target = synthetic_target()
    api = FakeNativeApi()
    api.entries[target] = b"\xff"
    store = WindowsCredentialStore(target, native_api=api)

    with pytest.raises(CredentialStoreError) as error:
        store.read()

    assert SYNTHETIC_NATIVE_KEY not in str(error.value)
    assert SYNTHETIC_NATIVE_KEY not in repr(error.value)
    assert api.free_calls == 1


def test_native_read_failure_is_normalized_without_leaking_error_text():
    target = synthetic_target()
    api = FakeNativeApi()
    api.read_error = OSError(f"synthetic native failure containing {SYNTHETIC_NATIVE_KEY}")
    store = WindowsCredentialStore(target, native_api=api)

    with pytest.raises(CredentialStoreError) as error:
        store.read()

    assert SYNTHETIC_NATIVE_KEY not in str(error.value)
    assert SYNTHETIC_NATIVE_KEY not in repr(error.value)


def test_native_write_uses_required_fields_exact_blob_size_and_readback():
    target = synthetic_target()
    api = FakeNativeApi()
    store = WindowsCredentialStore(target, native_api=api)

    assert store.write(f"  {SYNTHETIC_NATIVE_KEY}  ")

    assert api.write_calls == [
        {
            "target_name": target,
            "blob": SYNTHETIC_NATIVE_KEY.encode("utf-16-le"),
            "blob_size": len(SYNTHETIC_NATIVE_KEY.encode("utf-16-le")),
            "persist": CRED_PERSIST_LOCAL_MACHINE,
            "username": "SnipDoTranslate",
        }
    ]
    assert api.free_calls == 1


@pytest.mark.parametrize(
    "invalid_secret",
    ["", "   ", "synthetic\x00secret", "synthetic\nsecret", "synthetic\x1fsecret"],
)
def test_native_write_rejects_empty_nul_and_control_values_before_api_call(
    invalid_secret: str,
):
    api = FakeNativeApi()
    store = WindowsCredentialStore(synthetic_target(), native_api=api)

    assert store.write(invalid_secret) is False
    assert api.write_calls == []


def test_native_write_enforces_2560_byte_utf16_limit_before_api_call():
    target = synthetic_target()
    api = FakeNativeApi()
    store = WindowsCredentialStore(target, native_api=api)

    assert store.write("x" * (MAX_CREDENTIAL_BLOB_BYTES // 2)) is True
    calls_at_limit = len(api.write_calls)
    assert store.write("x" * (MAX_CREDENTIAL_BLOB_BYTES // 2 + 1)) is False
    assert len(api.write_calls) == calls_at_limit


def test_native_write_failure_is_normalized_and_redacted():
    api = FakeNativeApi()
    api.write_error = OSError(
        f"synthetic native write failure containing {SYNTHETIC_NATIVE_KEY}"
    )
    store = WindowsCredentialStore(synthetic_target(), native_api=api)

    with pytest.raises(CredentialStoreError) as error:
        store.write(SYNTHETIC_NATIVE_KEY)

    assert SYNTHETIC_NATIVE_KEY not in str(error.value)
    assert SYNTHETIC_NATIVE_KEY not in repr(error.value)


def test_native_readback_mismatch_is_a_redacted_store_error():
    api = FakeNativeApi()
    api.readback_blob = "codex-test-different-readback".encode("utf-16-le")
    store = WindowsCredentialStore(synthetic_target(), native_api=api)

    with pytest.raises(CredentialStoreError) as error:
        store.write(SYNTHETIC_NATIVE_KEY)

    assert SYNTHETIC_NATIVE_KEY not in str(error.value)
    assert SYNTHETIC_NATIVE_KEY not in repr(error.value)


def test_native_readback_failure_is_normalized_and_redacted():
    api = FakeNativeApi()
    api.readback_error = OSError(
        f"synthetic readback failure containing {SYNTHETIC_NATIVE_KEY}"
    )
    store = WindowsCredentialStore(synthetic_target(), native_api=api)

    with pytest.raises(CredentialStoreError) as error:
        store.write(SYNTHETIC_NATIVE_KEY)

    assert SYNTHETIC_NATIVE_KEY not in str(error.value)
    assert SYNTHETIC_NATIVE_KEY not in repr(error.value)


def test_store_passes_injected_synthetic_mutex_name_and_timeout():
    api = FakeNativeApi()
    mutex_name = f"Local\\SnipDoTranslate.Test.{uuid.uuid4()}"
    store = WindowsCredentialStore(
        synthetic_target(),
        native_api=api,
        migration_mutex_name=mutex_name,
        migration_lock_timeout_ms=1234,
    )

    with store.migration_lock():
        pass

    assert api.mutex_calls == [(mutex_name, 1234)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex only")
def test_windows_mutex_excludes_synthetic_subprocess_and_releases():
    mutex_name = f"Local\\SnipDoTranslate.Test.{uuid.uuid4()}"
    child_code = """
import sys
from credential_store import _WindowsCredentialApi

api = _WindowsCredentialApi()
with api.migration_lock(sys.argv[1], 5000):
    sys.stdout.write("locked\\n")
    sys.stdout.flush()
    sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, mutex_name],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == "locked\n"
        store = WindowsCredentialStore(
            synthetic_target(),
            migration_mutex_name=mutex_name,
            migration_lock_timeout_ms=50,
        )
        with pytest.raises(TimeoutError):
            with store.migration_lock():
                pass
    finally:
        if process.poll() is None and process.stdin is not None:
            process.stdin.write("x")
            process.stdin.flush()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    with store.migration_lock():
        pass


def test_production_target_and_persistence_contract_are_exact():
    parameter = inspect.signature(WindowsCredentialStore).parameters["target_name"]
    assert TARGET_NAME == "SnipDoTranslate/GPTSAPI"
    assert parameter.default == TARGET_NAME
    assert MAX_CREDENTIAL_BLOB_BYTES == 2560


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager only")
def test_windows_credential_contract_uses_uuid_only_synthetic_target():
    target = synthetic_target()
    assert str(uuid.UUID(target)) == target
    assert target != TARGET_NAME
    store = WindowsCredentialStore(target)
    try:
        assert store.write(SYNTHETIC_NATIVE_KEY)
        assert store.read() == SYNTHETIC_NATIVE_KEY
    finally:
        cleanup_succeeded = store.delete()
        assert cleanup_succeeded
        assert store.read_entry() == CredentialEntry(False, "")
