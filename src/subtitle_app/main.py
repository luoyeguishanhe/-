"""Desktop entry point for system audio recording and live speech recognition."""

from datetime import datetime
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from PyQt6.QtCore import QSettings, QStandardPaths, QTimer, QUrl, Qt
from PyQt6.QtGui import QCloseEvent, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .audio_capture import SystemAudioRecorder, list_output_devices
from .live_overlay import LiveSubtitleOverlay, LiveTranslationSettingsDialog
from .live_subtitles import LiveSubtitleTranslator
from .speech_pipeline import SpeechPipeline
from .media_pipeline import MediaTranscriber
from .runtime import default_capture_cache_directory, fallback_capture_cache_directory
from .subtitle_editor import SubtitleEditor
from .subtitle_tools import SubtitleToolsDialog
from .subtitles import SubtitleCue, render_srt, render_txt, save_new_exports, write_export


MAIN_STYLE = """
QMainWindow, QMainWindow > QWidget { background: #edf3f8; color: #21334d; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #21334d; }
QLabel#appTitle { font-size: 27px; font-weight: 700; color: #183457; }
QLabel#appDescription { color: #62758c; font-size: 12px; }
QFrame#sourcePanel, QFrame#resultPanel {
    background: #ffffff; border: 1px solid #d9e4ee; border-radius: 12px;
}
QFrame#statusPanel {
    background: #e3edf6; border: 1px solid #d2e2ef; border-radius: 9px;
}
QLabel#sectionTitle { font-size: 17px; font-weight: 700; color: #203b5d; }
QLabel#fieldLabel { font-weight: 600; color: #405673; }
QLabel#hint { color: #70839b; font-size: 11px; }
QLabel#exportStatus { color: #60738c; font-size: 11px; }
QLabel#statusText { font-weight: 600; }
QScrollArea { background: transparent; border: none; }
QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {
    background: #ffffff; border: 1px solid #cbd9e7; border-radius: 7px;
    padding: 7px 9px; selection-background-color: #3168c9;
}
QComboBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus {
    border: 2px solid #3168c9;
}
QComboBox, QDoubleSpinBox { min-height: 22px; }
QPlainTextEdit#transcript {
    background: #f8fbfe; border-color: #e0e9f2; font-size: 14px;
    line-height: 145%; padding: 15px;
}
QPushButton {
    background: #f4f7fb; color: #24466d; border: 1px solid #ccdaea;
    border-radius: 7px; padding: 8px 14px; min-height: 23px; font-weight: 600;
}
QPushButton:hover { background: #e6eff9; border-color: #8eb0d9; }
QPushButton:focus { border: 2px solid #3168c9; }
QPushButton:disabled { color: #9aaabe; background: #f4f7fa; border-color: #e3eaf1; }
QPushButton#primaryButton {
    background: #3168c9; color: #ffffff; border-color: #3168c9;
    padding-top: 12px; padding-bottom: 12px;
}
QPushButton#primaryButton:hover { background: #2457af; }
QPushButton#primaryButton:disabled { background: #aabedb; border-color: #aabedb; color: #eef4fb; }
QToolButton#advancedToggle {
    border: none; background: transparent; color: #3168c9;
    font-weight: 600; padding: 7px 0; text-align: left;
}
QToolButton#advancedToggle:hover { color: #1e4e9a; }
QProgressBar {
    background: #dce8f3; border: none; border-radius: 5px;
    min-height: 13px; text-align: center; color: #203b5d;
}
QProgressBar::chunk { background: #4b8ad6; border-radius: 5px; }
QSplitter::handle { background: transparent; width: 8px; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("巴别塔")
        icon_root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "packaging"
        self.setWindowIcon(QIcon(str(icon_root / "app-icon.ico")))
        self.resize(1100, 730)
        self.setMinimumSize(860, 590)
        self.recorder = SystemAudioRecorder()
        self.pipeline: SpeechPipeline | None = None
        self.file_job: MediaTranscriber | None = None
        self.output_path: Path | None = None
        self.pending_device_index: int | None = None
        self.recording_saved = False
        self.session_audio_path: Path | None = None
        self._capture_cache: TemporaryDirectory | None = None
        self._temporary_recording = False
        self.paragraphs: list[str] = []
        self.subtitle_cues: list[SubtitleCue] = []
        self._exports_saved = False
        self._session_has_warnings = False
        self._capture_started = False
        self._close_requested = False
        self._editor = None
        self._editor_key = None
        self.live_config = None
        self.live_translator: LiveSubtitleTranslator | None = None
        self.live_overlay: LiveSubtitleOverlay | None = None
        self._live_mode = False
        self._live_prompt_tokens = 0
        self._live_completion_tokens = 0
        self._live_unreported_calls = 0
        self._backend_text = ""
        self._asr_performance = ""
        self._translation_performance = ""

        self.setStyleSheet(MAIN_STYLE)
        title = QLabel("巴别塔")
        title.setObjectName("appTitle")
        description = QLabel("跨越语言隔阂，让每一句都能被听懂")
        description.setObjectName("appDescription")

        self.devices = QComboBox()
        self.devices.setMinimumWidth(0)
        self.devices.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.refresh_button = QPushButton("刷新")
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
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("更多设置")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setObjectName("advancedToggle")

        self.start_button = QPushButton("开始识别电脑声音")
        self.start_button.clicked.connect(lambda: self.start_recording())
        self.live_button = QPushButton("开始直播翻译字幕")
        self.live_button.setObjectName("primaryButton")
        self.live_button.clicked.connect(self.start_live_subtitles)
        self.live_settings_button = QPushButton("翻译服务设置…")
        self.live_settings_button.clicked.connect(self.configure_live_translation)
        self.show_overlay_button = QPushButton("显示字幕框")
        self.show_overlay_button.clicked.connect(self.show_live_overlay)
        self.show_overlay_button.setEnabled(False)
        self.stop_button = QPushButton("结束识别")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_recording)
        self.video_button = QPushButton("选择视频并生成字幕")
        self.video_button.clicked.connect(self.start_video)
        self.video_button.setObjectName("primaryButton")
        self.keep_recording = QCheckBox("保留录音 WAV 并自动保存字幕")
        self.keep_recording.setToolTip("勾选后在开始时选择录音保存位置；未勾选时使用临时缓存。")
        self.settings = QSettings("SubtitleApp", "SubtitleApp")
        self.cache_path = QLineEdit()
        self.cache_path.setReadOnly(True)
        self.cache_path.setText(str(self.settings.value(
            "capture_cache_dir", str(default_capture_cache_directory()))))
        self.cache_button = QPushButton("更改…")
        self.cache_button.clicked.connect(self.choose_cache_directory)
        self.tools_button = QPushButton("翻译与封装…")
        self.tools_button.clicked.connect(self.open_subtitle_tools)

        self.save_txt_button = QPushButton("保存 TXT…")
        self.save_txt_button.clicked.connect(lambda: self.export_text("txt"))
        self.export_srt_button = QPushButton("导出 SRT…")
        self.export_srt_button.clicked.connect(lambda: self.export_text("srt"))
        self.save_txt_button.setEnabled(False)
        self.export_srt_button.setEnabled(False)
        self.edit_button = QPushButton("预览与校对…")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self.open_editor)
        self.subtitle_offset = QDoubleSpinBox()
        self.subtitle_offset.setRange(-86400, 86400)
        self.subtitle_offset.setDecimals(3)
        self.subtitle_offset.setSingleStep(0.1)
        self.subtitle_offset.setToolTip("0 表示与当前源文件同步；正数让字幕延后，负数让字幕提前。调整后重新导出 SRT。")
        self.subtitle_offset.valueChanged.connect(self._offset_changed)
        self.export_status = QLabel("本地视频会自动保存字幕；电脑声音识别可在结束后手动导出。")
        self.export_status.setWordWrap(True)
        self.export_status.setObjectName("exportStatus")

        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setFormat("音量 %p%")
        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 1000)
        self.file_progress.setValue(0)
        self.file_progress.setVisible(False)
        self.status = QLabel("准备就绪")
        self.status.setWordWrap(True)
        self.status.setObjectName("statusText")
        self.asr_status = QLabel("选择视频开始；录制时音量条会随声音变化。")
        self.asr_status.setWordWrap(True)
        self.live_usage = QLabel("")
        self.live_usage.setWordWrap(True)
        self.performance_status = QLabel("")
        self.performance_status.setWordWrap(True)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("字幕会显示在这里。\n\n选择本地视频可直接生成字幕；录制电脑声音适合网页视频或直播。")
        self.transcript.setObjectName("transcript")

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)
        header_layout.addWidget(title)
        header_layout.addWidget(description)

        source_panel = QFrame()
        source_panel.setObjectName("sourcePanel")
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(22, 22, 22, 22)
        source_layout.setSpacing(7)
        source_layout.addWidget(self._section_title("从本地视频开始"))
        source_layout.addWidget(self._hint("直接读取视频音轨，完成后自动保存字幕。"))
        source_layout.addWidget(self.video_button)
        source_layout.addSpacing(10)
        source_layout.addWidget(self._section_title("录制电脑声音"))
        source_layout.addWidget(self._hint("播放网页视频或直播时，选中它正在使用的输出设备。"))
        source_layout.addWidget(self._field_label("声音输出设备"))
        source_layout.addLayout(device_row)
        source_layout.addWidget(self._field_label("原声语言"))
        source_layout.addWidget(self.language_combo)
        source_layout.addWidget(self.start_button)
        source_layout.addWidget(self.live_button)
        live_row = QHBoxLayout()
        live_row.addWidget(self.live_settings_button)
        live_row.addWidget(self.show_overlay_button)
        source_layout.addLayout(live_row)
        source_layout.addWidget(self.stop_button)
        source_layout.addWidget(self.keep_recording)
        source_layout.addWidget(self._hint("临时录音会自动删除；字幕可另存。"))
        source_layout.addWidget(self.level_bar)
        source_layout.addSpacing(8)

        advanced = QWidget()
        advanced.setObjectName("advancedPanel")
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 4, 0, 0)
        advanced_layout.setSpacing(8)
        advanced_layout.addWidget(self._field_label("识别模型"))
        advanced_layout.addWidget(self.model_combo)
        advanced_layout.addWidget(self._hint("默认 small；较大模型需要更多时间。"))
        advanced_layout.addWidget(self._field_label("字幕时间偏移（秒）"))
        advanced_layout.addWidget(self.subtitle_offset)
        advanced_layout.addWidget(self._hint("正数延后，负数提前；调整后重新导出字幕。"))
        advanced_layout.addWidget(self._field_label("录音缓存目录"))
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.cache_path, 1)
        cache_row.addWidget(self.cache_button)
        advanced_layout.addLayout(cache_row)
        advanced_layout.addWidget(self._hint("每次录音使用独立文件夹；不会清理目录中的其他文件。"))
        advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(advanced.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda checked: self.advanced_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        source_layout.addWidget(self.advanced_toggle)
        source_layout.addWidget(advanced)
        source_layout.addStretch()

        source_scroll = QScrollArea()
        source_scroll.setWidgetResizable(True)
        source_scroll.setFrameShape(QFrame.Shape.NoFrame)
        source_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        source_scroll.setWidget(source_panel)
        source_scroll.setMinimumWidth(310)

        result_panel = QFrame()
        result_panel.setObjectName("resultPanel")
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(26, 20, 26, 22)
        result_layout.setSpacing(12)
        result_header = QHBoxLayout()
        result_header.addWidget(self._section_title("识别结果"))
        result_header.addStretch()
        result_header.addWidget(self.edit_button)
        result_layout.addLayout(result_header)
        result_layout.addWidget(self.transcript, 1)
        export_row = QHBoxLayout()
        export_row.addWidget(self.export_srt_button)
        export_row.addWidget(self.save_txt_button)
        export_row.addStretch()
        result_layout.addLayout(export_row)
        result_layout.addWidget(self.export_status)
        result_layout.addSpacing(6)
        result_layout.addWidget(self.tools_button)
        result_layout.addWidget(self._hint("也可导入已有 SRT，再翻译或封装到视频中。"))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(source_scroll)
        splitter.addWidget(result_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 690])

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(16, 10, 16, 10)
        status_layout.setSpacing(4)
        status_layout.addWidget(self.file_progress)
        status_layout.addWidget(self.status)
        status_layout.addWidget(self.asr_status)
        status_layout.addWidget(self.live_usage)
        status_layout.addWidget(self.performance_status)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(16)
        layout.addWidget(header)
        layout.addWidget(splitter, 1)
        layout.addWidget(status_panel)
        self.setCentralWidget(content)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_recording_state)
        self.timer.start()
        self.refresh_devices()

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    def choose_cache_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择录音缓存目录", self.cache_path.text())
        if chosen:
            path = Path(chosen).resolve()
            self.cache_path.setText(str(path))
            self.settings.setValue("capture_cache_dir", str(path))

    def _cleanup_capture_cache(self) -> None:
        if self._capture_cache is None:
            return
        if self._editor is not None:
            self._editor.player.stop()
            self._editor.player.setSource(QUrl())
        self._capture_cache.cleanup()
        self._capture_cache = None
        self._temporary_recording = False

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
        self.start_button.setEnabled(bool(available) and self.pipeline is None and self.file_job is None)
        self.live_button.setEnabled(bool(available) and self.pipeline is None and self.file_job is None)

    def configure_live_translation(self) -> bool:
        dialog = LiveTranslationSettingsDialog(self.live_config, self)
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        if accepted:
            self.live_config = dialog.config
        dialog.deleteLater()
        return accepted

    def start_live_subtitles(self) -> None:
        if self.pipeline is not None or self.file_job is not None or self.recorder.is_active:
            return
        if self.live_config is None and not self.configure_live_translation():
            return
        self.start_recording(live=True)

    def show_live_overlay(self) -> None:
        if self.live_overlay is not None:
            self.live_overlay.show_overlay()

    def _reset_results(self, source: Path) -> bool:
        if self.live_translator is not None:
            self.live_translator.cancel()
            self.live_translator = None
        if self.live_overlay is not None:
            self.live_overlay.hide()
        self._live_mode = False
        self.live_usage.clear()
        self.performance_status.clear()
        self._backend_text = ""
        self._asr_performance = ""
        self._translation_performance = ""
        self.tools_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        if self._editor is not None:
            self._editor.player.stop()
            self._editor.player.setSource(QUrl())
            self._editor.deleteLater()
        self._editor = None
        self._editor_key = None
        try:
            self._cleanup_capture_cache()
        except OSError as exc:
            QMessageBox.warning(self, "缓存清理失败", f"无法删除上次录音的缓存：{exc}")
            return False
        self.session_audio_path = source
        self.recording_saved = False
        self.paragraphs.clear()
        self.subtitle_cues.clear()
        self._exports_saved = False
        self._session_has_warnings = False
        self._capture_started = False
        self.save_txt_button.setEnabled(False)
        self.export_srt_button.setEnabled(False)
        self.transcript.clear()
        return True

    def start_video(self) -> None:
        if self.pipeline is not None or self.file_job is not None or self.recorder.is_active:
            return
        if not self._confirm_unsaved("处理新视频并清空这些文字"):
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择本地视频", "",
            "视频文件 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mpeg *.mpg);;所有文件 (*)",
        )
        if not filename:
            return
        source = Path(filename)
        if not self._reset_results(source):
            return
        self.subtitle_offset.setValue(0)
        self.output_path = None
        self.pending_device_index = None
        self.devices.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.keep_recording.setEnabled(False)
        self.cache_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.live_button.setEnabled(False)
        self.video_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("取消处理")
        self.level_bar.setVisible(False)
        self.file_progress.setVisible(True)
        self.file_progress.setRange(0, 0)
        self.file_progress.setFormat("正在准备…")
        self.export_status.setText("识别完成后自动在视频旁保存 TXT 和 SRT；字幕默认按视频音轨同步。")
        self.status.setText(f"正在读取本地视频：{source}")
        self.asr_status.setText("正在检查音轨…")
        self.file_job = MediaTranscriber(source, str(self.model_combo.currentData()), self.language_combo.currentData())
        self.file_job.start()

    def start_recording(self, *, live: bool = False) -> None:
        if self.pipeline is not None or self.file_job is not None or self.recorder.is_active:
            return
        device_index = self.devices.currentData()
        if device_index is None:
            return
        if not self._confirm_unsaved("开始新的录音并清空这些文字"):
            return
        suggested = f"system-audio-{datetime.now():%Y%m%d-%H%M%S}.wav"
        temporary = live or not self.keep_recording.isChecked()
        capture_cache = None
        if temporary:
            root = Path(self.cache_path.text()).expanduser()
            try:
                root.mkdir(parents=True, exist_ok=True)
                capture_cache = TemporaryDirectory(prefix="session-", dir=root)
            except OSError as exc:
                if root != default_capture_cache_directory():
                    QMessageBox.warning(self, "缓存不可用", f"无法在所选目录创建录音缓存：{exc}")
                    return
                root = fallback_capture_cache_directory()
                try:
                    root.mkdir(parents=True, exist_ok=True)
                    capture_cache = TemporaryDirectory(prefix="session-", dir=root)
                except OSError as fallback_exc:
                    QMessageBox.warning(self, "缓存不可用", f"无法创建录音缓存：{fallback_exc}")
                    return
                self.cache_path.setText(str(root))
                self.status.setText(f"程序目录不可写，录音缓存改用：{root}")
            output_path = Path(capture_cache.name) / suggested
        else:
            filename, _ = QFileDialog.getSaveFileName(
                self, "保存系统录音", suggested, "WAV 音频 (*.wav)"
            )
            if not filename:
                return
            output_path = Path(filename)
            if output_path.suffix.lower() != ".wav":
                output_path = output_path.with_suffix(".wav")
        if not self._reset_results(output_path):
            if capture_cache is not None:
                capture_cache.cleanup()
            return
        self._capture_cache = capture_cache
        self._temporary_recording = temporary
        self.output_path = output_path
        self.pending_device_index = int(device_index)
        self.level_bar.setVisible(True)
        self.file_progress.setVisible(False)
        self.export_status.setText(
            "识别后可手动导出 TXT 或 SRT；临时录音供本次校对使用。"
            if temporary else "识别完成后将自动保存 TXT 和 SRT；也可以另存到其他位置。")
        self.transcript.clear()
        model_size = str(self.model_combo.currentData())
        self.pipeline = SpeechPipeline(model_size, self.language_combo.currentData(), low_latency=live)
        self._live_mode = live
        if live:
            self._live_prompt_tokens = 0
            self._live_completion_tokens = 0
            self._live_unreported_calls = 0
            self.live_usage.setText("直播翻译用量：等待接口返回")
            if self.live_translator is not None:
                self.live_translator.cancel()
            self.live_translator = LiveSubtitleTranslator(self.live_config)
            self.live_translator.start()
            if self.live_overlay is None:
                self.live_overlay = LiveSubtitleOverlay()
            self.live_overlay.reset()
            self.live_overlay.show_overlay()
            self.show_overlay_button.setEnabled(True)
        self.devices.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.keep_recording.setEnabled(False)
        self.cache_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.live_button.setEnabled(False)
        self.video_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("取消")
        self.status.setText("正在准备语音识别模型，录音尚未开始…")
        self.asr_status.setText(
            f"正在加载 {model_size} 模型；首次使用需下载，完成后会自动开始录音。"
        )
        self.pipeline.prepare()

    def stop_recording(self) -> None:
        if self.file_job is not None:
            self.file_job.cancel()
            self.stop_button.setEnabled(False)
            self.status.setText("正在取消；当前片段处理结束后保存已有结果。")
            return
        if self.recorder.is_active:
            self.recorder.stop()
            self.stop_button.setEnabled(False)
            self.status.setText("正在结束录音…" if self._temporary_recording else "正在保存 WAV 文件…")
        else:
            if self._capture_started:
                self.stop_button.setEnabled(False)
                self.asr_status.setText("正在等待最后的识别结果…")
                return
            if self.pipeline is not None:
                self.pipeline.cancel()
            self.status.setText("已取消，未开始录音。")
            self._recording_finished()

    def update_recording_state(self) -> None:
        self._poll_live_translation()
        self.level_bar.setValue(self.recorder.level)
        while (event := self.recorder.next_event()) is not None:
            kind, value = event
            if kind == "started":
                self.status.setText(f"正在录制：{value}")
            elif kind == "saved":
                duration = self.recorder.frames_written / self.recorder.sample_rate
                self.status.setText(
                    f"已缓存 {duration:.1f} 秒录音，正在完成识别…"
                    if self._temporary_recording else f"已保存 {duration:.1f} 秒录音：{value}")
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
                self._session_has_warnings = True
                self.status.setText(str(value))
        job = self.file_job
        if job is not None:
            while (event := job.next_event()) is not None:
                kind, value = event
                if kind == "media_info":
                    if value.duration_seconds is not None:
                        self.file_progress.setRange(0, 1000)
                        self.file_progress.setValue(0)
                        self.file_progress.setFormat(f"共 {value.duration_seconds:.1f} 秒，正在准备模型…")
                    self.asr_status.setText("已找到音轨，正在准备模型。")
                elif kind == "loading":
                    self.asr_status.setText(f"正在加载 {value}；首次使用可能需要下载。")
                elif kind == "progress":
                    position, duration = value
                    if duration is not None:
                        self.file_progress.setRange(0, 1000)
                        self.file_progress.setValue(min(999, round(position / duration * 1000)))
                        self.file_progress.setFormat(f"已处理 {position:.1f} / {duration:.1f} 秒（%p%）")
                    else:
                        self.file_progress.setFormat(f"已处理 {position:.1f} 秒（总时长未知）")
                elif kind == "segment":
                    start, end = value
                    self.asr_status.setText(f"正在识别视频 {start:.1f}～{end:.1f} 秒…")
                elif kind == "text":
                    self._receive_text(value)
                elif kind == "subtitles":
                    self.subtitle_cues.extend(value)
                elif kind == "warning":
                    self._session_has_warnings = True
                    self.asr_status.setText(str(value))
                elif kind in ("complete", "cancelled", "fatal"):
                    self.file_progress.setRange(0, 1000)
                    if kind == "complete":
                        self.file_progress.setValue(1000)
                        self.file_progress.setFormat("处理完成（100%）")
                        self.status.setText(f"本地视频处理完成：{self.session_audio_path}")
                        self.asr_status.setText("语音识别完成。")
                    elif kind == "cancelled":
                        self.file_progress.setFormat("已取消；已有结果标为 partial")
                        self.status.setText("已取消本地视频处理。")
                        self.asr_status.setText("已有识别结果会保留，可另存。")
                    else:
                        self.file_progress.setFormat("处理失败；已有结果标为 partial")
                        self.status.setText(str(value))
                        QMessageBox.warning(self, "本地视频处理失败", str(value))
                    self._recording_finished(completed=kind == "complete")
                    break
        pipeline = self.pipeline
        if pipeline is not None:
            while (event := pipeline.next_event()) is not None:
                kind, value = event
                if kind == "backend":
                    fallback = value.get("fallback_reason", "")
                    self._backend_text = ("GPU / " if value["device"] == "cuda" else "CPU / ") + value["compute_type"]
                    if fallback:
                        self._backend_text += "（GPU 不可用，已回退）"
                    self.performance_status.setToolTip(fallback)
                    self._update_performance_status()
                elif kind == "performance":
                    self._asr_performance = (
                        f"识别 {value['asr_seconds']:.2f}s / 音频 {value['audio_seconds']:.1f}s，"
                        f"排队 {value['queue_seconds']:.1f}s，落后 {value['lag_seconds']:.1f}s，"
                        f"跳过 {value['dropped_segments']} 段")
                    self._update_performance_status()
                elif kind == "ready" and self.output_path is not None:
                    try:
                        pipeline.begin_capture()
                        self.recorder.start(
                            int(self.pending_device_index),
                            self.output_path,
                            on_audio=pipeline.submit,
                        )
                        self._capture_started = True
                        self.stop_button.setText("结束识别" if self._temporary_recording else "停止并保存")
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
                    self._receive_text(value)
                elif kind == "subtitles":
                    self.subtitle_cues.extend(value)
                elif kind == "complete":
                    self.asr_status.setText("语音识别完成。")
                    self._recording_finished(completed=True)
                    break
                elif kind == "warning":
                    self._session_has_warnings = True
                    self.asr_status.setText(str(value))
                elif kind == "fatal":
                    self.asr_status.setText(str(value))
                    if self.recorder.is_active:
                        self.recorder.stop()
                    pipeline.cancel()
                    QMessageBox.warning(self, "语音识别不可用", str(value))
                    self._recording_finished()
                    break
        if self.pipeline is None and self.file_job is None and not self.recorder.is_active and self.devices.count() and not self._close_requested:
            self.start_button.setEnabled(True)
            self.live_button.setEnabled(True)

    def _poll_live_translation(self) -> None:
        worker = self.live_translator
        if worker is None:
            return
        while (event := worker.next_event()) is not None:
            kind, value = event
            if kind == "queued" and self.live_overlay is not None:
                _, original = value
                self.live_overlay.show_pending(original)
            elif kind == "translated" and self.live_overlay is not None:
                _, original, translated = value
                self.live_overlay.show_translation(original, translated)
            elif kind == "warning":
                self._session_has_warnings = True
                self.asr_status.setText(str(value))
            elif kind == "token_usage":
                self._live_prompt_tokens += value.prompt_tokens
                self._live_completion_tokens += value.completion_tokens
                self._live_unreported_calls += value.unreported_calls
                self.live_usage.setText(
                    f"直播翻译用量：输入 {self._live_prompt_tokens} token，"
                    f"输出 {self._live_completion_tokens} token"
                    + (f"；{self._live_unreported_calls} 次调用未返回用量" if self._live_unreported_calls else ""))
            elif kind == "performance":
                self._translation_performance = (
                    f"翻译 {value['translation_seconds']:.2f}s，排队 {value['queue_seconds']:.1f}s，"
                    f"跳过 {value['dropped_translations']} 条")
                self._update_performance_status()
            elif kind == "error":
                self.asr_status.setText(f"直播翻译失败：{value}")
                if self.live_overlay is not None:
                    self.live_overlay.show_error(f"翻译失败：{value}")
            elif kind == "complete":
                self.live_translator = None

    def _update_performance_status(self) -> None:
        self.performance_status.setText(" | ".join(filter(None, (
            self._backend_text, self._asr_performance, self._translation_performance))))

    def _receive_text(self, value) -> None:
        segment, transcript, language = value
        self.paragraphs.append(transcript)
        self.transcript.appendPlainText(
            f"[{segment.start_seconds:.1f}～{segment.end_seconds:.1f} 秒] {transcript}"
        )
        self.asr_status.setText(f"识别到 {language} 语音。")
        if self._live_mode and self.file_job is None and self.live_translator is not None:
            self.live_translator.submit(SubtitleCue(segment.start_seconds, segment.end_seconds, transcript))

    def _recording_finished(self, completed: bool = False) -> None:
        if self.live_translator is not None:
            if completed:
                self.live_translator.finish()
            else:
                self.live_translator.cancel()
        self._live_mode = False
        self.tools_button.setEnabled(True)
        self.devices.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.keep_recording.setEnabled(True)
        self.cache_button.setEnabled(True)
        self.start_button.setEnabled(False)
        self.live_button.setEnabled(False)
        self.video_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.stop_button.setText("结束识别")
        self.output_path = None
        self.pending_device_index = None
        self.pipeline = None
        self.file_job = None
        self._capture_started = False
        if self._temporary_recording and self.paragraphs:
            self.export_status.setText(
                "识别结果仅保留在本次窗口中。需要字幕请点击“导出 SRT”或“保存 TXT”；"
                "临时录音在关闭程序或开始下次任务时删除。")
        elif self.paragraphs and self.session_audio_path is not None and not self._exports_saved:
            try:
                paths = save_new_exports(
                    self.session_audio_path, render_txt(self.paragraphs),
                    render_srt(self.subtitle_cues, self.subtitle_offset.value()),
                    incomplete=not completed or self._session_has_warnings,
                )
                self._exports_saved = True
                note = "（有未完成或跳过的片段，文件名含 partial）" if not completed or self._session_has_warnings else ""
                self.export_status.setText("已自动保存" + note + "：\n" + "\n".join(str(path) for path in paths))
                if not any(path.suffix == ".srt" for path in paths):
                    self.export_status.setText(self.export_status.text() + "\n没有有效字幕时间或偏移后没有可显示的字幕，可调整偏移后另行导出。")
            except (OSError, ValueError) as exc:
                self.export_status.setText(f"自动保存失败：{exc}。识别文字仍保留在窗口，可点击保存按钮重试。")
        elif not self.paragraphs:
            self.export_status.setText("没有识别出文字，未生成 TXT 或 SRT。")
        self.save_txt_button.setEnabled(bool(self.paragraphs))
        self.export_srt_button.setEnabled(bool(self.subtitle_cues))
        self.edit_button.setEnabled(bool(self.subtitle_cues) and self.session_audio_path is not None)
        if self._close_requested:
            QTimer.singleShot(100, self.close)

    def _offset_changed(self) -> None:
        if self.subtitle_cues:
            self.export_status.setText("字幕偏移已修改；重新点击“导出 SRT”后生效。已保存文件不会自动改写。")

    def open_editor(self) -> None:
        if self.pipeline is not None or self.file_job is not None or self.recorder.is_active:
            return
        if self.session_audio_path is None or not self.subtitle_cues:
            return
        key = (self.session_audio_path, tuple(self.subtitle_cues), self.subtitle_offset.value())
        if key != self._editor_key:
            if self._editor is not None:
                self._editor.deleteLater()
            self._editor = SubtitleEditor(self.session_audio_path, self.subtitle_cues,
                                          self.subtitle_offset.value(), self)
            self._editor_key = key
        self._editor.exec()

    def open_subtitle_tools(self) -> None:
        if self.pipeline is not None or self.file_job is not None or self.recorder.is_active:
            return
        from .subtitle_editor import preview_cues
        dialog = SubtitleToolsDialog(None if self._temporary_recording else self.session_audio_path,
                                     preview_cues(self.subtitle_cues, self.subtitle_offset.value()), self)
        dialog.exec()
        dialog.deleteLater()

    def export_text(self, kind: str) -> None:
        if self.pipeline is not None or self.file_job is not None:
            return
        try:
            content = render_txt(self.paragraphs) if kind == "txt" else render_srt(self.subtitle_cues, self.subtitle_offset.value())
            if not content:
                raise ValueError("没有可导出的内容，请检查字幕偏移或等待识别完成")
            if self._temporary_recording and self.session_audio_path is not None:
                documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
                suggested = Path(documents or str(Path.home())) / self.session_audio_path.with_suffix("." + kind).name
            else:
                suggested = (self.session_audio_path or Path("transcript.wav")).with_suffix("." + kind)
            title = "保存识别文字" if kind == "txt" else "导出 SRT 字幕"
            name_filter = "文本文件 (*.txt)" if kind == "txt" else "SRT 字幕 (*.srt)"
            filename, _ = QFileDialog.getSaveFileName(
                self, title, str(suggested), name_filter,
                options=QFileDialog.Option.DontConfirmOverwrite,
            )
            if not filename:
                return
            path = Path(filename)
            if path.suffix.lower() != "." + kind:
                path = path.with_suffix("." + kind)
            if path.exists() and QMessageBox.question(
                self, "替换文件", f"文件已存在，是否替换？\n{path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
            write_export(path, content)
            self._exports_saved = True
            self.export_status.setText(f"已保存：{path}")
        except (OSError, ValueError) as exc:
            self.export_status.setText(f"保存失败：{exc}")
            QMessageBox.warning(self, "保存失败", str(exc))

    def _confirm_unsaved(self, action: str) -> bool:
        if not self.paragraphs or self._exports_saved:
            return True
        return QMessageBox.question(
            self, "识别文字尚未保存",
            f"还有未成功保存的识别文字。是否仍要{action}？\n选择“否”后可点击保存按钮另存。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.file_job is not None:
            self._close_requested = True
            self.file_job.cancel()
            self.stop_button.setEnabled(False)
            self.export_status.setText("正在取消视频处理并保存已有结果，然后退出…")
            event.ignore()
            return
        if self.pipeline is not None:
            self._close_requested = True
            event.ignore()
            if self._capture_started:
                if self.recorder.is_active:
                    self.stop_recording()
                self.export_status.setText("正在完成最后的识别并保存，然后退出…")
            else:
                self.pipeline.cancel()
                self._recording_finished()
            return
        if self.recorder.is_active:
            self._close_requested = True
            self.recorder.stop()
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        if not self._confirm_unsaved("关闭窗口并丢弃这些文字"):
            self._close_requested = False
            event.ignore()
            return
        try:
            self._cleanup_capture_cache()
        except OSError as exc:
            QMessageBox.warning(self, "缓存清理失败", f"临时录音未能删除，请稍后手动清理：{exc}")
        if self.live_translator is not None:
            self.live_translator.cancel()
        if self.live_overlay is not None:
            self.live_overlay.dispose()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("巴别塔")
    if getattr(sys, "frozen", False):
        app.setWindowIcon(QIcon(str(Path(sys._MEIPASS) / "app-icon.ico")))
    window = MainWindow()
    app.setWindowIcon(window.windowIcon())
    window.show()
    return app.exec()
