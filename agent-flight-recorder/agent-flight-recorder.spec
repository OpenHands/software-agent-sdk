# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


ROOT = Path.cwd()
PACKAGE = ROOT / "agent-flight-recorder"

a = Analysis(
    [str(PACKAGE / "src" / "flight_recorder" / "__main__.py")],
    pathex=[str(PACKAGE / "src"), str(ROOT / "openhands-sdk")],
    binaries=[],
    datas=[
        *collect_data_files("litellm"),
        *collect_data_files("tiktoken"),
        *copy_metadata("openhands-agent-flight-recorder"),
        *copy_metadata("openhands-sdk"),
        *copy_metadata("fastmcp"),
        *copy_metadata("litellm"),
    ],
    hiddenimports=[
        *collect_submodules("flight_recorder"),
        *collect_submodules("openhands.sdk"),
        *collect_submodules("tiktoken"),
        *collect_submodules("tiktoken_ext"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agent-flight-recorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)