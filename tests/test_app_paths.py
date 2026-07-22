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
    source = tmp_path / "project" / "snipdo_translate.pyw"
    source.parent.mkdir()
    source.write_text("", encoding="utf-8")
    local = tmp_path / "LocalAppData"
    paths = resolve_app_paths(source, source, False, local)
    assert paths.bundle_dir == source.parent.resolve()
    assert paths.data_dir == local / "SnipDoTranslate"
    assert paths.icon_path == source.parent / "snipdo_script_logo" / "snipdo-translate-enabled.png"
    assert paths.disabled_icon_path == source.parent / "snipdo_script_logo" / "snipdo-translate-disabled.png"
    assert paths.legacy_dirs == (source.parent.resolve(),)


def test_frozen_dist_build_discovers_project_parent(tmp_path: Path):
    project = tmp_path / "project"
    dist = project / "dist"
    dist.mkdir(parents=True)
    (project / "snipdo_translate.pyw").write_text("", encoding="utf-8")
    executable = dist / "SnipDoTranslate.exe"
    paths = resolve_app_paths(
        tmp_path / "_MEI123" / "snipdo_translate.pyw",
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
    paths = resolve_app_paths(
        legacy / "snipdo_translate.pyw",
        legacy / "pythonw.exe",
        False,
        tmp_path / "LocalAppData",
    )
    assert migrate_legacy_history(paths) is True
    assert old.exists()
    assert load_history(paths.history_path) == [valid_entry(1)]
    paths.history_path.write_text(json.dumps([valid_entry(2)]), encoding="utf-8")
    assert migrate_legacy_history(paths) is False
    assert load_history(paths.history_path) == [valid_entry(2)]
