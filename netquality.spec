# PyInstaller spec for NetQuality.
# Build:  .venv\Scripts\pyinstaller netquality.spec --noconfirm
# Output: dist\NetQuality.exe  (single-file, windowed)
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# Only the Qt modules the app uses; excluding the rest keeps the exe small.
QT_EXCLUDES = [
    "PyQt6.QtNetwork", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets",
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebChannel",
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtSql", "PyQt6.QtTest",
    "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtSvg", "PyQt6.QtSvgWidgets",
    "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets", "PyQt6.QtBluetooth", "PyQt6.QtPositioning",
    "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtDesigner", "PyQt6.QtHelp",
    "PyQt6.Qt3DCore", "PyQt6.QtCharts", "PyQt6.QtDataVisualization",
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "pytest", "numpy", *QT_EXCLUDES],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NetQuality",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "python3*.dll", "Qt6*.dll"],
    runtime_tmpdir=None,
    console=False,          # windowed app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
