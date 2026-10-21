"""Build captions from audio timestamps and save UTF-8 TXT/SRT files."""

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str


def parse_srt(content: str) -> list[SubtitleCue]:
    """Read numbered UTF-8 SRT blocks, rejecting malformed/inverted timings."""
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise ValueError("SRT 文件为空")
    timestamp = r"(\d+):([0-5]\d):([0-5]\d)[,.](\d{3})"
    pattern = re.compile(rf"^{timestamp}\s*-->\s*{timestamp}\s*$")
    cues = []
    for block in re.split(r"\n[ \t]*\n", content):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError("SRT 格式错误：每条应包含编号、时间行和文字")
        match = pattern.fullmatch(lines[1].strip())
        if match is None:
            raise ValueError(f"第 {lines[0]} 条字幕的时间格式错误")
        values = [int(item) for item in match.groups()]
        start, end = [(values[i] * 3600 + values[i + 1] * 60 + values[i + 2])
                      + values[i + 3] / 1000 for i in (0, 4)]
        text = "\n".join(line.strip() for line in lines[2:]).strip()
        if end <= start or not text:
            raise ValueError(f"第 {lines[0]} 条字幕的结束时间或文字无效")
        cues.append(SubtitleCue(start, end, text))
    cues.sort(key=lambda cue: cue.start)
    if any(right.start < left.end for left, right in zip(cues, cues[1:])):
        raise ValueError("字幕时间存在重叠，请先在字幕编辑器中校对")
    return cues


def _clean(text: str) -> str:
    return " ".join(str(text).split())


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _wrap(text: str, width: int = 42) -> str:
    remaining, lines = _clean(text), []
    while remaining:
        used, count = 0, 0
        for char in remaining:
            used += 2 if unicodedata.east_asian_width(char) in "WF" else 1
            if used > width:
                break
            count += 1
        if count == len(remaining):
            lines.append(remaining)
            break
        count = max(1, count)
        space = remaining.rfind(" ", 0, count + 1)
        if space > 0:
            count = space
        lines.append(remaining[:count].strip())
        remaining = remaining[count:].strip()
    return "\n".join(lines)


def cues_from_parts(parts: list[dict], start_seconds: float,
                    duration_seconds: float) -> list[SubtitleCue]:
    """Whisper's restored (post-Silero) timestamps are relative to this input."""
    cues = []

    def append(start: float, end: float, text: str) -> None:
        if not math.isfinite(start) or not math.isfinite(end):
            return
        start, end = max(0.0, start), min(duration_seconds, end)
        if end > start and _clean(text):
            cues.append(SubtitleCue(start_seconds + start, start_seconds + end, _wrap(text)))

    for part in parts:
        words = part.get("words") or []
        # If alignment misses characters, preserve the full text and use the
        # model's segment times rather than inventing per-character positions.
        joined = "".join(word["text"] for word in words)
        if not words or re.sub(r"\s", "", joined) != re.sub(r"\s", "", part["text"]):
            append(part["start"], part["end"], part["text"])
            continue
        group = []

        def flush() -> None:
            if group:
                append(group[0]["start"], group[-1]["end"], "".join(item["text"] for item in group))
                group.clear()

        for word in words:
            if group:
                candidate = "".join(item["text"] for item in group) + word["text"]
                if (word["start"] - group[-1]["end"] > 0.65
                        or word["end"] - group[0]["start"] > 6
                        or _width(_clean(candidate)) > 84):
                    flush()
            group.append(word)
            if word["text"].rstrip().endswith(("。", "！", "？", ".", "!", "?", "；", ";")):
                flush()
        flush()
    return cues


def _timestamp(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def render_srt(cues: list[SubtitleCue], offset_seconds: float = 0) -> str:
    if not math.isfinite(offset_seconds):
        raise ValueError("字幕偏移必须是有效秒数")
    offset_ms = round(offset_seconds * 1000)
    blocks, previous_end = [], 0
    for cue in sorted(cues, key=lambda item: item.start):
        if not math.isfinite(cue.start) or not math.isfinite(cue.end):
            continue
        start = max(0, previous_end, round(cue.start * 1000) + offset_ms)
        end = round(cue.end * 1000) + offset_ms
        text = "\n".join(line.strip() for line in cue.text.splitlines() if line.strip())
        if end <= start or not text:
            continue
        blocks.append(f"{len(blocks) + 1}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}")
        previous_end = end
    return "\n\n".join(blocks) + ("\n\n" if blocks else "")


def render_txt(paragraphs: list[str]) -> str:
    lines = [_clean(text) for text in paragraphs if _clean(text)]
    return "\n".join(lines) + ("\n" if lines else "")


def write_export(path: Path, content: str) -> None:
    """Replace only a user-selected destination, using a temporary file."""
    if not content:
        raise ValueError("没有可保存的文字；请检查识别结果或字幕偏移")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".subtitle-", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_new_exports(audio_path: Path, txt: str, srt: str,
                     incomplete: bool = False) -> list[Path]:
    """Automatically save beside the recording without overwriting old exports."""
    contents = {".txt": txt, ".srt": srt}
    contents = {suffix: text for suffix, text in contents.items() if text}
    if not contents:
        return []
    stem = audio_path.stem + ("-partial" if incomplete else "")
    for number in range(1, 10000):
        basename = stem if number == 1 else f"{stem}-{number}"
        paths = []
        try:
            for suffix, content in contents.items():
                path = audio_path.with_name(basename + suffix)
                # Exclusive creation is intentional: a prior corrected subtitle
                # must survive subsequent recordings with the same filename.
                with path.open("x", encoding="utf-8", newline="\n") as output:
                    paths.append(path)
                    output.write(content)
            return paths
        except FileExistsError:
            for path in paths:
                path.unlink(missing_ok=True)
        except OSError:
            for path in paths:
                path.unlink(missing_ok=True)
            raise
    raise FileExistsError("同名导出文件过多，请更换录音文件名")
