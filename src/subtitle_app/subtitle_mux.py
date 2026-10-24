"""Copy media streams into a new MKV containing a selectable SRT track."""

import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event

import av

from .background_job import Cancelled
from .subtitles import SubtitleCue, render_srt


def publish_new_file(temporary: Path, destination: Path, cancel: Event) -> None:
    """Publish without replacing an existing path, including races with other apps."""
    if cancel.is_set():
        raise Cancelled()
    try:
        os.link(temporary, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        # FAT/network volumes may not support hard links.
        pass
    created = False
    try:
        with destination.open("xb") as output, temporary.open("rb") as source:
            created = True
            while block := source.read(1024 * 1024):
                if cancel.is_set():
                    raise Cancelled()
                output.write(block)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def mux_subtitles(source: Path, cues: list[SubtitleCue], destination: Path,
                  cancel: Event, emit=lambda kind, value: None, language="und") -> Path:
    from imageio_ffmpeg import get_ffmpeg_exe

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("请选择存在的本地视频")
    if destination.suffix.lower() != ".mkv":
        raise ValueError("当前字幕封装输出为 MKV，请使用 .mkv 文件名")
    if source == destination or destination.exists():
        raise FileExistsError("目标文件已存在，请使用新文件名，保留原视频")
    content = render_srt(cues)
    if not content:
        raise ValueError("没有有效字幕，无法封装")
    with av.open(str(source)) as container:
        if not container.streams.video:
            raise ValueError("所选文件没有视频轨道；请使用原视频封装")
    if cancel.is_set():
        raise Cancelled()
    # This directory is owned by this job and is removed on success/cancel/failure.
    with tempfile.TemporaryDirectory(prefix=".subtitle-mux-", dir=destination.parent) as folder:
        folder = Path(folder)
        subtitle, output = folder / "subtitle.srt", folder / "video.mkv"
        subtitle.write_text(content, encoding="utf-8")
        command = [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-n", "-copyts", "-start_at_zero", "-i", str(source),
                   "-sub_charenc", "UTF-8", "-i", str(subtitle),
                   "-map", "0:v", "-map", "0:a?", "-map", "1:0",
                   "-map_metadata", "0", "-map_chapters", "0", "-c:v", "copy",
                   "-c:a", "copy", "-c:s", "srt", "-disposition:s:0", "default",
                   "-metadata:s:s:0", f"language={language}",
                   "-metadata:s:s:0", "title=巴别塔", "-avoid_negative_ts", "disabled",
                   str(output)]
        emit("status", "正在复制视频、音轨并封装字幕…")
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            try:
                while True:
                    try:
                        _, stderr = process.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        if cancel.is_set():
                            process.kill()
                            process.communicate()
                            raise Cancelled()
                if cancel.is_set():
                    raise Cancelled()
                if process.returncode:
                    reason = stderr.decode("utf-8", errors="replace")[-2000:]
                    raise ValueError(f"封装失败，原视频未改变。该编码可能不支持直接复制到 MKV。\n{reason}")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        # Validate the output before making the completed file visible.
        with av.open(str(output)) as result:
            if not result.streams.video or not result.streams.subtitles:
                raise ValueError("封装结果缺少视频或字幕轨道")
        publish_new_file(output, destination, cancel)
    return destination
