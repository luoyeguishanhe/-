"""Decode a local media timeline and transcribe it without capture queue loss."""

from dataclasses import dataclass
import math
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import time
from typing import Callable, Iterator

import av
import numpy as np

from .audio_processing import TARGET_SAMPLE_RATE
from .recognition import WhisperRecognizer
from .speech_pipeline import AudioChunk, SpeechSegmenter
from .subtitles import cues_from_parts


class MediaCancelled(Exception):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float | None
    origin_seconds: float
    audio_stream_index: int


def _origin_and_duration(container) -> tuple[float, float | None]:
    streams = [stream for stream in container.streams if stream.type in ("video", "audio")]
    starts = [float(stream.start_time * stream.time_base) for stream in streams
              if stream.start_time is not None and stream.time_base is not None]
    origin = float(container.start_time / av.time_base) if container.start_time is not None else min(starts, default=0.0)
    duration = float(container.duration / av.time_base) if container.duration is not None else None
    if duration is None:
        ends = [float((stream.start_time or 0) * stream.time_base + stream.duration * stream.time_base)
                for stream in streams if stream.duration is not None and stream.time_base is not None]
        duration = max(ends) - origin if ends else None
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None
    return origin, duration


class AudioTimeline:
    """Map resampled PTS to video positions, retaining gaps and initial delay."""
    def __init__(self, origin_seconds: float, fallback_start: float = 0,
                 tolerance_samples: int = 2) -> None:
        self.origin_seconds = origin_seconds
        self.expected = round(max(0, fallback_start) * TARGET_SAMPLE_RATE)
        self.has_output = False
        self.tolerance_samples = tolerance_samples

    def accept(self, samples: np.ndarray, time_seconds: float | None) -> AudioChunk | None:
        if time_seconds is not None and not math.isfinite(time_seconds):
            raise ValueError("音频时间戳无效，无法生成同步字幕")
        start = self.expected if time_seconds is None else round((time_seconds - self.origin_seconds) * TARGET_SAMPLE_RATE)
        if start < 0:
            samples = samples[-start:]
            start = 0
        if self.has_output:
            difference = start - self.expected
            if abs(difference) <= self.tolerance_samples:
                start = self.expected
            elif difference < 0:
                if -difference > TARGET_SAMPLE_RATE // 10:
                    raise ValueError("音频时间戳明显倒退，无法保证字幕同步")
                samples = samples[-difference:]
                start = self.expected
        if not samples.size:
            return None
        self.expected = start + len(samples)
        self.has_output = True
        return AudioChunk(start, samples)


def iter_media_audio(path: Path, cancel: Event,
                     on_info: Callable[[MediaInfo], None],
                     on_warning: Callable[[str], None]) -> Iterator[AudioChunk]:
    if not path.is_file():
        raise ValueError(f"找不到本地文件：{path}")
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("所选文件没有音轨，无法识别语音")
        stream = container.streams.audio[0]
        origin, duration = _origin_and_duration(container)
        fallback_start = float(stream.start_time * stream.time_base) - origin if stream.start_time is not None and stream.time_base is not None else 0
        # Some containers store audio PTS in milliseconds. Do not mistake that
        # quantization jitter for a real gap after resampling.
        tolerance = max(2, min(32, math.ceil(float(stream.time_base or 0) * TARGET_SAMPLE_RATE)))
        timeline = AudioTimeline(origin, fallback_start, tolerance)
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=TARGET_SAMPLE_RATE)
        on_info(MediaInfo(duration, origin, stream.index))
        warned = False

        def convert(frame):
            nonlocal warned
            if cancel.is_set():
                raise MediaCancelled()
            timestamp = float(frame.pts * frame.time_base) if frame.pts is not None and frame.time_base is not None else None
            if timestamp is None and not warned:
                on_warning("部分音频没有时间戳，已按连续音频位置处理；导出后请检查同步。")
                warned = True
            samples = np.array(frame.to_ndarray().reshape(-1), dtype=np.float32, copy=True)
            return timeline.accept(samples, timestamp)

        for frame in container.decode(stream):
            if cancel.is_set():
                raise MediaCancelled()
            for output in resampler.resample(frame):
                chunk = convert(output)
                if chunk is not None:
                    yield chunk
        for output in resampler.resample(None):
            chunk = convert(output)
            if chunk is not None:
                yield chunk


class MediaTranscriber:
    """One sequential worker: decoding and ASR are bounded to speech spans."""
    def __init__(self, path: Path, model_size: str = "small", language: str | None = None,
                 recognizer_factory=None) -> None:
        self.path = path
        self.model_size = model_size
        self.language = language
        self.events: Queue[tuple[str, object]] = Queue()
        self._cancel = Event()
        self._thread: Thread | None = None
        self._recognizer_factory = recognizer_factory or WhisperRecognizer
        self._duration: float | None = None
        self._last_progress = 0.0
        self._position = 0.0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("本地文件任务已经启动")
        self._thread = Thread(target=self._run, name="MediaTranscriber", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def next_event(self):
        try:
            return self.events.get_nowait()
        except Empty:
            return None

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise MediaCancelled()

    def _progress(self, position: float, force: bool = False) -> None:
        self._position = max(self._position, position)
        now = time.monotonic()
        if force or now - self._last_progress >= 0.1:
            self.events.put(("progress", (self._position, self._duration)))
            self._last_progress = now

    def _run(self) -> None:
        chunks = None
        try:
            self._check_cancel()

            def info(value: MediaInfo) -> None:
                self._duration = value.duration_seconds
                self.events.put(("media_info", value))

            chunks = iter_media_audio(self.path, self._cancel, info,
                                      lambda message: self.events.put(("warning", message)))
            # Probe/validate the actual audio before loading a model.
            first = next(chunks, None)
            if first is None:
                raise ValueError("文件音轨没有可解码的声音数据")
            self._check_cancel()
            self.events.put(("loading", self.model_size))
            recognizer = self._recognizer_factory(self.model_size)
            self._check_cancel()
            segmenter = SpeechSegmenter()

            def recognize(segment) -> None:
                self._check_cancel()
                self.events.put(("segment", (segment.start_seconds, segment.end_seconds)))
                result = recognizer.recognize(segment.audio, self.language)
                if result["text"]:
                    self.events.put(("text", (segment, result["text"], result["language"])))
                    self.events.put(("subtitles", cues_from_parts(
                        result["parts"], segment.start_seconds,
                        len(segment.audio) / TARGET_SAMPLE_RATE,
                    )))
                self._progress(segment.end_seconds, force=True)
                self._check_cancel()

            def process(chunk) -> None:
                self._check_cancel()
                for segment in segmenter.feed(chunk.audio, chunk.start_sample):
                    recognize(segment)
                self._progress((chunk.start_sample + len(chunk.audio)) / TARGET_SAMPLE_RATE)

            process(first)
            for chunk in chunks:
                process(chunk)
            for segment in segmenter.finish():
                recognize(segment)
            self._check_cancel()
            self.events.put(("complete", None))
        except MediaCancelled:
            self.events.put(("cancelled", None))
        except Exception as exc:
            if self._cancel.is_set():
                self.events.put(("cancelled", None))
            else:
                self.events.put(("fatal", f"无法处理本地文件：{exc}"))
        finally:
            if chunks is not None:
                chunks.close()
