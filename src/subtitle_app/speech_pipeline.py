"""Streaming voice detection and local speech recognition."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread

import numpy as np
import webrtcvad

from .audio_processing import TARGET_SAMPLE_RATE, float32_to_pcm16


FRAME_SAMPLES = 320  # 20 ms at 16 kHz; WebRTC VAD accepts 10, 20 or 30 ms.
SILENCE_FRAMES = 30  # 0.6 seconds after the last voiced frame.
MAX_SEGMENT_FRAMES = 600  # 12 seconds, to bound recognition latency.


@dataclass(frozen=True)
class SpeechSegment:
    start_sample: int
    end_sample: int
    audio: np.ndarray

    @property
    def start_seconds(self) -> float:
        return self.start_sample / TARGET_SAMPLE_RATE

    @property
    def end_seconds(self) -> float:
        return self.end_sample / TARGET_SAMPLE_RATE


@dataclass(frozen=True)
class AudioChunk:
    start_sample: int
    audio: np.ndarray


class SpeechSegmenter:
    """Turn consecutive 16 kHz mono chunks into speech spans with sample positions."""

    def __init__(self, aggressiveness: int = 2) -> None:
        self._vad = webrtcvad.Vad(aggressiveness)
        self._reset()

    def _reset(self) -> None:
        self.next_sample: int | None = None
        self._pending_start = 0
        self._pending = np.empty(0, dtype=np.float32)
        self._preroll: deque[tuple[int, np.ndarray]] = deque(maxlen=10)
        self._speech_hits = 0
        self.active = False
        self._frames: list[np.ndarray] = []
        self._segment_start = 0
        self._last_voice_end = 0
        self._voiced_frames = 0
        self._silent_frames = 0

    def feed(self, audio: np.ndarray, start_sample: int) -> list[SpeechSegment]:
        if audio.size == 0:
            return []
        completed = []
        if self.next_sample is not None and start_sample != self.next_sample:
            completed.extend(self.finish())
            self._reset()
        if self.next_sample is None:
            self._pending_start = start_sample
        self.next_sample = start_sample + audio.size
        self._pending = np.concatenate((self._pending, audio))

        while self._pending.size >= FRAME_SAMPLES:
            frame = self._pending[:FRAME_SAMPLES].copy()
            frame_start = self._pending_start
            self._pending = self._pending[FRAME_SAMPLES:]
            self._pending_start += FRAME_SAMPLES
            segment = self._accept_frame(frame, frame_start)
            if segment is not None:
                completed.append(segment)
        return completed

    def _accept_frame(self, frame: np.ndarray, start: int) -> SpeechSegment | None:
        is_speech = self._vad.is_speech(float32_to_pcm16(frame), TARGET_SAMPLE_RATE)
        if not self.active:
            self._preroll.append((start, frame))
            self._speech_hits = self._speech_hits + 1 if is_speech else 0
            if self._speech_hits >= 2:
                self.active = True
                self._segment_start = self._preroll[0][0]
                self._frames = [item for _, item in self._preroll]
                self._last_voice_end = start + FRAME_SAMPLES
                self._voiced_frames = 2
                self._silent_frames = 0
                self._preroll.clear()
            return None

        self._frames.append(frame)
        if is_speech:
            self._voiced_frames += 1
            self._last_voice_end = start + FRAME_SAMPLES
            self._silent_frames = 0
        else:
            self._silent_frames += 1
        if self._silent_frames >= SILENCE_FRAMES or len(self._frames) >= MAX_SEGMENT_FRAMES:
            return self._finish_active()
        return None

    def _finish_active(self) -> SpeechSegment | None:
        segment = None
        if self.active and self._voiced_frames >= 10:
            segment = SpeechSegment(
                self._segment_start,
                self._last_voice_end,
                np.concatenate(self._frames).astype(np.float32, copy=False),
            )
        self.active = False
        self._frames = []
        self._preroll.clear()
        self._speech_hits = 0
        self._voiced_frames = 0
        self._silent_frames = 0
        return segment

    def finish(self) -> list[SpeechSegment]:
        if self.active and self._pending.size:
            self._frames.append(self._pending)
        segment = self._finish_active()
        self._pending = np.empty(0, dtype=np.float32)
        return [segment] if segment is not None else []


class SpeechPipeline:
    """Keep model inference away from the capture and UI threads."""

    def __init__(self, model_size: str = "base") -> None:
        self.model_size = model_size
        self.events: Queue[tuple[str, object]] = Queue()
        self._audio: Queue[AudioChunk] = Queue(maxsize=256)
        self._segments: Queue[SpeechSegment | None] = Queue(maxsize=12)
        self._cancel = Event()
        self._audio_done = Event()
        self._ready = Event()
        self._capture_started = False
        self._overrun_reported = False
        self._vad_thread: Thread | None = None
        self._asr_thread: Thread | None = None

    def prepare(self) -> None:
        self._asr_thread = Thread(target=self._recognize, name="SpeechASR", daemon=True)
        self._asr_thread.start()

    def begin_capture(self) -> None:
        if not self._ready.is_set() or self._capture_started:
            raise RuntimeError("语音识别模型尚未就绪或采集已经开始")
        self._capture_started = True
        self._vad_thread = Thread(target=self._detect_speech, name="SpeechVAD", daemon=True)
        self._vad_thread.start()

    def submit(self, samples: np.ndarray, start_sample: int) -> None:
        if not self._capture_started or self._cancel.is_set():
            return
        try:
            self._audio.put_nowait(AudioChunk(start_sample, samples.copy()))
            self._overrun_reported = False
        except Full:
            if not self._overrun_reported:
                self.events.put(("warning", "VAD 处理跟不上录音，部分音频已跳过"))
                self._overrun_reported = True

    def finish_capture(self) -> None:
        self._audio_done.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._audio_done.set()

    def next_event(self) -> tuple[str, object] | None:
        try:
            return self.events.get_nowait()
        except Empty:
            return None

    def _detect_speech(self) -> None:
        segmenter = SpeechSegmenter()
        was_speaking = False
        try:
            while not self._cancel.is_set():
                try:
                    chunk = self._audio.get(timeout=0.1)
                except Empty:
                    if self._audio_done.is_set():
                        break
                    continue
                if segmenter.next_sample is not None and chunk.start_sample != segmenter.next_sample:
                    self.events.put(("warning", "采集音频出现缺口，已重新对齐 VAD"))
                completed = segmenter.feed(chunk.audio, chunk.start_sample)
                if segmenter.active != was_speaking:
                    self.events.put(("vad", segmenter.active))
                    was_speaking = segmenter.active
                for segment in completed:
                    self._enqueue_segment(segment)
            if not self._cancel.is_set():
                for segment in segmenter.finish():
                    self._enqueue_segment(segment)
                while not self._cancel.is_set():
                    try:
                        self._segments.put(None, timeout=0.2)
                        break
                    except Full:
                        continue
        except Exception as exc:
            self.events.put(("fatal", f"VAD 处理失败：{exc}"))
            self.cancel()

    def _enqueue_segment(self, segment: SpeechSegment) -> None:
        try:
            self._segments.put_nowait(segment)
            self.events.put(("segment", (segment.start_seconds, segment.end_seconds)))
        except Full:
            self.events.put(("warning", "ASR 处理跟不上语音，已跳过一个片段"))

    def _recognize(self) -> None:
        try:
            from faster_whisper import WhisperModel

            cache_root = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SubtitleApp" / "models"
            cache_root.mkdir(parents=True, exist_ok=True)
            model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                download_root=str(cache_root),
            )
        except Exception as exc:
            if not self._cancel.is_set():
                self.events.put(("fatal", f"ASR 模型加载失败：{exc}"))
            return
        if self._cancel.is_set():
            return
        self._ready.set()
        self.events.put(("ready", self.model_size))
        while not self._cancel.is_set():
            try:
                segment = self._segments.get(timeout=0.2)
            except Empty:
                continue
            if segment is None:
                self.events.put(("complete", None))
                return
            try:
                parts, info = model.transcribe(
                    segment.audio,
                    language=None,
                    beam_size=1,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                transcript = "".join(part.text for part in parts).strip()
                if transcript:
                    self.events.put(("text", (segment, transcript, info.language)))
            except Exception as exc:
                self.events.put(("warning", f"片段识别失败：{exc}"))
