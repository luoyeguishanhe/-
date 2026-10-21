from pathlib import Path
import tempfile
from threading import Event
import unittest

import av
import numpy as np

from media_fixtures import create_video
from subtitle_app.background_job import Cancelled
from subtitle_app.subtitle_mux import mux_subtitles, publish_new_file
from subtitle_app.subtitles import SubtitleCue, parse_srt, render_srt


class MuxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.video = self.folder / "原视频.mkv"
        self.output = self.folder / "带字幕.mkv"
        self.cues = [SubtitleCue(2.25, 2.8, "测试中文字幕")]

    def test_real_mux_copies_audio_video_and_preserves_subtitle_timing(self):
        create_video(self.video, np.zeros(16000, dtype="float32"), audio_start=2)
        before = self.video.read_bytes()
        mux_subtitles(self.video, self.cues, self.output, Event())
        self.assertEqual(self.video.read_bytes(), before)
        def media_packets(path):
            with av.open(str(path)) as container:
                return [(p.stream.type, bytes(p)) for p in container.demux()
                        if p.size and p.stream.type in ("video", "audio")]
        self.assertEqual(media_packets(self.video), media_packets(self.output))
        with av.open(str(self.output)) as container:
            self.assertEqual(len(container.streams.video), 1)
            self.assertEqual(len(container.streams.audio), 1)
            subtitles = container.streams.subtitles
            self.assertEqual(len(subtitles), 1)
            packet = next(p for p in container.demux(subtitles[0]) if p.size)
            self.assertAlmostEqual(float(packet.pts * packet.time_base), 2.25, places=3)
            self.assertAlmostEqual(float(packet.duration * packet.time_base), .55, places=3)
            self.assertIn("测试中文字幕".encode(), bytes(packet))
        self.assertFalse(list(self.folder.glob('.subtitle-mux-*')))

    def test_existing_destination_and_source_survive(self):
        self.video.write_bytes(b"original")
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            mux_subtitles(self.video, self.cues, self.output, Event())
        self.assertEqual(self.output.read_bytes(), b"existing")
        with self.assertRaises(FileExistsError):
            mux_subtitles(self.video, self.cues, self.video, Event())
        self.assertEqual(self.video.read_bytes(), b"original")

    def test_cancel_does_not_publish_output(self):
        create_video(self.video)
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            mux_subtitles(self.video, self.cues, self.output, cancel)
        self.assertFalse(self.output.exists())

    def test_cancel_after_mux_starts_cleans_temporary_output(self):
        create_video(self.video)
        cancel = Event()
        with self.assertRaises(Cancelled):
            mux_subtitles(self.video, self.cues, self.output, cancel,
                          lambda kind, value: cancel.set())
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.folder.glob('.subtitle-mux-*')))

    def test_audio_only_and_invalid_source_fail_without_output(self):
        self.video.write_bytes(b"not a video")
        with self.assertRaises(Exception):
            mux_subtitles(self.video, self.cues, self.output, Event())
        self.assertFalse(self.output.exists())

    def test_publish_refuses_to_replace_raced_destination(self):
        temporary = self.folder / "temporary"
        temporary.write_bytes(b"new")
        self.output.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            publish_new_file(temporary, self.output, Event())
        self.assertEqual(self.output.read_bytes(), b"old")

    def test_srt_roundtrip_and_bad_inputs(self):
        self.assertEqual(parse_srt("\ufeff" + render_srt(self.cues).replace('\n', '\r\n')), self.cues)
        for invalid in ("", "wrong", "1\n00:00:02,000 --> 00:00:01,000\na\n"):
            with self.assertRaises(ValueError):
                parse_srt(invalid)
        with self.assertRaises(ValueError):
            parse_srt("1\n00:00:00,000 --> 00:00:02,000\na\n\n2\n00:00:01,000 --> 00:00:03,000\nb")
