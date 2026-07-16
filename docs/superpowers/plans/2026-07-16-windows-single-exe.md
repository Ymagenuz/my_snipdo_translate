# Windows Single-File EXE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a single `SnipDoTranslate.exe` for Windows 10/11 x64 that needs no target-machine Python installation and safely preserves SnipDo, translation, dictionary, OCR, history, and single-instance behavior.

**Architecture:** Extract launch parsing, runtime paths/history, Windows credentials, privacy logging, and single-instance IPC into focused modules while keeping the existing PyQt6 UI and API business logic in `gemini_translate.pyw`. Use a user-session mutex plus length-framed JSON/ACK IPC, store history under LocalAppData and API keys in Windows Credential Manager, then validate an explicit PyInstaller spec first as onedir and finally as onefile.

**Tech Stack:** Python 3.11.9, PyQt6 6.11.0 / Qt 6.11.1, openai 2.37.0, Windows `ctypes`, pytest, PyInstaller 6.x, Pillow, PowerShell.

**Approved Design:** `docs/superpowers/specs/2026-07-16-windows-single-exe-design.md`. The runtime modules are interdependent through one startup and packaging contract, so they remain one plan with independently reviewable tasks rather than separate plans.

## Global Constraints

- Target only Windows 10/11 x64; output name is exactly `SnipDoTranslate.exe`.
- The final target machine must not need Python, PyQt6, OpenAI SDK, or `pip install`.
- Preserve the existing API endpoint, `gpt-5.4-nano` model, prompts, UI, translation, dictionary, and OCR behavior.
- New writable data root is exactly `%LOCALAPPDATA%\SnipDoTranslate`.
- Windows Credential Manager target name is exactly `SnipDoTranslate/GPTSAPI`.
- Legacy API Key and history migration is copy-only: never delete the old files and never overwrite new data.
- `--file` and `--image` retain inputs by default; deletion requires explicit `--delete-after` and a successful handoff/load.
- IPC frames use a 4-byte big-endian length, UTF-8 JSON, a 16 MiB maximum, request IDs, ACKs, and a 5-second total client deadline.
- Logs rotate at 1 MiB with 3 backups and never contain source text, translations, image data, or API keys.
- Automated tests are offline and must not read the real local API Key or call the external API.
- The PyInstaller spec must not bundle `.gptsapi_api_key`, history, logs, `.venv`, `legacy`, `diagnostics`, Git data, or editor data.
- Remove the invalid hard-coded legacy credential from the current tree; do not rewrite Git history.
- Do not add an installer, portable mode, non-Windows support, Authenticode signing, or unrelated UI refactors.
- Implement each production behavior test-first, run the focused test red then green, and commit after every task.

## File Map

**Create:**

- `app_cli.py` — CLI parsing, typed launch commands, structured app requests, and post-ACK deletion.
- `app_paths.py` — bundle/user paths, legacy discovery, history validation/migration, and atomic writes.
- `credential_store.py` — Windows Credential Manager adapter and API Key resolution/migration.
- `app_logging.py` — rotating privacy-safe application logging.
- `single_instance.py` — mutex ownership, frame codec, QLocalServer, QLocalSocket client, and ACK handling.
- `self_test.py` — packaged-runtime self-test and JSON report.
- `tests/conftest.py` — isolated Windows/Qt test environment.
- `tests/test_app_cli.py` — CLI and deletion contract tests.
- `tests/test_app_paths.py` — path, history, and migration tests.
- `tests/test_credential_store.py` — key precedence/migration and native-adapter contract tests.
- `tests/test_app_logging.py` — privacy and rotation tests.
- `tests/test_single_instance.py` — frame, ACK, message size, and mutex tests.
- `tests/test_main_integration.py` — UI request adaptation and import/startup regression tests.
- `tests/test_self_test.py` — packaged self-test report tests.
- `tools/create_icon.py` — reproducible multi-size ICO conversion.
- `tools/verify_artifact.py` — archive-member and embedded-secret safety check.
- `SnipDoTranslate.spec` — parametrized onedir/onefile PyInstaller build.
- `build_exe.ps1` — test, build, self-test, smoke-test, scan, hash, and report pipeline.
- `requirements-build.txt` — constrained build/test dependencies, replaced with exact verified pins after installation.

**Modify:**

- `gemini_translate.pyw:1-124` — imports, global paths/key initialization, and logger.
- `gemini_translate.pyw:691-731` — remove legacy unframed IPC sender.
- `gemini_translate.pyw:1119-1170` — remove legacy `SingleInstanceServer`.
- `gemini_translate.pyw:1310-1386` — inject paths/store and use bundled icon path.
- `gemini_translate.pyw:2087-2105` — delegate history load/save.
- `gemini_translate.pyw:2210-2234` — save manually entered keys to Credential Manager.
- `gemini_translate.pyw:2256-2274` — return image-load success and never delete in UI code.
- `gemini_translate.pyw:3073-3119` — consume structured `AppRequest` and return an ACK.
- `gemini_translate.pyw:3166-3174` and other payload log sites — remove raw text logging.
- `gemini_translate.pyw:3283-3356` — replace startup flow.
- `snipdo_script_powershell_code/snipdo_gemini.txt` — call the EXE directly and use explicit deletion.
- `start_gemini_translate.cmd` — prefer the built EXE while retaining a source-development fallback.
- `legacy/gemini_translate_textBox.pyw:16` — remove the invalid hard-coded key.
- `README.md` — document EXE use, CLI contract, data/key locations, build, and limitations.
- `requirements.txt` — exact verified runtime pins.
- `.gitignore` — generated build, report, cache, and test artifacts.

---

### Task 1: Typed CLI Requests and Safe Deletion

**Files:**
- Create: `app_cli.py`
- Create: `tests/conftest.py`
- Create: `tests/test_app_cli.py`

**Interfaces:**
- Produces: `CliError`, `LaunchCommand`, `AppRequest`, `PreparedRequest`, `parse_cli(argv)`, `prepare_request(command)`, `delete_acknowledged_source(prepared, accepted)`.
- `single_instance.py` and `gemini_translate.pyw` will consume `AppRequest.to_dict()` and `AppRequest.from_dict()`.

- [ ] **Step 1: Create the isolated test environment and failing CLI tests**

```python
# tests/conftest.py
import os
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("GPTSAPI_API_KEY", None)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["pytest"])


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
```

```python
# tests/test_app_cli.py
from pathlib import Path

import pytest

from app_cli import (
    CliError,
    delete_acknowledged_source,
    parse_cli,
    prepare_request,
)


def test_no_arguments_means_show():
    assert parse_cli([]).action == "show"


def test_direct_unicode_text_is_structured_request():
    prepared = prepare_request(parse_cli(["hello", "世界"]))
    assert prepared.request.action == "translate_text"
    assert prepared.request.payload == {"text": "hello 世界"}
    assert prepared.delete_path is None


def test_file_is_read_but_retained_without_delete_after(tmp_path: Path):
    source = tmp_path / "含 空格.txt"
    source.write_text("hello 世界", encoding="utf-8")
    prepared = prepare_request(parse_cli(["--file", str(source)]))
    assert prepared.request.payload == {"text": "hello 世界"}
    assert prepared.delete_path is None
    assert source.exists()


def test_file_deletes_only_after_accepted_ack(tmp_path: Path):
    source = tmp_path / "input.txt"
    source.write_text("hello", encoding="utf-8")
    prepared = prepare_request(parse_cli(["--file", str(source), "--delete-after"]))
    assert delete_acknowledged_source(prepared, accepted=False) is False
    assert source.exists()
    assert delete_acknowledged_source(prepared, accepted=True) is True
    assert not source.exists()


def test_image_request_keeps_path_for_primary_loader(tmp_path: Path):
    image = tmp_path / "截图.png"
    image.write_bytes(b"not-decoded-by-cli")
    prepared = prepare_request(parse_cli(["--image", str(image), "--delete-after"]))
    assert prepared.request.action == "ocr_image"
    assert prepared.request.payload == {"path": str(image.resolve())}
    assert prepared.delete_path == image.resolve()


@pytest.mark.parametrize(
    "argv",
    [
        ["--delete-after"],
        ["--show", "unexpected"],
        ["--file"],
        ["--image"],
        ["--unknown"],
    ],
)
def test_invalid_cli_raises_domain_error(argv):
    with pytest.raises(CliError):
        parse_cli(argv)


def test_request_json_round_trip():
    request = prepare_request(parse_cli(["hello"])).request
    restored = type(request).from_dict(request.to_dict())
    assert restored == request
```

- [ ] **Step 2: Run the tests and verify the intended red state**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_app_cli.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app_cli'`.

- [ ] **Step 3: Implement the minimal complete CLI module**

```python
# app_cli.py
from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Action = Literal["show", "translate_text", "ocr_image"]
CommandAction = Literal["show", "text", "file", "image", "self_test"]


class CliError(ValueError):
    pass


class RaisingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(message)


@dataclass(frozen=True)
class LaunchCommand:
    action: CommandAction
    value: str = ""
    delete_after: bool = False


@dataclass(frozen=True)
class AppRequest:
    action: Action
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.request_id,
            "action": self.action,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AppRequest":
        if value.get("version") != 1:
            raise CliError("unsupported request version")
        action = value.get("action")
        if action not in {"show", "translate_text", "ocr_image"}:
            raise CliError("unsupported request action")
        payload = value.get("payload")
        if not isinstance(payload, dict):
            raise CliError("request payload must be an object")
        request_id = value.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise CliError("request id is required")
        return cls(action=action, payload=payload, request_id=request_id)


@dataclass(frozen=True)
class PreparedRequest:
    request: AppRequest
    delete_path: Path | None = None


def parse_cli(argv: list[str]) -> LaunchCommand:
    parser = RaisingArgumentParser(prog="SnipDoTranslate", add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true")
    group.add_argument("--file")
    group.add_argument("--image")
    group.add_argument("--self-test")
    parser.add_argument("--delete-after", action="store_true")
    parser.add_argument("text", nargs="*")
    args = parser.parse_args(argv)

    selected = args.show or args.file is not None or args.image is not None or args.self_test is not None
    if selected and args.text:
        raise CliError("text cannot be combined with an option action")
    if args.delete_after and args.file is None and args.image is None:
        raise CliError("--delete-after requires --file or --image")
    if args.self_test is not None:
        return LaunchCommand("self_test", args.self_test)
    if args.file is not None:
        return LaunchCommand("file", args.file, args.delete_after)
    if args.image is not None:
        return LaunchCommand("image", args.image, args.delete_after)
    if args.show:
        return LaunchCommand("show")
    if args.text:
        return LaunchCommand("text", " ".join(args.text).strip())
    return LaunchCommand("show")


def prepare_request(command: LaunchCommand) -> PreparedRequest:
    if command.action == "show":
        return PreparedRequest(AppRequest("show"))
    if command.action == "text":
        if not command.value:
            raise CliError("text is empty")
        return PreparedRequest(AppRequest("translate_text", {"text": command.value}))
    if command.action == "file":
        path = Path(command.value).expanduser().resolve()
        if not path.is_file():
            raise CliError(f"file does not exist: {path}")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise CliError(f"file is empty: {path}")
        return PreparedRequest(
            AppRequest("translate_text", {"text": text}),
            path if command.delete_after else None,
        )
    if command.action == "image":
        path = Path(command.value).expanduser().resolve()
        if not path.is_file():
            raise CliError(f"image does not exist: {path}")
        return PreparedRequest(
            AppRequest("ocr_image", {"path": str(path)}),
            path if command.delete_after else None,
        )
    raise CliError("self-test commands are not app requests")


def delete_acknowledged_source(prepared: PreparedRequest, accepted: bool) -> bool:
    if not accepted or prepared.delete_path is None:
        return False
    prepared.delete_path.unlink(missing_ok=True)
    return True
```

- [ ] **Step 4: Run the focused tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_app_cli.py -q`

Expected: `11 passed` (the parametrized invalid cases count individually).

- [ ] **Step 5: Commit Task 1**

```powershell
git add app_cli.py tests/conftest.py tests/test_app_cli.py
git commit -m "feat: add typed CLI request handling"
```

---

### Task 2: Runtime Paths, History, and Copy-Only Migration

**Files:**
- Create: `app_paths.py`
- Create: `tests/test_app_paths.py`

**Interfaces:**
- Produces: `AppPaths`, `HistoryDataError`, `resolve_app_paths(...)`, `ensure_app_directories(paths)`, `load_history(path)`, `save_history_atomic(path, entries)`, `migrate_legacy_history(paths)`.
- `credential_store.py` consumes `AppPaths.legacy_dirs`; `gemini_translate.pyw` consumes `history_path`, `log_dir`, and `icon_path`.

- [ ] **Step 1: Write failing path and history tests**

```python
# tests/test_app_paths.py
import json
from pathlib import Path

import pytest

from app_paths import (
    HistoryDataError,
    load_history,
    migrate_legacy_history,
    resolve_app_paths,
    save_history_atomic,
)


def valid_entry(index: int) -> dict[str, str]:
    return {
        "time": f"2026-07-16 12:00:{index:02d}",
        "mode": "auto",
        "source": f"source-{index}",
        "result": f"result-{index}",
    }


def test_source_paths_separate_bundle_and_user_data(tmp_path: Path):
    source = tmp_path / "project" / "gemini_translate.pyw"
    source.parent.mkdir()
    source.write_text("", encoding="utf-8")
    local = tmp_path / "LocalAppData"
    paths = resolve_app_paths(source, source, False, local)
    assert paths.bundle_dir == source.parent.resolve()
    assert paths.data_dir == local / "SnipDoTranslate"
    assert paths.icon_path == source.parent / "snipdo_script_logo" / "gemini-color.png"
    assert paths.legacy_dirs == (source.parent.resolve(),)


def test_frozen_dist_build_discovers_project_parent(tmp_path: Path):
    project = tmp_path / "project"
    dist = project / "dist"
    dist.mkdir(parents=True)
    (project / "gemini_translate.pyw").write_text("", encoding="utf-8")
    executable = dist / "SnipDoTranslate.exe"
    paths = resolve_app_paths(
        tmp_path / "_MEI123" / "gemini_translate.pyw",
        executable,
        True,
        tmp_path / "LocalAppData",
    )
    assert paths.legacy_dirs == (dist.resolve(), project.resolve())


def test_atomic_history_round_trip_caps_at_50(tmp_path: Path):
    path = tmp_path / "translation_history.json"
    save_history_atomic(path, [valid_entry(i) for i in range(60)])
    assert len(load_history(path)) == 50
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_history_raises_and_is_not_replaced(tmp_path: Path):
    path = tmp_path / "translation_history.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(HistoryDataError):
        load_history(path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_migration_copies_once_without_deleting_or_overwriting(tmp_path: Path):
    legacy = tmp_path / "legacy"
    data = tmp_path / "LocalAppData" / "SnipDoTranslate"
    legacy.mkdir()
    old = legacy / "translation_history.json"
    old.write_text(json.dumps([valid_entry(1)]), encoding="utf-8")
    paths = resolve_app_paths(legacy / "gemini_translate.pyw", legacy / "pythonw.exe", False, tmp_path / "LocalAppData")
    assert migrate_legacy_history(paths) is True
    assert old.exists()
    assert load_history(paths.history_path) == [valid_entry(1)]
    paths.history_path.write_text(json.dumps([valid_entry(2)]), encoding="utf-8")
    assert migrate_legacy_history(paths) is False
    assert load_history(paths.history_path) == [valid_entry(2)]
```

- [ ] **Step 2: Verify the path tests fail before implementation**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_app_paths.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app_paths'`.

- [ ] **Step 3: Implement path resolution, validation, and atomic writes**

```python
# app_paths.py
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_HISTORY_ITEMS = 50
HISTORY_FILE_NAME = "translation_history.json"
LEGACY_KEY_FILE_NAME = ".gptsapi_api_key"
REQUIRED_HISTORY_FIELDS = ("time", "mode", "source", "result")


class HistoryDataError(ValueError):
    pass


@dataclass(frozen=True)
class AppPaths:
    bundle_dir: Path
    executable_dir: Path
    data_dir: Path
    history_path: Path
    log_dir: Path
    icon_path: Path
    legacy_dirs: tuple[Path, ...]


def resolve_app_paths(
    module_file: str | Path,
    executable: str | Path | None = None,
    frozen: bool | None = None,
    local_app_data: str | Path | None = None,
) -> AppPaths:
    module_path = Path(module_file).resolve()
    executable_path = Path(executable or sys.executable).resolve()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    local_root_value = local_app_data or os.environ.get("LOCALAPPDATA")
    if not local_root_value:
        raise RuntimeError("LOCALAPPDATA is not available")
    local_root = Path(local_root_value)
    bundle_dir = module_path.parent
    executable_dir = executable_path.parent
    legacy_dirs: list[Path] = [executable_dir if is_frozen else bundle_dir]
    project_parent = executable_dir.parent
    if is_frozen and (project_parent / "gemini_translate.pyw").is_file():
        legacy_dirs.append(project_parent)
    unique_legacy = tuple(dict.fromkeys(path.resolve() for path in legacy_dirs))
    data_dir = local_root / "SnipDoTranslate"
    return AppPaths(
        bundle_dir=bundle_dir,
        executable_dir=executable_dir,
        data_dir=data_dir,
        history_path=data_dir / HISTORY_FILE_NAME,
        log_dir=data_dir / "logs",
        icon_path=bundle_dir / "snipdo_script_logo" / "gemini-color.png",
        legacy_dirs=unique_legacy,
    )


def ensure_app_directories(paths: AppPaths) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)


def normalize_history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise HistoryDataError("history root must be a list")
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if not all(isinstance(item.get(field), str) for field in REQUIRED_HISTORY_FIELDS):
            continue
        normalized.append({field: item[field] for field in REQUIRED_HISTORY_FIELDS})
        if len(normalized) == MAX_HISTORY_ITEMS:
            break
    return normalized


def load_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        return normalize_history(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, HistoryDataError) as exc:
        raise HistoryDataError(str(exc)) from exc


def save_history_atomic(path: Path, entries: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_history(list(entries))
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def migrate_legacy_history(paths: AppPaths) -> bool:
    if paths.history_path.exists():
        return False
    for directory in paths.legacy_dirs:
        candidate = directory / HISTORY_FILE_NAME
        if candidate.is_file() and candidate.resolve() != paths.history_path.resolve():
            entries = load_history(candidate)
            save_history_atomic(paths.history_path, entries)
            return True
    return False
```

- [ ] **Step 4: Run focused path/history tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_app_paths.py -q`

Expected: `5 passed`.

- [ ] **Step 5: Commit Task 2**

```powershell
git add app_paths.py tests/test_app_paths.py
git commit -m "feat: add persistent app data migration"
```

---

### Task 3: Windows Credential Manager and Key Migration

**Files:**
- Create: `credential_store.py`
- Create: `tests/test_credential_store.py`

**Interfaces:**
- Consumes: `AppPaths.legacy_dirs` and `LEGACY_KEY_FILE_NAME` from `app_paths.py`.
- Produces: `CredentialStoreError`, `WindowsCredentialStore.read()`, `WindowsCredentialStore.write(secret)`, `KeyResolution`, `is_placeholder_api_key(value)`, `resolve_api_key(env_value, store, legacy_dirs)`.

- [ ] **Step 1: Write failing credential precedence and migration tests**

```python
# tests/test_credential_store.py
from pathlib import Path

from credential_store import KeyResolution, is_placeholder_api_key, resolve_api_key


class FakeStore:
    def __init__(self, value: str = "", fail_write: bool = False):
        self.value = value
        self.fail_write = fail_write

    def read(self) -> str:
        return self.value

    def write(self, secret: str) -> bool:
        if self.fail_write:
            return False
        self.value = secret
        return True


def test_environment_key_wins_and_is_not_persisted(tmp_path: Path):
    store = FakeStore("stored-key")
    result = resolve_api_key("environment-key", store, (tmp_path,))
    assert result == KeyResolution("environment-key", "environment", False, False)
    assert store.value == "stored-key"


def test_stored_key_wins_over_legacy_file(tmp_path: Path):
    (tmp_path / ".gptsapi_api_key").write_text("legacy-key", encoding="utf-8")
    result = resolve_api_key("", FakeStore("stored-key"), (tmp_path,))
    assert result.key == "stored-key"
    assert result.source == "credential_manager"
    assert result.migrated is False


def test_legacy_key_is_copied_and_old_file_remains(tmp_path: Path):
    old = tmp_path / ".gptsapi_api_key"
    old.write_text("legacy-key\n", encoding="utf-8")
    store = FakeStore()
    result = resolve_api_key("", store, (tmp_path,))
    assert result == KeyResolution("legacy-key", "legacy_file", True, True)
    assert store.value == "legacy-key"
    assert old.exists()


def test_failed_legacy_persistence_keeps_session_key_and_old_file(tmp_path: Path):
    old = tmp_path / ".gptsapi_api_key"
    old.write_text("legacy-key", encoding="utf-8")
    result = resolve_api_key("", FakeStore(fail_write=True), (tmp_path,))
    assert result == KeyResolution("legacy-key", "legacy_file", False, False)
    assert old.exists()


def test_placeholder_values_are_rejected():
    assert is_placeholder_api_key("")
    assert is_placeholder_api_key("your_api_key")
    assert not is_placeholder_api_key("real-looking-test-value")
```

- [ ] **Step 2: Verify credential tests fail before implementation**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_credential_store.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'credential_store'`.

- [ ] **Step 3: Implement the native adapter and resolution logic**

Create `credential_store.py` with these exact public types and behavior:

```python
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app_paths import LEGACY_KEY_FILE_NAME

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
TARGET_NAME = "SnipDoTranslate/GPTSAPI"
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


class CredentialStoreError(OSError):
    pass


class CredentialStore(Protocol):
    def read(self) -> str: ...
    def write(self, secret: str) -> bool: ...


class WindowsCredentialStore:
    def __init__(self, target_name: str = TARGET_NAME):
        self.target_name = target_name
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._cred_read = self._advapi32.CredReadW
        self._cred_read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
        self._cred_read.restype = wintypes.BOOL
        self._cred_write = self._advapi32.CredWriteW
        self._cred_write.argtypes = [PCREDENTIALW, wintypes.DWORD]
        self._cred_write.restype = wintypes.BOOL
        self._cred_free = self._advapi32.CredFree
        self._cred_free.argtypes = [wintypes.LPVOID]
        self._cred_free.restype = None
        self._cred_delete = self._advapi32.CredDeleteW
        self._cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._cred_delete.restype = wintypes.BOOL

    def read(self) -> str:
        pointer = PCREDENTIALW()
        if not self._cred_read(self.target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return ""
            raise CredentialStoreError(error, "CredReadW failed")
        try:
            credential = pointer.contents
            blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return blob.decode("utf-16-le").strip()
        finally:
            self._cred_free(pointer)

    def write(self, secret: str) -> bool:
        secret = secret.strip()
        if not secret:
            return False
        blob = secret.encode("utf-16-le")
        buffer = (wintypes.BYTE * len(blob)).from_buffer_copy(blob)
        credential = CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = self.target_name
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, LPBYTE)
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "SnipDoTranslate"
        if not self._cred_write(ctypes.byref(credential), 0):
            return False
        return self.read() == secret

    def delete(self) -> bool:
        if self._cred_delete(self.target_name, CRED_TYPE_GENERIC, 0):
            return True
        return ctypes.get_last_error() == ERROR_NOT_FOUND


@dataclass(frozen=True)
class KeyResolution:
    key: str
    source: str
    persisted: bool
    migrated: bool


def is_placeholder_api_key(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return not lowered or "填这里" in lowered or "your_" in lowered or "your-" in lowered or "api_key" in lowered


def resolve_api_key(env_value: str, store: CredentialStore, legacy_dirs: tuple[Path, ...]) -> KeyResolution:
    env_key = (env_value or "").strip()
    if not is_placeholder_api_key(env_key):
        return KeyResolution(env_key, "environment", False, False)
    stored = store.read().strip()
    if not is_placeholder_api_key(stored):
        return KeyResolution(stored, "credential_manager", True, False)
    for directory in legacy_dirs:
        candidate = directory / LEGACY_KEY_FILE_NAME
        if not candidate.is_file():
            continue
        legacy_key = candidate.read_text(encoding="utf-8").strip()
        if is_placeholder_api_key(legacy_key):
            continue
        persisted = store.write(legacy_key)
        return KeyResolution(legacy_key, "legacy_file", persisted, persisted)
    return KeyResolution("", "missing", False, False)
```

Add this Windows-only contract test; it never uses the application target or local real key:

```python
import sys
import uuid

import pytest

from credential_store import WindowsCredentialStore


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager only")
def test_windows_credential_contract_uses_synthetic_target():
    store = WindowsCredentialStore(f"SnipDoTranslate/Test/{uuid.uuid4()}")
    try:
        assert store.write("codex-self-test-not-a-real-key")
        assert store.read() == "codex-self-test-not-a-real-key"
    finally:
        assert store.delete()
```

- [ ] **Step 4: Run credential tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_credential_store.py -q`

Expected: all tests pass; the Windows synthetic contract test also passes and leaves no test credential.

- [ ] **Step 5: Commit Task 3**

```powershell
git add credential_store.py tests/test_credential_store.py
git commit -m "feat: store API keys in Windows credentials"
```

---

### Task 4: Privacy-Safe Rotating Logging

**Files:**
- Create: `app_logging.py`
- Create: `tests/test_app_logging.py`

**Interfaces:**
- Consumes: `AppPaths.log_dir`.
- Produces: `configure_logging(log_dir)`, `log_event(event, **fields)`, `get_logger()`.
- Allowed structured fields are exactly `request_id`, `action`, `chars`, `bytes`, `status`, `duration_ms`, `attempt`, `error_type`, and `pid`; event names must be static literals.

- [ ] **Step 1: Write failing privacy and rotation tests**

```python
# tests/test_app_logging.py
from pathlib import Path

import pytest

from app_logging import configure_logging, log_event


def test_logger_rejects_payload_fields_and_never_writes_secret(tmp_path: Path):
    configure_logging(tmp_path)
    with pytest.raises(ValueError):
        log_event("translate", text="private source text")
    log_event("translate", action="translate_text", chars=19, status="accepted")
    content = (tmp_path / "gemini_translate.log").read_text(encoding="utf-8")
    assert "private source text" not in content
    assert "chars=19" in content


def test_logger_rotates_at_configured_size(tmp_path: Path):
    configure_logging(tmp_path, max_bytes=180, backup_count=3)
    for index in range(40):
        log_event("probe", attempt=index)
    assert (tmp_path / "gemini_translate.log.1").exists()
```

- [ ] **Step 2: Verify logging tests fail before implementation**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_app_logging.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app_logging'`.

- [ ] **Step 3: Implement the whitelist-based rotating logger**

```python
# app_logging.py
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

ALLOWED_FIELDS = {
    "request_id", "action", "chars", "bytes", "status", "duration_ms",
    "attempt", "error_type", "pid",
}
_logger = logging.getLogger("SnipDoTranslate")
_logger.addHandler(logging.NullHandler())
_logger.propagate = False


def configure_logging(log_dir: Path, max_bytes: int = 1024 * 1024, backup_count: int = 3) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    for handler in list(_logger.handlers):
        handler.close()
        _logger.removeHandler(handler)
    handler = RotatingFileHandler(
        log_dir / "gemini_translate.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


def get_logger() -> logging.Logger:
    return _logger


def log_event(event: str, **fields: object) -> None:
    unexpected = set(fields) - ALLOWED_FIELDS
    if unexpected:
        raise ValueError(f"unsafe log fields: {sorted(unexpected)}")
    suffix = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
    _logger.info("%s%s", event, f" {suffix}" if suffix else "")
```

- [ ] **Step 4: Run logging tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_app_logging.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit Task 4**

```powershell
git add app_logging.py tests/test_app_logging.py
git commit -m "feat: add privacy-safe rotating logs"
```

---

### Task 5: Mutex and Framed JSON/ACK IPC

**Files:**
- Create: `single_instance.py`
- Create: `tests/test_single_instance.py`

**Interfaces:**
- Consumes: `AppRequest` from `app_cli.py`, `log_event` from `app_logging.py`.
- Produces: `Ack`, `encode_frame(payload)`, `FrameDecoder.feed(data)`, `InstanceMutex.acquire()`, `SingleInstanceServer.set_request_handler(handler)`, `send_request(request, server_name, deadline_ms=5000)`.
- Request handlers have signature `Callable[[AppRequest], Ack]` and run synchronously on the Qt main thread before the ACK is written.

- [ ] **Step 1: Write failing pure protocol and mutex tests**

```python
# tests/test_single_instance.py
import json
import struct

import pytest

from app_cli import AppRequest
from single_instance import Ack, FrameDecoder, FrameError, MAX_FRAME_BYTES, encode_frame


def test_fragmented_unicode_frame_decodes_only_when_complete():
    request = AppRequest("translate_text", {"text": "长文本🙂"})
    frame = encode_frame(request.to_dict())
    decoder = FrameDecoder()
    assert decoder.feed(frame[:3]) == []
    assert decoder.feed(frame[3:9]) == []
    assert decoder.feed(frame[9:]) == [request.to_dict()]


def test_multiple_frames_decode_in_order():
    first = {"version": 1, "id": "one", "status": "accepted", "message": ""}
    second = {"version": 1, "id": "two", "status": "error", "message": "bad request"}
    assert FrameDecoder().feed(encode_frame(first) + encode_frame(second)) == [first, second]


def test_oversized_frame_is_rejected_before_body_allocation():
    decoder = FrameDecoder()
    with pytest.raises(FrameError):
        decoder.feed(struct.pack(">I", MAX_FRAME_BYTES + 1))


def test_ack_round_trip_requires_matching_id():
    ack = Ack("request-id", "accepted", "")
    assert Ack.from_dict(ack.to_dict()) == ack
    with pytest.raises(FrameError):
        Ack.from_dict({"version": 1, "id": "", "status": "accepted", "message": ""})


def test_invalid_json_frame_is_rejected():
    body = b"not-json"
    with pytest.raises(FrameError):
        FrameDecoder().feed(struct.pack(">I", len(body)) + body)
```

Add this Windows mutex test:

```python
import sys
import uuid

from single_instance import InstanceMutex


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex only")
def test_unique_mutex_has_exactly_one_primary():
    name = rf"Local\SnipDoTranslate-Test-{uuid.uuid4()}"
    first = InstanceMutex(name)
    second = InstanceMutex(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.close()
        first.close()
```

- [ ] **Step 2: Run focused protocol tests red**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_single_instance.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'single_instance'`.

- [ ] **Step 3: Implement the pure frame and ACK layer first**

```python
# single_instance.py (protocol portion)
from __future__ import annotations

import ctypes
import json
import struct
import time
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from app_cli import AppRequest
from app_logging import log_event

MAX_FRAME_BYTES = 16 * 1024 * 1024
DEFAULT_SERVER_NAME = "SnipDoTranslate-v2"
DEFAULT_MUTEX_NAME = r"Local\SnipDoTranslate-v2"
ERROR_ALREADY_EXISTS = 183


class FrameError(ValueError):
    pass


@dataclass(frozen=True)
class Ack:
    request_id: str
    status: str
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    def to_dict(self) -> dict[str, object]:
        return {"version": 1, "id": self.request_id, "status": self.status, "message": self.message}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Ack":
        if value.get("version") != 1 or value.get("status") not in {"accepted", "error"}:
            raise FrameError("invalid ACK")
        request_id = value.get("id")
        message = value.get("message", "")
        if not isinstance(request_id, str) or not request_id or not isinstance(message, str):
            raise FrameError("invalid ACK fields")
        return cls(request_id, str(value["status"]), message)


def encode_frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise FrameError("message exceeds 16 MiB")
    return struct.pack(">I", len(body)) + body


class FrameDecoder:
    def __init__(self):
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, object]]:
        self._buffer.extend(data)
        messages: list[dict[str, object]] = []
        while len(self._buffer) >= 4:
            length = struct.unpack(">I", self._buffer[:4])[0]
            if length > MAX_FRAME_BYTES:
                raise FrameError("message exceeds 16 MiB")
            if len(self._buffer) < 4 + length:
                break
            body = bytes(self._buffer[4:4 + length])
            del self._buffer[:4 + length]
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise FrameError("invalid JSON frame") from exc
            if not isinstance(value, dict):
                raise FrameError("frame root must be an object")
            messages.append(value)
        return messages
```

- [ ] **Step 4: Implement and test Windows ownership and Qt transport**

Complete the same module with:

```python
class InstanceMutex:
    def __init__(self, name: str = DEFAULT_MUTEX_NAME):
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self.handle)
            self.handle = None


class SingleInstanceServer(QObject):
    def __init__(self, server_name: str = DEFAULT_SERVER_NAME):
        super().__init__()
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._handler: Callable[[AppRequest], Ack] | None = None
        self._pending: list[tuple[QLocalSocket, AppRequest]] = []
        self._decoders: dict[QLocalSocket, FrameDecoder] = {}
        QLocalServer.removeServer(server_name)
        if not self.server.listen(server_name):
            raise OSError(self.server.errorString())
        self.server.newConnection.connect(self._accept_connections)

    def set_request_handler(self, handler: Callable[[AppRequest], Ack]) -> None:
        self._handler = handler
        pending, self._pending = self._pending, []
        for socket, request in pending:
            self._dispatch(socket, request)

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._decoders[socket] = FrameDecoder()
            socket.readyRead.connect(lambda current=socket: self._read(current))
            socket.disconnected.connect(lambda current=socket: self._cleanup(current))

    def _read(self, socket: QLocalSocket) -> None:
        try:
            messages = self._decoders[socket].feed(bytes(socket.readAll()))
            for value in messages:
                request = AppRequest.from_dict(value)
                if self._handler is None:
                    self._pending.append((socket, request))
                else:
                    self._dispatch(socket, request)
        except Exception as exc:
            self._write_ack(socket, Ack("unknown", "error", type(exc).__name__))

    def _dispatch(self, socket: QLocalSocket, request: AppRequest) -> None:
        assert self._handler is not None
        self._write_ack(socket, self._handler(request))

    def _write_ack(self, socket: QLocalSocket, ack: Ack) -> None:
        socket.write(encode_frame(ack.to_dict()))
        socket.flush()
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()

    def _cleanup(self, socket: QLocalSocket) -> None:
        self._decoders.pop(socket, None)
        socket.deleteLater()


def send_request(request: AppRequest, server_name: str = DEFAULT_SERVER_NAME, deadline_ms: int = 5000) -> Ack:
    deadline = time.monotonic() + deadline_ms / 1000
    while time.monotonic() < deadline:
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if not socket.waitForConnected(250):
            socket.abort()
            time.sleep(0.05)
            continue
        socket.write(encode_frame(request.to_dict()))
        socket.flush()
        if not socket.waitForBytesWritten(1000):
            socket.abort()
            continue
        decoder = FrameDecoder()
        while time.monotonic() < deadline:
            if socket.bytesAvailable() or socket.waitForReadyRead(250):
                for value in decoder.feed(bytes(socket.readAll())):
                    ack = Ack.from_dict(value)
                    if ack.request_id != request.request_id:
                        raise FrameError("ACK request id mismatch")
                    socket.disconnectFromServer()
                    return ack
        socket.abort()
    return Ack(request.request_id, "error", "IPC timeout")
```

Do not call `QLocalServer.removeServer` after a second process has detected an existing mutex. Only the confirmed primary constructs `SingleInstanceServer`; add a focused test for this startup contract with injected unique names.

- [ ] **Step 5: Run IPC tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_single_instance.py -q`

Expected: all frame, ACK, mutex, and unique Qt local-server tests pass.

- [ ] **Step 6: Commit Task 5**

```powershell
git add single_instance.py tests/test_single_instance.py
git commit -m "feat: add reliable single-instance IPC"
```

---

### Task 6: Integrate Paths, Credentials, Logging, Requests, and Self-Test

**Files:**
- Create: `self_test.py`
- Create: `tests/test_self_test.py`
- Create: `tests/test_main_integration.py`
- Modify: `gemini_translate.pyw:1-124,691-731,1119-1170,1310-1386,2087-2105,2210-2234,2256-2274,3073-3119,3166-3174,3283-3356`

**Interfaces:**
- Consumes every public interface from Tasks 1–5.
- Produces: `TranslationWindow.handle_app_request(request) -> Ack`, `run_self_test(report_path, paths) -> int`, and `main(argv=None) -> int`.

- [ ] **Step 1: Write failing integration tests without importing a real key**

`tests/test_main_integration.py` must load `gemini_translate.pyw` with `importlib.machinery.SourceFileLoader` after monkeypatching `LOCALAPPDATA` and must assert:

```python
def test_import_does_not_read_legacy_key_or_create_openai_client(app_module):
    assert app_module.client is None


def test_text_request_handler_returns_matching_ack(window, monkeypatch):
    monkeypatch.setattr(window, "start_translation", lambda *args, **kwargs: None)
    request = AppRequest("translate_text", {"text": "hello 世界"}, "request-1")
    ack = window.handle_app_request(request)
    assert ack.request_id == "request-1"
    assert ack.accepted


def test_image_request_rejects_unreadable_file_without_deleting(window, tmp_path):
    image = tmp_path / "broken.png"
    image.write_bytes(b"broken")
    ack = window.handle_app_request(AppRequest("ocr_image", {"path": str(image)}, "image-1"))
    assert not ack.accepted
    assert image.exists()


def test_legacy_unstructured_logger_is_fully_removed(project_root):
    import re
    source = (project_root / "gemini_translate.pyw").read_text(encoding="utf-8")
    assert re.search(r"(?<!_)\blog\(", source) is None
```

`tests/test_self_test.py` must build temporary `AppPaths`, create a tiny PNG resource, call `run_self_test`, and assert exit code `0`, report field `ok is True`, probe cleanup, and no API/credential access.

- [ ] **Step 2: Run the integration tests red**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_main_integration.py tests/test_self_test.py -q`

Expected: FAIL because `self_test.py`, dependency injection, and `handle_app_request` do not exist.

- [ ] **Step 3: Add the packaged self-test**

```python
# self_test.py
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtGui import QIcon

from app_paths import AppPaths, ensure_app_directories
from single_instance import FrameDecoder, encode_frame


def run_self_test(report_path: str | Path, paths: AppPaths) -> int:
    report = {"ok": False, "checks": {}}
    try:
        ensure_app_directories(paths)
        report["checks"]["icon"] = paths.icon_path.is_file() and not QIcon(str(paths.icon_path)).isNull()
        probe = paths.data_dir / ".self-test-write.tmp"
        probe.write_text("ok", encoding="utf-8")
        report["checks"]["data_dir"] = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink(missing_ok=True)
        payload = {"version": 1, "id": "self-test", "status": "accepted", "message": ""}
        report["checks"]["ipc_codec"] = FrameDecoder().feed(encode_frame(payload)) == [payload]
        report["ok"] = all(report["checks"].values())
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1
```

- [ ] **Step 4: Replace global startup side effects and inject runtime services**

At the top of `gemini_translate.pyw`, import the new modules and replace the old local key/history/logger initialization with:

```python
from app_cli import AppRequest, CliError, delete_acknowledged_source, parse_cli, prepare_request
from app_logging import configure_logging, log_event
from app_paths import HistoryDataError, load_history, migrate_legacy_history, resolve_app_paths, save_history_atomic
from credential_store import KeyResolution, WindowsCredentialStore, is_placeholder_api_key, resolve_api_key
from self_test import run_self_test
from single_instance import Ack, InstanceMutex, SingleInstanceServer, send_request

client = None
GPTSAPI_API_KEY = ""
MODEL_NAME = "gpt-5.4-nano"
REQUEST_TIMEOUT_SECONDS = 15.0
```

Change `TranslationWindow.__init__` to accept `app_paths` and `credential_store`, store them on `self`, and load history through `load_history`. If `HistoryDataError` occurs, set `self.history_writable = False`, keep an empty in-memory list, and never overwrite the corrupt file. `save_translation_history` calls `save_history_atomic` only while writable. `setup_tray_icon` uses `self.app_paths.icon_path`.

Change manual key storage to:

```python
if ok and configure_api_client(api_key):
    try:
        persisted = self.credential_store.write(api_key)
    except OSError as exc:
        persisted = False
        log_event("credential_write_failed", error_type=type(exc).__name__)
    log_event("api_key_configured", status="persisted" if persisted else "memory_only")
    if not persisted:
        QMessageBox.warning(self, "API Key", "API Key 本次可用，但未能保存到 Windows 凭据管理器。")
    return True
```

`configure_api_client` must no longer copy the key into `os.environ`.

- [ ] **Step 5: Replace string sentinels with structured UI dispatch**

Implement this method on `TranslationWindow` and remove `build_ocr_image_request`, `parse_ocr_image_request`, legacy `send_to_existing_instance`, and legacy `SingleInstanceServer`:

```python
def handle_app_request(self, request: AppRequest) -> Ack:
    try:
        log_event(
            "request_received",
            request_id=request.request_id,
            action=request.action,
            chars=len(str(request.payload.get("text", ""))),
        )
        if request.action == "show":
            self.show_manual_window()
            return Ack(request.request_id, "accepted")
        if request.action == "ocr_image":
            path = str(request.payload.get("path", ""))
            if not path or not self.start_image_file_ocr(path):
                return Ack(request.request_id, "error", "image load failed")
            return Ack(request.request_id, "accepted")
        text = str(request.payload.get("text", ""))
        if not text.strip():
            return Ack(request.request_id, "error", "text is empty")
        self._handle_translation_text(text)
        return Ack(request.request_id, "accepted")
    except Exception as exc:
        log_event("request_error", request_id=request.request_id, error_type=type(exc).__name__)
        return Ack(request.request_id, "error", type(exc).__name__)
```

Move the existing non-sentinel text body from `handle_new_request` into `_handle_translation_text(text)`. Change `start_image_file_ocr` to return `False` on read failure and `True` after `start_ocr`; remove its `delete_after` argument and all file deletion from it.

Replace every existing `log(...)` call with a static-literal `log_event(...)` call so removing the legacy logger cannot leave a runtime `NameError`. For payload-bearing calls, including current lines 700, 856, 1061, 1127, 2257, 3075, 3106, 3171, and 3310, record only counts, action, request ID, status, timing, PID, attempt number, or exception type from the Global Constraints whitelist.

- [ ] **Step 6: Replace `main` with primary/secondary-before-UI startup**

Implement `main(argv=None) -> int` with this control flow:

```python
def main(argv=None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        command = parse_cli(raw_args)
    except CliError as exc:
        app = QApplication([sys.argv[0]])
        QMessageBox.critical(None, "SnipDoTranslate", str(exc))
        return 2

    app = QApplication([sys.argv[0]])
    app.setApplicationName("SnipDoTranslate")
    app.setOrganizationName("SnipDoTranslate")
    app.setQuitOnLastWindowClosed(False)
    test_local_app_data = os.environ.get("SNIPDO_TRANSLATE_TEST_LOCALAPPDATA") or None
    paths = resolve_app_paths(__file__, local_app_data=test_local_app_data)
    ensure_app_directories(paths)
    configure_logging(paths.log_dir)

    if command.action == "self_test":
        return run_self_test(command.value, paths)

    try:
        prepared = prepare_request(command)
    except (CliError, OSError, UnicodeError) as exc:
        QMessageBox.critical(None, "SnipDoTranslate", str(exc))
        return 2

    suffix = os.environ.get("SNIPDO_INSTANCE_SUFFIX", "").strip()
    server_name = "SnipDoTranslate-v2" + (f"-{suffix}" if suffix else "")
    mutex_name = r"Local\SnipDoTranslate-v2" + (f"-{suffix}" if suffix else "")
    mutex = InstanceMutex(mutex_name)
    if not mutex.acquire():
        ack = send_request(prepared.request, server_name)
        delete_acknowledged_source(prepared, ack.accepted)
        if not ack.accepted:
            QMessageBox.critical(None, "SnipDoTranslate", ack.message)
            return 1
        return 0

    try:
        store = WindowsCredentialStore()
        try:
            migrate_legacy_history(paths)
        except HistoryDataError as exc:
            log_event("history_migration_skipped", error_type=type(exc).__name__)
        try:
            resolution = resolve_api_key(os.getenv("GPTSAPI_API_KEY", ""), store, paths.legacy_dirs)
        except OSError as exc:
            log_event("credential_read_failed", error_type=type(exc).__name__)
            resolution = KeyResolution("", "credential_error", False, False)
        if resolution.key:
            configure_api_client(resolution.key)
        server = SingleInstanceServer(server_name)
        window = TranslationWindow(paths, store)
        server.set_request_handler(window.handle_app_request)
        if resolution.source == "legacy_file" and not resolution.persisted:
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                window,
                "API Key",
                "已读取旧 API Key，但未能保存到 Windows 凭据管理器；旧文件保持不变。",
            ))

        def dispatch_initial() -> None:
            ack = window.handle_app_request(prepared.request)
            delete_acknowledged_source(prepared, ack.accepted)

        QTimer.singleShot(0, dispatch_initial)
        auto_exit = os.environ.get("SNIPDO_TRANSLATE_TEST_AUTO_EXIT_MS", "").strip()
        if auto_exit.isdigit():
            QTimer.singleShot(int(auto_exit), app.quit)
        return app.exec()
    finally:
        mutex.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

Import `ensure_app_directories` and `QMessageBox`. Keep strong references to `server`, `window`, and `mutex` until `app.exec()` returns.

- [ ] **Step 7: Run focused and regression tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_main_integration.py tests/test_self_test.py tests/test_app_cli.py tests/test_app_paths.py tests/test_credential_store.py tests/test_app_logging.py tests/test_single_instance.py -q`

Expected: all tests pass with no network calls, no real credential reads, and no raw payload in test logs.

- [ ] **Step 8: Commit Task 6**

```powershell
git add gemini_translate.pyw self_test.py tests/test_self_test.py tests/test_main_integration.py
git commit -m "refactor: integrate packaged runtime services"
```

---

### Task 7: SnipDo, Developer Launcher, Documentation, and Legacy Sanitization

**Files:**
- Modify: `snipdo_script_powershell_code/snipdo_gemini.txt`
- Modify: `start_gemini_translate.cmd`
- Modify: `legacy/gemini_translate_textBox.pyw:16`
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `requirements.txt`
- Create: `requirements-build.txt`

**Interfaces:**
- Consumes the finalized CLI contract and output path from Tasks 1 and 6.
- Produces user-facing launch scripts and reproducible dependency declarations.

- [ ] **Step 1: Add a static safety test for scripts and tracked secrets**

Add to `tests/test_main_integration.py`:

```python
def test_snipdo_script_calls_exe_and_requests_safe_temp_deletion(project_root):
    script = (project_root / "snipdo_script_powershell_code" / "snipdo_gemini.txt").read_text(encoding="utf-8")
    assert "SnipDoTranslate.exe" in script
    assert "--delete-after" in script
    assert "pythonw.exe" not in script
    assert "$apiKey" not in script


def test_tracked_legacy_source_has_no_hardcoded_google_key(project_root):
    legacy = (project_root / "legacy" / "gemini_translate_textBox.pyw").read_text(encoding="utf-8")
    assert "GOOGLE_API_KEY = os.getenv" in legacy
    assert "GOOGLE_API_KEY = \"AI" not in legacy
```

- [ ] **Step 2: Run the two new tests red**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_main_integration.py -q`

Expected: the script/legacy safety tests fail against the current files.

- [ ] **Step 3: Replace the SnipDo script with direct EXE invocation**

Use this behavior in `snipdo_gemini.txt`:

```powershell
$exePath = "D:\test\my_snipdo_translate\dist\SnipDoTranslate.exe"
$inputText = $args -join " "

if (!(Test-Path -LiteralPath $exePath)) { exit }

if ([string]::IsNullOrWhiteSpace($inputText)) {
    Start-Process -FilePath $exePath -ArgumentList @("--show")
    exit
}

$tempFile = Join-Path $env:TEMP ("snipdo_gptsapi_" + [guid]::NewGuid().ToString() + ".txt")
[System.IO.File]::WriteAllText($tempFile, $inputText, [System.Text.UTF8Encoding]::new($false))
try {
    Start-Process -FilePath $exePath -ArgumentList @(
        "--file",
        "`"$tempFile`"",
        "--delete-after"
    )
} catch {
    Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
}
```

Change `start_gemini_translate.cmd` to launch `dist\SnipDoTranslate.exe` when present, otherwise retain the current `.venv\Scripts\pythonw.exe gemini_translate.pyw` development fallback.

- [ ] **Step 4: Sanitize legacy and document the new contract**

Replace the legacy hard-coded assignment with:

```python
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
```

Update README sections for:

- direct EXE launch and CLI examples;
- default-retain versus explicit `--delete-after` semantics;
- `%LOCALAPPDATA%\SnipDoTranslate` history/log locations;
- Windows Credential Manager Key storage and copy-only legacy migration;
- SnipDo EXE configuration;
- offline tests, build command, SHA-256, no code signature, and lack of a clean external Windows VM validation.

Set runtime pins to the already installed and verified versions:

```text
PyQt6==6.11.0
openai==2.37.0
```

Create `requirements-build.txt` initially as:

```text
PyInstaller>=6.14,<7
Pillow>=11,<13
pytest>=8.3,<9
```

After Task 8 installs and verifies the build stack, replace each range with the exact installed version before the release commit.

Add these generated paths to `.gitignore`:

```text
.pytest_cache/
build/
dist/
build-reports/
*.ico
```

Then add `!snipdo_script_logo/SnipDoTranslate.ico` so the reproducible app icon remains tracked.

- [ ] **Step 5: Run script safety and full offline tests green**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 7**

```powershell
git add .gitignore README.md requirements.txt requirements-build.txt start_gemini_translate.cmd snipdo_script_powershell_code/snipdo_gemini.txt legacy/gemini_translate_textBox.pyw tests/test_main_integration.py
git commit -m "docs: switch launchers to packaged executable"
```

---

### Task 8: Reproducible Icon, PyInstaller Spec, Artifact Scan, and Build Pipeline

**Files:**
- Create: `tools/create_icon.py`
- Create: `tools/verify_artifact.py`
- Create: `SnipDoTranslate.spec`
- Create: `build_exe.ps1`
- Create: `tests/test_build_files.py`
- Generate and track: `snipdo_script_logo/SnipDoTranslate.ico`
- Modify: `requirements-build.txt` with exact installed versions after verification.

**Interfaces:**
- Consumes the final entry point, self-test mode, and resource path.
- Produces `dist/SnipDoTranslate.exe`, `dist/SnipDoTranslate.exe.sha256`, and `build-reports/build-report.json`.

- [ ] **Step 1: Write failing static build configuration tests**

```python
# tests/test_build_files.py
from pathlib import Path


def test_spec_only_bundles_the_runtime_icon(project_root: Path):
    spec = (project_root / "SnipDoTranslate.spec").read_text(encoding="utf-8")
    assert "gemini-color.png" in spec
    for forbidden in [".gptsapi_api_key", "translation_history.json", ".venv", "legacy", "diagnostics"]:
        assert forbidden not in spec


def test_build_script_runs_tests_both_modes_self_tests_scan_and_hash(project_root: Path):
    script = (project_root / "build_exe.ps1").read_text(encoding="utf-8")
    for required in ["pytest", "SNIPDO_BUILD_MODE", "--self-test", "verify_artifact.py", "Get-FileHash"]:
        assert required in script


def test_icon_builder_declares_windows_sizes(project_root: Path):
    script = (project_root / "tools" / "create_icon.py").read_text(encoding="utf-8")
    for size in [16, 32, 48, 64, 128, 256]:
        assert f"({size}, {size})" in script
```

- [ ] **Step 2: Run build configuration tests red**

Run: `D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_build_files.py -q`

Expected: FAIL because the build files do not exist.

- [ ] **Step 3: Implement reproducible icon and artifact safety tools**

```python
# tools/create_icon.py
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "snipdo_script_logo" / "gemini-color.png"
destination = ROOT / "snipdo_script_logo" / "SnipDoTranslate.ico"
with Image.open(source) as image:
    image.convert("RGBA").save(
        destination,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
print(destination)
```

```python
# tools/verify_artifact.py
import sys
from pathlib import Path, PurePosixPath

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
artifact = Path(sys.argv[1]).resolve()
forbidden_parts = {".gptsapi_api_key", "translation_history.json", ".venv", "legacy", "diagnostics", ".git"}
members = CArchiveReader(str(artifact)).toc.keys()
bad_members = [
    name for name in members
    if forbidden_parts.intersection(PurePosixPath(name.replace("\\", "/")).parts)
]
if bad_members:
    raise SystemExit(f"forbidden archive members: {bad_members}")

legacy_key = ROOT / ".gptsapi_api_key"
if legacy_key.is_file():
    secret = legacy_key.read_bytes().strip()
    if len(secret) >= 8 and secret in artifact.read_bytes():
        raise SystemExit("local API key bytes were found in the executable")
print("artifact safety check passed")
```

- [ ] **Step 4: Create the parametrized spec**

```python
# SnipDoTranslate.spec
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPEC).resolve().parent
mode = os.environ.get("SNIPDO_BUILD_MODE", "onefile")
if mode not in {"onedir", "onefile"}:
    raise ValueError(f"Unsupported SNIPDO_BUILD_MODE: {mode}")

png = ROOT / "snipdo_script_logo" / "gemini-color.png"
ico = ROOT / "snipdo_script_logo" / "SnipDoTranslate.ico"
a = Analysis(
    [str(ROOT / "gemini_translate.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(png), "snipdo_script_logo")],
    hiddenimports=collect_submodules("openai"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if mode == "onedir":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SnipDoTranslate",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=str(ico),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="SnipDoTranslate",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="SnipDoTranslate",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=str(ico),
    )
```

- [ ] **Step 5: Create the safe PowerShell build pipeline**

Create this pipeline, keeping all recursive cleanup inside the verified project root:

```powershell
# build_exe.ps1
$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$rootPrefix = $root.TrimEnd('\') + '\'
$python = Join-Path $root ".venv\Scripts\python.exe"
$dist = Join-Path $root "dist"
$build = Join-Path $root "build"
$reports = Join-Path $root "build-reports"

function Assert-GeneratedPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing generated-path operation outside project root: $full"
    }
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-SelfTest([string]$ExePath, [string]$ReportPath) {
    $process = Start-Process -FilePath $ExePath -ArgumentList @(
        "--self-test",
        "`"$ReportPath`""
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Self-test failed for $ExePath with exit code $($process.ExitCode)"
    }
    $report = Get-Content -Raw -LiteralPath $ReportPath | ConvertFrom-Json
    if (!$report.ok) {
        throw "Self-test report is not ok: $ReportPath"
    }
}

function Get-PackageVersion([string]$PackageName) {
    $value = & $python -c "import importlib.metadata,sys; print(importlib.metadata.version(sys.argv[1]))" $PackageName
    if ($LASTEXITCODE -ne 0) { throw "Cannot read package version: $PackageName" }
    return $value.Trim()
}

foreach ($target in @($build, $dist, $reports)) {
    Assert-GeneratedPath $target
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $reports | Out-Null
Push-Location $root

try {
    $env:SNIPDO_TRANSLATE_TEST_LOCALAPPDATA = Join-Path $reports "test-localappdata"
    Invoke-Native $python @("-m", "pytest", "-q")
    Invoke-Native $python @("tools\create_icon.py")

    $env:SNIPDO_BUILD_MODE = "onedir"
    Invoke-Native $python @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", "dist", "--workpath", "build\onedir",
        "SnipDoTranslate.spec"
    )
    $onedirExe = Join-Path $dist "SnipDoTranslate\SnipDoTranslate.exe"
    Invoke-SelfTest $onedirExe (Join-Path $reports "onedir-self-test.json")

    $env:SNIPDO_BUILD_MODE = "onefile"
    Invoke-Native $python @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", "dist", "--workpath", "build\onefile",
        "SnipDoTranslate.spec"
    )
    $onefileExe = Join-Path $dist "SnipDoTranslate.exe"
    Invoke-SelfTest $onefileExe (Join-Path $reports "onefile-self-test.json")

    $env:SNIPDO_INSTANCE_SUFFIX = [guid]::NewGuid().ToString("N")
    $env:SNIPDO_TRANSLATE_TEST_AUTO_EXIT_MS = "5000"
    $primary = Start-Process -FilePath $onefileExe -ArgumentList @("--show") -PassThru
    Start-Sleep -Milliseconds 1200
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $secondary = Start-Process -FilePath $onefileExe -ArgumentList @("--show") -Wait -PassThru
    $stopwatch.Stop()
    if ($secondary.ExitCode -ne 0 -or $stopwatch.ElapsedMilliseconds -gt 5000) {
        throw "Single-instance smoke test failed"
    }
    if (!$primary.WaitForExit(10000)) {
        throw "Primary smoke-test process did not exit"
    }

    Invoke-Native $python @("tools\verify_artifact.py", $onefileExe)
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $onefileExe).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $dist "SnipDoTranslate.exe.sha256") -Encoding ASCII -Value "$hash  SnipDoTranslate.exe"

    $commit = (& git -c "safe.directory=$root" rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot read Git commit" }
    $report = [ordered]@{
        git_commit = $commit
        python = (& $python --version 2>&1).ToString().Trim()
        pyqt6 = Get-PackageVersion "PyQt6"
        openai = Get-PackageVersion "openai"
        pyinstaller = Get-PackageVersion "PyInstaller"
        tests_passed = $true
        onedir_self_test = "build-reports/onedir-self-test.json"
        onefile_self_test = "build-reports/onefile-self-test.json"
        exe_bytes = (Get-Item -LiteralPath $onefileExe).Length
        sha256 = $hash
        signed = $false
        clean_external_windows_verified = $false
    }
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reports "build-report.json") -Encoding UTF8
} finally {
    Remove-Item Env:SNIPDO_BUILD_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:SNIPDO_INSTANCE_SUFFIX -ErrorAction SilentlyContinue
    Remove-Item Env:SNIPDO_TRANSLATE_TEST_AUTO_EXIT_MS -ErrorAction SilentlyContinue
    Remove-Item Env:SNIPDO_TRANSLATE_TEST_LOCALAPPDATA -ErrorAction SilentlyContinue
    if ($primary -and !$primary.HasExited) {
        Stop-Process -Id $primary.Id -Force
    }
    Pop-Location
}
```

- [ ] **Step 6: Install constrained build dependencies**

Run:

```powershell
D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Expected: PyInstaller 6.x, Pillow 11.x or 12.x, and pytest 8.x install successfully. If sandbox networking fails, rerun only this exact pip-install command with the required network approval.

Run:

```powershell
D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pip show PyInstaller Pillow pytest
```

Patch `requirements-build.txt` to exact `==` versions from this output, then run `pip install -r requirements-build.txt` again and require `Requirement already satisfied` for all three direct dependencies.

- [ ] **Step 7: Generate the icon and run static tests green**

Run:

```powershell
D:\test\my_snipdo_translate\.venv\Scripts\python.exe tools\create_icon.py
D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest tests/test_build_files.py -q
```

Expected: ICO exists and all build configuration tests pass.

- [ ] **Step 8: Commit Task 8 source and reproducible icon**

```powershell
git add SnipDoTranslate.spec build_exe.ps1 requirements-build.txt tools/create_icon.py tools/verify_artifact.py tests/test_build_files.py snipdo_script_logo/SnipDoTranslate.ico
git commit -m "build: add reproducible Windows executable pipeline"
```

---

### Task 9: Full Verification, Final EXE, and Release Evidence

**Files:**
- Generated, ignored: `build/`, `dist/`, `build-reports/`
- Modify only if verification exposes a proven defect: files owned by the failing earlier task.

**Interfaces:**
- Consumes the whole implementation and build pipeline.
- Produces the user-deliverable EXE, SHA-256, and verification report; no new runtime features.

- [ ] **Step 1: Invoke verification-before-completion before any success claim**

Read and follow `superpowers:verification-before-completion`. Do not reuse prior test output.

- [ ] **Step 2: Run source verification from a clean Git state**

Run:

```powershell
git status --short
D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m pytest -q
D:\test\my_snipdo_translate\.venv\Scripts\python.exe -m compileall -q app_cli.py app_paths.py credential_store.py app_logging.py single_instance.py self_test.py gemini_translate.pyw
git diff --check
```

Expected: clean status before generated artifacts, all tests pass, compileall exits `0`, and diff check has no output.

- [ ] **Step 3: Build and verify both packaging modes**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Expected: script exits `0`; both self-test reports contain `"ok": true`; the single-instance smoke call exits `0`; artifact safety scan passes; final EXE, SHA file, and build report exist.

- [ ] **Step 4: Inspect fresh release evidence**

Run:

```powershell
Get-Item dist\SnipDoTranslate.exe | Select-Object FullName,Length,LastWriteTime
Get-Content dist\SnipDoTranslate.exe.sha256
Get-Content -Raw build-reports\build-report.json
Get-Content -Raw build-reports\onefile-self-test.json
git status --short
```

Expected: report versions and hash match the current executable; only ignored generated artifacts are absent from `git status`; no user Key or history appears in output.

- [ ] **Step 5: Request a final two-stage code review**

Use `superpowers:requesting-code-review` for spec compliance and code quality. Address only confirmed findings, rerun affected focused tests, then rerun Steps 2–4 if production/build code changed.

- [ ] **Step 6: Hand off the verified artifact**

Report clickable absolute paths for:

- `dist/SnipDoTranslate.exe`
- `dist/SnipDoTranslate.exe.sha256`
- `build-reports/build-report.json`
- `snipdo_script_powershell_code/snipdo_gemini.txt`

State the verified test/build evidence, EXE size and SHA-256, plus the two honest limitations: unsigned EXE/possible SmartScreen warning and no independent clean Windows 10/11 VM verification. Do not claim live API success because the automated build intentionally never calls it.
