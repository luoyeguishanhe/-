"""Offline comparison: python -m subtitle_app.benchmark --manifest FILE."""

import argparse
import csv
from datetime import datetime
import gc
import hashlib
from html import escape
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import time
import wave
from urllib.parse import urlparse

import numpy as np

from .evaluation import character_errors
from .recognition import TRANSCRIBE_SETTINGS, WhisperRecognizer
from .speech_pipeline import SpeechSegmenter


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_manifest(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if document.get("version") != 1 or not document.get("samples"):
        raise ValueError("清单需要 version: 1 和非空 samples 列表")
    samples, seen = [], set()
    for item in document["samples"]:
        sample = dict(item)
        sample_id = sample.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sample_id) or sample_id in seen:
            raise ValueError("样本 id 必须唯一，且只含英文字母、数字、下划线或短横线")
        seen.add(sample_id)
        sample["audio"] = str(resolve_path(path.parent, sample["audio"]))
        if not Path(sample["audio"]).is_file():
            raise ValueError(f"录音不存在：{sample['audio']}")
        evaluation = sample.get("evaluation")
        if evaluation:
            evaluation = dict(evaluation)
            if evaluation.get("status") not in ("draft", "verified"):
                raise ValueError("参考文字 status 必须为 draft（待核对）或 verified（已逐字核对）")
            evaluation["reference"] = str(resolve_path(path.parent, evaluation["reference"]))
            if not Path(evaluation["reference"]).is_file():
                raise ValueError(f"参考文字不存在：{evaluation['reference']}")
            sample["evaluation"] = evaluation
        samples.append(sample)
    return samples


def load_audio(path: Path) -> tuple[np.ndarray, dict]:
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError(f"{path.name} 需要 16 kHz、单声道、16 位 PCM WAV；请先统一格式")
        raw = source.readframes(source.getnframes())
        if len(raw) != source.getnframes() * 2:
            raise ValueError(f"{path.name} 的 WAV 数据不完整")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if not audio.size:
        raise ValueError(f"{path.name} 是空录音")
    return audio, {"duration_seconds": len(audio) / 16000,
                   "peak": float(np.max(np.abs(audio))),
                   "rms": float(np.sqrt(np.mean(audio ** 2))),
                   "zero_fraction": float(np.mean(audio == 0)),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def segment_audio(audio: np.ndarray):
    segmenter = SpeechSegmenter()
    segments = []
    for start in range(0, len(audio), 1600):
        segments.extend(segmenter.feed(audio[start:start + 1600], start))
    segments.extend(segmenter.finish())
    return segments


def score_prediction(prediction: dict, sample: dict) -> dict:
    evaluation = sample.get("evaluation")
    if prediction.get("error"):
        return {"status": "failed", "reason": prediction["error"]}
    if not evaluation:
        return {"status": "unscored", "reason": "没有匹配录音的参考文字"}
    start = float(evaluation.get("start", 0))
    end = float(evaluation.get("end", prediction["audio_seconds"]))
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= prediction["audio_seconds"] + 0.001):
        raise ValueError(f"{sample['id']} 评分范围超出录音：{start}–{end}")
    selected = []
    for segment in prediction["segments"]:
        if segment["end"] <= start or segment["start"] >= end:
            continue
        if segment["start"] < start - 0.001 or segment["end"] > end + 0.001:
            return {"status": "unscored", "reason": "评分边界穿过了一个语音片段，请调整范围后重新评分"}
        selected.append(segment)
    reference_path = Path(evaluation["reference"])
    reference = reference_path.read_text(encoding="utf-8-sig")
    errors = character_errors(reference, "".join(item["text"] for item in selected))
    # Empty references cannot produce a CER; insertion count is still meaningful.
    return {"status": evaluation["status"], "start": start, "end": end,
            "reference": str(reference_path),
            "reference_sha256": hashlib.sha256(reference.encode("utf-8")).hexdigest(),
            "source_url": evaluation.get("source_url", ""), **errors}


def create_html_report(folder: Path, results: dict, samples: list[dict]) -> None:
    by_id = {sample["id"]: sample for sample in samples}
    blocks = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>中文语音模型对比</title><style>',
        'body{font:16px/1.7 "Microsoft YaHei",sans-serif;max-width:1120px;margin:40px auto;padding:0 24px;color:#213046;background:#f7f9fc}h1,h2{line-height:1.3}section{background:white;padding:24px;margin:24px 0;border:1px solid #d9e2ef;border-radius:10px}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #d9e2ef;padding:9px;text-align:left;vertical-align:top}th{background:#eef3f9}a{color:#1257a7}code{overflow-wrap:anywhere}.note{padding:16px;background:#fff4dc;border-left:4px solid #ba842a}.muted{color:#67788b}audio{width:100%}summary{cursor:pointer}pre{white-space:pre-wrap}',
        '</style><h1>中文语音模型对比</h1>',
        '<p>同一台电脑、相同音频与识别设置，一次离线测试。模型没有重新训练。</p>',
        '<p class="note">待核对参考文字得到的是“草稿 CER”，不能当成正式准确率。先听录音并校对参考 TXT，再重新评分。</p>',
        '<section><h2>结果一览</h2><table><tr><th>录音</th><th>模型</th><th>录音时长</th><th>识别耗时</th><th>RTF</th><th>参考状态</th><th>CER</th></tr>']
    for prediction in results["predictions"]:
        sample, score = by_id[prediction["sample_id"]], prediction["scoring"]
        state = {"draft": "待核对草稿", "verified": "已逐字核对", "unscored": "未评分", "failed": "失败"}[score["status"]]
        cer = f"{score['cer']:.2%}" if score.get("cer") is not None else "—"
        blocks.append(f"<tr><td>{escape(sample.get('label', sample['id']))}</td><td>{escape(prediction['model'])}</td><td>{prediction['audio_seconds']:.2f} 秒</td><td>{prediction['inference_seconds']:.2f} 秒</td><td>{prediction['rtf']:.3f}</td><td>{state}</td><td>{cer}</td></tr>")
    blocks += ['</table><p>CER =（替换字 + 漏字 + 多出字）÷参考字数，越低越好，可能超过 100%。统一繁简、全半角和大小写，忽略标点空白；数字写法仍可能造成差异。</p>',
        '<p>RTF = 识别耗时÷整段录音时长。加载、下载、预热不计入；静音也算录音时长。RTF 不是字幕显示延迟，实际还需要等待断句。</p>',
        '<p><a href="summary.csv">CSV 汇总</a> · <a href="results.json">完整参数及逐字差异 JSON</a> · <a href="report.md">文字版报告</a></p></section>']
    if results.get("status", "complete") != "complete":
        blocks.append('<p class="note">本轮尚未全部完成或存在失败，下面只列出已保存的结果。</p>')
    for model_name, preparation in results["models"].items():
        if preparation.get("error"):
            blocks.append(f"<p class='note'>{escape(model_name)} 加载失败：{escape(preparation['error'])}</p>")
    for prediction in results["predictions"]:
        sample, score = by_id[prediction["sample_id"]], prediction["scoring"]
        stem = f"{sample['id']}-{prediction['model']}"
        blocks += [f"<section><h2>{escape(sample.get('label', sample['id']))} / {escape(prediction['model'])}</h2>",
                   f'<audio controls preload="none" src="{escape(Path(sample["audio"]).as_uri(), quote=True)}"></audio>',
                   f'<p><a href="{stem}-timed.txt">逐段文字</a> · <a href="{stem}.txt">纯文字</a></p>']
        if score["status"] in ("draft", "verified"):
            blocks.append(f"<p>仅评分录音 {score['start']:.2f}～{score['end']:.2f} 秒；替换 {score['substitutions']} 字、漏 {score['deletions']} 字、多出 {score['insertions']} 字。</p>")
            blocks.append(f'<p><a href="{escape(Path(score["reference"]).as_uri(), quote=True)}">待核对参考 TXT</a></p>')
            source_url = score.get("source_url", "")
            if urlparse(source_url).scheme in ("https", "http"):
                blocks.append(f'<p><a href="{escape(source_url, quote=True)}">网上原文参考</a></p>')
            blocks.append('<details><summary>查看逐字差异（参考尚未核对时，差异也可能来自参考文本）</summary><table><tr><th>操作</th><th>参考</th><th>模型输出</th></tr>')
            for edit in score["edits"]:
                label = {"replace": "替换", "delete": "漏字", "insert": "多出"}[edit["operation"]]
                blocks.append(f'<tr><td>{label}</td><td>{escape(edit["reference"]) or "—"}</td><td>{escape(edit["hypothesis"]) or "—"}</td></tr>')
            blocks.append('</table></details>')
        else:
            blocks.append(f'<p class="note">{escape(score["reason"])}</p>')
        blocks.append('<table><tr><th>录音时间</th><th>本段识别耗时</th><th>文字</th></tr>')
        for segment in prediction["segments"]:
            unscored = score.get("end") is not None and (segment["start"] >= score["end"] or segment["end"] <= score["start"])
            suffix = '<br><span class="muted">此段未评分</span>' if unscored else ''
            blocks.append(f'<tr><td>{segment["start"]:.2f}～{segment["end"]:.2f} 秒{suffix}</td><td>{segment["inference_seconds"]:.2f} 秒</td><td>{escape(segment["text"]) or "（未输出文字）"}</td></tr>')
        blocks.append('</table></section>')
    blocks += ['<section><h2>复现配置</h2><pre>', escape(json.dumps(results["configuration"], ensure_ascii=False, indent=2)), '</pre></section></html>']
    (folder / "report.html").write_text("\n".join(blocks), encoding="utf-8")


def create_reports(folder: Path, results: dict, samples: list[dict]) -> None:
    by_id = {sample["id"]: sample for sample in samples}
    csv_rows = []
    lines = ["# 模型对比结果", "", "这是同一台电脑上的一次离线测试，不是模型训练。",
             "", "待核对原文得到的是草稿 CER，不能当成经过人工确认的正确率。",
             "CER =（替换字 + 漏字 + 多出字）÷参考字数，越低越好，可能大于 100%。",
             "统一繁简、大小写及全角字符，忽略标点和空白；阿拉伯数字与中文数字没有自动等价。",
             "", "RTF = 识别耗时÷整段录音时长；不含下载、模型加载、预热和音频切段。",
             "整段录音中的静音也计入分母。RTF 不等于字幕出现延迟，实时字幕还要等待断句。",
             "", "| 录音 | 模型 | 录音秒数 | 识别秒数 | RTF | 参考状态 | CER |",
             "| --- | --- | ---: | ---: | ---: | --- | ---: |"]
    for prediction in results["predictions"]:
        sample = by_id[prediction["sample_id"]]
        scoring = score_prediction(prediction, sample)
        prediction["scoring"] = scoring
        cer = scoring.get("cer")
        state = {"draft": "待核对草稿", "verified": "已逐字核对", "unscored": "未评分", "failed": "失败"}[scoring["status"]]
        csv_rows.append({"sample": sample["id"], "model": prediction["model"],
                         "audio_seconds": prediction["audio_seconds"],
                         "inference_seconds": round(prediction["inference_seconds"], 3),
                         "rtf": round(prediction["rtf"], 4), "reference_status": scoring["status"],
                         "cer": cer if cer is not None else "",
                         "substitutions": scoring.get("substitutions", ""),
                         "deletions": scoring.get("deletions", ""),
                         "insertions": scoring.get("insertions", ""),
                         "reference_characters": scoring.get("reference_characters", ""),
                         "error": prediction.get("error", "")})
        cer_display = f"{cer:.2%}" if cer is not None else "—"
        label = sample.get("label", sample["id"]).replace("|", "／")
        lines.append(f"| {label} | {prediction['model']} | {prediction['audio_seconds']:.2f} | {prediction['inference_seconds']:.2f} | {prediction['rtf']:.3f} | {state} | {cer_display} |")
        stem = f"{sample['id']}-{prediction['model']}"
        (folder / f"{stem}.txt").write_text("\n".join(segment["text"] for segment in prediction["segments"] if segment["text"]), encoding="utf-8")
        (folder / f"{stem}-timed.txt").write_text("\n".join(f"[{segment['start']:.2f}～{segment['end']:.2f} 秒] {segment['text'] or '（未输出文字）'}" for segment in prediction["segments"]), encoding="utf-8")
    if results.get("status", "complete") != "complete":
        lines[2:2] = ["本轮尚未全部完成或存在失败，下面只列出已保存的结果。", ""]
    lines += ["", "## 测试配置", "", "```json", json.dumps(results["configuration"], ensure_ascii=False, indent=2), "```",
              "", "每个模型依次运行，使用相同的 WebRTC 切段、Silero 过滤、中文语言设置、简体转换和解码参数。",
              "固定 temperature=0，不使用随机采样回退；窗口中的识别也使用同一套代码。",
              "", "模型下载、加载和预热耗时见 results.json 的 models；每段耗时及模型输出时间戳见 predictions。",
              "", "## 逐段文字与评分范围", ""]
    for model_name, preparation in results["models"].items():
        if preparation.get("error"):
            lines += [f"模型 {model_name} 未能完成加载：{preparation['error']}", ""]
    for prediction in results["predictions"]:
        sample, score = by_id[prediction["sample_id"]], prediction["scoring"]
        stem = f"{sample['id']}-{prediction['model']}"
        lines += [f"### {sample.get('label', sample['id'])} / {prediction['model']}", "",
                  f"[逐段文字]({stem}-timed.txt) · [纯文字]({stem}.txt)", ""]
        if score["status"] in ("draft", "verified"):
            lines += [f"评分范围：原录音 {score['start']:.2f}～{score['end']:.2f} 秒，参考文字为{('待核对草稿' if score['status'] == 'draft' else '已核对原文')}。",
                      f"替换 {score['substitutions']} 字，漏 {score['deletions']} 字，多出 {score['insertions']} 字；逐字差异见 results.json。", ""]
            if score.get("source_url"):
                lines += [f"[网上原文参考]({score['source_url']})", ""]
        else:
            lines += [score["reason"], ""]
        if prediction.get("error"):
            lines += [f"识别失败：{prediction['error']}", ""]
        lines += ["| 录音时间 | 本段识别耗时 | 文字 |", "| --- | ---: | --- |"]
        for segment in prediction["segments"]:
            text = segment["text"].replace("|", "／").replace("\n", " ") or "（未输出文字）"
            lines.append(f"| {segment['start']:.2f}～{segment['end']:.2f} | {segment['inference_seconds']:.2f} 秒 | {text} |")
        lines.append("")
    lines += ["## 怎样校对后重新评分", "", "1. 对照录音修改清单中指定的参考 TXT，只保留该评分范围实际读出的字。",
              "2. 确认无误后，把清单里对应的 status 从 draft 改成 verified。",
              "3. 用 --score-only 指定本次结果目录，并用 --manifest 指定修改后的清单。不会重新运行模型。", ""]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    with (folder / "summary.csv").open("w", encoding="utf-8-sig", newline="") as output:
        fields = ["sample", "model", "audio_seconds", "inference_seconds", "rtf", "reference_status", "cer", "substitutions", "deletions", "insertions", "reference_characters", "error"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(csv_rows)
    write_json(folder / "results.json", results)
    create_html_report(folder, results, samples)


def run(args) -> int:
    if args.score_only:
        folder = args.score_only.resolve()
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        manifest = (args.manifest or Path(results["manifest_path"])).resolve()
        samples = load_manifest(manifest)
        by_id = {sample["id"]: sample for sample in samples}
        for prediction in results["predictions"]:
            sample_id = prediction["sample_id"]
            if sample_id not in by_id:
                raise ValueError("新清单缺少原测试样本，无法重新评分")
            current_hash = hashlib.sha256(Path(by_id[sample_id]["audio"]).read_bytes()).hexdigest()
            if current_hash != results["audio"][sample_id]["sha256"]:
                raise ValueError(f"{sample_id} 录音已变化，请重新测试，不能沿用旧识别结果评分")
        history = folder / ("results-before-rescore-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        write_json(history, results)
        results["rescored_at"] = datetime.now().astimezone().isoformat()
        create_reports(folder, results, samples)
        print("重新评分完成：", folder / "report.html", flush=True)
        return 0
    manifest = (args.manifest or Path("benchmark.local.json")).resolve()
    samples = load_manifest(manifest)
    # Validate all recordings and scoring ranges before any model downloads.
    prepared = []
    for sample in samples:
        audio, metadata = load_audio(Path(sample["audio"]))
        start = time.perf_counter()
        segments = segment_audio(audio)
        segmentation_seconds = time.perf_counter() - start
        probe = {"audio_seconds": len(audio) / 16000, "segments": [{"start": seg.start_seconds, "end": seg.end_seconds, "text": ""} for seg in segments]}
        score_prediction(probe, sample)
        prepared.append((sample, audio, metadata, segments, segmentation_seconds))
        print(f"录音 {sample.get('label', sample['id'])}：{len(audio)/16000:.2f} 秒，{len(segments)} 个 VAD 片段", flush=True)
    folder = (args.output or Path("benchmark-results") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    results = {"schema_version": 1, "status": "running", "created_at": datetime.now().astimezone().isoformat(),
               "manifest_path": str(manifest), "configuration": {
                   "models": args.models, "language": "zh", "device": "cpu", "compute_type": "int8",
                   "cpu_threads": args.cpu_threads, **TRANSCRIBE_SETTINGS,
                   "python": platform.python_version(), "platform": platform.platform(),
                   "processor": platform.processor(), "logical_cpus": os.cpu_count(),
                   "faster_whisper": version("faster-whisper"), "ctranslate2": version("ctranslate2"),
               }, "audio": {}, "models": {}, "predictions": []}
    write_json(folder / "manifest-snapshot.json", {"version": 1, "samples": samples})
    for sample, _, metadata, segments, segmentation_seconds in prepared:
        results["audio"][sample["id"]] = {"path": sample["audio"], **metadata,
            "segmentation_seconds": segmentation_seconds,
            "vad_input_seconds": sum(len(seg.audio) / 16000 for seg in segments),
            "segment_boundaries": [[seg.start_seconds, seg.end_seconds] for seg in segments]}
    from faster_whisper.utils import download_model
    cache = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SubtitleApp" / "models"
    failures = False
    for model_name in args.models:
        print(f"准备模型 {model_name}（首次可能需要下载）", flush=True)
        try:
            start = time.perf_counter()
            snapshot = download_model(model_name, cache_dir=str(cache), local_files_only=args.local_files_only)
            download_seconds = time.perf_counter() - start
            start = time.perf_counter()
            recognizer = WhisperRecognizer(snapshot, args.cpu_threads, local_files_only=True)
            load_seconds = time.perf_counter() - start
            start = time.perf_counter()
            recognizer.recognize(prepared[0][1][:48000], "zh")
            results["models"][model_name] = {"snapshot": snapshot,
                "download_or_cache_seconds": download_seconds, "load_seconds": load_seconds,
                "warmup_seconds": time.perf_counter() - start}
        except Exception as exc:
            failures = True
            results["models"][model_name] = {"error": str(exc)}
            print(f"模型 {model_name} 加载失败：{exc}", flush=True)
            write_json(folder / "results.json", results)
            continue
        for sample, audio, _, segments, _ in prepared:
            prediction = {"sample_id": sample["id"], "model": model_name,
                          "audio_seconds": len(audio) / 16000, "segments": []}
            for index, segment in enumerate(segments, 1):
                start = time.perf_counter()
                try:
                    text = recognizer.recognize(segment.audio, "zh")
                except Exception as exc:
                    prediction["error"] = f"第 {index} 段失败：{exc}"
                    failures = True
                    break
                elapsed = time.perf_counter() - start
                prediction["segments"].append({"start": segment.start_seconds, "end": segment.end_seconds,
                    "inference_seconds": elapsed, **text})
                print(f"{model_name} / {sample['id']} [{index}/{len(segments)}] {segment.start_seconds:.2f}–{segment.end_seconds:.2f}s：{text['text'] or '（无文字）'}", flush=True)
            prediction["inference_seconds"] = sum(seg["inference_seconds"] for seg in prediction["segments"])
            prediction["rtf"] = prediction["inference_seconds"] / prediction["audio_seconds"]
            results["predictions"].append(prediction)
            create_reports(folder, results, samples)
        del recognizer
        gc.collect()
    results["status"] = "partial_failure" if failures else "complete"
    create_reports(folder, results, samples)
    print("对比完成：", folder / "report.html", flush=True)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="用同一份 WAV 对比 Whisper 模型；参考文字可在之后校对并重新评分")
    parser.add_argument("--manifest", type=Path, help="样本清单 JSON；默认 benchmark.local.json")
    parser.add_argument("--models", nargs="+", choices=("base", "small", "medium", "large-v3"), default=["small", "medium"])
    parser.add_argument("--output", type=Path, help="新建结果目录，必须尚不存在")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--local-files-only", action="store_true", help="只使用已经下载的模型")
    parser.add_argument("--score-only", type=Path, help="重新评分一个结果目录，不运行模型")
    args = parser.parse_args()
    if args.cpu_threads < 1:
        parser.error("--cpu-threads 必须大于零")
    if len(set(args.models)) != len(args.models):
        parser.error("--models 不可重复")
    try:
        return run(args)
    except (ValueError, OSError, KeyError, wave.Error) as exc:
        print(f"无法完成对比：{exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
