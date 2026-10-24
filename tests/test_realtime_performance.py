import json
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np

from subtitle_app.live_subtitles import LiveSubtitleTranslator
from subtitle_app.recognition import WhisperRecognizer
from subtitle_app.speech_pipeline import SpeechPipeline, SpeechSegment
from subtitle_app.subtitles import SubtitleCue
from subtitle_app.translation import TranslationConfig


def wait_until(predicate, seconds=5):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("worker did not reach expected state")


class CudaFallbackTests(TestCase):
    def test_auto_falls_back_when_gpu_dependencies_are_missing(self):
        with patch("ctranslate2.get_cuda_device_count", return_value=1), \
                patch("subtitle_app.cuda_runtime.configure_cuda_runtime", side_effect=OSError("missing cuDNN")), \
                patch("faster_whisper.WhisperModel") as model:
            recognizer = WhisperRecognizer("small", device="auto", live=True)
        self.assertEqual(recognizer.backend["device"], "cpu")
        self.assertIn("missing cuDNN", recognizer.fallback_reason)
        self.assertEqual(model.call_args.kwargs["compute_type"], "int8")

    def test_auto_uses_gpu_and_retries_current_audio_on_runtime_failure(self):
        gpu, cpu = Mock(), Mock()
        gpu.transcribe.side_effect = [(iter([]), None), RuntimeError("out of memory")]
        cpu.transcribe.return_value = (iter([]), SimpleNamespace(language="en", duration_after_vad=0))
        with patch("ctranslate2.get_cuda_device_count", return_value=1), \
                patch("subtitle_app.cuda_runtime.configure_cuda_runtime"), \
                patch("faster_whisper.WhisperModel", side_effect=[gpu, cpu]) as model:
            recognizer = WhisperRecognizer("small", device="auto", live=True)
            self.assertEqual(recognizer.device, "cuda")
            result = recognizer.recognize(np.zeros(16000, dtype=np.float32), "en")
        self.assertEqual(result["text"], "")
        self.assertEqual(recognizer.device, "cpu")
        self.assertEqual(model.call_args_list[0].kwargs["compute_type"], "float16")
        self.assertFalse(cpu.transcribe.call_args.kwargs["word_timestamps"])
        self.assertFalse(cpu.transcribe.call_args.kwargs["vad_filter"])


class RealtimeBacklogTests(TestCase):
    def test_slow_asr_processes_latest_waiting_segment_and_then_completes(self):
        entered, release = Event(), Event()
        calls = []

        class SlowRecognizer:
            backend = {"device": "cuda", "compute_type": "float16", "fallback_reason": ""}

            def recognize(self, audio, language):
                calls.append(int(audio[0]))
                if len(calls) == 1:
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test timed out")
                return {"text": str(calls[-1]), "language": "en", "parts": []}

        pipeline = SpeechPipeline(low_latency=True)
        with patch("subtitle_app.speech_pipeline.WhisperRecognizer", return_value=SlowRecognizer()):
            pipeline.prepare()
            self.addCleanup(pipeline.cancel)
            wait_until(pipeline._ready.is_set)
            segment = lambda number: SpeechSegment(number * 16000, (number + 1) * 16000,
                                                   np.full(16000, number, dtype=np.float32))
            pipeline._enqueue_segment(segment(1))
            self.assertTrue(entered.wait(5))
            pipeline._enqueue_segment(segment(2))
            pipeline._enqueue_segment(segment(3))
            release.set()
            wait_until(lambda: len(calls) == 2)
            pipeline._segments.put(None, timeout=2)
            pipeline._asr_thread.join(5)
        self.assertFalse(pipeline._asr_thread.is_alive())
        self.assertEqual(calls, [1, 3])
        self.assertEqual(pipeline._dropped_segments, 1)
        events = list(pipeline.events.queue)
        self.assertTrue(any(kind == "complete" for kind, _ in events))
        self.assertTrue(any(kind == "performance" for kind, _ in events))

    def test_slow_translation_keeps_latest_waiting_text_and_finishes_it(self):
        entered, release = Event(), Event()
        calls = []

        def transport(config, payload):
            text = json.loads(payload["messages"][1]["content"])[0]["text"]
            calls.append(text)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test timed out")
            return {"choices": [{"message": {"content": json.dumps(
                {"translations": [{"id": 1, "text": text}]})}}]}

        worker = LiveSubtitleTranslator(TranslationConfig("http://localhost/v1", "test"), transport)
        self.addCleanup(worker.cancel)
        worker.start()
        worker.submit(SubtitleCue(0, 1, "first"))
        self.assertTrue(entered.wait(5))
        worker.submit(SubtitleCue(1, 2, "old"))
        worker.submit(SubtitleCue(2, 3, "latest"))
        worker.finish()
        release.set()
        worker._thread.join(5)
        self.assertFalse(worker.is_active)
        self.assertEqual(calls, ["first", "latest"])
        self.assertEqual(worker._dropped, 1)
