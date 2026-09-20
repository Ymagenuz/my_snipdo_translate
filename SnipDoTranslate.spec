# -*- mode: python ; coding: utf-8 -*-

import os
import runpy
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


ROOT = Path(SPECPATH).resolve()
release = runpy.run_path(str(ROOT / "app_version.py"))
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=release["WINDOWS_VERSION"],
        prodvers=release["WINDOWS_VERSION"],
        mask=0x3F,
        flags=0,
        OS=0x40004,
        fileType=0x1,
        subtype=0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("FileDescription", "SnipDo Translate"),
                StringStruct("FileVersion", release["APP_VERSION"]),
                StringStruct("InternalName", "SnipDoTranslate"),
                StringStruct("OriginalFilename", "SnipDoTranslate.exe"),
                StringStruct("ProductName", "SnipDo Translate"),
                StringStruct("ProductVersion", release["APP_VERSION"]),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)
BUILD_MODE = os.environ.get("SNIPDO_BUILD_MODE", "onefile").strip().lower()
if BUILD_MODE not in {"onedir", "onefile"}:
    raise ValueError(f"Unsupported SNIPDO_BUILD_MODE: {BUILD_MODE}")

ENTRY_POINT = ROOT / "snipdo_translate.pyw"
APP_IMAGE = ROOT / "snipdo_script_logo" / "snipdo-translate-enabled.png"
DISABLED_APP_IMAGE = ROOT / "snipdo_script_logo" / "snipdo-translate-disabled.png"
WINDOWS_ICON = ROOT / "snipdo_script_logo" / "SnipDoTranslate.ico"
if not ENTRY_POINT.is_file():
    raise FileNotFoundError(f"Entry point not found: {ENTRY_POINT}")
if not APP_IMAGE.is_file():
    raise FileNotFoundError(f"Application image not found: {APP_IMAGE}")
if not DISABLED_APP_IMAGE.is_file():
    raise FileNotFoundError(f"Disabled application image not found: {DISABLED_APP_IMAGE}")
if not WINDOWS_ICON.is_file():
    raise FileNotFoundError(f"Windows icon not found: {WINDOWS_ICON}")

# Resolve Windows-provided libraries before unrelated tools on PATH. In
# particular, Qt uses Windows' unversioned ICU API; a third-party icuuc.dll
# (for example from Poppler) has incompatible exports and breaks QtCore import.
system_directory = Path(os.environ["SystemRoot"]) / "System32"
os.environ["PATH"] = str(system_directory) + os.pathsep + os.environ.get("PATH", "")

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(APP_IMAGE), "snipdo_script_logo"),
        (str(DISABLED_APP_IMAGE), "snipdo_script_logo"),
    ],
    hiddenimports=collect_submodules("openai"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if BUILD_MODE == "onedir":
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
        icon=str(WINDOWS_ICON),
        version=version_info,
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
        icon=str(WINDOWS_ICON),
        version=version_info,
    )
