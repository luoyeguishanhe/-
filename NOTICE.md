# 项目与第三方说明

Copyright (C) 2026 luoyeguishanhe

巴别塔是由在校大学生个人维护的独立项目，项目自身源代码以 **GPL-3.0-only** 许可发布，完整条款见 [LICENSE](LICENSE)。允许使用、学习、修改和再分发；分发本项目或基于它的衍生作品时，应遵守 GPLv3 对许可声明、对应源码等方面的要求。本项目按许可证条款提供，不附带质量保证。

## 项目来源

项目最初受 [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate) 的使用体验启发，参考了系统音频采集、语音识别、翻译和悬浮字幕这类功能思路。当前仓库未直接复制或合并该项目的应用源代码，不是其代码库的 fork。音频采集、语音处理队列、字幕流程、应用界面及打包流程在本仓库中独立实现。开发过程中使用了 AI 编程辅助工具。

这不意味着底层语音模型或第三方库由本项目原创。项目使用 Whisper 及 faster-whisper、CTranslate2、PyQt6、PyAV、OpenCC 等开源组件。它们及 NVIDIA GPU 运行库保留各自许可，具体见 [第三方组件说明](packaging/THIRD_PARTY.md) 和发行包中的 `licenses`、`_internal` 许可文件。项目自身 GPLv3 声明不替代第三方原有条款。

Whisper 识别模型为开源免费模型，本项目没有训练专属识别模型。外部翻译模型与服务由用户自行配置，服务费用、额度及模型使用条件由对应提供方决定。
