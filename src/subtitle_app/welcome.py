"""A quiet brand introduction, shown before importing the main workspace."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from . import __version__
from .branding import APP_NAME, TAGLINE, DEVELOPER_NOTE, PROJECT_NOTE, MODEL_NOTE, icon_path


class WelcomeWindow(QDialog):
    def __init__(self, parent=None, *, about=False):
        super().__init__(parent)
        self.setWindowTitle("关于巴别塔" if about else "巴别塔 · 启动")
        self.setWindowIcon(QIcon(str(icon_path())))
        self.resize(680, 720)
        self.setMinimumSize(520, 540)
        self.setStyleSheet("""
            QDialog { background: #f5f9f8; }
            QWidget { color: #21444a; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
            QScrollArea, QWidget#introContent { background: transparent; border: none; }
            QLabel#brandName { font-size: 32px; font-weight: 700; color: #105761; }
            QLabel#tagline { font-size: 16px; color: #105761; }
            QLabel#introBody { color: #425f64; }
            QLabel#modelNote, QLabel#startupStatus { color: #60797d; font-size: 12px; }
            QLabel#version { color: #72888b; font-size: 11px; }
            QFrame#divider { background: #d6e4e1; max-height: 1px; border: none; }
            QPushButton { background: #105761; color: white; border: none; border-radius: 8px;
                          min-height: 40px; padding: 4px 32px; font-weight: 600; font-size: 14px; }
            QPushButton:hover { background: #0b4650; }
            QPushButton:focus { border: 2px solid #dda749; }
            QPushButton:disabled { background: #c5d7d4; color: #f5f9f8; }
            QProgressBar { background: #dfeae7; border: none; border-radius: 2px; max-height: 3px; }
            QProgressBar::chunk { background: #c3933f; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 22)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("introContent")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(54, 28, 54, 12)
        layout.setSpacing(15)
        layout.addStretch()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(120, 120))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        name = QLabel(APP_NAME)
        name.setObjectName("brandName")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name)
        tagline = QLabel(TAGLINE)
        tagline.setObjectName("tagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setWordWrap(True)
        layout.addWidget(tagline)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        for text in (DEVELOPER_NOTE, PROJECT_NOTE):
            label = QLabel(text)
            label.setObjectName("introBody")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        model = QLabel(MODEL_NOTE)
        model.setObjectName("modelNote")
        model.setWordWrap(True)
        layout.addWidget(model)
        version = QLabel(f"v{__version__}  ·  个人独立项目  ·  GPLv3")
        version.setObjectName("version")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        outer.addWidget(self.progress)
        self.status = QLabel("正在准备音频与字幕组件…")
        self.status.setObjectName("startupStatus")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setContentsMargins(24, 2, 24, 2)
        outer.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        self.enter_button = QPushButton("返回工作台" if about else "正在准备…")
        self.enter_button.setEnabled(about)
        row.addWidget(self.enter_button)
        row.addStretch()
        outer.addLayout(row)
        if about:
            self.progress.hide()
            self.status.setText("开发历程与使用教程随便携包提供，也可在 GitHub 仓库查看。")
            self.enter_button.clicked.connect(self.accept)

    def set_ready(self):
        self.progress.hide()
        self.status.setText("工作台已就绪；语音模型将在你开始任务时加载。")
        self.enter_button.setText("进入巴别塔")
        self.enter_button.setEnabled(True)
        self.enter_button.setFocus()

    def set_error(self, message):
        self.progress.hide()
        self.status.setText("启动失败：" + str(message))
        self.enter_button.setText("重试加载")
        self.enter_button.setEnabled(True)
