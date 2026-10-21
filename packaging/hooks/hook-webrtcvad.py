"""The app uses webrtcvad-wheels, whose metadata name differs from its module."""
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
hiddenimports = ["_webrtcvad"]
