from pathlib import Path
import tempfile
import unittest

from subtitle_app.subtitles import SubtitleCue, cues_from_parts, render_srt, render_txt, save_new_exports, write_export


class SubtitleTimingTests(unittest.TestCase):
    def test_word_times_include_input_offset_and_preserve_a_silero_gap(self):
        parts = [{"start": 7, "end": 10, "text": "你好世界", "words": [
            {"start": 7.38, "end": 8.0, "text": "你好"},
            {"start": 8.1, "end": 9.5, "text": "世界"}]}]
        cues = cues_from_parts(parts, 66.68, 15)
        self.assertAlmostEqual(cues[0].start, 74.06)
        self.assertAlmostEqual(cues[-1].end, 76.18)
        self.assertIn("00:01:14,060 --> 00:01:16,180", render_srt(cues))

    def test_pause_and_duration_split_captions_without_fabricated_times(self):
        words = [{"start": 0, "end": 1, "text": "Hello"},
                 {"start": 1, "end": 2, "text": " world"},
                 {"start": 4, "end": 5, "text": " again"}]
        cues = cues_from_parts([{"start": 0, "end": 5, "text": "Hello world again", "words": words}], 0, 5)
        self.assertEqual([(cue.start, cue.end, cue.text) for cue in cues], [(0, 2, "Hello world"), (4, 5, "again")])
        words = [{"start": i, "end": i + 1, "text": "甲"} for i in range(12)]
        cues = cues_from_parts([{"start": 0, "end": 12, "text": "甲" * 12, "words": words}], 0, 12)
        self.assertEqual(len(cues), 2)
        self.assertTrue(all(cue.end - cue.start <= 6 for cue in cues))

    def test_missing_alignment_uses_segment_times_and_keeps_all_text(self):
        parts = [{"start": 2, "end": 30, "text": "完整文字", "words": [{"start": 2, "end": 3, "text": "完整"}]}]
        self.assertEqual(cues_from_parts(parts, 10, 15), [SubtitleCue(12, 25, "完整文字")])

    def test_srt_offset_clamping_numbering_and_hour_rollover(self):
        self.assertIn("01:00:00,000 --> 01:00:01,001", render_srt([SubtitleCue(3599.9996, 3601.001, "小时")]))
        cues = [SubtitleCue(0, 1, "应移出"), SubtitleCue(2, 4, "保留"), SubtitleCue(4, 5, "下一句")]
        output = render_srt(cues, -2.5)
        self.assertTrue(output.startswith("1\n00:00:00,000 --> 00:00:01,500\n保留"))
        self.assertIn("2\n00:00:01,500 --> 00:00:02,500", output)
        self.assertNotIn("应移出", output)
        with self.assertRaises(ValueError):
            render_srt(cues, float("nan"))

    def test_srt_never_overlaps_or_emits_empty_blocks(self):
        cues = [SubtitleCue(1, 3, "第一句"), SubtitleCue(2, 4, "第二句"), SubtitleCue(5, 5, "零时长"), SubtitleCue(6, 7, "")]
        output = render_srt(cues)
        self.assertIn("00:00:03,000 --> 00:00:04,000", output)
        self.assertNotIn("零时长", output)
        self.assertEqual(output.count(" --> "), 2)

    def test_chinese_caption_wrapping_preserves_characters(self):
        words = [{"start": i / 10, "end": (i + 1) / 10, "text": "甲"} for i in range(60)]
        cues = cues_from_parts([{"start": 0, "end": 6, "text": "甲" * 60, "words": words}], 0, 6)
        self.assertEqual("".join(cue.text.replace("\n", "") for cue in cues), "甲" * 60)
        self.assertTrue(all(len(line) <= 21 for cue in cues for line in cue.text.splitlines()))


class ExportFileTests(unittest.TestCase):
    def test_automatic_exports_preserve_existing_files_and_choose_a_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "中文.wav"
            audio.with_suffix(".srt").write_text("人工校对", encoding="utf-8")
            paths = save_new_exports(audio, "文字\n", "字幕\n")
            self.assertEqual([path.name for path in paths], ["中文-2.txt", "中文-2.srt"])
            self.assertEqual(audio.with_suffix(".srt").read_text(encoding="utf-8"), "人工校对")
            self.assertFalse(audio.with_suffix(".txt").exists())
            self.assertEqual(paths[0].read_text(encoding="utf-8"), "文字\n")

    def test_partial_exports_empty_input_and_manual_atomic_save(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            self.assertEqual(save_new_exports(audio, "", ""), [])
            paths = save_new_exports(audio, "部分文字", "", incomplete=True)
            self.assertEqual(paths[0].name, "audio-partial.txt")
            with self.assertRaises(ValueError):
                write_export(paths[0], "")
            self.assertEqual(paths[0].read_text(encoding="utf-8"), "部分文字")
            write_export(paths[0], "修改后的文字")
            self.assertEqual(paths[0].read_text(encoding="utf-8"), "修改后的文字")
            self.assertFalse(list(Path(directory).glob(".subtitle-*")))

    def test_txt_is_plain_unicode_text(self):
        self.assertEqual(render_txt(["  简体中文。 ", "", "Hello world"]), "简体中文。\nHello world\n")


if __name__ == "__main__":
    unittest.main()
