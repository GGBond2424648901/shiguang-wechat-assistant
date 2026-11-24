# 许可与来源

原创程序代码的许可见根目录 LICENSE。应用图标和角色头像采用使用者指定恢复的原动漫图片，`packaging/brand_assets.py` 仅原样复制品牌文件，不再自动替换图案；这些图像不因随附而重新授予本项目代码的 MIT 许可。文档截图来自使用者授权展示的实际运行界面和历史收发，真实会话名称、成员头像、昵称及密钥信息已遮挡；原图与个人素材库不公开分发。

| 随附内容 | 来源及许可证 |
| --- | --- |
| 大肥鱼气泡、角色情绪和人格 | [上游固定提交](https://github.com/whyever77/dafeiyu-astrbot/tree/fef1e99d20381423145455b278488d951ec1de1e)。保留 [原 MIT 许可证](xiaomao/_vendor/dafeiyu/LICENSE) 与 [改动及散列记录](xiaomao/_vendor/dafeiyu/PROVENANCE.md)。部分历史人设的版权声明见 [鲸鱼娘人设许可](docs/third-party/whale-persona-MIT.txt)。 |
| 只读 WCDB 配置扫描适配 | [wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)，版权及 MIT 全文见 [wcdb-key-tool](licenses/wcdb-key-tool.txt)。 |
| OCR 模型 | 安装包从锁定的 RapidOCR wheel 取得默认三份识别权重，保留其 Apache-2.0 与 PaddleOCR 来源记录。 |
| Windows 安装包依赖 | 详见安装目录内 licenses。包括 Python 运行时、图像处理、OCR、Windows 接口与打包工具的随附版权及第三方声明。PyInstaller 引导程序使用其 GPL 例外条款。 |

ChatLuna 仅作为 YAML 字段格式的参考。公开包仅保留本项目自行实现的解析器及简短预设。安装包省略来源与再分发许可未经确认的表情集合、背景图片和 FFmpeg 可执行程序。用户添加到本地的内容由各自的权利人授权。

会话表达偏好与语言调整参考了 [HaireXe-dsh-bili-whale 的人格示例](https://github.com/HaireXe-d/HaireXe-dsh-bili-whale/blob/main/persona.example.md) 和 [private_companion](https://github.com/menglimi/astrbot_plugin_private_companion) 的按会话配置、互动状态思路。本次没有引入它们的 QQ 通信层或照搬角色背景；微信身份、消息发送与回执仍由本项目的适配层处理。

安装器简体中文翻译取自 [Inno Setup 6.7.1](https://github.com/jrsoftware/issrc/blob/is-6_7_1/Files/Languages/Unofficial/ChineseSimplified.isl)，保留 [原作者 MIT 许可](licenses/inno-chinese-MIT.txt)。

可选音频路由组件 [VB-CABLE](https://vb-audio.com/Cable/) 由 VB-Audio Software 的 Vincent Burel 和 JS-Loezic 开发。它采用 Donationware 模式，欢迎在 [VB-Audio 官网](https://shop.vb-audio.com/en/win-apps/11-vb-cable.html) 支持作者。拾光按需从官网获取原版安装包，包内作者声明和许可随原包保留。Windows 驱动由 VB-Audio 的原版安装程序安装及维护。详见 [VB-Audio 许可与分发说明](https://vb-audio.com/Services/licensing.htm)。

## 语音合成与社区音色

拾光的语音合成和社区音色功能使用由**图灵镜（Turing Mirror）**开发和维护的 [GPT-SoVITS Fabric 1.0.1](https://github.com/Turing-Mirror/GPT-SoVITS-Fabric/releases/tag/v1.0.1) 及其 [本机接口协议](https://github.com/Turing-Mirror/trm-app-protocol)。Fabric 基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)。拾光按需获取原版引擎，在后台调用本机接口，软件内的音色管理和微信语音发送由拾光实现。Fabric 所附的 [MIT 许可与版权声明](https://github.com/Turing-Mirror/GPT-SoVITS-Fabric/blob/v1.0.1/app/src-tauri/engine-payload/LICENSE) 随原版引擎保留。社区角色包与模型权重沿用各发布者的署名、来源及许可。

