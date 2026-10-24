import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from subtitle_app.live_subtitles import LiveSubtitleTranslator
from subtitle_app.main import MainWindow
from subtitle_app.subtitles import SubtitleCue
from subtitle_app.translation import TranslationConfig


class LiveSubtitleTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_translates_final_caption_after_finish(self):
        calls = []

        def transport(config, payload):
            calls.append(payload["messages"][1]["content"])
            return {"choices": [{"message": {"content":
                    '{"translations":[{"id":1,"text":"你好"}]}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

        worker = LiveSubtitleTranslator(
            TranslationConfig("http://127.0.0.1:11434/v1", "test"), transport)
        worker.start()
        self.assertTrue(worker.submit(SubtitleCue(0, 1, "hello")))
        worker.finish()
        deadline = monotonic() + 5
        events = []
        while monotonic() < deadline:
            if event := worker.next_event():
                events.append(event)
                if event[0] == "complete":
                    break
            else:
                sleep(0.01)
        self.assertEqual(len(calls), 1)
        self.assertIn(("translated", (1, "hello", "你好")), events)
        self.assertTrue(any(kind == "token_usage" and value.prompt_tokens == 10
                            for kind, value in events))
        self.assertEqual(events[-1][0], "complete")

    def test_live_window_uses_cache_and_updates_movable_overlay(self):
        with TemporaryDirectory() as folder, patch("subtitle_app.main.list_output_devices", return_value=[]):
            window = MainWindow()
            window.timer.stop()
            self.addCleanup(lambda: self._close_window(window))
            window.devices.addItem("测试设备", 1)
            window.cache_path.setText(folder)
            window.keep_recording.setChecked(True)
            window.live_config = TranslationConfig("http://127.0.0.1:11434/v1", "test")
            fake_pipeline = SimpleNamespace(prepare=lambda: None)
            with patch("subtitle_app.main.SpeechPipeline", return_value=fake_pipeline) as pipeline, \
                    patch("subtitle_app.main.QFileDialog.getSaveFileName") as save_dialog, \
                    patch("subtitle_app.main.LiveSubtitleTranslator") as translator:
                window.start_live_subtitles()
            pipeline.assert_called_once_with("small", None, low_latency=True)
            save_dialog.assert_not_called()
            self.assertEqual(window.output_path.parent.parent, Path(folder))
            self.assertTrue(window.live_overlay.isVisible())
            segment = SimpleNamespace(start_seconds=0.0, end_seconds=1.0)
            window._receive_text((segment, "hello", "en"))
            translator.return_value.submit.assert_called_once_with(SubtitleCue(0, 1, "hello"))
            translator.return_value.next_event.side_effect = [
                ("translated", (1, "hello", "你好")), None]
            window._poll_live_translation()
            self.assertEqual(window.live_overlay.translated.text(), "你好")
            window.live_overlay.show_pending("next sentence")
            self.assertEqual(window.live_overlay.translated.text(), "你好")
            self.assertEqual(window.live_overlay.original.text(), "hello")
            window.live_overlay.hide()
            window.show_live_overlay()
            self.assertTrue(window.live_overlay.isVisible())
            window._recording_finished(completed=True)
            translator.return_value.finish.assert_called_once()

    @staticmethod
    def _close_window(window):
        window.pipeline = None
        window._exports_saved = True
        window.close()
