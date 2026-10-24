"""Shared product identity and public project introduction."""

from pathlib import Path
import sys

APP_NAME = "巴别塔"
TAGLINE = "跨越语言隔阂，让每一句都能被听懂"
DEVELOPER_NOTE = (
    "这是一个由在校大学生个人开发的项目。受学业和个人精力所限，"
    "功能与体验仍在逐步完善，感谢你的理解与反馈。"
)
PROJECT_NOTE = (
    "我希望把巴别塔做成一个适用于各种语音场景的翻译器。"
    "目前主要完成了本地视频识别与字幕处理、系统声音实时识别和翻译；"
    "更多场景与功能会在后续逐步跟进。"
)
MODEL_NOTE = (
    "本地语音识别使用开源免费的 Whisper 模型，没有自研或训练专属模型。"
    "翻译需要另行接入本地大模型或云端接口，云端服务可能收费。"
)
PROJECT_URL = "https://github.com/luoyeguishanhe/-"


def icon_path() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "packaging"
    return root / "app-icon.ico"
