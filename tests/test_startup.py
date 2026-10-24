import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import TestCase
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication, QWidget

from subtitle_app.startup import StartupController
from subtitle_app.welcome import WelcomeWindow


class StartupTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_loading_does_not_enter_workspace_until_requested(self):
        window = QWidget()
        controller = StartupController(self.app, window_factory=lambda: window)
        self.addCleanup(controller.welcome.accept)
        self.addCleanup(window.close)
        self.assertFalse(controller.welcome.enter_button.isEnabled())
        controller.load()
        self.assertTrue(controller.welcome.enter_button.isEnabled())
        self.assertFalse(window.isVisible())
        controller.enter()
        self.assertTrue(window.isVisible())
        self.assertFalse(controller.welcome.isVisible())

    def test_failed_loading_can_retry_without_constructing_duplicate_windows(self):
        window = QWidget()
        factory = Mock(side_effect=[RuntimeError("audio unavailable"), window])
        controller = StartupController(self.app, window_factory=factory)
        self.addCleanup(controller.welcome.accept)
        self.addCleanup(window.close)
        with patch("subtitle_app.startup.traceback.print_exc"):
            controller.load()
        self.assertIn("audio unavailable", controller.welcome.status.text())
        self.assertTrue(controller.welcome.enter_button.isEnabled())
        controller.enter()
        self.assertIs(controller.window, window)
        controller.load()
        self.assertEqual(factory.call_count, 2)

    def test_dismissed_startup_does_not_load_hidden_workspace(self):
        fake_app = Mock()
        # QObject parenting requires a real application; replace only quit.
        factory = Mock()
        controller = StartupController(self.app, window_factory=factory)
        controller.app = fake_app
        self.addCleanup(controller.welcome.accept)
        controller.dismiss()
        controller.load()
        factory.assert_not_called()
        fake_app.quit.assert_called_once()

    def test_about_window_is_immediately_readable(self):
        dialog = WelcomeWindow(about=True)
        self.addCleanup(dialog.accept)
        self.assertTrue(dialog.enter_button.isEnabled())
        self.assertEqual(dialog.windowTitle(), "关于巴别塔")
