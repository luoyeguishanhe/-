"""Locate the CUDA libraries shipped with the app or installed by pip."""

import ctypes
import os
from pathlib import Path
import sys

_dll_handles = []
_configured = False


def configure_cuda_runtime() -> None:
    global _configured
    if _configured or os.name != "nt":
        return
    import ctranslate2
    roots = [Path(ctranslate2.__file__).resolve().parent.parent]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys._MEIPASS))
    folders = sorted({folder for root in roots for folder in (root / "nvidia").glob("*/bin")
                      if folder.is_dir()})
    for folder in folders:
        _dll_handles.append(os.add_dll_directory(str(folder)))
    if folders:
        os.environ["PATH"] = os.pathsep.join(map(str, folders)) + os.pathsep + os.environ.get("PATH", "")
    # Check dependencies before entering inference, where native missing-library
    # failures can terminate the process instead of raising a Python exception.
    for name in ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll",
                 "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll"):
        _dll_handles.append(ctypes.WinDLL(name))
    _configured = True
