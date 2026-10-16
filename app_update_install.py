"""Detached, rollback-capable replacement of the running Windows onefile app.

This module intentionally imports only the standard library so the copied app
can dispatch --apply-update before importing Qt or opening its normal IPC server.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time


STAGE_PREFIX = ".snipdo-update-"
MANIFEST_NAME = "install.json"
HELPER_NAME = "helper.exe"
UPDATE_NAME = "update.exe"
BACKUP_NAME = "original.exe"
ENV_MANIFEST = "SNIPDO_UPDATE_MANIFEST"
ENV_TOKEN = "SNIPDO_UPDATE_TOKEN"
ENV_ERROR = "SNIPDO_UPDATE_ERROR"
READY_TIMEOUT = 120.0
SHUTDOWN_TIMEOUT = 120.0
REPLACE_TIMEOUT = 60.0
STARTUP_TIMEOUT = 120.0
STALE_SECONDS = 60 * 60
COMMIT_TIMEOUT = 15 * 60
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_STAGE = re.compile(re.escape(STAGE_PREFIX) + r"[0-9a-f]{32}\Z")
_PROTOCOL_FILES = (
    MANIFEST_NAME, "ready", "commit", "cancel", "ack", "complete",
    "error.json", "startup-error.json",
)
_OWNED_FILES = (
    MANIFEST_NAME, HELPER_NAME, UPDATE_NAME, BACKUP_NAME, "restore.exe",
    "ready", "commit", "cancel", "ack", "complete", "error.json", "startup-error.json",
) + tuple(name + ".pending" for name in _PROTOCOL_FILES)


class UpdateInstallError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, int]:
    info = path.stat()
    return {name: getattr(info, name) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns")}


def _regular_file(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _runtime_target() -> Path:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateInstallError("直接安装更新需要运行 Windows 单文件发行版；Python 源码运行方式不支持自动替换。")
    target = Path(sys.executable).absolute()
    bundle = Path(getattr(sys, "_MEIPASS", target.parent)).resolve()
    if bundle == target.parent or target.parent in bundle.parents or not bundle.name.startswith("_MEI"):
        raise UpdateInstallError("直接安装更新仅支持单文件发行版；文件夹发行版不能只替换可执行文件。")
    if target.suffix.lower() != ".exe" or target.resolve() != target or not _regular_file(target):
        raise UpdateInstallError("当前程序必须是位于固定位置的普通 Windows .exe 文件。")
    return target


def install_unavailable_reason() -> str:
    try:
        target = _runtime_target()
        if not os.access(target.parent, os.W_OK):
            return "当前程序所在文件夹不可写。请将程序移到有写入权限的文件夹后重试。"
    except (OSError, UpdateInstallError) as exc:
        return str(exc)
    return ""


def _kernel32():
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    return api


class _ParentProcess:
    def __init__(self, pid: int, expected_target: Path, expected_created: int | None = None):
        from ctypes import wintypes
        self.api = _kernel32()
        self.handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise UpdateInstallError("The original application process is no longer available.")
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(self.handle, *(ctypes.byref(value) for value in times)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not self.api.QueryFullProcessImageNameW(self.handle, 0, image, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            if Path(image.value).resolve() != expected_target or (
                expected_created is not None and self.created != expected_created
            ):
                raise UpdateInstallError("The original application process identity has changed.")
        except Exception:
            self.close()
            raise

    def exited(self) -> bool:
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _capture_parent(target: Path) -> dict[str, int]:
    parent = _ParentProcess(os.getpid(), target)
    try:
        return {"pid": os.getpid(), "created": parent.created}
    finally:
        parent.close()


def _open_parent(manifest: dict, target: Path):
    return _ParentProcess(manifest["parent"]["pid"], target, manifest["parent"]["created"])


def _write_json(path: Path, value: dict):
    # Readers must never observe a marker before its JSON is fully written.
    # Windows rename is atomic and refuses to overwrite an existing marker.
    temporary = path.with_name(path.name + ".pending")
    created = False
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            created = True
            json.dump(value, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise FileExistsError(f"Update protocol marker already exists: {path.name}")
        os.rename(temporary, path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def _mark(directory: Path, name: str, token: str):
    _write_json(directory / name, {"token": token})


def _marked(directory: Path, name: str, token: str) -> bool:
    path = directory / name
    if not path.exists():
        return False
    if not _regular_file(path) or path.stat().st_size > 4096:
        raise UpdateInstallError("An update protocol marker is invalid.")
    try:
        return json.loads(path.read_text(encoding="utf-8")) == {"token": token}
    except (ValueError, UnicodeError) as exc:
        raise UpdateInstallError("An update protocol marker is invalid.") from exc


def _child_env(directory: Path | None = None, token: str | None = None, *, error=False):
    env = dict(os.environ)
    for key in (ENV_MANIFEST, ENV_TOKEN, ENV_ERROR):
        env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        old_bundle = os.path.normcase(os.path.abspath(bundle))
        env["PATH"] = os.pathsep.join(
            entry for entry in env.get("PATH", "").split(os.pathsep)
            if os.path.normcase(os.path.abspath(entry)) != old_bundle
        )
    if directory is not None:
        env[ENV_MANIFEST] = str(directory / MANIFEST_NAME)
        env[ENV_TOKEN] = token
        if error:
            env[ENV_ERROR] = "1"
    return env


def _launch(arguments: list[str], *, env: dict, cwd: Path):
    return subprocess.Popen(
        arguments, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    )


def _stop_process(process):
    if process.poll() is not None:
        return
    # The Popen process is the onefile bootloader; its child also holds the EXE.
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    api.SetDllDirectoryW.restype = ctypes.c_int
    if not api.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        subprocess.run(
            [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000, timeout=15, check=False,
        )
    finally:
        api.SetDllDirectoryW(getattr(sys, "_MEIPASS", None))
    process.wait(timeout=15)


def _notify_install_failure(message: str):
    """Last-resort visible recovery guidance when no app could be restarted."""
    try:
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        api.MessageBoxW.restype = ctypes.c_int
        api.MessageBoxW(None, message, "SnipDo Translate 更新失败", 0x10 | 0x10000)
    except (AttributeError, OSError):
        pass


def _cleanup_directory(directory: Path):
    if not _STAGE.fullmatch(directory.name) or directory.resolve() != directory:
        return
    for name in _OWNED_FILES:
        path = directory / name
        try:
            if path.exists() and _regular_file(path):
                path.unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


class InstallJob:
    def __init__(self, directory: Path, target: Path, token: str):
        self.directory = directory
        self.target = target
        self._token = token
        self._process = None
        self._started_at = None
        self._committed = False
        self._cancelled = False

    def start(self):
        if self._process is not None or self._cancelled or self._committed:
            raise UpdateInstallError("This update job has already started or was cancelled.")
        try:
            self._process = _launch(
                [str(self.directory / HELPER_NAME), "--apply-update", str(self.directory / MANIFEST_NAME)],
                env=_child_env(), cwd=self.directory,
            )
            self._started_at = time.monotonic()
        except OSError as exc:
            raise UpdateInstallError(f"The update helper could not start: {exc}") from exc

    def is_ready(self) -> bool:
        try:
            return self._is_ready()
        except (OSError, ValueError) as exc:
            raise UpdateInstallError(f"The update helper status could not be read: {exc}") from exc

    def _is_ready(self) -> bool:
        if self._cancelled:
            raise UpdateInstallError("The update was cancelled.")
        error = _read_error(self.directory, self._token)
        if error:
            raise UpdateInstallError(error)
        if self._process is None:
            return False
        if self._process.poll() is not None:
            raise UpdateInstallError("The update helper exited before installation could begin.")
        if _marked(self.directory, "ready", self._token):
            return True
        if time.monotonic() - self._started_at > READY_TIMEOUT:
            raise UpdateInstallError("The update helper did not become ready in time. The installed application was not changed.")
        return False

    def commit(self):
        if self._committed or not self.is_ready():
            raise UpdateInstallError("The update helper is not ready for application shutdown.")
        _mark(self.directory, "commit", self._token)
        self._committed = True

    def cancel(self):
        try:
            self._cancel()
        except (OSError, ValueError):
            # GUI shutdown/cancellation callbacks must not throw. Without a
            # commit marker, a helper cannot change the installed executable.
            pass

    def _cancel(self):
        if self._committed:
            return
        if self._cancelled:
            return
        self._cancelled = True
        if self._process is None or self._process.poll() is not None:
            _cleanup_directory(self.directory)
            return
        try:
            _mark(self.directory, "cancel", self._token)
        except OSError:
            # Without a commit the helper cannot replace anything; preserve the
            # staging files until it exits even if cancellation cannot be saved.
            return

        def finish():
            try:
                self._process.wait(timeout=READY_TIMEOUT + COMMIT_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                return
            _cleanup_directory(self.directory)
        threading.Thread(target=finish, daemon=True, name="update-cancel-cleanup").start()

    def cleanup(self):
        self.cancel()


def prepare_install(download_path: Path, sha256: str, version: str) -> InstallJob:
    """Stage a verified download; the current executable remains untouched."""
    target = _runtime_target()
    if not isinstance(sha256, str) or not _DIGEST.fullmatch(sha256.lower()):
        raise UpdateInstallError("The update SHA-256 digest is invalid.")
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or len(version) > 64:
        raise UpdateInstallError("The update version is invalid.")
    download = Path(download_path).resolve()
    directory = target.parent / (STAGE_PREFIX + secrets.token_hex(16))
    created = False
    try:
        if download == target or not _regular_file(download):
            raise UpdateInstallError("The downloaded update must be a separate regular executable file.")
        identity = _identity(target)
        original_sha256 = _sha256(target)
        parent = _capture_parent(target)
        directory.mkdir(mode=0o700)
        created = True
        shutil.copy2(target, directory / HELPER_NAME)
        shutil.copyfile(download, directory / UPDATE_NAME)
        if _sha256(directory / UPDATE_NAME) != sha256.lower():
            raise UpdateInstallError("The downloaded update failed SHA-256 verification.")
        with (directory / UPDATE_NAME).open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise UpdateInstallError("The downloaded update is not a Windows executable.")
        if _identity(target) != identity or _sha256(directory / HELPER_NAME) != original_sha256:
            raise UpdateInstallError("The installed executable changed while the update was being prepared.")
        token = secrets.token_hex(32)
        manifest = {
            "schema": 1, "target_name": target.name, "original_identity": identity,
            "original_sha256": original_sha256, "sha256": sha256.lower(), "version": version,
            "parent": parent, "token": token, "created": time.time(),
        }
        _write_json(directory / MANIFEST_NAME, manifest)
        return InstallJob(directory, target, token)
    except (OSError, ValueError, UpdateInstallError) as exc:
        if created:
            _cleanup_directory(directory)
        if isinstance(exc, UpdateInstallError):
            raise
        raise UpdateInstallError(f"The update could not be staged in the application folder: {exc}") from exc


def _load_manifest(manifest_path: Path) -> tuple[dict, Path, Path]:
    path = Path(manifest_path).absolute()
    directory = path.parent
    if path.name != MANIFEST_NAME or not _STAGE.fullmatch(directory.name) or path.resolve() != path:
        raise UpdateInstallError("The update manifest path is not a confined staging path.")
    if not _regular_file(path) or path.stat().st_size > 16384:
        raise UpdateInstallError("The update manifest is invalid.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    fields = {"schema", "target_name", "original_identity", "original_sha256", "sha256", "version", "parent", "token", "created"}
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest["schema"] != 1:
        raise UpdateInstallError("The update manifest schema is invalid.")
    name = manifest["target_name"]
    if not isinstance(name, str) or Path(name).name != name or any(char in name for char in '<>:"/\\|?*') or not name.lower().endswith(".exe"):
        raise UpdateInstallError("The update target is not a confined executable path.")
    for field in ("original_sha256", "sha256", "token"):
        if not isinstance(manifest[field], str) or not _DIGEST.fullmatch(manifest[field]):
            raise UpdateInstallError("The update manifest digest or token is invalid.")
    if not isinstance(manifest["version"], str) or not _VERSION.fullmatch(manifest["version"]):
        raise UpdateInstallError("The update manifest version is invalid.")
    created = manifest["created"]
    if type(created) not in (int, float) or not 0 <= time.time() - created <= STALE_SECONDS:
        raise UpdateInstallError("This update helper is stale. Download and prepare the update again.")
    identity = manifest["original_identity"]
    if not isinstance(identity, dict) or set(identity) != {"st_dev", "st_ino", "st_size", "st_mtime_ns"} or any(type(v) is not int or v < 0 for v in identity.values()):
        raise UpdateInstallError("The original executable identity is invalid.")
    parent = manifest["parent"]
    if not isinstance(parent, dict) or set(parent) != {"pid", "created"} or any(type(v) is not int or v <= 0 for v in parent.values()):
        raise UpdateInstallError("The original process identity is invalid.")
    target = directory.parent / name
    if target.resolve() != target or not _regular_file(target):
        raise UpdateInstallError("The installed executable path has changed.")
    for child in directory.iterdir():
        if child.name not in _OWNED_FILES or not _regular_file(child):
            raise UpdateInstallError("The update staging directory contains unexpected files.")
    return manifest, directory, target


def _read_error(directory: Path, token: str, name: str = "error.json") -> str:
    path = directory / name
    if not path.exists():
        return ""
    if not _regular_file(path) or path.stat().st_size > 16384:
        raise UpdateInstallError("The update error marker is invalid.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise UpdateInstallError("The update error marker is invalid.") from exc
    if isinstance(payload, dict) and payload.get("token") == token and isinstance(payload.get("message"), str):
        return payload["message"]
    raise UpdateInstallError("The update error marker is invalid.")


def _wait_until(predicate, timeout: float, message: str):
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise UpdateInstallError(message)
        time.sleep(0.1)


def _replace_with_retry(source: Path, target: Path):
    def attempt():
        try:
            os.replace(source, target)
            return True
        except PermissionError:
            return False
    _wait_until(attempt, REPLACE_TIMEOUT, "Windows still has the executable locked. Close other running copies and retry the update.")


def run_apply_update(manifest_path: str | Path) -> int:
    """Internal helper dispatch. Never call this from the GUI process."""
    parent = None
    manifest = directory = target = None
    child = None
    replaced = False
    shutdown_finished = False
    try:
        manifest, directory, target = _load_manifest(Path(manifest_path))
        helper = directory / HELPER_NAME
        if _runtime_target() != helper or _sha256(helper) != manifest["original_sha256"]:
            raise UpdateInstallError("Only the detached copy of the original application can apply this update.")
        if _identity(target) != manifest["original_identity"] or _sha256(target) != manifest["original_sha256"]:
            raise UpdateInstallError("The installed executable changed after preparation; this update was cancelled.")
        if _sha256(directory / UPDATE_NAME) != manifest["sha256"]:
            raise UpdateInstallError("The staged update failed SHA-256 verification.")
        if _marked(directory, "cancel", manifest["token"]):
            return 0
        if (directory / "ready").exists() or (directory / "commit").exists() or (directory / BACKUP_NAME).exists():
            raise UpdateInstallError("This update helper has already run.")
        parent = _open_parent(manifest, target)
        _mark(directory, "ready", manifest["token"])

        def committed():
            if _marked(directory, "cancel", manifest["token"]):
                raise UpdateInstallError("The update was cancelled before shutdown.")
            if parent.exited() and not _marked(directory, "commit", manifest["token"]):
                raise UpdateInstallError("The application exited without authorizing installation.")
            return _marked(directory, "commit", manifest["token"])
        _wait_until(committed, COMMIT_TIMEOUT, "The application did not authorize installation in time.")
        _wait_until(parent.exited, SHUTDOWN_TIMEOUT, "The application did not finish shutting down; the installed executable was not changed.")
        shutdown_finished = True
        if _identity(target) != manifest["original_identity"] or _sha256(target) != manifest["original_sha256"]:
            raise UpdateInstallError("The installed executable changed during shutdown; it was not replaced.")
        if _sha256(directory / UPDATE_NAME) != manifest["sha256"]:
            raise UpdateInstallError("The staged update changed before installation.")
        backup = directory / BACKUP_NAME
        shutil.copy2(target, backup)
        if _sha256(backup) != manifest["original_sha256"]:
            raise UpdateInstallError("The original executable backup could not be verified.")
        _replace_with_retry(directory / UPDATE_NAME, target)
        replaced = True
        child = _launch([str(target), "--show"], env=_child_env(directory, manifest["token"]), cwd=target.parent)

        def started():
            error = _read_error(directory, manifest["token"], "startup-error.json")
            if error:
                raise UpdateInstallError(error)
            if _marked(directory, "ack", manifest["token"]):
                return True
            if child.poll() is not None:
                raise UpdateInstallError("The new version exited before its window and IPC server were ready.")
            return False
        _wait_until(started, STARTUP_TIMEOUT, "The new version did not acknowledge startup in time.")
        _mark(directory, "complete", manifest["token"])
        return 0
    except Exception as exc:
        message = f"Update installation failed: {exc}"
        needs_relaunch = shutdown_finished
        if replaced:
            try:
                if child is not None:
                    _stop_process(child)
                backup = directory / BACKUP_NAME
                if _sha256(backup) != manifest["original_sha256"]:
                    raise UpdateInstallError("The original backup failed verification.")
                shutil.copy2(backup, directory / "restore.exe")
                _replace_with_retry(directory / "restore.exe", target)
                message += " The previous version was restored."
            except Exception as rollback_error:
                message += f" Automatic rollback also failed: {rollback_error}. Restore the backup at {directory / BACKUP_NAME}."
                shutdown_finished = False
        if directory is not None and manifest is not None:
            try:
                _write_json(directory / "error.json", {"token": manifest["token"], "message": message})
            except OSError:
                pass
            if shutdown_finished:
                try:
                    if _sha256(target) == manifest["original_sha256"]:
                        _launch([str(target), "--show"], env=_child_env(directory, manifest["token"], error=True), cwd=target.parent)
                        needs_relaunch = False
                except OSError:
                    pass
            if needs_relaunch:
                _notify_install_failure(message + f"\n\n程序路径：{target}\n备份位置：{directory / BACKUP_NAME}")
        return 1
    finally:
        if parent is not None:
            parent.close()


def _startup_context():
    raw = os.environ.get(ENV_MANIFEST)
    token = os.environ.get(ENV_TOKEN)
    if not raw or not token:
        return None
    manifest, directory, target = _load_manifest(Path(raw))
    if _runtime_target() != target or token != manifest["token"]:
        raise UpdateInstallError("The update startup acknowledgement does not match this executable.")
    return manifest, directory, target


def acknowledge_update_startup(version: str | None = None) -> bool:
    """Call after the primary application's UI and IPC server are initialized."""
    try:
        context = _startup_context()
        if context is None or os.environ.get(ENV_ERROR) == "1":
            return False
        manifest, directory, target = context
        if version != manifest["version"]:
            _write_json(directory / "startup-error.json", {
                "token": manifest["token"],
                "message": f"The downloaded executable reports version {version!r}, but release {manifest['version']} was requested.",
            })
            return False
        if _sha256(target) != manifest["sha256"] or not _marked(directory, "commit", manifest["token"]):
            return False
        _mark(directory, "ack", manifest["token"])
    except (OSError, ValueError, UpdateInstallError):
        return False
    for name in (ENV_MANIFEST, ENV_TOKEN, ENV_ERROR):
        os.environ.pop(name, None)

    def finish():
        try:
            _wait_until(lambda: _marked(directory, "complete", manifest["token"]), STARTUP_TIMEOUT, "Update completion timed out.")
            deadline = time.monotonic() + REPLACE_TIMEOUT
            while directory.exists() and time.monotonic() < deadline:
                _cleanup_directory(directory)
                if directory.exists():
                    time.sleep(0.5)
        except (OSError, ValueError, UpdateInstallError):
            return
    threading.Thread(target=finish, daemon=True, name="update-success-cleanup").start()
    return True


def consume_update_error() -> str:
    try:
        if os.environ.get(ENV_ERROR) != "1":
            return ""
        context = _startup_context()
        if context is None:
            return ""
        manifest, directory, target = context
        if _sha256(target) != manifest["original_sha256"]:
            return ""
        return _read_error(directory, manifest["token"])
    except (OSError, ValueError, UpdateInstallError):
        return ""
    finally:
        if os.environ.get(ENV_ERROR) == "1":
            for name in (ENV_MANIFEST, ENV_TOKEN, ENV_ERROR):
                os.environ.pop(name, None)
