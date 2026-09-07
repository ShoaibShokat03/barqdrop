# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BarqDrop.

Produces a self-contained Windows application in dist/BarqDrop that runs on a
machine with no Python installed. Set BARQDROP_ONEFILE=1 to collapse it into a
single portable .exe instead of a folder.
"""
import os

from PyInstaller.utils.hooks import collect_submodules

ONEFILE = os.environ.get("BARQDROP_ONEFILE") == "1"
ROOT = os.path.abspath(os.getcwd())
ICON = os.path.join(ROOT, "assets", "barqdrop.ico")

hidden = []
try:
    hidden += collect_submodules("winsdk.windows.networking")
    hidden += ["winsdk.windows.networking.networkoperators",
               "winsdk.windows.networking.connectivity"]
except Exception:
    pass  # Wi-Fi Direct hotspot support is optional

# Qt ships far more than a file-transfer UI needs; leaving these out keeps the
# package roughly half the size and speeds up startup.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtWebSockets", "PySide6.QtWebChannel",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtSerialPort", "PySide6.QtNfc", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtTextToSpeech",
    "tkinter", "unittest", "pydoc_data", "test", "matplotlib", "numpy", "PIL",
]

datas = []
if os.path.exists(ICON):
    datas.append((ICON, "assets"))

a = Analysis(
    ["run_barqdrop.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_kwargs = dict(
    name="BarqDrop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI application, no console window
    disable_windowed_traceback=False,
    icon=ICON if os.path.exists(ICON) else None,
    version=None,
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_kwargs)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_kwargs)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BarqDrop")
