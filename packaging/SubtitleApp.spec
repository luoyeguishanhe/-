# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import importlib.util
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
datas += [(str(root / "packaging" / "app-icon.ico"), ".")]
# CUDA libraries are explicitly included: CTranslate2 loads them dynamically,
# so dependency analysis cannot discover them from Python imports alone.
for module in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:
        spec = None
    if spec and spec.submodule_search_locations:
        datas += copy_metadata(module.replace(".", "-").replace("_", "-") + "-cu12")
        for location in spec.submodule_search_locations:
            package_root = Path(location)
            for library in package_root.rglob("*.dll"):
                destination = Path("nvidia") / module.split(".")[-1] / library.parent.relative_to(package_root)
                binaries.append((str(library), str(destination)))

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
    pyz, a.scripts, [], exclude_binaries=True, name="巴别塔",
    debug=False, strip=False, upx=False, console=False,
    icon=str(root / "packaging" / "app-icon.ico"),
    version=str(root / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="巴别塔")
