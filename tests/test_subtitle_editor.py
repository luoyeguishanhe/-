import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from subtitle_app.subtitle_editor import SubtitleEditor, preview_cues
from subtitle_app.subtitles import SubtitleCue, render_srt


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "video.mp4"
        self.original = [SubtitleCue(1, 2, "原文一"), SubtitleCue(3, 4, "原文二")]
        # Deterministic unit tests do not require a working multimedia backend.
        with patch("subtitle_app.subtitle_editor.QMediaPlayer.setSource"):
            self.editor = SubtitleEditor(self.source, self.original)
        self.addCleanup(self.editor.deleteLater)

    def test_preview_matches_export_offset_clipping_and_overlaps(self):
        cues = [SubtitleCue(0, 1, "开头"), SubtitleCue(0.5, 2, "后面"),
                SubtitleCue(4, 5, "末尾")]
        for offset in (-3, -0.75, 0, 2.1234):
            self.assertEqual(render_srt(preview_cues(cues, offset)), render_srt(cues, offset))

    def test_select_commits_text_and_seeks_without_changing_original(self):
        editor = self.editor
        editor.text_edit.setPlainText("校对文字")
        editor.start_time.setValue(1.25)
        with patch.object(editor.player, "setPosition") as seek:
            editor.table.setCurrentCell(1, 0)
        seek.assert_called_with(3000)
        self.assertEqual(editor.cues[0], SubtitleCue(1.25, 2, "校对文字"))
        self.assertEqual(self.original[0].text, "原文一")

    def test_invalid_times_and_blank_text_keep_selection_and_draft(self):
        editor = self.editor
        for start, end, text in ((2, 1, "错误"), (1, 3.5, "重叠"), (1, 2, "  ")):
            editor.start_time.setValue(start)
            editor.end_time.setValue(end)
            editor.text_edit.setPlainText(text)
            editor.table.setCurrentCell(1, 0)
            self.assertEqual(editor.current_row, 0)
            self.assertEqual(editor.table.currentRow(), 0)
            self.assertEqual(editor.cues, self.original)

    def test_save_uses_current_form_and_protects_existing_files(self):
        editor = self.editor
        output = self.source.with_name("video-校对.srt")
        editor.text_edit.setPlainText("已校对")
        with patch("subtitle_app.subtitle_editor.QFileDialog.getSaveFileName", return_value=(str(output), "")):
            editor.save()
        self.assertIn("已校对", output.read_text(encoding="utf-8"))
        saved = output.read_bytes()
        editor.text_edit.setPlainText("不能覆盖")
        with patch("subtitle_app.subtitle_editor.QFileDialog.getSaveFileName", return_value=(str(output), "")):
            editor.save()
        self.assertEqual(output.read_bytes(), saved)
        self.assertNotEqual(editor.saved_cues, editor.cues)

    def test_failed_save_retains_unsaved_changes(self):
        editor = self.editor
        editor.text_edit.setPlainText("仍保留")
        with patch("subtitle_app.subtitle_editor.QFileDialog.getSaveFileName", return_value=(str(self.source.with_suffix('.srt')), "")), \
                patch("subtitle_app.subtitle_editor.write_export", side_effect=OSError("磁盘不可写")):
            editor.save()
        self.assertIn("磁盘不可写", editor.status.text())
        self.assertEqual(editor.cues[0].text, "仍保留")
        self.assertEqual(editor.saved_cues, self.original)

    def test_close_and_escape_guard_unsaved_form_and_restore_saved_state(self):
        editor = self.editor
        editor.text_edit.setPlainText("尚未应用")
        with patch("subtitle_app.subtitle_editor.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question:
            editor.reject()
        question.assert_called_once()
        self.assertEqual(editor.text_edit.toPlainText(), "尚未应用")
        with patch("subtitle_app.subtitle_editor.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            editor.reject()
        self.assertEqual(editor.text_edit.toPlainText(), "原文一")
        self.assertEqual(editor.cues, self.original)

    def test_preview_uses_corrected_times_and_blanks_in_gaps(self):
        editor = self.editor
        editor.position_changed(1500)
        self.assertEqual(editor.caption.text(), "原文一")
        editor.position_changed(2500)
        self.assertEqual(editor.caption.text(), "")
        editor.position_changed(3000)
        self.assertEqual(editor.caption.text(), "原文二")

    def test_editor_integration_reuses_saved_corrections(self):
        from subtitle_app.main import MainWindow
        with patch("subtitle_app.main.list_output_devices", return_value=[]):
            window = MainWindow()
        window.timer.stop()
        self.addCleanup(window.deleteLater)
        window.session_audio_path = self.source
        window.subtitle_cues = self.original
        with patch("subtitle_app.main.SubtitleEditor.exec"):
            window.open_editor()
            first = window._editor
            first.text_edit.setPlainText("校对结果")
            first.apply_current()
            first.saved_cues = list(first.cues)
            window.open_editor()
            self.assertIs(window._editor, first)
            self.assertEqual(window._editor.cues[0].text, "校对结果")
            window.subtitle_offset.setValue(1)
            window.open_editor()
            self.assertIsNot(window._editor, first)
            self.assertEqual(window._editor.cues[0].start, 2)


if __name__ == "__main__":
    unittest.main()
