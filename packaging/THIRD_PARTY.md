# Third-party components

This portable build includes third-party runtime libraries and tools. Their license
files/metadata are retained under `_internal` where provided by the distributions.
`build-dependencies.txt` records the versions used for this build.

Primary upstream source and license references:

- Python: https://www.python.org/ (PSF license)
- PyQt6: https://www.riverbankcomputing.com/software/pyqt/ (GPL v3 or commercial)
- Qt: https://www.qt.io/licensing/ (module-specific GPL/LGPL/commercial terms)
- PyInstaller: https://github.com/pyinstaller/pyinstaller (GPL with bootloader exception)
- faster-whisper: https://github.com/SYSTRAN/faster-whisper (MIT)
- Whisper / model weights: https://github.com/openai/whisper (MIT)
- CTranslate2: https://github.com/OpenNMT/CTranslate2 (MIT)
- ONNX Runtime: https://github.com/microsoft/onnxruntime (MIT)
- Hugging Face libraries: https://github.com/huggingface (package-specific licenses)
- OpenCC Python: https://github.com/yichen0831/opencc-python (Apache 2.0)
- PyAV: https://github.com/PyAV-Org/PyAV (BSD 3-Clause; linked FFmpeg has its own terms)
- imageio-ffmpeg: https://github.com/imageio/imageio-ffmpeg (BSD 2-Clause wrapper)
- FFmpeg binary: https://ffmpeg.org/ and https://github.com/BtbN/FFmpeg-Builds
  (license depends on the included binary's build configuration; see `ffmpeg -L`)
- PyAudioWPatch: https://github.com/s0d3s/PyAudioWPatch (MIT; PortAudio has its own license)
- python-soxr / libsoxr: https://github.com/dofuuz/python-soxr (wrapper/library licenses)
- webrtcvad-wheels: https://github.com/daanzu/py-webrtcvad-wheels (MIT and WebRTC notices)
- NumPy: https://github.com/numpy/numpy (BSD and bundled library notices)
- GPU builds include NVIDIA cuBLAS, cuDNN and NVRTC CUDA 12 runtime libraries:
  https://docs.nvidia.com/cuda/eula/ and https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html
  (NVIDIA license terms; distribution-provided license files are retained in `_internal`.)

Project source and build scripts: https://github.com/luoyeguishanhe/babel-subtitle-translator
No personal audio/video, API keys, transcripts, or usage history are included.
