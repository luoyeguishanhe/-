import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from subtitle_app.runtime import configure_windowed_runtime, model_source


class RuntimeTests(unittest.TestCase):
    def test_source_run_does_not_use_a_model_next_to_python(self):
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(model_source('small'), 'small')

    def test_frozen_app_uses_adjacent_complete_portable_model(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'models/small'
            model.mkdir(parents=True)
            for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'):
                (model / name).write_bytes(b'fixture')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(Path(folder) / 'SubtitleApp.exe')):
                self.assertEqual(model_source('small'), str(model))
                self.assertEqual(model_source('medium'), 'medium')
                (model / 'model.bin').unlink()
                with self.assertRaisesRegex(ValueError, '不完整'):
                    model_source('small')

    def test_windowless_python_has_writable_utf8_streams(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'LOCALAPPDATA': folder}), \
                    patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
                path = configure_windowed_runtime()
                stream = sys.stdout
                try:
                    self.assertIs(sys.stderr, stream)
                    print('窗口程序日志')
                    stream.flush()
                    self.assertIn('窗口程序日志', path.read_text(encoding='utf-8'))
                finally:
                    stream.close()
