"""Writable runtime paths and logging for the windowed Windows executable."""

import os
from pathlib import Path
import sys
import tempfile


def model_source(model_size: str) -> str:
    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / "models" / model_size
        if portable.is_dir():
            required = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
            if not all((portable / name).is_file() for name in required):
                raise ValueError(f"便携模型文件不完整，请重新解压整个程序包：{portable}")
            return str(portable)
    return model_size


def default_capture_cache_directory() -> Path:
    """Keep portable captures beside the app, with a writable fallback available."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return base / "cache"


def fallback_capture_cache_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SubtitleApp" / "cache"


def configure_windowed_runtime() -> Path:
    # The EXE may be on a read-only drive; logs belong in the user's data folder.
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SubtitleApp" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    log = base / "app.log"
    if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
        try:
            log.replace(base / "app-previous.log")
        except OSError:
            pass
    if sys.stdout is None or sys.stderr is None:
        stream = log.open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    return log
