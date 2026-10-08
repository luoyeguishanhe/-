"""Windows WASAPI loopback recording, kept separate from the desktop UI."""

from __future__ import annotations

from array import array
from contextlib import suppress
from dataclasses import dataclass
from math import log10
import os
from pathlib import Path
from queue import Empty, Queue
import tempfile
from threading import Event, Thread
import wave

import pyaudiowpatch as pyaudio


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
    """Record one selected output device to a PCM WAV file in a worker thread."""

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

    def start(self, device_index: int, output_path: Path) -> None:
        if self.is_active:
            raise RuntimeError("录音已经在进行中")
        self._stop.clear()
        self.level = 0
        self.frames_written = 0
        self.sample_rate = 0
        self._thread = Thread(
            target=self._record,
            args=(device_index, output_path),
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

    def _record(self, device_index: int, output_path: Path) -> None:
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
            chunk_frames = 1024
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=chunk_frames,
            )
            with tempfile.NamedTemporaryFile(
                prefix=".system-audio-", suffix=".wav", dir=output_path.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)

            self.sample_rate = rate
            self.events.put(("started", info["name"]))
            with wave.open(str(temporary_path), "wb") as wav:
                wav.setnchannels(channels)
                wav.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
                wav.setframerate(rate)
                while not self._stop.is_set():
                    data = stream.read(chunk_frames, exception_on_overflow=False)
                    wav.writeframesraw(data)
                    samples = array("h")
                    samples.frombytes(data)
                    peak = max((abs(x) for x in samples), default=0)
                    self.level = (
                        min(100, max(1, round((20 * log10(peak / 32768) + 60) * 100 / 60)))
                        if peak else 0
                    )
                    self.frames_written += len(data) // (2 * channels)

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
