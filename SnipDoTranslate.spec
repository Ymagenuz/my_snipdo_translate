# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve()
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
    )
