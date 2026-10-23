"""File-based subtitle tools, available independently of recognition."""

from pathlib import Path
import json

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from .background_job import BackgroundJob
from .billing import Pricing, UsageRecord, usage_report, window_summary
from .subtitle_mux import mux_subtitles
from .subtitles import parse_srt, render_srt, write_export
from .translation import TranslationConfig, translate_subtitles


TOOLS_STYLE = """
QDialog, QScrollArea, QScrollArea > QWidget > QWidget {
    background: #edf3f8; color: #21334d;
}
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #21334d; }
QLabel#toolsTitle { font-size: 25px; font-weight: 700; color: #183457; }
QLabel#toolsSection {
    font-size: 16px; font-weight: 700; color: #203b5d;
    padding-top: 9px;
}
QLabel#toolsHint { color: #70839b; font-size: 11px; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit {
    background: #ffffff; border: 1px solid #cbd9e7;
    border-radius: 7px; padding: 7px 9px;
    selection-background-color: #3168c9;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus,
QSpinBox:focus, QPlainTextEdit:focus { border: 2px solid #3168c9; }
QPlainTextEdit { background: #f8fbfe; }
QPushButton {
    background: #ffffff; color: #24466d; border: 1px solid #ccdaea;
    border-radius: 7px; padding: 8px 13px; min-height: 22px; font-weight: 600;
}
QPushButton:hover { background: #e6eff9; border-color: #8eb0d9; }
QPushButton:focus { border: 2px solid #3168c9; }
QPushButton:disabled { color: #9aaabe; background: #f4f7fa; border-color: #e3eaf1; }
QToolButton#toolsAdvancedToggle {
    border: none; background: transparent; color: #3168c9;
    font-weight: 600; padding: 7px 0;
}
QProgressBar { background: #dce8f3; border: none; border-radius: 5px; min-height: 16px; }
QProgressBar::chunk { background: #4b8ad6; border-radius: 5px; }
"""


class SubtitleToolsDialog(QDialog):
    def __init__(self, source=None, cues=None, parent=None):
        super().__init__(parent)
        self.source = Path(source) if source else None
        self.cues = list(cues or [])
        self.translated = []
        self.translation_complete = False
        self.translation_saved = True
        self.translation_target = None
        self.job_kind = None
        self.job = None
        self.close_requested = False
        self.usage_records = []
        self.current_usage_record = None
        self.setWindowTitle("字幕翻译与封装")
        self.resize(900, 780)
        self.setStyleSheet(TOOLS_STYLE)
        self.source_label = QLineEdit(str(self.source or ""))
        self.source_label.setReadOnly(True)
        self.source_label.setPlaceholderText("选择原视频")
        self.video_button = QPushButton("选择视频…")
        self.video_button.clicked.connect(self.choose_video)
        self.srt_button = QPushButton("导入 SRT…")
        self.srt_button.clicked.connect(self.choose_srt)
        self.cue_label = QLabel()
        self.track = QComboBox()
        self.track.addItems(["原文字幕", "译文字幕"])
        self.track.currentIndexChanged.connect(self.refresh_preview)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("可导入已保存的 SRT，或从识别/校对窗口带入字幕。")
        self.preview.setMaximumHeight(130)
        self.preset = QComboBox()
        self.preset.addItems(["自定义兼容接口", "千问云端（中国内地）", "Ollama 本地"])
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://你的服务地址/v1")
        self.model = QLineEdit()
        self.model.setPlaceholderText("填写服务支持的模型名称")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("云端服务填写 Key；本地无鉴权可留空")
        self.preset.currentIndexChanged.connect(self.apply_preset)
        self.base_url.textEdited.connect(lambda: self.api_key.clear())
        self.base_url.textEdited.connect(self.invalidate_pricing)
        self.model.textEdited.connect(self.invalidate_pricing)
        self.target = QComboBox()
        self.target.addItems(["简体中文", "英语", "日语", "韩语"])
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 50)
        self.batch_size.setValue(12)
        self.batch_size.setSuffix(" 条 / 次")
        self.translate_button = QPushButton("开始翻译原文字幕")
        self.translate_button.clicked.connect(self.start_translation)
        self.save_translation_button = QPushButton("另存译文 SRT…")
        self.save_translation_button.clicked.connect(self.save_translation)
        self.usage_label = QLabel("Token 用量：尚未请求")
        self.usage_label.setWordWrap(True)
        self.pricing_enabled = QCheckBox("启用费用估算（单价仅用于下次翻译）")
        self.currency = QComboBox()
        self.currency.addItem("人民币 CNY", "CNY")
        self.currency.addItem("美元 USD", "USD")
        self.input_price = self.price_box()
        self.output_price = self.price_box()
        self.pricing_enabled.toggled.connect(self.update_pricing_controls)
        self.cost_label = QLabel("本次费用估算：尚未请求")
        self.cost_label.setWordWrap(True)
        self.window_usage_label = QLabel(window_summary(self.usage_records))
        self.window_usage_label.setWordWrap(True)
        self.report_button = QPushButton("导出用量报告…")
        self.report_button.clicked.connect(self.export_usage_report)
        self.report_button.setEnabled(False)
        self.mux_button = QPushButton("封装为带字幕的 MKV…")
        self.mux_button.clicked.connect(self.start_mux)
        self.cancel_button = QPushButton("取消处理")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.status = QLabel("选择视频和字幕，生成可在播放器中开关字幕的新视频。")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(12)
        heading = QLabel("翻译与封装")
        heading.setObjectName("toolsTitle")
        layout.addWidget(heading)
        layout.addWidget(self._section_title("视频与字幕"))
        row = QHBoxLayout()
        row.addWidget(self.source_label, 1)
        row.addWidget(self.video_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.cue_label, 1)
        row.addWidget(self.track)
        row.addWidget(self.srt_button)
        layout.addLayout(row)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self._section_title("翻译字幕"))
        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("翻译服务", self.preset)
        form.addRow("接口地址", self.base_url)
        form.addRow("模型名称", self.model)
        form.addRow("API Key", self.api_key)
        form.addRow("目标语言", self.target)
        layout.addLayout(form)
        privacy = QLabel("只发送字幕文字，不上传视频或音频。API Key 关闭窗口后清除。")
        privacy.setWordWrap(True)
        privacy.setObjectName("toolsHint")
        layout.addWidget(privacy)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("更多翻译设置与费用估算")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setObjectName("toolsAdvancedToggle")
        layout.addWidget(self.advanced_toggle)
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(9)
        batch_row = QHBoxLayout()
        batch_row.addWidget(QLabel("每批翻译条数"))
        batch_row.addWidget(self.batch_size)
        batch_row.addStretch()
        advanced_layout.addLayout(batch_row)
        advanced_layout.addWidget(self.pricing_enabled)
        pricing = QHBoxLayout()
        pricing.addWidget(self.currency)
        pricing.addWidget(QLabel("输入 / 百万 token"))
        pricing.addWidget(self.input_price, 1)
        pricing.addWidget(QLabel("输出 / 百万 token"))
        pricing.addWidget(self.output_price, 1)
        advanced_layout.addLayout(pricing)
        price_note = QLabel("请按服务商单价填写；本地免费服务可填 0。估算未扣缓存折扣或赠送额度，以服务账单为准。")
        price_note.setWordWrap(True)
        price_note.setObjectName("toolsHint")
        advanced_layout.addWidget(price_note)
        advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(advanced.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda checked: self.advanced_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        layout.addWidget(advanced)
        translate_row = QHBoxLayout()
        translate_row.addWidget(self.translate_button)
        translate_row.addWidget(self.save_translation_button)
        layout.addLayout(translate_row)
        layout.addWidget(self.usage_label)
        layout.addWidget(self.cost_label)
        layout.addWidget(self.window_usage_label)
        layout.addWidget(self.report_button)
        layout.addWidget(self._section_title("放进视频"))
        note = QLabel("输出 MKV，直接复制全部视频和音轨，使用当前字幕；不带入旧字幕轨。原文件保留。")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        actions.addWidget(self.mux_button)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        outer.addWidget(self.progress)
        self.status.setContentsMargins(30, 9, 30, 12)
        outer.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll_job)
        self.timer.start()
        self.refresh_preview()
        self.update_pricing_controls()

    @staticmethod
    def _section_title(text):
        label = QLabel(text)
        label.setObjectName("toolsSection")
        return label

    @staticmethod
    def price_box():
        box = QDoubleSpinBox()
        box.setDecimals(6)
        box.setRange(0, 1_000_000)
        box.setSingleStep(0.1)
        return box

    def invalidate_pricing(self):
        # Avoid carrying a different provider/model's tariff into a new request.
        self.pricing_enabled.setChecked(False)

    def update_pricing_controls(self):
        enabled = self.pricing_enabled.isChecked() and self.job is None
        for widget in (self.currency, self.input_price, self.output_price):
            widget.setEnabled(enabled)

    def refresh_billing(self):
        record = self.current_usage_record
        if record is not None:
            usage = record.usage
            total = f"；已知合计 {usage.prompt_tokens + usage.completion_tokens} tokens" if usage.reported_calls else ""
            self.usage_label.setText("本次翻译：" + usage.summary() + total)
            self.cost_label.setText(record.cost_summary())
        self.window_usage_label.setText(window_summary(self.usage_records))
        self.report_button.setEnabled(self.job is None and any(
            item.usage.reported_calls + item.usage.unreported_calls for item in self.usage_records))

    def export_usage_report(self):
        if self.job is not None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出本窗口用量报告", "翻译用量报告.json", "JSON 报告 (*.json)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        path = Path(filename).with_suffix(".json")
        if path.exists():
            self.status.setText("报告文件已存在，请使用新文件名。")
            return
        try:
            write_export(path, json.dumps(usage_report(self.usage_records), ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError) as exc:
            self.status.setText(f"报告保存失败：{exc}")
            return
        self.status.setText(f"用量报告已保存：{path}（不含 API Key 或字幕文字）")

    def refresh_preview(self):
        self.cue_label.setText(f"原文 {len(self.cues)} 条；已翻译 {len(self.translated)} 条")
        self.preview.setPlainText(render_srt(self.selected_cues()))
        self.track.model().item(1).setEnabled(bool(self.translated))
        busy = self.job is not None
        self.mux_button.setEnabled(not busy and bool(self.source and self.selected_cues())
                                  and (self.track.currentIndex() == 0 or self.translation_complete))
        self.translate_button.setEnabled(not busy and bool(self.cues))
        self.save_translation_button.setEnabled(not busy and bool(self.translated))

    def selected_cues(self):
        return self.translated if self.track.currentIndex() == 1 else self.cues

    def apply_preset(self, index):
        self.api_key.clear()
        self.invalidate_pricing()
        if index == 1:
            self.base_url.setText("https://dashscope.aliyuncs.com/compatible-mode/v1")
            self.model.setText("qwen-plus")
        elif index == 2:
            self.base_url.setText("http://127.0.0.1:11434/v1")
            self.model.setText("qwen2.5:7b")

    def confirm_discard_translation(self):
        if not self.translated or self.translation_saved:
            return True
        return QMessageBox.question(
            self, "译文尚未保存", "是否丢弃尚未保存的译文？选择“否”可先另存译文 SRT。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def choose_video(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择原视频", "", "视频 (*.mp4 *.mkv *.mov *.avi *.webm *.ts);;所有文件 (*)")
        if filename:
            self.source = Path(filename)
            self.source_label.setText(filename)
            self.refresh_preview()

    def choose_srt(self):
        filename, _ = QFileDialog.getOpenFileName(self, "导入 SRT", "", "SRT 字幕 (*.srt)")
        if not filename:
            return
        try:
            cues = parse_srt(Path(filename).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            self.status.setText(f"导入失败：{exc}。请使用 UTF-8 SRT 文件。")
            return
        if not self.confirm_discard_translation():
            return
        self.cues = cues
        self.translated = []
        self.translation_complete = False
        self.translation_saved = True
        self.translation_target = None
        self.track.setCurrentIndex(0)
        self.refresh_preview()
        self.status.setText(f"已导入 {filename}；请确认字幕时间对应所选视频。")

    def set_busy(self, busy):
        for widget in (self.video_button, self.srt_button, self.track, self.preset,
                       self.base_url, self.model, self.api_key, self.target, self.batch_size,
                       self.pricing_enabled):
            widget.setEnabled(not busy)
        self.refresh_preview()
        self.cancel_button.setEnabled(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 100)
        self.update_pricing_controls()
        self.refresh_billing()

    def start_mux(self):
        if self.job is not None or not self.source or not self.selected_cues():
            return
        suggested = self.source.with_name(self.source.stem + "-带字幕.mkv")
        filename, _ = QFileDialog.getSaveFileName(
            self, "封装字幕", str(suggested), "MKV 视频 (*.mkv)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        destination = Path(filename).with_suffix(".mkv")
        if destination.exists():
            self.status.setText("目标文件已存在，请换一个文件名。")
            return
        if self.track.currentIndex() == 1 and not self.translation_complete:
            self.status.setText("译文尚未全部完成，可先另存 partial SRT；完整译文才可封装。")
            return
        source, cues = self.source, list(self.selected_cues())
        language = ({"简体中文": "zho", "英语": "eng", "日语": "jpn", "韩语": "kor"}
                    .get(self.translation_target, "und") if self.track.currentIndex() == 1 else "und")
        self.job_kind = "mux"
        self.job = BackgroundJob(lambda cancel, emit: mux_subtitles(source, cues, destination, cancel, emit, language))
        self.set_busy(True)
        self.status.setText("正在准备字幕封装…")
        self.job.start()

    def start_translation(self):
        if self.job is not None or not self.cues:
            return
        config = TranslationConfig(self.base_url.text(), self.model.text(), self.api_key.text().strip(),
                                   self.target.currentText(), batch_size=self.batch_size.value())
        try:
            config.endpoint()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if not self.confirm_discard_translation():
            return
        pricing = (Pricing(self.input_price.value(), self.output_price.value(), self.currency.currentData())
                   if self.pricing_enabled.isChecked() else None)
        self.current_usage_record = UsageRecord(config.endpoint(), config.model.strip(), pricing)
        self.usage_records.append(self.current_usage_record)
        self.translated = []
        self.translation_complete = False
        self.translation_saved = True
        self.translation_target = config.target
        self.job_kind = "translation"
        cues = list(self.cues)
        self.job = BackgroundJob(lambda cancel, emit: translate_subtitles(cues, config, cancel, emit))
        self.set_busy(True)
        self.status.setText("正在连接翻译服务…取消会等待当前请求返回，已完成译文可另存。")
        self.usage_label.setText("Token 用量：等待服务返回")
        self.job.start()

    def save_translation(self):
        if self.job is not None or not self.translated:
            return
        suffix = "-译文" + ("" if self.translation_complete else "-partial") + ".srt"
        suggested = (self.source or Path("subtitle.mp4")).with_name((self.source or Path("subtitle")).stem + suffix)
        filename, _ = QFileDialog.getSaveFileName(
            self, "另存译文 SRT", str(suggested), "SRT 字幕 (*.srt)",
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        path = Path(filename).with_suffix(".srt")
        if path.exists():
            self.status.setText("文件已存在，请换一个新文件名，保留原字幕。")
            return
        try:
            write_export(path, render_srt(self.translated))
        except (OSError, ValueError) as exc:
            self.status.setText(f"译文保存失败：{exc}。译文仍保留，可重试。")
            return
        self.translation_saved = True
        self.status.setText(f"已保存：{path}")

    def cancel(self):
        if self.job:
            self.job.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消处理；翻译需等待当前请求返回，已完成译文会保留…")

    def poll_job(self):
        if not self.job:
            return
        while (event := self.job.next_event()) is not None:
            kind, value = event
            if kind == "status":
                self.status.setText(str(value))
            elif kind == "usage":
                self.usage_label.setText(str(value))
            elif kind == "token_usage":
                if self.current_usage_record is not None:
                    self.current_usage_record.usage = value
                    self.refresh_billing()
            elif kind == "translated":
                self.translated = list(value)
                self.translation_saved = False
                self.track.setCurrentIndex(1)
                self.refresh_preview()
            elif kind == "progress":
                done, total = value
                self.progress.setRange(0, total)
                self.progress.setValue(done)
            elif kind in ("complete", "cancelled", "error"):
                was_translation = self.job_kind == "translation"
                self.job = None
                if was_translation and kind == "complete":
                    self.translated = list(value)
                    self.translation_complete = True
                    self.translation_saved = False
                self.set_busy(False)
                if kind == "complete" and was_translation:
                    self.status.setText("翻译完成，时间轴保持不变。可另存译文 SRT，或选择译文封装视频。")
                elif kind == "complete":
                    self.status.setText(f"已生成：{value}。请在支持 MKV 的播放器中选择字幕轨道。")
                elif kind == "cancelled":
                    self.status.setText("已取消。已完成的译文可另存 partial SRT。" if was_translation else "已取消，原视频未改变，未生成完成文件。")
                else:
                    self.status.setText(f"处理失败：{value}")
                if was_translation and self.usage_label.text() == "Token 用量：等待服务返回":
                    self.usage_label.setText("Token 用量：本次未返回用量，未知")
                if self.close_requested:
                    self.close_requested = False
                    self.reject()
                break

    def reject(self):
        if self.job:
            self.close_requested = True
            self.cancel()
            return
        if not self.confirm_discard_translation():
            return
        self.api_key.clear()
        super().reject()
