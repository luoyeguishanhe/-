"""Local media preview and non-destructive, per-cue subtitle correction."""

from pathlib import Path
import math

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSlider,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .subtitles import SubtitleCue, render_srt, write_export


def preview_cues(cues: list[SubtitleCue], offset: float) -> list[SubtitleCue]:
    """Use the same millisecond clipping/ordering as the original SRT export."""
    result = []
    previous_end = 0
    if not math.isfinite(offset):
        raise ValueError("字幕偏移必须是有效秒数")
    for cue in sorted(cues, key=lambda item: item.start):
        if not math.isfinite(cue.start) or not math.isfinite(cue.end):
            continue
        start = max(0, previous_end, round(cue.start * 1000) + round(offset * 1000))
        end = round(cue.end * 1000) + round(offset * 1000)
        text = "\n".join(line.strip() for line in cue.text.splitlines() if line.strip())
        if end > start and text:
            result.append(SubtitleCue(start / 1000, end / 1000, text))
            previous_end = end
    return result


def clock_text(milliseconds: int) -> str:
    seconds, millis = divmod(max(0, milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


class SubtitleEditor(QDialog):
    def __init__(self, source: Path, cues: list[SubtitleCue], offset: float = 0, parent=None):
        super().__init__(parent)
        self.source = source
        self.cues = preview_cues(cues, offset)
        self.saved_cues = list(self.cues)
        self.current_row = -1
        self.setWindowTitle(f"字幕校对 · {source.name}")
        self.resize(1100, 740)
        self.setMinimumSize(800, 600)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumSize(280, 180)
        self.player.setVideoOutput(self.video)
        self.caption = QLabel("选择一条字幕，跳转到对应时间")
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setMinimumHeight(64)
        self.caption.setStyleSheet("background: #182b41; color: white; padding: 12px; font-size: 18px;")
        self.play_button = QPushButton("播放")
        self.play_button.clicked.connect(self.toggle_playback)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.seek, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 12, 0)
        left_layout.addWidget(self.video, 1)
        left_layout.addWidget(self.caption)
        left_layout.addLayout(controls)
        left_layout.addWidget(self.time_label)

        self.table = QTableWidget(len(self.cues), 3)
        self.table.setHorizontalHeaderLabels(["开始（秒）", "结束（秒）", "字幕"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 95)
        self.table.setColumnWidth(1, 95)
        for row in range(len(self.cues)):
            self._update_row(row)
        self.table.currentCellChanged.connect(self.select_cue)
        self.table.cellClicked.connect(self.seek_to_cue)
        self.start_time = self._time_box()
        self.end_time = self._time_box()
        self.text_edit = QPlainTextEdit()
        self.text_edit.setMaximumHeight(110)
        timing = QHBoxLayout()
        timing.addWidget(QLabel("开始"))
        timing.addWidget(self.start_time)
        timing.addWidget(QLabel("结束"))
        timing.addWidget(self.end_time)
        self.use_start = QPushButton("当前播放位置设为开始")
        self.use_end = QPushButton("设为结束")
        self.use_start.clicked.connect(lambda: self.start_time.setValue(self.player.position() / 1000))
        self.use_end.clicked.connect(lambda: self.end_time.setValue(self.player.position() / 1000))
        stamp_row = QHBoxLayout()
        stamp_row.addWidget(self.use_start)
        stamp_row.addWidget(self.use_end)
        self.apply_button = QPushButton("应用本条修改")
        self.apply_button.clicked.connect(self.apply_current)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.addWidget(QLabel("点击字幕跳转；切换条目时自动应用当前修改。"))
        right_layout.addWidget(self.table, 1)
        right_layout.addLayout(timing)
        right_layout.addLayout(stamp_row)
        right_layout.addWidget(self.text_edit)
        right_layout.addWidget(self.apply_button)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([540, 500])

        self.status = QLabel("修改后另存为校对版 SRT。原始识别 TXT 和 SRT 保留。")
        self.status.setWordWrap(True)
        self.save_button = QPushButton("另存校对版 SRT…")
        self.save_button.clicked.connect(self.save)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.save_button)
        self.tools_button = QPushButton("翻译与封装…")
        self.tools_button.clicked.connect(self.open_tools)
        bottom.addWidget(self.tools_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        heading = QLabel(source.name)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(split, 1)
        layout.addLayout(bottom)

        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.playback_changed)
        self.player.errorOccurred.connect(self.media_error)
        self.player.setSource(QUrl.fromLocalFile(str(source.resolve())))
        for widget in (self.start_time, self.end_time, self.text_edit, self.apply_button,
                       self.use_start, self.use_end, self.save_button):
            widget.setEnabled(bool(self.cues))
        if self.cues:
            self.table.setCurrentCell(0, 0)

    @staticmethod
    def _time_box():
        box = QDoubleSpinBox()
        box.setRange(0, 2147483)
        box.setDecimals(3)
        box.setSingleStep(0.1)
        box.setSuffix(" 秒")
        return box

    def _update_row(self, row):
        cue = self.cues[row]
        for col, text in enumerate((f"{cue.start:.3f}", f"{cue.end:.3f}", cue.text)):
            self.table.setItem(row, col, QTableWidgetItem(text))

    def form_cue(self):
        return SubtitleCue(self.start_time.value(), self.end_time.value(),
                           self.text_edit.toPlainText().strip())

    def apply_current(self) -> bool:
        if self.current_row < 0:
            return True
        row, cue = self.current_row, self.form_cue()
        error = None
        if not cue.text:
            error = "字幕文字不能为空。"
        elif cue.end - cue.start < 0.0005:
            error = "结束时间必须晚于开始时间（至少 0.001 秒）。"
        elif row > 0 and cue.start < self.cues[row - 1].end:
            error = "开始时间不能早于上一条字幕的结束时间。"
        elif row + 1 < len(self.cues) and cue.end > self.cues[row + 1].start:
            error = "结束时间不能晚于下一条字幕的开始时间。"
        if error:
            self.status.setText(error)
            return False
        if cue != self.cues[row]:
            self.cues[row] = cue
            self._update_row(row)
            self.status.setText("修改已应用，尚未保存。点击“另存校对版 SRT”。")
        self.position_changed(self.player.position())
        return True

    def select_cue(self, row, column, previous_row, previous_column):
        if row < 0 or row == self.current_row:
            return
        if not self.apply_current():
            self.table.blockSignals(True)
            self.table.setCurrentCell(self.current_row, 0)
            self.table.blockSignals(False)
            return
        self.current_row = row
        cue = self.cues[row]
        self.start_time.setValue(cue.start)
        self.end_time.setValue(cue.end)
        self.text_edit.setPlainText(cue.text)
        self.player.setPosition(round(cue.start * 1000))
        self.position_changed(round(cue.start * 1000))

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_to_cue(self, row, column):
        if row == self.current_row and self.apply_current():
            self.player.setPosition(round(self.cues[row].start * 1000))

    def playback_changed(self, state):
        self.play_button.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放")

    def duration_changed(self, duration):
        self.seek.setRange(0, min(duration, 2147483647))
        self.position_changed(self.player.position())

    def position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.time_label.setText(f"{clock_text(position)} / {clock_text(self.player.duration())}")
        seconds = position / 1000
        self.caption.setText(next((cue.text for cue in self.cues
                                   if cue.start <= seconds < cue.end), ""))

    def media_error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.status.setText(f"无法预览视频：{message}。仍可修改和保存字幕；可尝试转换为 MP4（H.264/AAC）。")

    def save(self):
        if not self.apply_current():
            return
        suggested = self.source.with_name(self.source.stem + "-校对.srt")
        filename, _ = QFileDialog.getSaveFileName(
            self, "另存校对版 SRT", str(suggested), "SRT 字幕 (*.srt)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not filename:
            return
        path = Path(filename).with_suffix(".srt")
        # Protect the media and all existing recognition/correction outputs.
        if path.resolve() == self.source.resolve() or path.exists():
            self.status.setText("该文件已存在，请选择一个新文件名以保留原文件。")
            return
        try:
            write_export(path, render_srt(self.cues))
        except (OSError, ValueError) as exc:
            self.status.setText(f"保存失败：{exc}。修改仍保留，可重新保存。")
            return
        self.saved_cues = list(self.cues)
        self.status.setText(f"已保存：{path}")

    def open_tools(self):
        if not self.apply_current():
            return
        from .subtitle_tools import SubtitleToolsDialog
        self.player.pause()
        dialog = SubtitleToolsDialog(self.source, self.cues, self)
        dialog.exec()
        dialog.deleteLater()

    def reject(self):
        # QDialog routes Escape and the window close button through reject().
        dirty = self.cues != self.saved_cues or (
            self.current_row >= 0 and self.form_cue() != self.cues[self.current_row])
        if dirty and QMessageBox.question(
            self, "校对修改尚未保存", "是否丢弃未保存的修改并关闭？选择“否”可返回另存 SRT。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
        # Reopening the dialog restores the last saved state.
        self.cues = list(self.saved_cues)
        for row in range(len(self.cues)):
            self._update_row(row)
        row = self.current_row
        self.current_row = -1
        if row >= 0:
            self.select_cue(row, 0, -1, -1)
        super().reject()
