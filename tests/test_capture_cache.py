import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from subtitle_app.main import MainWindow
from subtitle_app.subtitles import SubtitleCue


class CaptureCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch("subtitle_app.main.list_output_devices", return_value=[]):
            self.window = MainWindow()
        self.window.timer.stop()
        self.window.devices.addItem("测试设备", 1)
        self.window.cache_path.setText(self.directory.name)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.pipeline = None
        self.window._exports_saved = True
        self.window.close()

    def test_temporary_recording_needs_no_save_dialog_and_cleans_only_its_session(self):
        root = Path(self.directory.name)
        unrelated = root / "我的文件.txt"
        unrelated.write_text("保留", encoding="utf-8")
        fake_pipeline = SimpleNamespace(prepare=lambda: None)
        with patch("subtitle_app.main.QFileDialog.getSaveFileName") as dialog, \
                patch("subtitle_app.main.SpeechPipeline", return_value=fake_pipeline):
            self.window.start_recording()
        dialog.assert_not_called()
        capture = self.window.output_path
        self.assertIsNotNone(capture)
        self.assertEqual(capture.parent.parent, root)
        self.assertTrue(capture.parent.is_dir())
        capture.write_bytes(b"temporary recording")
        self.window.paragraphs = ["测试字幕"]
        self.window.subtitle_cues = [SubtitleCue(0, 1, "测试字幕")]
        self.window._recording_finished(completed=True)
        self.assertFalse(capture.with_suffix(".txt").exists())
        self.assertFalse(capture.with_suffix(".srt").exists())
        self.assertTrue(self.window.export_srt_button.isEnabled())
        with patch("subtitle_app.main.QFileDialog.getSaveFileName", return_value=("", "")) as export_dialog:
            self.window.export_text("srt")
        self.assertNotEqual(Path(export_dialog.call_args.args[2]).parent, capture.parent)
        self.window._exports_saved = True
        self.window.close()
        self.assertFalse(capture.parent.exists())
        self.assertFalse(capture.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "保留")

    def test_keep_recording_still_uses_chosen_file(self):
        output = Path(self.directory.name) / "permanent.wav"
        self.window.keep_recording.setChecked(True)
        fake_pipeline = SimpleNamespace(prepare=lambda: None)
        with patch("subtitle_app.main.QFileDialog.getSaveFileName", return_value=(str(output), "")) as dialog, \
                patch("subtitle_app.main.SpeechPipeline", return_value=fake_pipeline):
            self.window.start_recording()
        dialog.assert_called_once()
        self.assertEqual(self.window.output_path, output)
        self.assertFalse(self.window._temporary_recording)
        self.assertIsNone(self.window._capture_cache)


if __name__ == "__main__":
    unittest.main()
