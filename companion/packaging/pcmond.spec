# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for pcmond: single-file, windowed exe.
# Built by packaging/build_exe.ps1 (run: powershell -File packaging/build_exe.ps1).

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "assets", "pcmond.ico")
ENTRY = os.path.join(SPECPATH, "entry.py")

# Conda 的 Python 把 ffi/lzma/ssl 等 DLL 放在 base_prefix/Library/bin，PyInstaller
# 默认解析不到（_ctypes 会 "DLL load failed"），这里显式带上。
CONDA_LIB = os.path.join(sys.base_prefix, "Library", "bin")
CONDA_DLLS = (
    "ffi.dll",
    "ffi-7.dll",
    "ffi-8.dll",
    "liblzma.dll",
    "libbz2.dll",
    "libexpat.dll",
    "libssl-3-x64.dll",
    "libcrypto-3-x64.dll",
    "libzstd.dll",
)
binaries = []
for dll in CONDA_DLLS:
    path = os.path.join(CONDA_LIB, dll)
    if os.path.exists(path):
        binaries.append((path, "."))
    else:
        print(f"WARNING: 缺少 DLL {path}")

hiddenimports = [
    # tray (pystray picks its backend dynamically)
    "pystray",
    "pystray._win32",
    "pystray._util",
    # Pillow (icon drawing + tray image)
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL._tkinter_finder",
    # zstd log decoding
    "zstandard",
    # tkinter submodules used by the GUI
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.filedialog",
    # serial port enumeration
    "serial.tools.list_ports",
]

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=binaries,
    datas=[(ICON, "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

# 本机 PATH 里有 ModelSim 的旧 Tcl 8.6.5（D:\modeltech64_10.6e\win64），PyInstaller 会按
# 依赖搜索顺序把它的 tcl86t.dll/tk86t.dll 打进包，和 conda 的 tcl8.6 数据（8.6.15）版本冲突
# → "version conflict for package Tcl: have 8.6.5, need exactly 8.6.15"。这里显式换成 conda 的。
_TCL_DLLS = ("tcl86t.dll", "tk86t.dll", "zlib.dll")
a.binaries = [entry for entry in a.binaries if os.path.basename(str(entry[0])).lower() not in _TCL_DLLS]
for _dll in _TCL_DLLS:
    _source = os.path.join(CONDA_LIB, _dll)
    if os.path.exists(_source):
        a.binaries.append((_dll, _source, "BINARY"))
    else:
        print(f"WARNING: 缺少 {_source}")

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pcmond",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
