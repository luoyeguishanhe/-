"""The same Whisper configuration for the live app and offline comparisons."""

import os
from pathlib import Path

import numpy as np

from .runtime import model_source


TRANSCRIBE_SETTINGS = {
    "beam_size": 5,
    "vad_filter": True,
    "condition_on_previous_text": False,
    # Zero temperature makes model comparisons repeatable (no sampling fallback).
    "temperature": 0.0,
    "word_timestamps": True,
}


class WhisperRecognizer:
    def __init__(self, model_size: str, cpu_threads: int = 0,
                 local_files_only: bool = False) -> None:
        from faster_whisper import WhisperModel
        from opencc import OpenCC

        cache = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SubtitleApp" / "models"
        cache.mkdir(parents=True, exist_ok=True)
        self.converter = OpenCC("t2s")
        self.model = WhisperModel(
            model_source(model_size), device="cpu", compute_type="int8", cpu_threads=cpu_threads,
            download_root=str(cache), local_files_only=local_files_only,
        )

    def recognize(self, audio: np.ndarray, language: str | None) -> dict:
        parts, info = self.model.transcribe(audio, language=language, **TRANSCRIBE_SETTINGS)
        records = []
        texts = []
        for part in parts:
            text = part.text
            if info.language == "zh":
                text = self.converter.convert(text)
            texts.append(text)
            records.append({"start": part.start, "end": part.end, "text": text.strip(),
                            "avg_logprob": part.avg_logprob,
                            "no_speech_prob": part.no_speech_prob,
                            "words": [{"start": word.start, "end": word.end,
                                       "text": self.converter.convert(word.word) if info.language == "zh" else word.word}
                                      for word in (getattr(part, "words", None) or [])]})
        return {
            "text": "".join(texts).strip(),
            "language": info.language,
            "duration_after_vad": info.duration_after_vad,
            "parts": records,
        }
