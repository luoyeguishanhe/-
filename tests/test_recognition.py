from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from subtitle_app.recognition import WhisperRecognizer


class RecognitionTextTests(unittest.TestCase):
    def test_spaces_between_english_parts_are_preserved(self):
        recognizer = WhisperRecognizer.__new__(WhisperRecognizer)
        parts = [SimpleNamespace(start=i, end=i + 1, text=text, avg_logprob=-0.1, no_speech_prob=0.01)
                 for i, text in enumerate((" Hello", " world"))]
        recognizer.model = Mock()
        recognizer.model.transcribe.return_value = (iter(parts), SimpleNamespace(language="en", duration_after_vad=2))
        result = recognizer.recognize(np.zeros(32000, dtype=np.float32), "en")
        self.assertEqual(result["text"], "Hello world")


if __name__ == "__main__":
    unittest.main()
