from __future__ import annotations

from pathlib import Path

import pytest

import windows_startup as startup


class _RegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _RegistryHarness:
    HKEY_CURRENT_USER = "current user"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, *, key_exists=True, values=None, error_at=None):
        self.key_exists = key_exists
        self.values = dict(values or {})
        self.error_at = error_at
        self.calls = []

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if self.error_at == name:
            raise PermissionError("access denied")

    def OpenKey(self, root, path, reserved, access):
        self._call("open", root, path, reserved, access)
        assert root == self.HKEY_CURRENT_USER
        assert path == startup.RUN_KEY
        if not self.key_exists:
            raise FileNotFoundError("missing key")
        return _RegistryKey()

    def CreateKeyEx(self, root, path, reserved, access):
        self._call("create", root, path, reserved, access)
        assert root == self.HKEY_CURRENT_USER
        assert path == startup.RUN_KEY
        self.key_exists = True
        return _RegistryKey()

    def QueryValueEx(self, key, name):
        self._call("query", name)
        assert isinstance(key, _RegistryKey)
        if name not in self.values:
            raise FileNotFoundError("missing value")
        return self.values[name]

    def SetValueEx(self, key, name, reserved, value_type, value):
        self._call("set", name, reserved, value_type, value)
        assert isinstance(key, _RegistryKey)
        self.values[name] = (value, value_type)

    def DeleteValue(self, key, name):
        self._call("delete", name)
        assert isinstance(key, _RegistryKey)
        if name not in self.values:
            raise FileNotFoundError("missing value")
        del self.values[name]


@pytest.fixture
def launch_files(monkeypatch):
    # Keep the command independent of the host's often very long temp root.
    source = Path(r"C:\Source 译文\snipdo_translate.pyw")
    executable = Path(r"C:\Python 安装\python.exe")
    pythonw = executable.with_name("pythonw.exe")
    existing = {source, executable, pythonw}
    monkeypatch.setattr(startup, "_is_file", lambda path: path in existing)
    return source, executable, pythonw


def test_source_command_quotes_gui_python_and_absolute_unicode_script(launch_files):
    source, executable, pythonw = launch_files

    command = startup.build_startup_command(source, executable=executable, frozen=False)

    assert command == f'"{pythonw}" "{source}" --startup'
    assert "python.exe\"" not in command


def test_source_command_resolves_relative_script_and_pythonw_launcher(launch_files, tmp_path, monkeypatch):
    _, _, pythonw = launch_files
    source = tmp_path / "app.pyw"
    monkeypatch.chdir(tmp_path)

    command = startup.build_startup_command(
        source.name, executable=pythonw, frozen=False, file_exists=lambda _path: True
    )

    assert command == f'"{pythonw}" "{source}" --startup'


def test_frozen_default_uses_sys_executable_and_ignores_extracted_bundle(tmp_path, monkeypatch):
    executable = tmp_path / "Installed app" / "SnipDoTranslate.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    monkeypatch.setattr(startup.sys, "executable", str(executable))
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "_MEIPASS", str(tmp_path / "_MEI123"), raising=False)

    command = startup.build_startup_command(tmp_path / "_MEI123" / "snipdo_translate.pyw")

    assert command == f'"{executable}" --startup'
    assert "_MEI123" not in command


def test_source_requires_pythonw_and_never_falls_back_to_console(launch_files, monkeypatch):
    source, executable, pythonw = launch_files
    monkeypatch.setattr(startup, "_is_file", lambda path: path != pythonw)

    with pytest.raises(startup.StartupRegistrationError, match="requires pythonw.exe"):
        startup.build_startup_command(source, executable=executable, frozen=False)


@pytest.mark.parametrize("missing", ["source", "frozen executable"])
def test_missing_startup_file_rejected(launch_files, missing, monkeypatch):
    source, executable, _ = launch_files
    missing_path = source if missing == "source" else executable
    monkeypatch.setattr(startup, "_is_file", lambda path: path != missing_path)

    with pytest.raises(startup.StartupRegistrationError, match="does not exist"):
        startup.build_startup_command(
            source, executable=executable, frozen=missing != "source"
        )


@pytest.mark.parametrize(
    "invalid",
    [
        "", 'C:\\bad"name\\app.exe', "C:\\bad\nname\\app.exe",
        "C:\\bad\x00name\\app.exe", "C:\\bad\x7fname\\app.exe",
        "C:\\bad|name\\app.exe", "C:\\NUL\\app.exe",
        "C:\\bad.\\app.exe", "C:\\bad \\app.exe", "C:\\app.cmd",
        "\\\\.\\device.exe", "\\\\?\\C:\\app.exe",
        '\\\\bad"server\\share\\app.exe', "\\\\bad:server\\share\\app.exe",
    ],
)
def test_invalid_executable_paths_rejected(invalid):
    with pytest.raises(startup.StartupRegistrationError):
        startup.build_startup_command(
            "unused.pyw", executable=invalid, frozen=True, file_exists=lambda _path: True
        )


@pytest.mark.parametrize("invalid", ['C:\\bad"name\\app.pyw', "C:\\bad\rname\\app.pyw", "C:\\app.py"])
def test_invalid_source_paths_rejected(invalid):
    with pytest.raises(startup.StartupRegistrationError):
        startup.build_startup_command(
            invalid, executable=r"C:\Python\python.exe", frozen=False,
            file_exists=lambda _path: True,
        )


@pytest.mark.parametrize("length", [260, 261])
def test_run_command_length_boundary(length):
    # Quoted absolute executable plus the startup flag has 19 fixed characters.
    executable = "C:\\" + ("x" * (length - 19)) + ".exe"
    build = lambda: startup.build_startup_command(
        "unused.pyw", executable=executable, frozen=True, file_exists=lambda _path: True
    )
    if length == 260:
        assert len(build()) == 260
    else:
        with pytest.raises(startup.StartupRegistrationError, match="260 characters"):
            build()


def test_run_limit_counts_unicode_utf16_units():
    executable = "C:\\" + ("😀" * 122) + ".exe"
    with pytest.raises(startup.StartupRegistrationError, match="260 characters"):
        startup.build_startup_command(
            "unused.pyw", executable=executable, frozen=True, file_exists=lambda _path: True
        )


def test_constructing_registration_does_not_touch_registry_or_validate_files():
    registry = _RegistryHarness()
    startup.WindowsStartupRegistration("missing.pyw", executable="missing.exe", winreg_api=registry)

    assert registry.calls == []


@pytest.mark.parametrize("key_exists", [True, False])
def test_absence_defaults_to_disabled_without_writes(key_exists):
    registry = _RegistryHarness(key_exists=key_exists)
    registration = startup.WindowsStartupRegistration("missing.pyw", winreg_api=registry)

    assert not registration.is_enabled()
    assert registry.values == {}
    assert all(call[0] in ("open", "query") for call in registry.calls)
    assert registry.calls[0][-1] == registry.KEY_QUERY_VALUE


def test_registry_is_source_of_truth_including_prior_install():
    registry = _RegistryHarness()
    registration = startup.WindowsStartupRegistration("missing.pyw", winreg_api=registry)
    assert not registration.is_enabled()

    registry.values[startup.VALUE_NAME] = ('"C:\\old install\\app.exe" --startup', registry.REG_SZ)
    assert registration.is_enabled()
    registry.values.pop(startup.VALUE_NAME)
    assert not registration.is_enabled()


@pytest.mark.parametrize("value", [("", 1), ("   ", 1), ("command", 2), (7, 4)])
def test_empty_or_non_string_registration_is_disabled(value):
    registry = _RegistryHarness(values={startup.VALUE_NAME: value})
    registration = startup.WindowsStartupRegistration("missing.pyw", winreg_api=registry)

    assert not registration.is_enabled()


def test_enable_creates_only_current_user_value_as_reg_sz(launch_files):
    source, executable, pythonw = launch_files
    unrelated = ('"C:\\Other App\\other.exe"', 1)
    registry = _RegistryHarness(key_exists=False, values={"OtherApp": unrelated})
    registration = startup.WindowsStartupRegistration(
        source, executable=executable, frozen=False, winreg_api=registry
    )

    registration.set_enabled(True)

    assert registry.values == {
        "OtherApp": unrelated,
        startup.VALUE_NAME: (f'"{pythonw}" "{source}" --startup', registry.REG_SZ),
    }
    assert registry.calls[0] == ("create", registry.HKEY_CURRENT_USER, startup.RUN_KEY, 0, registry.KEY_SET_VALUE)
    assert registry.calls[1][:4] == ("set", startup.VALUE_NAME, 0, registry.REG_SZ)
    assert registration.is_enabled()


def test_invalid_command_does_not_open_or_write_registry(launch_files, monkeypatch):
    source, executable, pythonw = launch_files
    monkeypatch.setattr(startup, "_is_file", lambda path: path != pythonw)
    registry = _RegistryHarness()
    registration = startup.WindowsStartupRegistration(
        source, executable=executable, frozen=False, winreg_api=registry
    )

    with pytest.raises(startup.StartupRegistrationError):
        registration.set_enabled(True)

    assert registry.calls == []


def test_disable_removes_only_own_value_even_with_missing_launch_files():
    unrelated = ("another command", 1)
    registry = _RegistryHarness(values={startup.VALUE_NAME: ("old command", 1), "OtherApp": unrelated})
    registration = startup.WindowsStartupRegistration("missing.pyw", executable="missing.exe", winreg_api=registry)

    registration.set_enabled(False)
    registration.set_enabled(False)

    assert registry.values == {"OtherApp": unrelated}
    assert all(call[0] in ("open", "delete") for call in registry.calls)
    assert registry.calls[0] == ("open", registry.HKEY_CURRENT_USER, startup.RUN_KEY, 0, registry.KEY_SET_VALUE)
    assert all(call[1] == startup.VALUE_NAME for call in registry.calls if call[0] == "delete")


def test_disable_missing_key_does_not_create_it():
    registry = _RegistryHarness(key_exists=False)
    registration = startup.WindowsStartupRegistration("missing.pyw", winreg_api=registry)

    registration.set_enabled(False)

    assert not registry.key_exists
    assert [call[0] for call in registry.calls] == ["open"]


@pytest.mark.parametrize("operation", ["open", "query", "create", "set", "delete"])
def test_registry_permission_errors_are_reported(launch_files, operation):
    source, executable, _ = launch_files
    registry = _RegistryHarness(values={startup.VALUE_NAME: ("old command", 1)}, error_at=operation)
    registration = startup.WindowsStartupRegistration(
        source, executable=executable, frozen=False, winreg_api=registry
    )

    with pytest.raises(startup.StartupRegistrationError, match="access denied"):
        if operation in ("open", "query"):
            registration.is_enabled()
        else:
            registration.set_enabled(operation in ("create", "set"))

    assert registry.values == {startup.VALUE_NAME: ("old command", 1)}


def test_missing_registry_backend_reports_failure(monkeypatch):
    monkeypatch.setattr(startup, "_WINREG", None)
    registration = startup.WindowsStartupRegistration("missing.pyw")

    with pytest.raises(startup.StartupRegistrationError, match="unavailable"):
        registration.is_enabled()
    with pytest.raises(startup.StartupRegistrationError, match="unavailable"):
        registration.set_enabled(False)


@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_enabled_argument_requires_bool(value):
    registry = _RegistryHarness()
    registration = startup.WindowsStartupRegistration("missing.pyw", winreg_api=registry)

    with pytest.raises(TypeError, match="bool"):
        registration.set_enabled(value)
    assert registry.calls == []
