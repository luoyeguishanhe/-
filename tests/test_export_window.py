import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QMessageBox

from subtitle_app.main import MainWindow
from subtitle_app.subtitles import SubtitleCue


class ExportWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch("subtitle_app.main.list_output_devices", return_value=[]):
            self.window = MainWindow()
        self.window.timer.stop()
        self.addCleanup(self.window.close)

    def test_complete_saves_files_keeps_results_and_enables_export(self):
        window = self.window
        window.session_audio_path = Path(self.directory.name) / "recording.wav"
        window.output_path = window.session_audio_path
        segment = SimpleNamespace(start_seconds=0, end_seconds=15)
        events = iter([("text", (segment, "简体文字", "zh")),
                       ("subtitles", [SubtitleCue(3.2, 4.8, "简体文字")]), ("complete", None)])
        window.pipeline = SimpleNamespace(next_event=lambda: next(events, None))
        window.update_recording_state()
        self.assertIsNone(window.pipeline)
        self.assertTrue(window.save_txt_button.isEnabled())
        self.assertTrue(window.export_srt_button.isEnabled())
        self.assertEqual(window.session_audio_path.with_suffix(".txt").read_text(encoding="utf-8"), "简体文字\n")
        srt = window.session_audio_path.with_suffix(".srt").read_text(encoding="utf-8")
        self.assertIn("00:00:03,200 --> 00:00:04,800", srt)
        self.assertTrue(window.transcript.toPlainText())

    def test_warnings_mark_automatic_files_partial(self):
        window = self.window
        window.session_audio_path = Path(self.directory.name) / "recording.wav"
        window.paragraphs = ["已识别部分"]
        window._session_has_warnings = True
        window._recording_finished(completed=True)
        self.assertTrue((Path(self.directory.name) / "recording-partial.txt").is_file())

    def test_closing_during_asr_waits_for_completion_instead_of_cancelling(self):
        window = self.window
        pipeline = SimpleNamespace(cancel=lambda: self.fail("尚在识别时不应取消"))
        window.pipeline = pipeline
        window._capture_started = True
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertIs(window.pipeline, pipeline)
        window.pipeline = None

    def test_manual_srt_uses_current_offset_and_normalizes_extension(self):
        window = self.window
        window.subtitle_cues = [SubtitleCue(1, 2, "字幕")]
        window.subtitle_offset.setValue(30)
        target = Path(self.directory.name) / "chosen"
        with patch("subtitle_app.main.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            window.export_text("srt")
        self.assertIn("00:00:31,000 --> 00:00:32,000", target.with_suffix(".srt").read_text(encoding="utf-8"))

    def test_automatic_save_failure_keeps_text_available(self):
        window = self.window
        window.session_audio_path = Path(self.directory.name) / "recording.wav"
        window.paragraphs = ["需要保留的文字"]
        with patch("subtitle_app.main.save_new_exports", side_effect=OSError("无法写入")):
            window._recording_finished(completed=True)
        self.assertEqual(window.paragraphs, ["需要保留的文字"])
        self.assertFalse(window._exports_saved)
        self.assertTrue(window.save_txt_button.isEnabled())
        self.assertIn("自动保存失败", window.export_status.text())
        window._exports_saved = True

    def test_manual_overwrite_confirmation_uses_the_final_extension(self):
        window = self.window
        window.subtitle_cues = [SubtitleCue(1, 2, "新字幕")]
        target = Path(self.directory.name) / "chosen.srt"
        target.write_text("人工校对过的字幕", encoding="utf-8")
        with patch("subtitle_app.main.QFileDialog.getSaveFileName", return_value=(str(target.with_suffix("")), "")), \
                patch("subtitle_app.main.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            window.export_text("srt")
        self.assertEqual(target.read_text(encoding="utf-8"), "人工校对过的字幕")


if __name__ == "__main__":
    unittest.main()
