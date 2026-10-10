"""Regression checks for sparse and opposite-phase multichannel output."""

import unittest

import numpy as np

from subtitle_app.audio_processing import AudioNormalizer


class AudioNormalizerTests(unittest.TestCase):
    def test_sparse_eight_channel_audio_keeps_speech_level(self) -> None:
        sample_rate = 48_000
        time = np.arange(sample_rate, dtype=np.float32) / sample_rate
        voice = np.rint(12000 * np.sin(2 * np.pi * 440 * time)).astype("<i2")
        source = np.zeros((sample_rate, 8), dtype="<i2")
        source[:, :2] = voice[:, None]

        for second_channel in (voice, -voice):
            with self.subTest(opposite_phase=second_channel is not voice):
                source[:, 1] = second_channel
                normalizer = AudioNormalizer(sample_rate, 8)
                output = [
                    normalizer.process(source[start:start + 1024].tobytes())
                    for start in range(0, sample_rate, 1024)
                ]
                output.append(normalizer.finish())
                mono = np.concatenate(output)

                self.assertLessEqual(abs(mono.size - 16_000), 1)
                self.assertGreater(float(np.max(np.abs(mono))), 0.3)
                spectrum = np.abs(np.fft.rfft(mono))
                dominant_hz = np.fft.rfftfreq(mono.size, 1 / 16_000)[np.argmax(spectrum)]
                self.assertAlmostEqual(float(dominant_hz), 440.0, delta=2.0)


if __name__ == "__main__":
    unittest.main()
