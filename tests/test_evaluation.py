import json
from pathlib import Path
import tempfile
import unittest
import wave

from subtitle_app.benchmark import load_audio, load_manifest, score_prediction
from subtitle_app.evaluation import character_errors, normalize_for_cer


class CharacterErrorTests(unittest.TestCase):
    def test_character_styles_and_punctuation_do_not_count_as_errors(self):
        self.assertEqual(normalize_for_cer("漢語，ＡＳＲ！\n"), "汉语asr")
        self.assertEqual(character_errors("漢語，ASR！", "汉语asr")["cer"], 0)

    def test_separate_error_types(self):
        for reference, hypothesis, expected in (
            ("甲乙丙", "甲丁丙", (1, 0, 0)),
            ("甲乙丙", "甲丙", (0, 1, 0)),
            ("甲乙", "甲丁乙", (0, 0, 1)),
            ("kitten", "sitting", (2, 0, 1)),
        ):
            with self.subTest(reference=reference, hypothesis=hypothesis):
                score = character_errors(reference, hypothesis)
                self.assertEqual(tuple(score[key] for key in ("substitutions", "deletions", "insertions")), expected)
                self.assertEqual(len(score["edits"]), sum(expected))

    def test_empty_reference_and_excess_output(self):
        empty = character_errors("", "文字")
        self.assertIsNone(empty["cer"])
        self.assertEqual(empty["insertions"], 2)
        self.assertGreater(character_errors("甲", "甲乙丙丁")["cer"], 1)

    def test_repetition_is_counted(self):
        self.assertEqual(character_errors("青春", "青春青春")["insertions"], 2)

    def test_numbers_are_not_silently_equated(self):
        self.assertNotEqual(normalize_for_cer("1344"), normalize_for_cer("一三四四"))


class BenchmarkInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.audio = self.folder / "录音.wav"
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00\x00" * 16000)
        self.reference = self.folder / "reference.txt"
        self.reference.write_text("甲乙", encoding="utf-8")

    def test_wav_format_is_validated(self):
        audio, metadata = load_audio(self.audio)
        self.assertEqual(len(audio), 16000)
        self.assertEqual(metadata["zero_fraction"], 1)
        bad = self.folder / "bad.wav"
        with wave.open(str(bad), "wb") as output:
            output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00" * 192000)
        with self.assertRaisesRegex(ValueError, "16 kHz"):
            load_audio(bad)

    def test_draft_status_and_range_are_preserved(self):
        sample = {"id": "one", "evaluation": {"reference": str(self.reference), "status": "draft", "start": 0, "end": 1.5}}
        prediction = {"audio_seconds": 3, "segments": [
            {"start": 0, "end": 1, "text": "甲乙"},
            {"start": 2, "end": 3, "text": "无关文字"}]}
        score = score_prediction(prediction, sample)
        self.assertEqual(score["status"], "draft")
        self.assertEqual(score["cer"], 0)
        sample["evaluation"]["end"] = 2.5
        self.assertEqual(score_prediction(prediction, sample)["status"], "unscored")
        sample["evaluation"]["end"] = 4
        with self.assertRaises(ValueError):
            score_prediction(prediction, sample)

    def test_failed_recognition_never_gets_a_score(self):
        self.assertEqual(score_prediction({"error": "failure"}, {})["status"], "failed")

    def test_manifest_paths_and_duplicate_ids(self):
        path = self.folder / "清单.json"
        sample = {"id": "one", "audio": "录音.wav", "evaluation": {"reference": "reference.txt", "status": "draft"}}
        path.write_text(json.dumps({"version": 1, "samples": [sample]}), encoding="utf-8-sig")
        self.assertEqual(load_manifest(path)[0]["audio"], str(self.audio.resolve()))
        path.write_text(json.dumps({"version": 1, "samples": [sample, sample]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_manifest(path)


if __name__ == "__main__":
    unittest.main()
