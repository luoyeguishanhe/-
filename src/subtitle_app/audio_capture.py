"""Windows WASAPI loopback recording, kept separate from the desktop UI."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from math import log10
import os
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
from threading import Event, Thread
from typing import Callable
import wave

import numpy as np
import pyaudiowpatch as pyaudio

from .audio_processing import AudioNormalizer, TARGET_SAMPLE_RATE, float32_to_pcm16


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    sample_rate: int
    channels: int
    is_default: bool


def list_output_devices() -> list[OutputDevice]:
    """List the output endpoints that WASAPI can capture through loopback."""
    audio = pyaudio.PyAudio()
    try:
        try:
            default_index = audio.get_default_wasapi_loopback()["index"]
        except (OSError, ValueError):
            default_index = None
        return [
            OutputDevice(
                index=int(info["index"]),
                name=str(info["name"]).removesuffix(" [Loopback]"),
                sample_rate=int(info["defaultSampleRate"]),
                channels=int(info["maxInputChannels"]),
                is_default=info["index"] == default_index,
            )
            for info in audio.get_loopback_device_info_generator()
            if info["maxInputChannels"] > 0
        ]
    finally:
        audio.terminate()


class SystemAudioRecorder:
    """Record one output device as 16 kHz mono PCM WAV in a worker thread."""

    def __init__(self) -> None:
        self.events: Queue[tuple[str, object]] = Queue()
        self.level = 0
        self.frames_written = 0
        self.sample_rate = 0
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        device_index: int,
        output_path: Path,
        on_audio: Callable[[np.ndarray, int], None] | None = None,
    ) -> None:
        if self.is_active:
            raise RuntimeError("录音已经在进行中")
        self._stop.clear()
        self.level = 0
        self.frames_written = 0
        self.sample_rate = 0
        self._thread = Thread(
            target=self._record,
            args=(device_index, output_path, on_audio),
            name="SystemAudioRecorder",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def next_event(self) -> tuple[str, object] | None:
        try:
            return self.events.get_nowait()
        except Empty:
            return None

    def _record(
        self,
        device_index: int,
        output_path: Path,
        on_audio: Callable[[np.ndarray, int], None] | None,
    ) -> None:
        audio = None
        stream = None
        temporary_path = None
        try:
            audio = pyaudio.PyAudio()
            info = audio.get_device_info_by_index(device_index)
            if not info.get("isLoopbackDevice") or info["maxInputChannels"] < 1:
                raise ValueError("所选设备不是可录制的系统输出设备，请刷新设备列表")

            rate = int(info["defaultSampleRate"])
            channels = int(info["maxInputChannels"])
            normalizer = AudioNormalizer(rate, channels)
            chunk_frames = 1024
            captured: Queue[bytes] = Queue(maxsize=256)
            overflow_reported = False

            def receive_audio(data: bytes, _frames: int, _time: dict, _status: int):
                nonlocal overflow_reported
                if data:
                    try:
                        captured.put_nowait(data)
                        overflow_reported = False
                    except Full:
                        if not overflow_reported:
                            self.events.put(("warning", "录音处理跟不上播放速度，部分声音已跳过"))
                            overflow_reported = True
                return (None, pyaudio.paContinue)

            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=chunk_frames,
                stream_callback=receive_audio,
            )
            with tempfile.NamedTemporaryFile(
                prefix=".system-audio-", suffix=".wav", dir=output_path.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)

            self.sample_rate = TARGET_SAMPLE_RATE
            self.events.put(("started", f"{info['name']}（{rate} Hz、{channels} 声道 → 16 kHz 单声道）"))
            with wave.open(str(temporary_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(TARGET_SAMPLE_RATE)
                while not self._stop.is_set():
                    try:
                        data = captured.get(timeout=0.1)
                    except Empty:
                        continue
                    self._write_chunk(wav, normalizer.process(data), on_audio)
                stream.stop_stream()
                while not captured.empty():
                    self._write_chunk(wav, normalizer.process(captured.get_nowait()), on_audio)
                self._write_chunk(wav, normalizer.finish(), on_audio)

            if self.frames_written == 0:
                raise RuntimeError("没有采集到音频数据，请确认设备正在播放声音")
            os.replace(temporary_path, output_path)
            temporary_path = None
            self.events.put(("saved", output_path))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.level = 0
            if stream is not None:
                with suppress(Exception):
                    stream.stop_stream()
                with suppress(Exception):
                    stream.close()
            if audio is not None:
                with suppress(Exception):
                    audio.terminate()
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    def _write_chunk(
        self,
        wav: wave.Wave_write,
        samples: np.ndarray,
        on_audio: Callable[[np.ndarray, int], None] | None,
    ) -> None:
        if samples.size == 0:
            return
        start_sample = self.frames_written
        wav.writeframesraw(float32_to_pcm16(samples))
        self.frames_written += samples.size
        if on_audio is not None:
            on_audio(samples, start_sample)
        peak = float(np.max(np.abs(samples)))
        self.level = (
            min(100, max(1, round((20 * log10(peak) + 60) * 100 / 60)))
            if peak else 0
        )
