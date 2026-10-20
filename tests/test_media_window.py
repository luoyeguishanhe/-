import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from subtitle_app.main import MainWindow
from subtitle_app.media_pipeline import MediaInfo
from subtitle_app.subtitles import SubtitleCue


class MediaWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch("subtitle_app.main.list_output_devices", return_value=[]):
            self.window = MainWindow()
        self.window.timer.stop()
        self.source = Path(self.directory.name) / "video.mkv"
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.file_job = None
        self.window.pipeline = None
        self.window._exports_saved = True
        self.window.close()

    def test_import_works_without_devices_and_never_starts_loopback(self):
        window = self.window
        self.assertFalse(window.start_button.isEnabled())
        self.assertTrue(window.video_button.isEnabled())
        window.subtitle_offset.setValue(30)
        job = Mock()
        with patch("subtitle_app.main.QFileDialog.getOpenFileName", return_value=(str(self.source), "")), \
                patch("subtitle_app.main.MediaTranscriber", return_value=job) as factory, \
                patch.object(window.recorder, "start") as capture:
            window.start_video()
        factory.assert_called_once_with(self.source, "small", None)
        job.start.assert_called_once()
        capture.assert_not_called()
        self.assertFalse(window.video_button.isEnabled())
        self.assertEqual(window.subtitle_offset.value(), 0)
        self.assertEqual(window.stop_button.text(), "取消处理")

    def test_complete_restores_controls_and_exports_video_timestamps(self):
        window = self.window
        window.session_audio_path = self.source
        segment = SimpleNamespace(start_seconds=2, end_seconds=3)
        events = iter([("media_info", MediaInfo(5, 0, 1)), ("progress", (3, 5)),
                       ("text", (segment, "字幕", "zh")),
                       ("subtitles", [SubtitleCue(2.25, 2.8, "字幕")]), ("complete", None)])
        window.file_job = SimpleNamespace(next_event=lambda: next(events, None))
        window.update_recording_state()
        self.assertIsNone(window.file_job)
        self.assertEqual(window.file_progress.value(), 1000)
        self.assertTrue(window.video_button.isEnabled())
        self.assertTrue(window.export_srt_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        output = self.source.with_suffix(".srt").read_text(encoding="utf-8")
        self.assertIn("00:00:02,250 --> 00:00:02,800", output)

    def test_cancel_waits_for_terminal_event_and_saves_partial(self):
        window = self.window
        window.session_audio_path = self.source
        window.paragraphs = ["已完成一段"]
        window.subtitle_cues = [SubtitleCue(2, 3, "已完成一段")]
        events = iter([("cancelled", None)])
        job = SimpleNamespace(cancel=Mock(), next_event=lambda: next(events, None))
        window.file_job = job
        window.stop_recording()
        job.cancel.assert_called_once()
        self.assertIs(window.file_job, job)
        window.update_recording_state()
        self.assertTrue(self.source.with_name("video-partial.srt").is_file())
        self.assertIn("已取消", window.status.text())

    def test_close_requests_cancel_and_waits_for_file_worker(self):
        window = self.window
        job = SimpleNamespace(cancel=Mock())
        window.file_job = job
        event = QCloseEvent()
        window.closeEvent(event)
        job.cancel.assert_called_once()
        self.assertFalse(event.isAccepted())
        self.assertIs(window.file_job, job)

    def test_file_processing_blocks_recording_and_manual_export(self):
        window = self.window
        window.devices.addItem("device", 1)
        window.file_job = SimpleNamespace()
        with patch("subtitle_app.main.QFileDialog.getSaveFileName") as dialog:
            window.start_recording()
            window.export_text("txt")
        dialog.assert_not_called()

    def test_unknown_duration_and_no_audio_error_restore_the_window(self):
        window = self.window
        events = iter([("media_info", MediaInfo(None, 0, 0)), ("progress", (3, None)),
                       ("fatal", "所选文件没有音轨")])
        window.file_job = SimpleNamespace(next_event=lambda: next(events, None))
        with patch("subtitle_app.main.QMessageBox.warning") as message:
            window.update_recording_state()
        message.assert_called_once()
        self.assertIsNone(window.file_job)
        self.assertTrue(window.video_button.isEnabled())
        self.assertFalse(window.save_txt_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
