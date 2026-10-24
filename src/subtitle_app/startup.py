"""Paint the welcome screen before loading the audio and model dependencies."""

import sys
import traceback

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication

from .branding import APP_NAME
from .welcome import WelcomeWindow


class StartupController(QObject):
    def __init__(self, app, window_factory=None):
        super().__init__(app)
        self.app = app
        self.welcome = WelcomeWindow()
        self.window = None
        self.window_factory = window_factory
        self._dismissed = False
        self.welcome.enter_button.clicked.connect(self.enter)
        self.welcome.rejected.connect(self.dismiss)

    def start(self):
        self.app.setWindowIcon(self.welcome.windowIcon())
        self.welcome.show()
        self.app.processEvents()
        QTimer.singleShot(0, self.load)

    def load(self):
        if self._dismissed or self.window is not None:
            return
        self.welcome.enter_button.setEnabled(False)
        self.welcome.status.setText("正在初始化音频与字幕组件…")
        self.welcome.progress.show()
        try:
            factory = self.window_factory
            if factory is None:
                from .main import MainWindow
                factory = MainWindow
            self.window = factory()
        except Exception as exc:
            traceback.print_exc()
            self.welcome.set_error(exc)
            return
        self.welcome.set_ready()

    def enter(self):
        if self.window is None:
            self.load()
            return
        self.window.show()
        self.welcome.accept()

    def dismiss(self):
        self._dismissed = True
        if self.window is not None:
            self.window.close()
        self.app.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    controller = StartupController(app)
    controller.start()
    return app.exec()
