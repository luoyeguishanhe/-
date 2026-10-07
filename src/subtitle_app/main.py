"""桌面应用入口。后续功能将在此基础上逐步接入。"""

import sys

from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("实时中文字幕 · 开发版")
        self.resize(640, 360)

        title = QLabel("实时中文字幕")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        status = QLabel("应用框架已建立；音频采集和字幕功能尚未接入。")
        status.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(status)
        layout.addStretch()

        content = QWidget()
        content.setLayout(layout)
        self.setCentralWidget(content)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
