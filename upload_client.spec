# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the STS-Upload operator GUI (Issue #15, Slice 2).

Builds a single-file, windowed binary. A Windows ``.exe`` MUST be built on
Windows (PyInstaller does not cross-compile); the same spec produces a Linux
binary when run on Linux, which is used as a packaging smoke test.

    python -m pip install -r requirements-client.txt pyinstaller
    pyinstaller upload_client.spec --noconfirm

Output: ``dist/STS-Upload`` (``.exe`` on Windows).
"""

from PyInstaller.utils.hooks import collect_submodules

# Bundle the whole client package so dynamically-referenced submodules
# (ui.*, mounts, mount_watcher) are always present. PySide6 is picked up by
# PyInstaller's bundled hooks.
hidden = collect_submodules("upload_client")

a = Analysis(
    ["scripts/sts_upload_gui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="STS-Upload",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # windowed GUI - no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
