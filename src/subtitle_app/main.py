"""Desktop entry point for recording the current Windows system audio."""

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
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .audio_capture import SystemAudioRecorder, list_output_devices


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("实时中文字幕 · 开发版")
        self.resize(640, 360)
        self.recorder = SystemAudioRecorder()
        self.output_path: Path | None = None

        title = QLabel("系统声音录制")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel("选择正在播放视频声音的设备，录制结束后保存为 16 kHz 单声道 WAV。")
        description.setWordWrap(True)

        self.devices = QComboBox()
        self.refresh_button = QPushButton("刷新设备")
        self.refresh_button.clicked.connect(self.refresh_devices)
        device_row = QHBoxLayout()
        device_row.addWidget(self.devices, 1)
        device_row.addWidget(self.refresh_button)

        self.start_button = QPushButton("开始录音")
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

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(device_row)
        layout.addLayout(button_row)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.status)
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
        self.devices.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("正在打开录音设备…")
        self.recorder.start(int(device_index), self.output_path)

    def stop_recording(self) -> None:
        self.recorder.stop()
        self.stop_button.setEnabled(False)
        self.status.setText("正在保存 WAV 文件…")

    def update_recording_state(self) -> None:
        self.level_bar.setValue(self.recorder.level)
        while (event := self.recorder.next_event()) is not None:
            kind, value = event
            if kind == "started":
                self.status.setText(f"正在录制：{value}")
            elif kind == "saved":
                duration = self.recorder.frames_written / self.recorder.sample_rate
                self.status.setText(f"已保存 {duration:.1f} 秒录音：{value}")
                self._recording_finished()
            elif kind == "error":
                self.status.setText(f"录音失败：{value}")
                QMessageBox.warning(self, "录音失败", str(value))
                self._recording_finished()
        if self.output_path is None and not self.recorder.is_active and self.devices.count():
            self.start_button.setEnabled(True)

    def _recording_finished(self) -> None:
        self.devices.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.output_path = None

    def closeEvent(self, event: QCloseEvent) -> None:
        self.recorder.stop()
        self.recorder.wait(3)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
