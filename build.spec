# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包設定。

    pyinstaller build.spec --noconfirm

產生 dist/NarakaStarMonitor/NarakaStarMonitor.exe（onedir 啟動較快）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).resolve()

# plyer 是 namespace package，各後端為動態載入，需明確收集
hiddenimports = (
    collect_submodules("plyer")
    + collect_submodules("customtkinter")
    + ["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"]
)

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NarakaStarMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(project_root / "assets" / "icon.ico")
    if (project_root / "assets" / "icon.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NarakaStarMonitor",
)
