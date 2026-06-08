# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

block_cipher = None

project_root = Path(SPECPATH).parents[1]
src_root = project_root / "src"

os.environ.setdefault("COREFLOW_BUILD_CHANNEL", "packaged")
build_stamp_hook = Path(SPECPATH) / "generated_build_stamp.py"
runtime_hooks = [str(build_stamp_hook)] if build_stamp_hook.exists() else []


def collect_conda_qt_binaries():
    """Collect conda Qt/Shiboken DLLs that wheel-oriented hooks can miss."""

    library_bin = Path(sys.prefix) / "Library" / "bin"
    if not library_bin.exists():
        return []

    names = {
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Network.dll",
        "Qt6OpenGL.dll",
        "Qt6OpenGLWidgets.dll",
        "Qt6Svg.dll",
        "Qt6Test.dll",
        "Qt6Widgets.dll",
    }
    patterns = ("pyside6*.dll", "shiboken6*.dll")
    binaries = [(str(library_bin / name), ".") for name in names if (library_bin / name).exists()]
    for pattern in patterns:
        binaries.extend((str(path), ".") for path in library_bin.glob(pattern))
    return binaries


a = Analysis(
    [str(src_root / "coreflow" / "__main__.py")],
    pathex=[str(src_root), str(project_root)],
    binaries=[
        (src, "PySide6") for src, _dest in collect_dynamic_libs("PySide6")
    ]
    + [
        (src, "PySide6") for src, _dest in collect_dynamic_libs("shiboken6")
    ]
    + collect_conda_qt_binaries(),
    datas=[],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtOpenGL",
        "PySide6.QtWidgets",
        "pyqtgraph",
        "serial.tools.list_ports",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[],
    noarchive=False,
)
a.binaries = [
    binary
    for binary in a.binaries
    if Path(binary[0]).name.lower() not in {"icudt73.dll", "icuuc.dll"}
]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CoreFlowStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

console_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CoreFlowStudioConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    gui_exe,
    console_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CoreFlowStudio",
)
