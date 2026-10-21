# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = [], [], ["package_smoke", "_webrtcvad"]
# Include native libraries and non-Python assets that are loaded on demand.
for package in ("faster_whisper", "ctranslate2", "onnxruntime", "opencc",
                "imageio_ffmpeg", "pyaudiowpatch", "soxr", "av"):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
for distribution in ("faster-whisper", "opencc-python-reimplemented", "imageio-ffmpeg", "PyQt6", "PyQt6-Qt6"):
    datas += copy_metadata(distribution)
datas += [(str(root / "build" / "app-icon.ico"), ".")]

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src"), str(root / "packaging")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[str(root / "packaging/hooks")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="SubtitleApp",
    debug=False, strip=False, upx=False, console=False,
    icon=str(root / "build" / "app-icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SubtitleApp")
