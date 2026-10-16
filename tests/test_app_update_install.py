from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import app_update_install as install


OLD = b"MZoriginal packaged executable"
NEW = b"MZupdated packaged executable"


class _Process:
    pid = 9876

    def __init__(self, result=None):
        self.result = result

    def poll(self):
        return self.result

    def wait(self, timeout):
        self.result = 0
        return 0


@pytest.fixture
def app_files(tmp_path, monkeypatch):
    folder = tmp_path / "Installed app 译文"
    folder.mkdir()
    target = folder / "SnipDoTranslate.exe"
    target.write_bytes(OLD)
    download = tmp_path / "download.exe"
    download.write_bytes(NEW)
    monkeypatch.setattr(install.sys, "frozen", True, raising=False)
    monkeypatch.setattr(install.sys, "_MEIPASS", str(tmp_path / "_MEIfake"), raising=False)
    monkeypatch.setattr(install.sys, "executable", str(target))
    monkeypatch.setattr(install, "_capture_parent", lambda _target: {"pid": 1234, "created": 5678})
    for name in (install.ENV_MANIFEST, install.ENV_TOKEN, install.ENV_ERROR):
        monkeypatch.delenv(name, raising=False)
    return target, download


@pytest.fixture
def staged(app_files):
    target, download = app_files
    return install.prepare_install(download, hashlib.sha256(NEW).hexdigest(), "2.0.0")


@pytest.fixture
def launches(monkeypatch):
    calls = []

    def launch(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return _Process()
    monkeypatch.setattr(install, "_launch", launch)
    return calls


@pytest.fixture
def delayed_threads(monkeypatch):
    callbacks = []

    class Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            callbacks.append(self.target)
    monkeypatch.setattr(install.threading, "Thread", Thread)
    return callbacks


def _manifest(job):
    return json.loads((job.directory / install.MANIFEST_NAME).read_text(encoding="utf-8"))


def _rewrite_manifest(job, **changes):
    value = _manifest(job)
    value.update(changes)
    (job.directory / install.MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")


def _helper_harness(job, monkeypatch, *, authorize=True, exits=True, acknowledge=True, launch_error=False):
    manifest = _manifest(job)
    calls = []
    monkeypatch.setattr(install.sys, "executable", str(job.directory / install.HELPER_NAME))

    class Parent:
        closed = False

        def exited(self):
            if authorize and not (job.directory / "commit").exists():
                install._mark(job.directory, "commit", manifest["token"])
            return exits

        def close(self):
            self.closed = True
    parent = Parent()
    monkeypatch.setattr(install, "_open_parent", lambda *_args: parent)
    normal_wait = install._wait_until
    monkeypatch.setattr(install, "_wait_until", lambda predicate, _timeout, message: normal_wait(predicate, 0.01, message))
    monkeypatch.setattr(install, "_stop_process", lambda process: setattr(process, "result", 0))

    def launch(arguments, **kwargs):
        calls.append((arguments, kwargs))
        rollback = kwargs["env"].get(install.ENV_ERROR) == "1"
        if launch_error and not rollback:
            raise OSError("new executable could not start")
        if acknowledge and not rollback:
            install._mark(job.directory, "ack", manifest["token"])
        return _Process()
    monkeypatch.setattr(install, "_launch", launch)
    return manifest, parent, calls


def test_prepare_only_stages_verified_copies_in_unique_same_parent(staged, app_files):
    target, download = app_files
    assert staged.target == target
    assert staged.directory.parent == target.parent
    assert staged.directory.name.startswith(install.STAGE_PREFIX)
    assert target.read_bytes() == OLD
    assert download.read_bytes() == NEW
    assert (staged.directory / install.HELPER_NAME).read_bytes() == OLD
    assert (staged.directory / install.UPDATE_NAME).read_bytes() == NEW
    assert not staged.is_ready()
    manifest = _manifest(staged)
    assert manifest["target_name"] == target.name
    assert manifest["original_identity"] == install._identity(target)
    assert manifest["sha256"] == hashlib.sha256(NEW).hexdigest()


@pytest.mark.parametrize("mode", ["source", "onedir", "onedir_internal"])
def test_source_and_folder_builds_are_refused_without_changes(app_files, monkeypatch, mode):
    target, download = app_files
    if mode == "source":
        monkeypatch.setattr(install.sys, "frozen", False)
    else:
        monkeypatch.setattr(install.sys, "_MEIPASS", str(target.parent if mode == "onedir" else target.parent / "_internal"))
    assert install.install_unavailable_reason()
    with pytest.raises(install.UpdateInstallError):
        install.prepare_install(download, hashlib.sha256(NEW).hexdigest(), "2.0.0")
    assert target.read_bytes() == OLD
    assert list(target.parent.glob(install.STAGE_PREFIX + "*")) == []


def test_unwritable_install_location_is_reported_before_download(app_files, monkeypatch):
    monkeypatch.setattr(install.os, "access", lambda *_args: False)
    assert "不可写" in install.install_unavailable_reason()


@pytest.mark.parametrize("failure", ["hash", "format", "permission"])
def test_prepare_failure_keeps_installed_app_and_cleans_staging(app_files, monkeypatch, failure):
    target, download = app_files
    digest = hashlib.sha256(NEW).hexdigest()
    if failure == "hash":
        digest = "0" * 64
    elif failure == "format":
        download.write_bytes(b"not an executable")
        digest = install._sha256(download)
    else:
        def fail_copy(*_args, **_kwargs):
            raise PermissionError("folder denies writing")
        monkeypatch.setattr(install.shutil, "copyfile", fail_copy)
    with pytest.raises(install.UpdateInstallError):
        install.prepare_install(download, digest, "2.0.0")
    assert target.read_bytes() == OLD
    assert list(target.parent.glob(install.STAGE_PREFIX + "*")) == []


@pytest.mark.parametrize(("digest", "version"), [("", "2.0.0"), ("f" * 63, "2.0.0"), ("a" * 64, "../2.0.0"), ("a" * 64, "2.0")])
def test_invalid_metadata_is_rejected_before_staging(app_files, digest, version):
    target, download = app_files
    with pytest.raises(install.UpdateInstallError):
        install.prepare_install(download, digest, version)
    assert list(target.parent.glob(install.STAGE_PREFIX + "*")) == []


def test_start_launches_copied_helper_with_argument_list_and_reset_environment(staged, launches, monkeypatch):
    monkeypatch.setenv(install.ENV_MANIFEST, "stale")
    staged.start()
    arguments, options = launches[0]
    assert arguments == [str(staged.directory / install.HELPER_NAME), "--apply-update", str(staged.directory / install.MANIFEST_NAME)]
    assert options["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert install.ENV_MANIFEST not in options["env"]
    assert options["cwd"] == staged.directory
    assert staged.target.read_bytes() == OLD


def test_launch_uses_detached_hidden_process_without_shell(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    install._launch(["program with spaces.exe", "--show"], env={"TEST": "1"}, cwd=tmp_path)
    arguments, kwargs = calls[0]
    assert arguments == ["program with spaces.exe", "--show"]
    assert "shell" not in kwargs
    assert kwargs["creationflags"] & 0x8
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["close_fds"]


def test_ready_requires_live_helper_and_commit_is_explicit(staged, launches):
    staged.start()
    assert not staged.is_ready()
    with pytest.raises(install.UpdateInstallError, match="not ready"):
        staged.commit()
    install._mark(staged.directory, "ready", staged._token)
    assert staged.is_ready()
    assert not (staged.directory / "commit").exists()
    staged.commit()
    assert install._marked(staged.directory, "commit", staged._token)
    staged.cancel()
    assert staged.directory.exists()


def test_dead_helper_is_not_ready_despite_ready_marker(staged, launches):
    staged.start()
    install._mark(staged.directory, "ready", staged._token)
    staged._process.result = 0
    with pytest.raises(install.UpdateInstallError, match="exited"):
        staged.is_ready()


def test_readiness_timeout_leaves_installed_file_unchanged(staged, launches, monkeypatch):
    staged.start()
    monkeypatch.setattr(install.time, "monotonic", lambda: staged._started_at + install.READY_TIMEOUT + 1)
    with pytest.raises(install.UpdateInstallError, match="in time"):
        staged.is_ready()
    assert staged.target.read_bytes() == OLD


def test_cancel_before_start_cleans_only_staged_files(staged):
    staged.cancel()
    staged.cancel()
    assert not staged.directory.exists()
    assert staged.target.read_bytes() == OLD


def test_cancel_running_helper_defers_cleanup_until_helper_exits(staged, launches, delayed_threads):
    staged.start()
    staged.cancel()
    assert install._marked(staged.directory, "cancel", staged._token)
    assert staged.directory.exists()
    delayed_threads[0]()
    assert not staged.directory.exists()
    assert staged.target.read_bytes() == OLD


def test_cancel_marker_permission_error_does_not_escape(staged, launches, monkeypatch):
    staged.start()
    monkeypatch.setattr(install, "_mark", lambda *_args: (_ for _ in ()).throw(PermissionError("denied")))
    staged.cancel()
    assert not (staged.directory / "commit").exists()
    assert staged.target.read_bytes() == OLD


def test_helper_replaces_only_after_commit_exit_then_waits_for_startup_ack(staged, monkeypatch):
    manifest, parent, calls = _helper_harness(staged, monkeypatch)
    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 0
    assert staged.target.read_bytes() == NEW
    assert (staged.directory / install.BACKUP_NAME).read_bytes() == OLD
    assert install._marked(staged.directory, "complete", manifest["token"])
    assert calls[0][0] == [str(staged.target), "--show"]
    assert calls[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert parent.closed


@pytest.mark.parametrize("failure", ["no_commit", "shutdown", "launch", "ack"])
def test_failed_install_keeps_or_restores_original(staged, monkeypatch, failure):
    manifest, parent, calls = _helper_harness(
        staged, monkeypatch, authorize=failure != "no_commit", exits=failure != "shutdown",
        acknowledge=failure != "ack", launch_error=failure == "launch",
    )
    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 1
    assert staged.target.read_bytes() == OLD
    assert install._read_error(staged.directory, manifest["token"])
    assert not (staged.directory / "complete").exists()
    if failure in ("launch", "ack"):
        assert (staged.directory / install.BACKUP_NAME).read_bytes() == OLD
        assert calls[-1][1]["env"][install.ENV_ERROR] == "1"
    else:
        assert calls == []
    assert parent.closed


def test_permanently_locked_executable_is_not_removed_and_original_relaunches(staged, monkeypatch):
    manifest, _, calls = _helper_harness(staged, monkeypatch)
    monkeypatch.setattr(install.os, "replace", lambda *_args: (_ for _ in ()).throw(PermissionError("locked")))
    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 1
    assert staged.target.read_bytes() == OLD
    assert (staged.directory / install.BACKUP_NAME).read_bytes() == OLD
    assert "locked" in install._read_error(staged.directory, manifest["token"])
    assert calls[-1][1]["env"][install.ENV_ERROR] == "1"


def test_transient_bootloader_lock_is_retried(staged, monkeypatch):
    _helper_harness(staged, monkeypatch)
    real_replace = install.os.replace
    attempts = []

    def replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) == 1:
            raise PermissionError("old bootloader still exiting")
        real_replace(source, destination)
    monkeypatch.setattr(install.os, "replace", replace)
    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 0
    assert len(attempts) == 2
    assert staged.target.read_bytes() == NEW


@pytest.mark.parametrize("tamper", ["original", "update", "manifest_target", "stale", "unexpected_file"])
def test_tampered_or_stale_helper_cannot_replace_target(staged, monkeypatch, tamper):
    _, _, calls = _helper_harness(staged, monkeypatch)
    expected = OLD
    if tamper == "original":
        expected = b"MZexternally changed original"
        staged.target.write_bytes(expected)
    elif tamper == "update":
        (staged.directory / install.UPDATE_NAME).write_bytes(b"MZtampered")
    elif tamper == "manifest_target":
        _rewrite_manifest(staged, target_name="../victim.exe")
    elif tamper == "stale":
        _rewrite_manifest(staged, created=0)
    else:
        (staged.directory / "unrelated.txt").write_text("must not touch", encoding="utf-8")
    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 1
    assert staged.target.read_bytes() == expected
    assert calls == []


def test_normal_process_cannot_apply_helper_manifest(staged):
    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 1
    assert staged.target.read_bytes() == OLD


def test_ordinary_launch_acknowledgement_is_noop(app_files):
    assert not install.acknowledge_update_startup()
    assert install.consume_update_error() == ""


def test_acknowledgement_rejects_arbitrary_environment_path(app_files, tmp_path, monkeypatch):
    arbitrary = tmp_path / "outside.json"
    monkeypatch.setenv(install.ENV_MANIFEST, str(arbitrary))
    monkeypatch.setenv(install.ENV_TOKEN, "a" * 64)
    assert not install.acknowledge_update_startup()
    assert not arbitrary.exists()
    assert not (tmp_path / "ack").exists()


def test_primary_app_acknowledges_verified_install_and_schedules_cleanup(staged, monkeypatch, delayed_threads):
    manifest = _manifest(staged)
    staged.target.write_bytes(NEW)
    install._mark(staged.directory, "commit", manifest["token"])
    monkeypatch.setenv(install.ENV_MANIFEST, str(staged.directory / install.MANIFEST_NAME))
    monkeypatch.setenv(install.ENV_TOKEN, manifest["token"])
    assert install.acknowledge_update_startup("2.0.0")
    assert install._marked(staged.directory, "ack", manifest["token"])
    assert install.ENV_MANIFEST not in install.os.environ
    install._mark(staged.directory, "complete", manifest["token"])
    delayed_threads[0]()
    assert not staged.directory.exists()
    assert staged.target.read_bytes() == NEW


def test_rollback_error_is_returned_only_to_original_application(staged, monkeypatch):
    manifest = _manifest(staged)
    install._write_json(staged.directory / "error.json", {"token": manifest["token"], "message": "Previous version restored."})
    monkeypatch.setenv(install.ENV_MANIFEST, str(staged.directory / install.MANIFEST_NAME))
    monkeypatch.setenv(install.ENV_TOKEN, manifest["token"])
    monkeypatch.setenv(install.ENV_ERROR, "1")
    assert not install.acknowledge_update_startup()
    assert install.consume_update_error() == "Previous version restored."
    assert install.consume_update_error() == ""
    assert (staged.directory / "error.json").exists()


def test_protocol_marker_is_invisible_until_fully_written(staged, monkeypatch):
    original_dump = install.json.dump
    observed = []

    def dump(value, stream, **kwargs):
        stream.write('{"token":')
        stream.flush()
        observed.append(install._marked(staged.directory, "ready", staged._token))
        stream.seek(0)
        stream.truncate()
        original_dump(value, stream, **kwargs)
    monkeypatch.setattr(install.json, "dump", dump)

    install._mark(staged.directory, "ready", staged._token)

    assert observed == [False]
    assert install._marked(staged.directory, "ready", staged._token)
    assert not (staged.directory / "ready.pending").exists()


def test_exclusive_marker_writer_does_not_remove_another_writers_pending_file(staged):
    pending = staged.directory / "ready.pending"
    pending.write_text("another writer", encoding="utf-8")
    with pytest.raises(FileExistsError):
        install._mark(staged.directory, "ready", staged._token)
    assert pending.read_text(encoding="utf-8") == "another writer"


def test_existing_directory_collision_is_never_cleaned(app_files, monkeypatch):
    target, download = app_files
    monkeypatch.setattr(install.secrets, "token_hex", lambda _size: "a" * 32)
    existing = target.parent / (install.STAGE_PREFIX + "a" * 32)
    existing.mkdir()
    sentinel = existing / install.HELPER_NAME
    sentinel.write_bytes(b"existing unrelated content")
    with pytest.raises(install.UpdateInstallError):
        install.prepare_install(download, hashlib.sha256(NEW).hexdigest(), "2.0.0")
    assert sentinel.read_bytes() == b"existing unrelated content"


def test_malformed_error_marker_is_a_controlled_error(staged, launches):
    staged.start()
    (staged.directory / "error.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(install.UpdateInstallError, match="marker is invalid"):
        staged.is_ready()


def test_version_mismatch_refuses_acknowledgement_and_informs_helper(staged, monkeypatch):
    staged.target.write_bytes(NEW)
    install._mark(staged.directory, "commit", staged._token)
    monkeypatch.setenv(install.ENV_MANIFEST, str(staged.directory / install.MANIFEST_NAME))
    monkeypatch.setenv(install.ENV_TOKEN, staged._token)
    assert not install.acknowledge_update_startup("1.0.0")
    assert not (staged.directory / "ack").exists()
    error = install._read_error(staged.directory, staged._token, "startup-error.json")
    assert "1.0.0" in error and "2.0.0" in error


@pytest.mark.parametrize("failure", ["rollback", "relaunch"])
def test_unrecoverable_install_failure_shows_native_recovery_guidance(staged, monkeypatch, failure):
    _helper_harness(staged, monkeypatch, launch_error=True)
    messages = []
    monkeypatch.setattr(install, "_notify_install_failure", messages.append)
    if failure == "rollback":
        real_replace = install._replace_with_retry

        def replace(source, target):
            if source.name == "restore.exe":
                raise PermissionError("restore locked")
            real_replace(source, target)
        monkeypatch.setattr(install, "_replace_with_retry", replace)
    else:
        monkeypatch.setattr(install, "_launch", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launch denied")))

    assert install.run_apply_update(staged.directory / install.MANIFEST_NAME) == 1
    assert len(messages) == 1
    assert str(staged.directory / install.BACKUP_NAME) in messages[0]
    assert (staged.directory / install.BACKUP_NAME).read_bytes() == OLD


def test_child_environment_removes_old_bundle_path(app_files, monkeypatch):
    bundle = str(install.sys._MEIPASS)
    monkeypatch.setenv("PATH", bundle + install.os.pathsep + r"C:\Windows\System32")
    assert install._child_env()["PATH"] == r"C:\Windows\System32"
