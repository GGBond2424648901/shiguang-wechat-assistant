# 微信保持最小化时的发送核查

本机个人微信 Windows 版 4.1.7.59 在最小化时，没有暴露足以复核目标会话、草稿和发送动作的公开控件。继续使用现有方案：最小化时后台接收、生成，微信由用户自然切回前台并满足空闲条件后，再按原流程核对并发送。此次发送尝试为 0，没有向群或文件传输助手发测试消息。

## 实机观测

11 月 5 日 19:50 和 19:52（中国标准时间）检查时，微信已经最小化，另一个应用在前台。检查没有还原窗口、切换会话、执行输入或访问剪贴板内容。复核记录只含控件元数据及画面亮度统计。[脱敏实测记录](../../design/qa/wechat-minimized-capabilities-2026-11-05.json)

| 只读检查 | 本机结果 |
| --- | --- |
| UI Automation 原始树 | 两个节点，顶层 Window 和一个 Pane。顶层只支持 Window、Transform 模式，Pane 无支持模式。没有编辑框、会话标题和可调用的发送控件。 |
| Win32 和 MSAA | 仅一个 Qt 子窗口，微信线程没有焦点 HWND 和插入符 HWND。两个窗口的 MSAA 客户区分别有一个窗口子对象和零个子对象。两者的原生对象模型查询均返回 `0x80004005`。 |
| 项目现用的 PrintWindow | 按还原尺寸 1040×728 连取两帧，两帧各有 99.11% 的像素为同一亮度。选取的标题、消息和编辑区域分别为单一亮度，不能用来确认目标或草稿。像素未落盘。 |
| 采样时的桌面状态 | 两次控件检查前后，微信保持最小化，前台句柄与剪贴板序列未变。绘图期间另有 40 次状态采样，均看到同一前台窗口，微信均为最小化。这些采样针对读取过程。 |

## 检查过的替代路径

- wx4auto 作者文档将“最小化也能发”描述为先显示窗口、等待控件树重建，然后执行输入，并说明有显示或激活后归还前台的步骤。其免键盘写入依赖 ValuePattern。本机控件树和持续最小化要求均不满足这个前提。[后台实现](https://wx4auto.lightisle.cn/docs/background/) · [版本与限制](https://wx4auto.lightisle.cn/docs/environment/)
- 已收录的 DSH 桥接在实际取得发送引擎时停用了静默补丁，其代码解释激活被跳过会导致点击和粘贴失效。底层库为唤醒微信无障碍树提供写微信进程内存的实现，未运行该库。[DSH 发送引擎](https://github.com/thawline/dsh-wechat-bridge/blob/ee9d268d93cd1bf93e7fd0b253954e6d0574f82f/bridge/wechat_bridge.py#L760-L776) · [底层辅助功能实现](https://github.com/fanyuantaier/wechatauto-replica/blob/bc2e6f2461330eb1a53996d2f2afc18416376b52/wechatauto/accessibility.py#L493-L500)
- Windows 的 PostMessage 成功代表消息入队。缺少可核验的编辑框或画面时，即使投递 Enter，也无法在提交前确认去向和正文。SendInput 写入系统输入流，接口没有指定后台窗口的参数。通知监听器提供读取和移除通知的方法，没有向其他应用提交回复的方法。未查询用户通知内容。[PostMessage](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew) · [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) · [通知监听器](https://learn.microsoft.com/en-us/uwp/api/windows.ui.notifications.management.usernotificationlistener)
- 腾讯公开的 OpenClaw 微信渠道需要单独扫码授权，目前渠道源码声明 `chatTypes: ["direct"]`。其协议发送到 iLink 用户 ID，公开实现没有给出以当前桌面个人账号向原有群聊发送的入口。[腾讯渠道源码](https://github.com/Tencent/openclaw-weixin/blob/main/src/channel.ts#L216-L231) · [官方接入与授权](https://github.com/Tencent/openclaw-weixin#quick-start) · [发送协议](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md#sendmessage)
