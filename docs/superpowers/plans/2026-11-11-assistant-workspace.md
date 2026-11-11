# 拾光任务中心与体验优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 完整实现已确认的十项优化，并交付经过验证的 v0.3.18 正式安装版和公开源码。

**Architecture:** 三个分离模块分别负责任务/诊断/统计/临时休息、资讯质量与订阅、隔离试聊与会话总结；根代理整合公共 RPC、任务中心、诊断和休息 UI。沿用现有账号绑定、原生发送守卫、共享外壳和持久化边界。

**Tech Stack:** Python 3.13、SQLite、httpx、React 19、TypeScript、现有 Electron、原生微信发送和回执。

**Spec:** `docs/superpowers/specs/2026-11-11-assistant-workspace-design.md`

## Global Constraints

- 版本 v0.3.18；仅新增一个导航「任务中心」，顺序在每日资讯之后；快捷键 Alt+1–8。
- 保留原背景、头像、标题无副标题、固定边距、连到底部的操作栏和圆润顺滑控件。
- 保留原多人会话、资讯计划、人格与发送节奏；所有新增字段兼容旧数据。
- 耗时任务不堵塞界面 RPC；换号、换会话、取消和过期响应均有边界检查。
- 只用已确认目标和既有队列发送，回执不明不重发；测试仅文件传输助手。
- 私人配置、人格、聊天、凭据留本机；公开人格两句；费用只估算，不限额。
- 各子任务遵守文件所有权，不改其他代理文件；公共路由只由根代理改。

## Review Focus

- 同名会话换号/换身份、后台重启与手动暂停后，不自动恢复错误对象。
- URL/事件去重不得吞掉版本、比分、日期、新进展或多元观点。
- RSS XML、超长正文、重定向和内网地址不能绕过抓取边界。
- 试聊/总结的旧响应不能覆盖新账号、会话或草稿，不能污染正式人格和记忆。
- 断网、无凭据、模型超时、缺 usage、数据不足时显示真实状态并保持界面可操作。

### Task 1: 规划、设计稿与基线

Files: 本文、设计说明、`design/v0318/`（本地证据，不公开）、QA ledger。

- [x] 核对 v0.3.17 清洁源码和基线测试，冻结十项目标及页面分配。
- [x] 生成任务中心参考，记录共享外壳几何、图片路径和必要偏差；不把设计图铺成页面。
- [x] 保存独立模块 brief 与文件所有权，逐项检查接口冲突。

### Task 2: 任务、诊断、统计与休息后端

Files: Create `xiaomao/desktop_operations.py`, supporting operations/usage/rest modules and corresponding tests. Modify only `resident.py`, `engine.py`, `client.py`, `control.py`, `model.py`, `web_search.py` as necessary. Root owns `desktop_service.py` routing.

Interfaces: `DesktopOperations(root, client, read_only=False)` exposes `overview(hours=24)`, `history(limit=80)`, `usage(days=7)`, `save_prices(prices, expected=None)`, `diagnostics(network=False)`, `rest(session, minutes)`, `cancel_rest(session)`; read result contracts are in task-2 brief. Root registers tasks.overview/history/usage, tasks.savePrices, diagnostics.run, sessions.rest/cancelRest.

- [x] 先写失败测试：真实时区计划、条件窗口、不在线、无数据、只读与账号隔离。
- [x] 实现未来计划和执行记录聚合；直接复用 news_schedule 与原关怀规则，不能重新发明调度器。
- [x] 实现本地/主动联网诊断；真实统计、单价估算和隐私字段白名单。
- [x] 实现持久休息与到期恢复守卫，覆盖手动暂停、换号、删除、重启和待发送竞争。
- [x] 专项测试通过，自查，交付报告及新增路由契约。

### Task 3: 资讯事件、主题条件、来源和反馈

Files: Own `news.py`, `news_store.py`, `news_research.py`, `news_runtime.py`, `desktop_news.py`, `desktop/src/news.tsx`, `news-api.ts`, `news.css`, new news/feed helpers and corresponding tests. Root owns shared routes/package/manifest.

Interfaces: DesktopNews existing read/save/state/command remain; add explicit source/feedback methods reported in task-3 brief. Preserve canonical legacy categoryTopics; optional per-topic settings and feed sources validated server-side. Feed retrieval reuses scoped research pipeline and per-target dedup/receipt handling.

- [x] 失败测试覆盖旧配置、新字段、逐主题生效、相似事件新进展保留、RSS/Atom/XML/URL边界、反馈撤销和隔离。
- [x] 通过原采集链路接入事件合并、主题条件、RSS 来源和偏好权重；所有预览和计划都生效。
- [x] 扩展现有资讯页，提供实际可用的编辑、反馈与管理，不新增导航；保留草稿和轮询保护。
- [x] 专项测试和前端构建通过，自查并交付报告及路由说明。

### Task 4: 人格试聊与会话总结

Files: Create `xiaomao/desktop_studio.py` and helper/tests, own `desktop/src/persona.tsx`, `companion.tsx`, new studio UI/stylesheet. Do not edit global model/resident/client/control/routes/shared CSS.

Interfaces: `DesktopStudio(root, client, read_only=False)` provides `chat(...)`, `summary(...)`, `state(job)`, `cancel(job)`, `send_summary(job)`; methods exact signatures returned before root wiring. Summary uses existing `ControlClient.queue_outbound` with stable request ID and scope guard.

- [x] 失败测试覆盖隔离试聊、草稿编译、重置、换号、取消、时间范围、记录不足、总结发送幂等与身份检查。
- [x] 有界异步真实模型任务，独立测试历史，不写正式记忆或人格。
- [x] 总结现有授权会话记录，来源范围透明；显式操作走既有队列发送。
- [x] 原人格页增加试聊、记忆与陪伴增加总结标签，完整状态与草稿保护。
- [x] 专项测试和构建通过，交付报告与路由说明。

### Task 5: 公共集成与新页面

Files: Root owns `desktop_service.py`, `desktop/src/main.tsx`, `api.ts`, `styles.css`, `fixed-actions.css`, `settings.tsx`, `sessions.tsx`, `workbench.tsx`, new `tasks.tsx/tasks.css/diagnostics.tsx`, desktop QA scripts, packaging/version/docs.

- [x] 按参考稿实现任务中心原生 DOM，接入 overview/history/usage，具备筛选、刷新、空态、失败态及返回原页面编辑。
- [x] 运行设置增加诊断标签；会话/工作台接同一休息状态，倒计时真实更新。
- [x] 接入所有受限 RPC，更新导航和快捷键，修正只读/离开清理/过期响应，前端构建和实际交互检查。
- [x] 各独立模块任务审查；修复后完整测试，所有可见控件有实际行为。

### Task 6: 正式版验证与发布

- [x] 真实 Electron：8 导航、4 档缩放、保留旧页功能、模型/网络故障、RSS、试聊、总结与临时休息，保存布局和设计对比证据。
- [x] 使用本机凭据进行受控真实检索、RSS采集和试聊；显式发送测试只在文件传输助手。
- [x] 全分支独立审查，完整 Python 测试、前端构建、既有性能检查和差异检查通过。
- [x] 白名单导出、双入口冻结模块对比、两句默认人格和隐私扫描；构建 v0.3.18 安装包。
- [x] 本机正式安装/启动脚本同步、个人配置保持。

发布交付：上传同批 GitHub 源码、安装包和校验值；核对远端大小、摘要、主分支及版本标签后公开 Release。详见[验收记录](2026-11-11-assistant-workspace-verification.md)。
