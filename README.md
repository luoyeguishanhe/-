# 实时中文字幕应用（开发中）

这是一款计划捕获 Windows 系统音频、结合本地语音模型和外部大模型进行实时翻译的字幕应用。面向普通用户，后续还计划支持本地视频字幕导出。当前仓库只包含可启动的桌面界面骨架，音频采集、语音识别、翻译和字幕导出尚未接入。

## 启动开发版

需要 Python 3.11 或更新版本。在项目目录中运行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m subtitle_app
```

后续将按功能逐步复用 [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate) 的实现，并保留所复用代码的 MIT 版权及许可声明。目前的界面骨架未复制其源代码。

## 开发方向

1. 接入系统音频采集、语音识别和实时翻译，并显示清晰的运行状态。
2. 支持常见云端及本地翻译模型，显示 token 用量和费用。
3. 基于视频音轨时间轴导出校时字幕，再支持字幕封装与烧录。
4. 简化设置、完善界面和 Windows 安装包。

正式产品名称尚未确定，当前包名仅用于开发。
