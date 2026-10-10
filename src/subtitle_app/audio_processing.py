"""Convert device PCM into streaming, 16 kHz mono audio for speech recognition."""

from __future__ import annotations

import numpy as np
import soxr


TARGET_SAMPLE_RATE = 16_000


class AudioNormalizer:
    """Keep resampler state across chunks so no samples are lost at boundaries."""

    def __init__(self, source_rate: int, channels: int) -> None:
        if source_rate <= 0 or channels <= 0:
            raise ValueError("采样率和声道数必须大于零")
        self.channels = channels
        self._channel_activity = np.zeros(channels, dtype=np.float32)
        self._resampler = soxr.ResampleStream(
            source_rate, TARGET_SAMPLE_RATE, 1, dtype="float32", quality="HQ"
        )

    def process(self, pcm16: bytes) -> np.ndarray:
        """Return a mono float32 chunk in [-1, 1] at 16 kHz."""
        if not pcm16:
            return np.empty(0, dtype=np.float32)
        if len(pcm16) % (2 * self.channels):
            raise ValueError("音频数据不包含完整的采样帧")
        source = np.frombuffer(pcm16, dtype="<i2").reshape(-1, self.channels)
        channels = source.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(channels * channels, axis=0))
        self._channel_activity = 0.8 * self._channel_activity + 0.2 * rms
        strongest = float(np.max(self._channel_activity))
        if strongest == 0:
            mono = np.zeros(source.shape[0], dtype=np.float32)
        else:
            active = self._channel_activity >= strongest * 0.05
            mono = np.mean(channels[:, active], axis=1)
            # Opposite-phase channels can cancel a voice when averaged.
            mono_rms = float(np.sqrt(np.mean(mono * mono)))
            if mono_rms < float(np.max(rms)) * 0.2:
                mono = channels[:, int(np.argmax(rms))]
        return self._resampler.resample_chunk(mono)

    def finish(self) -> np.ndarray:
        """Flush the resampler's buffered samples at the end of recording."""
        return self._resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Encode normalized mono audio for a standard 16-bit PCM WAV file."""
    return np.clip(samples * 32768, -32768, 32767).astype("<i2").tobytes()
