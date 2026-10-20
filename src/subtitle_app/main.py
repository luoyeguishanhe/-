"""Desktop entry point for system audio recording and live speech recognition."""

from datetime import datetime
from pathlib import Path
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .audio_capture import SystemAudioRecorder, list_output_devices
from .speech_pipeline import SpeechPipeline


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("实时中文字幕 · 开发版")
        self.resize(720, 520)
        self.recorder = SystemAudioRecorder()
        self.pipeline: SpeechPipeline | None = None
        self.output_path: Path | None = None
        self.pending_device_index: int | None = None
        self.recording_saved = False

        title = QLabel("系统声音识别")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel("选择播放视频声音的设备，程序会录制 WAV 并实时识别语音。")
        description.setWordWrap(True)

        self.devices = QComboBox()
        self.refresh_button = QPushButton("刷新设备")
        self.refresh_button.clicked.connect(self.refresh_devices)
        device_row = QHBoxLayout()
        device_row.addWidget(self.devices, 1)
        device_row.addWidget(self.refresh_button)

        self.model_combo = QComboBox()
        self.model_combo.addItem("标准 · small", "small")
        self.model_combo.addItem("速度优先 · base", "base")
        self.model_combo.addItem("较大模型 · medium（更慢）", "medium")
        self.language_combo = QComboBox()
        self.language_combo.addItem("自动识别语言", None)
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("英语", "en")
        self.language_combo.addItem("日语", "ja")
        self.language_combo.addItem("韩语", "ko")
        recognition_row = QHBoxLayout()
        recognition_row.addWidget(QLabel("识别模型"))
        recognition_row.addWidget(self.model_combo, 1)
        recognition_row.addWidget(QLabel("原声语言"))
        recognition_row.addWidget(self.language_combo, 1)

        self.start_button = QPushButton("开始录音与识别")
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button = QPushButton("停止并保存")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_recording)
        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)

        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setFormat("音量 %p%")
        self.status = QLabel("准备就绪。音量条跳动表示已采集到声音。")
        self.status.setWordWrap(True)
        self.asr_status = QLabel("语音识别尚未启动。首次运行需下载本地模型。")
        self.asr_status.setWordWrap(True)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("检测到语音后，识别文字会显示在这里。")

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(device_row)
        layout.addLayout(recognition_row)
        layout.addLayout(button_row)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.status)
        layout.addWidget(self.asr_status)
        layout.addWidget(self.transcript, 1)
        layout.addStretch()

        content = QWidget()
        content.setLayout(layout)
        self.setCentralWidget(content)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_recording_state)
        self.timer.start()
        self.refresh_devices()

    def refresh_devices(self) -> None:
        self.devices.clear()
        try:
            available = list_output_devices()
        except Exception as exc:
            self.status.setText(f"无法读取系统输出设备：{exc}")
            available = []

        default_row = 0
        for row, device in enumerate(available):
            label = f"{device.name}（默认）" if device.is_default else device.name
            self.devices.addItem(label, device.index)
            if device.is_default:
                default_row = row
        if available:
            self.devices.setCurrentIndex(default_row)
            self.status.setText("请选择视频实际使用的输出设备；开始录音后音量条应跳动。")
        else:
            self.status.setText("没有找到 WASAPI 系统输出设备，请检查 Windows 音频设备。")
        self.start_button.setEnabled(bool(available))

    def start_recording(self) -> None:
        device_index = self.devices.currentData()
        if device_index is None:
            return
        suggested = f"system-audio-{datetime.now():%Y%m%d-%H%M%S}.wav"
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存系统录音", suggested, "WAV 音频 (*.wav)"
        )
        if not filename:
            return
        self.output_path = Path(filename)
        if self.output_path.suffix.lower() != ".wav":
            self.output_path = self.output_path.with_suffix(".wav")
        self.pending_device_index = int(device_index)
        self.recording_saved = False
        self.transcript.clear()
        model_size = str(self.model_combo.currentData())
        self.pipeline = SpeechPipeline(model_size, self.language_combo.currentData())
        self.devices.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("取消")
        self.status.setText("正在准备语音识别模型，录音尚未开始…")
        self.asr_status.setText(
            f"正在加载 {model_size} 模型；首次使用需下载，完成后会自动开始录音。"
        )
        self.pipeline.prepare()

    def stop_recording(self) -> None:
        if self.recorder.is_active:
            self.recorder.stop()
            self.stop_button.setEnabled(False)
            self.status.setText("正在保存 WAV 文件…")
        else:
            if self.pipeline is not None:
                self.pipeline.cancel()
            self.status.setText("已取消，未开始录音。")
            self._recording_finished()

    def update_recording_state(self) -> None:
        self.level_bar.setValue(self.recorder.level)
        while (event := self.recorder.next_event()) is not None:
            kind, value = event
            if kind == "started":
                self.status.setText(f"正在录制：{value}")
            elif kind == "saved":
                duration = self.recorder.frames_written / self.recorder.sample_rate
                self.status.setText(f"已保存 {duration:.1f} 秒录音：{value}")
                self.recording_saved = True
                if self.pipeline is not None:
                    self.pipeline.finish_capture()
                    self.asr_status.setText("正在识别最后的语音片段…")
            elif kind == "error":
                self.status.setText(f"录音失败：{value}")
                QMessageBox.warning(self, "录音失败", str(value))
                if self.pipeline is not None:
                    self.pipeline.cancel()
                self._recording_finished()
            elif kind == "warning":
                self.status.setText(str(value))
        pipeline = self.pipeline
        if pipeline is not None:
            while (event := pipeline.next_event()) is not None:
                kind, value = event
                if kind == "ready" and self.output_path is not None:
                    try:
                        pipeline.begin_capture()
                        self.recorder.start(
                            int(self.pending_device_index),
                            self.output_path,
                            on_audio=pipeline.submit,
                        )
                        self.stop_button.setText("停止并保存")
                        self.status.setText("正在打开录音设备…")
                        self.asr_status.setText("模型已就绪，等待语音。")
                    except Exception as exc:
                        pipeline.cancel()
                        self.status.setText(f"无法开始录音：{exc}")
                        self._recording_finished()
                elif kind == "vad":
                    self.asr_status.setText("检测到语音…" if value else "等待语音／识别中…")
                elif kind == "segment":
                    start, end = value
                    self.asr_status.setText(f"正在识别 {start:.1f}～{end:.1f} 秒的语音…")
                elif kind == "text":
                    segment, transcript, language = value
                    self.transcript.appendPlainText(
                        f"[{segment.start_seconds:.1f}～{segment.end_seconds:.1f} 秒] "
                        f"{transcript}"
                    )
                    self.asr_status.setText(f"识别到 {language} 语音。")
                elif kind == "complete":
                    self.asr_status.setText("语音识别完成。")
                    self._recording_finished()
                    break
                elif kind == "warning":
                    self.asr_status.setText(str(value))
                elif kind == "fatal":
                    self.asr_status.setText(str(value))
                    if self.recorder.is_active:
                        self.recorder.stop()
                    pipeline.cancel()
                    QMessageBox.warning(self, "语音识别不可用", str(value))
                    self._recording_finished()
                    break
        if self.output_path is None and not self.recorder.is_active and self.devices.count():
            self.start_button.setEnabled(True)

    def _recording_finished(self) -> None:
        self.devices.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.stop_button.setText("停止并保存")
        self.output_path = None
        self.pending_device_index = None
        self.pipeline = None

    def closeEvent(self, event: QCloseEvent) -> None:
        self.recorder.stop()
        self.recorder.wait(3)
        if self.pipeline is not None:
            self.pipeline.cancel()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
