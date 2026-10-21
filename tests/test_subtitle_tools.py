import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import av
from PyQt6.QtWidgets import QApplication, QMessageBox

from media_fixtures import create_video
from test_translation import LocalChatServer
from subtitle_app.subtitles import SubtitleCue, parse_srt
from subtitle_app.subtitle_tools import SubtitleToolsDialog


class SubtitleToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.video = self.folder / 'video.mkv'
        self.cues = [SubtitleCue(.5, 1.5, 'Hello'), SubtitleCue(2, 3, 'World')]
        self.dialog = SubtitleToolsDialog(self.video, self.cues)
        self.dialog.timer.stop()
        self.addCleanup(self.dialog.deleteLater)

    def wait_job(self):
        deadline = time.monotonic() + 10
        while self.dialog.job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            self.dialog.poll_job()
            time.sleep(.01)
        self.assertIsNone(self.dialog.job, self.dialog.status.text())

    def test_actual_http_translation_srt_save_and_mkv_mux_via_window(self):
        create_video(self.video)
        server = LocalChatServer()
        self.addCleanup(server.close)
        dialog = self.dialog
        dialog.base_url.setText(server.base_url)
        dialog.model.setText('test-model')
        dialog.start_translation()
        self.assertFalse(dialog.srt_button.isEnabled())
        self.assertFalse(dialog.mux_button.isEnabled())
        self.wait_job()
        self.assertTrue(dialog.translation_complete)
        self.assertEqual(dialog.track.currentIndex(), 1)
        self.assertEqual(dialog.translated[0], SubtitleCue(.5, 1.5, '这是测试译文'))
        self.assertEqual(dialog.cues, self.cues)
        srt = self.folder / 'translated.srt'
        with patch('subtitle_app.subtitle_tools.QFileDialog.getSaveFileName', return_value=(str(srt), '')):
            dialog.save_translation()
        self.assertTrue(dialog.translation_saved)
        self.assertEqual(parse_srt(srt.read_text(encoding='utf-8')), dialog.translated)
        output = self.folder / 'translated.mkv'
        with patch('subtitle_app.subtitle_tools.QFileDialog.getSaveFileName', return_value=(str(output), '')):
            dialog.start_mux()
        self.wait_job()
        with av.open(str(output)) as container:
            sub = container.streams.subtitles[0]
            self.assertEqual(sub.metadata['language'], 'zho')
            packets = [packet for packet in container.demux(sub) if packet.size]
            self.assertEqual(len(packets), 2)
            self.assertAlmostEqual(float(packets[0].pts * packets[0].time_base), .5)
            self.assertIn('这是测试译文'.encode(), bytes(packets[0]))

    def test_partial_translation_is_saveable_but_cannot_be_muxed(self):
        dialog = self.dialog
        dialog.translated = self.cues[:1]
        dialog.translation_saved = False
        dialog.track.setCurrentIndex(1)
        dialog.refresh_preview()
        self.assertFalse(dialog.mux_button.isEnabled())
        self.assertTrue(dialog.save_translation_button.isEnabled())
        output = self.folder / 'partial.srt'
        with patch('subtitle_app.subtitle_tools.QFileDialog.getSaveFileName', return_value=(str(output), '')) as choose:
            dialog.save_translation()
        self.assertIn('partial', choose.call_args.args[2])
        self.assertTrue(output.exists())

    def test_import_does_not_discard_unsaved_translation_without_confirmation(self):
        dialog = self.dialog
        dialog.translated = [SubtitleCue(.5, 1.5, '未保存')]
        dialog.translation_saved = False
        source = self.folder / 'new.srt'
        source.write_text('1\n00:00:01,000 --> 00:00:02,000\nnew\n', encoding='utf-8')
        with patch('subtitle_app.subtitle_tools.QFileDialog.getOpenFileName', return_value=(str(source), '')), \
                patch('subtitle_app.subtitle_tools.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            dialog.choose_srt()
        self.assertEqual(dialog.cues, self.cues)
        self.assertEqual(dialog.translated[0].text, '未保存')

    def test_no_credentials_saved_and_presets_clear_old_key(self):
        dialog = self.dialog
        dialog.api_key.setText('old-provider-secret')
        dialog.preset.setCurrentIndex(2)
        self.assertEqual(dialog.api_key.text(), '')
        self.assertEqual(dialog.base_url.text(), 'http://127.0.0.1:11434/v1')
        dialog.api_key.setText('local-key')
        dialog.reject()
        self.assertEqual(dialog.api_key.text(), '')

    def test_failed_save_and_close_keep_unsaved_translation(self):
        dialog = self.dialog
        dialog.translated = self.cues[:1]
        dialog.translation_saved = False
        output = self.folder / 'translate.srt'
        with patch('subtitle_app.subtitle_tools.QFileDialog.getSaveFileName', return_value=(str(output), '')), \
                patch('subtitle_app.subtitle_tools.write_export', side_effect=OSError('磁盘不可写')):
            dialog.save_translation()
        self.assertFalse(dialog.translation_saved)
        self.assertEqual(dialog.translated, self.cues[:1])
        with patch('subtitle_app.subtitle_tools.QMessageBox.question', return_value=QMessageBox.StandardButton.No) as question:
            dialog.reject()
        question.assert_called_once()
        self.assertEqual(dialog.translated, self.cues[:1])

    def test_actual_http_costs_accumulate_across_runs_and_export_without_secrets(self):
        import json
        server = LocalChatServer()
        self.addCleanup(server.close)
        dialog = self.dialog
        dialog.base_url.setText(server.base_url)
        dialog.model.setText('test-model')
        dialog.api_key.setText('never-in-report')
        dialog.pricing_enabled.setChecked(True)
        dialog.input_price.setValue(2)
        dialog.output_price.setValue(8)
        dialog.batch_size.setValue(1)
        dialog.start_translation()
        self.assertFalse(dialog.pricing_enabled.isEnabled())
        self.assertFalse(dialog.input_price.isEnabled())
        self.wait_job()
        self.assertIn('CNY 0.000240', dialog.cost_label.text())
        dialog.input_price.setValue(3)
        dialog.output_price.setValue(9)
        # Editing future rates does not rewrite the completed request's cost.
        self.assertIn('CNY 0.000240', dialog.cost_label.text())
        with patch('subtitle_app.subtitle_tools.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            dialog.start_translation()
        self.wait_job()
        self.assertIn('CNY 0.000300', dialog.cost_label.text())
        self.assertIn('CNY 0.000540', dialog.window_usage_label.text())
        output = self.folder / 'usage.json'
        with patch('subtitle_app.subtitle_tools.QFileDialog.getSaveFileName', return_value=(str(output), '')):
            dialog.export_usage_report()
        text = output.read_text(encoding='utf-8')
        report = json.loads(text)
        self.assertEqual(report['reported_input_tokens'], 80)
        self.assertEqual(report['reported_output_tokens'], 40)
        self.assertEqual(len(report['tasks']), 2)
        self.assertNotIn('never-in-report', text)
        self.assertNotIn('Hello', text)
        self.assertEqual(report['estimated_known_cost_by_currency']['CNY'], '0.00054')

    def test_changing_provider_or_model_requires_reenabling_pricing(self):
        dialog = self.dialog
        dialog.pricing_enabled.setChecked(True)
        dialog.preset.setCurrentIndex(2)
        self.assertFalse(dialog.pricing_enabled.isChecked())
        dialog.pricing_enabled.setChecked(True)
        dialog.model.textEdited.emit('different-model')
        self.assertFalse(dialog.pricing_enabled.isChecked())
