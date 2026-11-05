# 大肥鱼上游来源

来源为 [dafeiyu-astrbot 固定提交](https://github.com/whyever77/dafeiyu-astrbot/tree/fef1e99d20381423145455b278488d951ec1de1e)。此目录保留原 MIT 许可证。以下 SHA-256 均按 Git blob 的 UTF-8/LF 原文计算，包含末尾换行。

| 随包文件 | 上游来源及处理 | SHA-256 |
| --- | --- | --- |
| chat_layout.py | deploy/patches/chat_layout.py，完整原文件 | 7f8b02636f18f6454178895fb039bbbef58cd0bdc2075ac301eeba7b0f3c12a4 |
| persona-v2.1.md | persona/大肥鱼-人格.md，完整原文件 | 5b1aa08e8433fd96af2e659240bfc7fbadbfb859c2d8582f783a91e7e94a7cab |
| LICENSE | LICENSE，完整原文件 | f3e798dacee6d9203fd8bcd3ca8639f3193e151ab6701a3cd6cb3c9b59bcb051 |
| emotion.py 中的 EmotionalState | deploy/patches/emotionai_v3.4_separate_analysis/main.py 第 27–62 行，包括装饰器，原样抽取 | 0e0b4de7df94b081383633569321312ce3548b843ddd3685327f5c19b7ecf64e |
| emotion.py 中的 EmotionAnalyzer | 同一文件第 297–364 行，原样抽取 | ce54e9d847f5d6448806830879c17a39aedab0ab78cafd1f441b43142595d8be |

情绪插件 main.py 完整原文件 SHA-256 为 aa1aa12c9c44a6112c87d80cd5cfd863c15636c5a44edc82f24611f2961f3322。抽取文件另加标准库导入与来源提示。运行时使用上游数据类、十二个维度和主/次情绪画像，文字气泡使用原 group_bubbles。AstrBot 插件顶层、QQ 语音与权限命令均未导入。

persona-wechat.md 是随包的微信适配默认值，config/persona.md 是可编辑副本。主体源自原版 v2.1，按当前宿主可选本地表情/GIF、静态图片、成员身份、陪伴调度和证据记忆重写相应条款。默认 YAML 仅引用人格一次。此前两份人格配置保存在 design/migration/dafeiyu-v2。

微信默认版将口吻示例改成针对不同情境的表达意图，减少逐字照搬。上游原文完整保存在 persona-v2.1.md。自定义人格及自定义 YAML 仍由原加载器读取。

稳定成员、稳定消息 ID 且启用上下文时，直接文字回复使用 provider JSON 模式。助手的历史发言按同一结构传入，当前回复仅允许 text 及可选 emotion_delta。无可追踪身份或关闭上下文的直接文字接口保留普通正文模式。自然参与和媒体选择沿用各自的 send/text/sticker 协议。代码、表格和业务 JSON 可作为 text 的内容；不完整的直接回复控制包会触发可重试的生成错误，不会作为正文投递。

微信适配在同一次回复 JSON 解析可选 emotion_delta，白名单与单步 -10/+5 上限源自上游 config.example.json。按既有会话 scope 和稳定成员分隔，只在本机 Windows 当前用户 DPAPI 内保存数值及最多 16 个已处理消息的哈希。角色状态与可追溯的用户事实采用单独文件。各维度随时间衰减；本地设计的半衰期为情绪 12 小时，好感与熟悉度 7 天。单会话上限 128 人，满额先替换最久未更新的角色状态。

记忆阶段源自同一提交的 plugin/astrbot_plugin_bili_activity/memory_policy.py（SHA-256 为 958658957ef7593abc70546c69d1bf1e2c7ad1e98060cd2a99ef97b9f074b95e）及 deploy/bili_activity/MEMORY_POLICY_V01.md。微信端将 7 天细节、60 天淡出与明确偏好和未完事项的保留顺序适配到已有的引用式 recall 与 200 条证据预算。阶段改变返回视图，原证据仍保存在加密笔记中；查询不会改写证据、合成人物经历或推断跨会话身份。

200 条满额时，微信适配先留最近 40 条来源证据（可容纳两批各 20 条的新反思），剩余 160 个位置优先留给较旧的明确偏好和未完成事项。已结案事项按其有来源的结案时间计入近期；反思和备份导入共用这个预算。查询个人资料时会从匹配当前成员原文的记录中留出最近的一条，让新更正与仍相关的旧证据一起按时间进入上下文，不按字眼猜哪一条事实应删除。
