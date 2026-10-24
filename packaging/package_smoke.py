"""Acceptance checks executed by the actual frozen EXE (no cloud calls)."""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--startup-screenshot", type=Path)
    args = parser.parse_args(sys.argv[2:])
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "bundle_root": str(getattr(sys, "_MEIPASS", "")), "checks": {}}
    checks = report["checks"]
    try:
        import numpy as np
        import av
        import soxr
        import webrtcvad
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QFont
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
        from PyQt6.QtWidgets import QApplication
        from subtitle_app.main import MainWindow
        from subtitle_app.recognition import WhisperRecognizer
        from subtitle_app.audio_capture import list_output_devices
        from subtitle_app.runtime import model_source
        from subtitle_app.subtitle_mux import mux_subtitles
        from subtitle_app.subtitles import SubtitleCue
        from subtitle_app.translation import TranslationConfig, translate_subtitles
        from subtitle_app.billing import Pricing
        from threading import Event, Thread
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        app = QApplication.instance() or QApplication([])
        app.setFont(QFont("Microsoft YaHei", 9))
        window = MainWindow()
        window.timer.stop()
        checks["main_window"] = window.windowTitle()
        from subtitle_app.startup import StartupController
        controller = StartupController(app, window_factory=lambda: window)
        controller.start()
        app.processEvents()
        assert controller.window is window
        assert controller.welcome.enter_button.isEnabled()
        if args.startup_screenshot:
            controller.welcome.grab().save(str(args.startup_screenshot))
        controller.welcome.accept()
        checks["startup"] = "welcome ready; manual entry; reusable about screen"
        checks["audio_devices"] = len(list_output_devices())
        checks["webrtcvad"] = not webrtcvad.Vad(2).is_speech(bytes(640), 16000)
        checks["resampler"] = int(soxr.resample(np.zeros(4800, dtype=np.float32), 48000, 16000).size)
        origins = {}
        for name in ("numpy", "av", "faster_whisper", "ctranslate2", "onnxruntime", "opencc", "imageio_ffmpeg", "pyaudiowpatch"):
            origins[name] = str(importlib.import_module(name).__file__)
        report["module_origins"] = origins
        if report["frozen"]:
            bundle = Path(sys._MEIPASS).resolve()
            assert all(Path(value).resolve().is_relative_to(bundle) for value in origins.values()), origins

        samples = np.zeros(16000, dtype=np.float32)
        if args.audio:
            chunks = []
            with av.open(str(args.audio)) as container:
                resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
                total = 0
                for frame in container.decode(audio=0):
                    for converted in resampler.resample(frame):
                        data = converted.to_ndarray().reshape(-1)
                        chunks.append(data)
                        total += data.size
                    if total >= 8 * 16000:
                        break
                chunks += [frame.to_ndarray().reshape(-1) for frame in resampler.resample(None)]
            samples = np.concatenate(chunks)[:8 * 16000].astype(np.float32)
        recognizer = WhisperRecognizer("small", local_files_only=True,
                                       device=args.device, live=args.live)
        recognized = recognizer.recognize(samples, "zh")
        if args.audio:
            assert recognized["text"], "Expected speech in the supplied audio fixture"
        checks["asr"] = {"model_source": model_source("small"), "characters": len(recognized["text"]),
                         "parts": len(recognized["parts"]), "samples": samples.size,
                         **recognizer.backend}
        assert recognizer.converter.convert("漢語") == "汉语"
        checks["simplified_chinese"] = True

        def wait_for(condition, seconds=15):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.processEvents()
                if condition():
                    return
                time.sleep(.01)
            raise RuntimeError("Timed out waiting for packaged multimedia")

        if args.video:
            player = QMediaPlayer()
            output = QAudioOutput()
            output.setMuted(True)
            player.setAudioOutput(output)
            sink = QVideoSink()
            player.setVideoSink(sink)
            frames, errors = [], []
            sink.videoFrameChanged.connect(lambda frame: frames.append(frame.startTime()))
            player.errorOccurred.connect(lambda error, message: errors.append(message))
            player.setSource(QUrl.fromLocalFile(str(args.video.resolve())))
            player.play()
            wait_for(lambda: frames and player.duration() > 0)
            player.pause()
            assert not errors, errors
            checks["video_playback"] = {"duration_ms": player.duration(), "decoded_frames": len(frames)}
            player.stop()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                rows = json.loads(body["messages"][1]["content"])
                content = {"translations": [{"id": row["id"], "text": "測試譯文"} for row in rows]}
                response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                            "usage": {"prompt_tokens": 20, "completion_tokens": 10}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = []
        try:
            cues = translate_subtitles([SubtitleCue(.5, 1.5, "Hello")],
                TranslationConfig(f"http://127.0.0.1:{server.server_port}/v1", "mock-verification"),
                Event(), lambda *event: events.append(event))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        assert cues[0].text == "测试译文"
        usage = [value for kind, value in events if kind == "token_usage"][-1]
        checks["translation"] = {"service": "local mock, not a cloud model", "tokens": usage.prompt_tokens + usage.completion_tokens,
                                  "demonstration_cost_cny": str(Pricing(2, 8).cost(usage))}
        if args.video:
            with tempfile.TemporaryDirectory(prefix="subtitle-package-check-") as temporary:
                muxed = Path(temporary) / "字幕测试.mkv"
                mux_subtitles(args.video, cues, muxed, Event())
                with av.open(str(muxed)) as container:
                    assert container.streams.video and container.streams.subtitles
                    packet = next(p for p in container.demux(container.streams.subtitles[0]) if p.size)
                    assert float(packet.pts * packet.time_base) == .5
                    assert "测试译文".encode() in bytes(packet)
                checks["soft_subtitle_mux"] = True
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        window.close()
        report["success"] = True
        return 0
    except Exception:
        report["success"] = False
        report["error"] = traceback.format_exc()
        return 1
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
