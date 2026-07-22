from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from PyQt6.QtGui import QImage


def test_spec_supports_windowed_onedir_and_onefile(project_root: Path):
    spec = (project_root / "SnipDoTranslate.spec").read_text(encoding="utf-8")
    compile(spec, "SnipDoTranslate.spec", "exec")

    assert "import os" in spec
    assert 'os.environ.get("SNIPDO_BUILD_MODE", "onefile")' in spec
    assert '{"onedir", "onefile"}' in spec
    assert 'BUILD_MODE == "onedir"' in spec
    assert "COLLECT(" in spec
    assert spec.count("console=False") == 2
    assert 'ROOT / "snipdo_translate.pyw"' in spec
    assert 'ROOT / "snipdo_script_logo" / "snipdo-translate-enabled.png"' in spec
    assert 'ROOT / "snipdo_script_logo" / "snipdo-translate-disabled.png"' in spec
    assert 'ROOT / "snipdo_script_logo" / "SnipDoTranslate.ico"' in spec
    assert '(str(APP_IMAGE), "snipdo_script_logo")' in spec
    assert '(str(DISABLED_APP_IMAGE), "snipdo_script_logo")' in spec
    assert spec.count("icon=str(WINDOWS_ICON)") == 2


def test_spec_does_not_collect_private_or_development_trees(project_root: Path):
    spec = (project_root / "SnipDoTranslate.spec").read_text(encoding="utf-8").lower()

    for forbidden in (
        ".gptsapi_api_key",
        "translation_history.json",
        ".venv",
        "legacy",
        "diagnostics",
        ".git",
    ):
        assert forbidden not in spec


def test_build_pipeline_orders_tests_onedir_check_and_onefile(project_root: Path):
    script = (project_root / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert '.venv\\Scripts\\python.exe' in script
    assert '"-m", "pytest", "-q"' in script
    assert '"-m", "PyInstaller"' in script
    assert 'scripts\\check_artifact.ps1' in script
    assert 'build\\pyinstaller' in script
    assert 'Join-Path $distRoot "preflight-onedir"' in script
    assert 'Join-Path $distRoot "SnipDoTranslate.exe"' in script
    assert "struct.calcsize('P') == 8" in script
    assert "[System.IO.FileAttributes]::ReparsePoint" in script
    assert '@("scripts\\create_icon.py", "--check")' in script

    tests_gate = script.index('"-m", "pytest", "-q"')
    onedir_build = script.index('$env:SNIPDO_BUILD_MODE = "onedir"')
    onedir_check = script.index("& $checkArtifact", onedir_build)
    onefile_build = script.index('$env:SNIPDO_BUILD_MODE = "onefile"')
    final_check = script.index("& $checkArtifact", onefile_build)
    assert tests_gate < onedir_build < onedir_check < onefile_build < final_check


def test_checked_in_icon_is_reproducible_and_has_windows_sizes(project_root: Path):
    generator = project_root / "scripts" / "create_icon.py"
    source = generator.read_text(encoding="utf-8")
    assert "Pillow" not in source
    assert "from PIL" not in source
    assert "ICON_SIZES = (16, 32, 48, 64, 128, 256)" in source

    result = subprocess.run(
        [sys.executable, str(generator), "--check"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    image_paths = (
        project_root / "snipdo_script_logo" / "snipdo-translate-enabled.png",
        project_root / "snipdo_script_logo" / "snipdo-translate-disabled.png",
    )
    enabled_image, disabled_image = (QImage(str(path)) for path in image_paths)
    for app_image in (enabled_image, disabled_image):
        assert not app_image.isNull()
        assert (app_image.width(), app_image.height()) == (512, 512)
        assert app_image.hasAlphaChannel()
        for x, y in ((0, 0), (511, 0), (0, 511), (511, 511), (256, 256)):
            assert app_image.pixelColor(x, y).alpha() == 0
        assert app_image.pixelColor(100, 168).alpha() == 255
        assert app_image.pixelColor(400, 344).alpha() == 255
        assert app_image.pixelColor(188, 180).getRgb() == (255, 255, 255, 255)
        assert app_image.pixelColor(320, 329).getRgb() == (255, 255, 255, 255)

    assert enabled_image.createAlphaMask() == disabled_image.createAlphaMask()
    assert enabled_image.pixelColor(100, 168) != disabled_image.pixelColor(100, 168)
    assert enabled_image.pixelColor(400, 344) != disabled_image.pixelColor(400, 344)

    nontransparent = [
        (x, y)
        for y in range(enabled_image.height())
        for x in range(enabled_image.width())
        if enabled_image.pixelColor(x, y).alpha() > 0
    ]
    minimum_x = min(x for x, _y in nontransparent)
    maximum_x = max(x for x, _y in nontransparent)
    minimum_y = min(y for _x, y in nontransparent)
    maximum_y = max(y for _x, y in nontransparent)
    assert maximum_x - minimum_x + 1 >= 460
    assert maximum_y - minimum_y + 1 >= 390

    for icon_name in ("SnipDoTranslate.ico", "SnipDoTranslate-disabled.ico"):
        icon = (project_root / "snipdo_script_logo" / icon_name).read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", icon)
        assert (reserved, image_type, count) == (0, 1, 6)
        declared_sizes = []
        for index in range(count):
            entry = struct.unpack_from("<BBBBHHII", icon, 6 + 16 * index)
            width_byte, height_byte, _colors, _reserved, planes, bits, length, offset = entry
            size = 256 if width_byte == 0 else width_byte
            declared_sizes.append(size)
            assert height_byte == (0 if size == 256 else size)
            assert (planes, bits) == (1, 32)
            image = icon[offset : offset + length]
            assert image.startswith(b"\x89PNG\r\n\x1a\n")
            assert struct.unpack_from(">II", image, 16) == (size, size)
        assert declared_sizes == [16, 32, 48, 64, 128, 256]


def test_active_branding_uses_snipdo_translate_only(project_root: Path):
    entry_point = project_root / "snipdo_translate.pyw"
    source = entry_point.read_text(encoding="utf-8")

    assert 'APP_DISPLAY_NAME = "SnipDo Translate"' in source
    assert "gemini" not in source.casefold()
    assert not (project_root / "gemini_translate.pyw").exists()
    assert not (project_root / "start_gemini_translate.cmd").exists()
    assert not (project_root / "snipdo_script_logo" / "gemini-color.png").exists()


def test_artifact_checker_enforces_offline_x64_unsigned_hash_contract(
    project_root: Path,
):
    script = (project_root / "scripts" / "check_artifact.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "0x4D",
        "0x5A",
        "0x8664",
        "0x020B",
        "Get-AuthenticodeSignature",
        '"NotSigned"',
        '@("--self-test", "offline")',
        "WaitForExit",
        "Get-FileHash -Algorithm SHA256",
        '"$resolvedExe.sha256"',
        "[System.Text.Encoding]::ASCII",
    ):
        assert required in script

    for network_primitive in (
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "System.Net.Http",
        "api.openai.com",
        "api.gptsapi.net",
    ):
        assert network_primitive not in script


@pytest.mark.parametrize("relative_path", ["scripts/build_windows.ps1", "scripts/check_artifact.ps1"])
def test_powershell_scripts_parse_on_windows(project_root: Path, relative_path: str):
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell parser is only available on Windows")

    script_path = str(project_root / relative_path).replace("'", "''")
    parser_command = (
        "$tokens = $null; $errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script_path}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", parser_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
