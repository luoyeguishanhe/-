from fractions import Fraction
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from media_fixtures import create_video
from subtitle_app.media_pipeline import AudioTimeline, MediaTranscriber, _origin_and_duration, iter_media_audio
from subtitle_app.speech_pipeline import SpeechSegment


class AllFramesSegmenter:
    def feed(self, audio, start):
        return [SpeechSegment(start, start + len(audio), audio)]

    def finish(self):
        return []


class FakeRecognizer:
    def __init__(self, _model):
        pass

    def recognize(self, audio, _language):
        return {"text": "测试", "language": "zh", "parts": [
            {"start": 0, "end": len(audio) / 16000, "text": "测试", "words": []}]}


class MediaDecodeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def test_real_mkv_retains_delayed_audio_and_resamples(self):
        path = self.folder / "中文视频.mkv"
        rate = 48000
        audio = 0.2 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)
        create_video(path, audio=audio, audio_rate=rate)
        info, warnings = [], []
        chunks = list(iter_media_audio(path, Event(), info.append, warnings.append))
        self.assertAlmostEqual(chunks[0].start_sample / 16000, 2, delta=0.002)
        self.assertLessEqual(abs(sum(len(chunk.audio) for chunk in chunks) - 16000), 32)
        self.assertAlmostEqual((chunks[-1].start_sample + len(chunks[-1].audio)) / 16000, 3, delta=0.003)
        self.assertEqual(info[0].origin_seconds, 0)
        self.assertGreater(info[0].duration_seconds, 3)
        self.assertFalse(warnings)

    def test_real_mp4_aac_encoder_padding_does_not_remove_audio_delay(self):
        path = self.folder / "aac.mp4"
        rate = 48000
        audio = 0.2 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)
        create_video(path, audio=audio, audio_rate=rate, mp4=True)
        chunks = list(iter_media_audio(path, Event(), lambda _info: None, lambda _message: None))
        sounding = [chunk.start_sample / 16000 for chunk in chunks if np.max(np.abs(chunk.audio)) > 0.01]
        self.assertTrue(sounding)
        self.assertAlmostEqual(sounding[0], 2, delta=0.04)

    def test_real_container_timestamp_gap_survives_resampling(self):
        path = self.folder / "gap.mkv"
        create_video(path, audio=np.ones(32768, dtype=np.float32) * 0.1,
                     duration=6, audio_gap_after=16384, gap_seconds=1)
        chunks = list(iter_media_audio(path, Event(), lambda _info: None, lambda _message: None))
        gaps = [(b.start_sample - a.start_sample - len(a.audio)) / 16000 for a, b in zip(chunks, chunks[1:])]
        self.assertTrue(any(abs(gap - 1) <= 0.002 for gap in gaps), gaps)
        self.assertEqual(sum(len(chunk.audio) for chunk in chunks), 32768)

    def test_no_audio_and_corrupt_file_fail_before_loading_model(self):
        silent = self.folder / "no-audio.mkv"
        create_video(silent, duration=0.5)
        corrupt = self.folder / "corrupt.mp4"
        corrupt.write_bytes(b"not a video")
        for path in (silent, corrupt):
            with self.subTest(path=path):
                factory = Mock()
                job = MediaTranscriber(path, recognizer_factory=factory)
                job.start()
                job.wait(5)
                events = list(job.events.queue)
                self.assertEqual([kind for kind, _ in events], ["fatal"])
                factory.assert_not_called()

    def test_worker_does_not_drop_more_than_twelve_offline_segments(self):
        path = self.folder / "long-enough.mkv"
        create_video(path, audio=np.ones(32000, dtype=np.float32) * 0.1, duration=5)
        expected = len(list(iter_media_audio(path, Event(), lambda _info: None, lambda _message: None)))
        self.assertGreater(expected, 12)
        with patch("subtitle_app.media_pipeline.SpeechSegmenter", AllFramesSegmenter):
            job = MediaTranscriber(path, recognizer_factory=FakeRecognizer)
            job.start()
            job.wait(5)
        events = list(job.events.queue)
        self.assertEqual(sum(kind == "text" for kind, _ in events), expected)
        self.assertEqual(events[-1][0], "complete")
        cues = [cue for kind, value in events if kind == "subtitles" for cue in value]
        self.assertGreaterEqual(cues[0].start, 2)
        progress = [value[0] for kind, value in events if kind == "progress"]
        self.assertEqual(progress, sorted(progress))

    def test_cancellation_preserves_current_completed_result_and_closes_input(self):
        path = self.folder / "cancel.mkv"
        create_video(path, audio=np.ones(16000, dtype=np.float32) * 0.1)

        class CancelAfterFirst(FakeRecognizer):
            def recognize(self, audio, language):
                result = super().recognize(audio, language)
                job.cancel()
                return result

        with patch("subtitle_app.media_pipeline.SpeechSegmenter", AllFramesSegmenter):
            job = MediaTranscriber(path, recognizer_factory=CancelAfterFirst)
            job.start()
            job.wait(5)
        kinds = [kind for kind, _ in list(job.events.queue)]
        self.assertEqual(kinds.count("text"), 1)
        self.assertEqual(kinds.count("cancelled"), 1)
        self.assertNotIn("complete", kinds)
        path.unlink()  # Windows refuses this if the input container is still open.

    def test_cancel_before_start_does_not_load_model(self):
        factory = Mock()
        job = MediaTranscriber(self.folder / "unused.mp4", recognizer_factory=factory)
        job.cancel()
        job.start()
        job.wait(5)
        self.assertEqual(list(job.events.queue), [("cancelled", None)])
        factory.assert_not_called()


class MediaTimelineTests(unittest.TestCase):
    def test_gaps_are_preserved_and_negative_preroll_is_trimmed(self):
        timeline = AudioTimeline(10)
        first = timeline.accept(np.ones(16000), 9.5)
        self.assertEqual((first.start_sample, len(first.audio)), (0, 8000))
        second = timeline.accept(np.ones(16000), 12)
        self.assertEqual(second.start_sample, 32000)
        fallback = timeline.accept(np.ones(100), None)
        self.assertEqual(fallback.start_sample, 48000)
        with self.assertRaises(ValueError):
            timeline.accept(np.ones(16000), 10)

    def test_timestamp_quantization_is_not_treated_as_a_gap(self):
        timeline = AudioTimeline(0, tolerance_samples=16)
        timeline.accept(np.ones(1024), 0)
        chunk = timeline.accept(np.ones(1024), 1030 / 16000)
        self.assertEqual(chunk.start_sample, 1024)

    def test_duration_and_origin_fallback_do_not_use_audio_start_as_video_zero(self):
        video = SimpleNamespace(type="video", start_time=0, duration=100, time_base=Fraction(1, 10))
        audio = SimpleNamespace(type="audio", start_time=2000, duration=4000, time_base=Fraction(1, 1000))
        container = SimpleNamespace(streams=[video, audio], start_time=None, duration=None)
        self.assertEqual(_origin_and_duration(container), (0, 10))


if __name__ == "__main__":
    unittest.main()
