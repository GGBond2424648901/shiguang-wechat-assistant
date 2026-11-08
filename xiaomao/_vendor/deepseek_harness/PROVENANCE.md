# DeepSeek Harness 提示词组装接入

2026-11-07 核对官方仓库 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。本项目实际执行其公开 npm 包 `@deepseek-ai/dsh-system-prompt@0.2.2-rc.1` 的 `SystemPrompt` 与 `renderPrompt`，并使用 `@deepseek-ai/cordis@4.1.0`；不是把自写缓存函数称为 Harness。

`compile.mjs` 是本项目的窄桥接：只加载提示词注册服务，关闭 Harness 身份与动态环境注入，以 `interpolate: false` 原样注册来源，确认工具数和上下文提供者数均为零，再返回编译文本。它不启动 agent loop、SDK、模型、会话、文件工具、终端、MCP 或发送接口。原始人格、平台“小玲 / 小猫”名称与鲸鱼娘角色由本项目自己的来源决定。

Python SDK 会启动完整 `dsh` 子进程；官方 `sdk-minimal` 默认也有终端、读图和工作目录工具，并把会话保存为 JSONL。当前微信应用已有密钥加密、消息回执和加密记忆，因此保留现有模型 HTTP 通道与记忆，实际接入 Harness 的提示词组装模块。参见 [SDK 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)、[minimal profile](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/sdk-minimal/README.md)、[SystemPrompt API](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/system-prompt.md)。

精确版本与 npm 原始包 SHA-512 integrity 均在 `package-lock.json`。依赖从 `https://registry.npmjs.org` 获取，安装使用 `--ignore-scripts`，没有执行包安装脚本。此次安装为 13 包、912 文件、5,841,082 字节，不包含 Node 可执行程序。`LICENSE` 为上游仓库 MIT 原文，`THIRD_PARTY_LICENSES.txt` 合并锁定依赖的原始许可证。

## 开发环境与打包

当前开发环境把依赖放在忽略发布的 `runtime/harness-core/node_modules`，与聊天数据目录分开；包清单和锁文件保存在本目录。首次安装时将本目录的 `package.json` 和 `package-lock.json` 复制到 `runtime/harness-core`，执行：

```powershell
npm ci --prefix runtime/harness-core --ignore-scripts --no-audit --no-fund --registry https://registry.npmjs.org
```

打包必须包含 `compile.mjs`、锁定依赖与许可证，并提供兼容的 Node（此次验证为 Node v24.14.0）。打包后的依赖也可放在本目录的 `node_modules`。不要把用户导入的 `config/persona_documents.json`、`config/persona_sources` 或 `runtime` 打入公开安装包。

适配器仅在文档/预设/编译器版本变化时启动一次固定 Node 脚本，之后复用编译结果；全文只经标准输入传入，不放进命令行，不继承 API 密钥环境变量。若 Node、依赖或编译器不可用，完整来源仍由 Python 原样拼接，`ChatModel.prompt_snapshot()` 与人格设置页明确显示 `python-fallback`，不会声称 Harness 已启用。

## DeepSeek 服务端缓存

[官方 Context Caching 文档](https://api-docs.deepseek.com/guides/kv_cache/)说明缓存由 API 服务端自动管理，当前机制匹配已持久化的完整前缀单元，共同前缀识别也可能需要多次请求。Harness 不是 API 缓存开关。

本项目把完整稳定人格放在首条 system 消息，当前会话、成员状态、记忆和素材放在其后。只有响应 `usage` 中合法且与输入 token 总数一致的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 才算缓存证据。未报告不等于零命中，也不等于已命中；`usage_snapshot()` 对“已报告请求数”单独计数，不保存聊天正文、回复或密钥。真实命中率仍由服务端回执确认。

## 来源与冲突处理

用户导入的主稿和启用文档保留完整内容，修订按 SHA-256 归档在本地。分类只影响编辑入口，不摘要替代正文；导入文档、历史和归档不进入公开构建。
运行协议、本机设置与当前明确边界优先，身份和风格由用户配置定义。默认发行版只附两句通用提示，字面引用、代码和路径保持原样。
