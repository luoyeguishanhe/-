# Windows EXE 便携版

## 用户怎么启动

完整解压便携 ZIP 后，双击 `巴别塔/巴别塔.exe`。不需要安装 Python，也不需要运行 PowerShell 命令。

目录结构如下：

```text
巴别塔/
  巴别塔.exe
  _internal/          Python、Qt、语音库、FFmpeg 等运行资源
  models/small/       随包提供的 small 识别模型
  docs/               功能说明
  licenses/           模型等许可文件
  使用说明.md
  THIRD_PARTY.md
  build-dependencies.txt
  release.json
```

这些目录是一套完整程序，不能只把 EXE 单独复制走。本版是 Windows 64 位便携测试版，产品名为“巴别塔”；没有安装向导或自动更新。

small 可离线识别。base、medium 未随包提供，仍从用户缓存读取，缺少时需要联网下载。外部云端翻译需要配置自己的服务和 Key；本地 Ollama 服务仍需单独准备。

视频、字幕与用量报告保存在操作时选择的位置，不写进 EXE。日志位于 `%LOCALAPPDATA%\SubtitleApp\logs\app.log`。升级可解压到新目录，不覆盖自己保存的媒体和字幕。

## 打包原理

使用 PyInstaller 的 `onedir` 模式：把 Python 解释器、Python 代码和依赖库收集起来，用 Windows 启动程序作为入口。运行时无需调用用户电脑里的 Python。

采用目录式便携包，是为了让较大的语音模型和视频库直接读取，不必每次启动把一个巨大的单文件 EXE 解压到临时目录。最终用 ZIP 方便复制和分发。

`packaging/launcher.py` 在导入界面前准备窗口版日志输出，避免无终端时第三方库写入空的 stdout/stderr。`runtime.py` 优先查找 EXE 旁 `models/small`，所以移动整个文件夹后仍可找到模型。

`SubtitleApp.spec` 收集 Qt Multimedia、PyAV、CTranslate2、Silero VAD、OpenCC 字典、FFmpeg 和 WASAPI 库。项目使用 `webrtcvad-wheels`，额外的打包 hook 负责处理它与导入名 `webrtcvad` 不一致的问题。

构建进程使用独立的 PATH，避免开发工具自带的其他版本 DLL 被误收集，例如名字相同而接口不兼容的 ICU。small 模型从本机现有缓存复制，构建不自动下载模型。

## 从源代码重新构建

在 Windows 的项目目录，使用现有虚拟环境安装打包工具：

```powershell
.venv\Scripts\python -m pip install -e ".[build]"
.venv\Scripts\python packaging\build_windows.py
```

也可双击 `build-windows.cmd`。默认输出 `dist/巴别塔` 和 `dist/巴别塔-0.1.0-win64.zip`。目标目录已存在时拒绝覆盖，请换一个输出位置：

```powershell
.venv\Scripts\python packaging\build_windows.py --output dist-next
```

默认需要本机已有 small 模型缓存。可通过 `--model-source` 指定包含四个模型文件的目录，或使用 `--without-model` 制作需要单独准备模型的精简版；当前交付的便携包包含 small。

打包产物和个人媒体不会提交 Git。Git 保存的是代码、打包配置、图标、说明与自检程序，成品 ZIP 单独交付。

制作 GPU 版时先安装 `.venv\Scripts\python -m pip install -e ".[build,gpu]"`，构建脚本会将已安装的 cuBLAS、cuDNN、NVRTC DLL 和对应许可信息一并收集。GPU 包体积更大；没有这些可选依赖的构建仍可在 CPU 上运行。系统声音模式自动选择 GPU 并在不可用时回退 CPU，文件识别参数保持原配置。

图标使用 `packaging/app-icon.svg` 作为源文件，构建时自动生成包含 16、24、32、48、64、128、256 像素的 `packaging/app-icon.ico`。仅更新图标时也可运行 `.venv\Scripts\python packaging\generate_icon.py`。EXE 的产品名称、说明和原始文件名由 `packaging/version_info.txt` 提供。内部包名和用户数据目录继续沿用 `SubtitleApp`，兼容已有缓存与设置。

## 验证方式

源码测试覆盖便携模型定位、缺文件报错及无终端日志输出。成品使用 EXE 内置的构建自检入口，直接执行打包后的识别、播放、模拟翻译与费用计算和软字幕封装。

自检使用独立工作目录、空白用户模型缓存、离线模型模式，并从 PATH 去掉 Python；同时检查关键模块确实从便携包内部导入。模拟翻译服务不使用个人 API Key，也不产生云端费用。

这能验证当前电脑上的便携依赖与主要功能；尚未在全新 Windows 虚拟机、不同显卡和所有视频编码上逐一验证。
