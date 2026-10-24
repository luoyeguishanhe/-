"""Small movable subtitle window and its in-memory translation settings."""

from PyQt6.QtCore import QEvent, QPoint, QSettings, Qt
from PyQt6.QtGui import QCloseEvent, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .translation import TranslationConfig


class LiveTranslationSettingsDialog(QDialog):
    def __init__(self, config: TranslationConfig | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("直播字幕翻译设置")
        self.setMinimumWidth(520)
        self.config: TranslationConfig | None = None
        self.preset = QComboBox()
        self.preset.addItems(["自定义兼容接口", "千问云端（中国内地）", "Ollama 本地"])
        self.base_url = QLineEdit(config.base_url if config else "")
        self.base_url.setPlaceholderText("https://服务地址/v1")
        self.model = QLineEdit(config.model if config else "")
        self.model.setPlaceholderText("填写接口支持的模型名称")
        self.api_key = QLineEdit(config.api_key if config else "")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("云端填写 API Key；本地无鉴权可留空")
        if config:
            if config.base_url.startswith("https://dashscope.aliyuncs.com/"):
                self.preset.setCurrentIndex(1)
            elif config.base_url.startswith("http://127.0.0.1:11434/"):
                self.preset.setCurrentIndex(2)
        self.preset.currentIndexChanged.connect(self.apply_preset)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        title = QLabel("直播字幕翻译设置")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #183457;")
        layout.addWidget(title)
        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("翻译服务", self.preset)
        form.addRow("接口地址", self.base_url)
        form.addRow("模型名称", self.model)
        form.addRow("API Key", self.api_key)
        layout.addLayout(form)
        note = QLabel("识别到的每段语音会发送文字进行翻译，目标语言为简体中文。"
                      "API Key 只保留在本次程序运行的内存中。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #62758c;")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_preset(self, index: int) -> None:
        self.api_key.clear()
        if index == 1:
            self.base_url.setText("https://dashscope.aliyuncs.com/compatible-mode/v1")
            self.model.setText("qwen-plus")
        elif index == 2:
            self.base_url.setText("http://127.0.0.1:11434/v1")
            self.model.setText("qwen2.5:7b")

    def validate(self) -> None:
        config = TranslationConfig(
            self.base_url.text(), self.model.text(), self.api_key.text().strip(),
            "简体中文", timeout=30, batch_size=1)
        try:
            config.endpoint()
        except ValueError as exc:
            QMessageBox.warning(self, "翻译设置无效", str(exc))
            return
        self.config = config
        self.accept()


class LiveSubtitleOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("直播字幕")
        self.resize(760, 164)
        self.setMinimumSize(460, 130)
        self._drag_offset: QPoint | None = None
        self._allow_close = False
        self._settings = QSettings("SubtitleApp", "SubtitleApp")

        frame = QWidget(self)
        frame.setObjectName("captionFrame")
        frame.setStyleSheet("""
            QWidget#captionFrame { background: rgba(16, 28, 45, 230); border-radius: 12px; }
            QLabel { color: #ffffff; font-family: 'Microsoft YaHei UI', 'Segoe UI'; }
            QLabel#captionTitle { color: #99b5d6; font-size: 11px; font-weight: 600; }
            QLabel#originalText { color: #c7d4e4; font-size: 13px; }
            QLabel#translatedText { color: #ffffff; font-size: 23px; font-weight: 700; }
            QPushButton { color: #c9d6e6; background: transparent; border: none; font-size: 11px; }
            QPushButton:hover { color: #ffffff; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(20, 12, 20, 16)
        inner.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel("直播字幕  ·  拖动窗口调整位置")
        title.setObjectName("captionTitle")
        self.caption_title = title
        self._has_translation = False
        top.addWidget(title)
        top.addStretch()
        hide_button = QPushButton("隐藏")
        hide_button.clicked.connect(self.hide)
        top.addWidget(hide_button)
        inner.addLayout(top)
        self.original = QLabel("")
        self.original.setObjectName("originalText")
        self.original.setTextFormat(Qt.TextFormat.PlainText)
        self.original.setWordWrap(True)
        inner.addWidget(self.original)
        self.translated = QLabel("等待语音…")
        self.translated.setObjectName("translatedText")
        self.translated.setTextFormat(Qt.TextFormat.PlainText)
        self.translated.setWordWrap(True)
        inner.addWidget(self.translated, 1)
        for child in (frame, title, self.original, self.translated):
            child.installEventFilter(self)

    def show_overlay(self) -> None:
        position = self._settings.value("overlay_position")
        if isinstance(position, QPoint) and QGuiApplication.screenAt(position):
            self.move(position)
        elif screen := QGuiApplication.primaryScreen():
            area = screen.availableGeometry()
            self.move(area.center().x() - self.width() // 2,
                      area.bottom() - self.height() - 85)
        self.show()
        self.raise_()

    def show_pending(self, original: str) -> None:
        self.caption_title.setText("正在翻译下一段 · 按住文字区域拖动")
        if not self._has_translation:
            self.original.setText(original)
            self.translated.setText("正在翻译…")

    def reset(self) -> None:
        self._has_translation = False
        self.caption_title.setText("直播字幕 · 按住文字区域拖动")
        self.original.clear()
        self.translated.setText("等待语音…")

    def show_translation(self, original: str, translated: str) -> None:
        self._has_translation = True
        self.caption_title.setText("直播字幕 · 按住文字区域拖动")
        self.original.setText(original)
        self.translated.setText(translated)

    def show_error(self, message: str) -> None:
        self._has_translation = False
        self.translated.setText(message)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
            self._drag_offset = None
            self._settings.setValue("overlay_position", self.pos())
            return True
        return super().eventFilter(watched, event)

    def dispose(self) -> None:
        self._allow_close = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            super().closeEvent(event)
        else:
            self.hide()
            event.ignore()
