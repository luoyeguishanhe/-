"""Compare old CPU, live CPU and live CUDA on the same local WAV excerpt."""

import argparse
import gc
import json
from pathlib import Path
from time import perf_counter
import wave

import numpy as np

from subtitle_app.recognition import WhisperRecognizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    with wave.open(str(args.audio)) as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            parser.error("Use a 16 kHz mono PCM16 WAV")
        audio = np.frombuffer(source.readframes(int(args.seconds * 16000)), dtype="<i2").astype(np.float32) / 32768
    rows = []
    for name, device, live in (("previous_cpu", "cpu", False),
                                ("live_cpu", "cpu", True), ("live_gpu", "cuda", True)):
        loaded = perf_counter()
        recognizer = WhisperRecognizer(args.model, device=device, live=live, local_files_only=True)
        load_seconds = perf_counter() - loaded
        recognizer.recognize(audio, args.language)  # warm each configuration equally
        started = perf_counter()
        result = recognizer.recognize(audio, args.language)
        seconds = perf_counter() - started
        row = {"configuration": name, **recognizer.backend, "audio_seconds": len(audio) / 16000,
               "load_seconds": round(load_seconds, 3), "recognition_seconds": round(seconds, 3),
               "realtime_factor": round(seconds / (len(audio) / 16000), 3), "text": result["text"]}
        rows.append(row)
        print(name, "seconds=", row["recognition_seconds"], "RTF=", row["realtime_factor"], flush=True)
        del recognizer
        gc.collect()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"audio": str(args.audio), "results": rows},
                                      ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
