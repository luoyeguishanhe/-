"""Whisper recognition with optional CUDA and a lighter live configuration."""

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
                 local_files_only: bool = False, *, device: str = "cpu",
                 live: bool = False) -> None:
        from faster_whisper import WhisperModel
        from opencc import OpenCC

        cache = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SubtitleApp" / "models"
        cache.mkdir(parents=True, exist_ok=True)
        self.converter = OpenCC("t2s")
        if device not in ("cpu", "cuda", "auto"):
            raise ValueError("识别设备必须为 auto、cuda 或 cpu")
        self.requested_device = device
        self.live = live
        self.fallback_reason = ""
        self._model_class = WhisperModel
        self._model_source = model_source(model_size)
        self._model_options = dict(cpu_threads=cpu_threads, download_root=str(cache),
                                   local_files_only=local_files_only)
        if device != "cpu":
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() < 1:
                    raise RuntimeError("未检测到可用的 NVIDIA CUDA 显卡")
                from .cuda_runtime import configure_cuda_runtime
                configure_cuda_runtime()
                self._load_model("cuda")
                # Run real kernels before reporting readiness; successful model
                # construction alone does not verify cuBLAS/cuDNN availability.
                parts, _ = self.model.transcribe(
                    np.zeros(16000, dtype=np.float32), language="en",
                    beam_size=1, vad_filter=False, word_timestamps=False)
                list(parts)
                return
            except Exception as exc:
                if device == "cuda":
                    raise
                self.fallback_reason = f"GPU 不可用，已回退 CPU：{exc}"
        self._load_model("cpu")

    def _load_model(self, device: str) -> None:
        # Release GPU memory before loading the fallback model.
        if hasattr(self, "model"):
            del self.model
        self.device = device
        self.compute_type = "float16" if device == "cuda" else "int8"
        self.model = self._model_class(self._model_source, device=device,
                                       compute_type=self.compute_type, **self._model_options)

    @property
    def backend(self) -> dict:
        return {"device": self.device, "compute_type": self.compute_type,
                "fallback_reason": self.fallback_reason}

    def recognize(self, audio: np.ndarray, language: str | None) -> dict:
        try:
            return self._recognize(audio, language)
        except RuntimeError as exc:
            if getattr(self, "requested_device", "cpu") != "auto" or self.device != "cuda":
                raise
            self.fallback_reason = f"GPU 推理失败，已回退 CPU：{exc}"
            self._load_model("cpu")
            return self._recognize(audio, language)

    def _recognize(self, audio: np.ndarray, language: str | None) -> dict:
        settings = dict(TRANSCRIBE_SETTINGS)
        if getattr(self, "live", False):
            settings.update(beam_size=1, word_timestamps=False, vad_filter=False)
        parts, info = self.model.transcribe(audio, language=language, **settings)
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
