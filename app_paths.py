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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
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
