# 每日资讯多次推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. User has authorized autonomous implementation without intermediate questions.

**Goal:** 同一资讯订阅支持每天多个固定时刻，以及一个或多个按间隔重复的时间段，并完成回归、正式版和 GitHub 安装包发布。

**Architecture:** 复用 v0.3.16 页面、私有存储、采集与回执管线，增加独立计划编译器。每个实际时刻拥有唯一计划记录；重复规则合并、恢复只补最近一期，旧期停止继续补发但持续核对已发送回执。

**Tech Stack:** Electron / React / TypeScript / Python / SQLite，沿用当前版本。

**Spec:** 本文件中的配置与调度契约。

## Global Constraints

- 保留当前背景、页面外壳和圆润控件；只调整资讯页的推送计划模块。
- 旧版 `time` 字段和按日期记录的计划继续有效，升级不能重新发送已经执行的当天单次计划。
- 不自动启用订阅或会话；不覆盖个人配置、人格、密钥、聊天记录。公开默认人格仍为两句。
- 使用北京时间。星期应用于时间段开始日；跨午夜部分沿用开始日的星期。
- 复用既有发送队列和真实回执，普通聊天优先；暂停、删除、账号切换、重启与迟到回执的保护保留。

## 配置契约

新增 `times: string[]`（多个 HH:MM，最多 48 项）、`timeRanges: {start:string,end:string,intervalMinutes:number}[]`（最多 8 段，间隔 1..1440 分钟）。两种方式可以组合；至少保留一个时间点或时间段。

旧数据没有 times 时从 time 迁移；明确传入空 times 则表示只使用时间段。time 保留为兼容字段，规范化为第一个固定时间，或者第一个时间段开始时间。重复时刻排序去重；时间段不能首尾相同，结束时刻若落在步长上则包含该时刻。结束早于开始表示跨午夜。

用户补充：分类支持独立的细分内容。新增 `categoryTopics: { [categoryId]: string[] }`，默认 `{}`。例如 `game:["三角洲"]`、`sports:["张雪摩托"]`。每类最多 8 个公开主题、总计最多 24 个，每项 1..60 字，去重；未选择的分类保留主题但不参与检索。全局关键词仍单独存在，不能把某分类主题套到所有分类。

方向选定后，界面提供按分类添加、修改、删除主题的控件。指定细分主题的分类只检索这些主题，不另混入宽泛类别查询；返回内容核对主题相关性。资讯携带 `topic` 元数据，在预览中可辨认；多个主题轮流取结果，条数不足或来源不足时明确说明。查询有界、并发仍最多 3 个，不能静默丢弃后面的主题。

多轮采集在截取每期条数之前避开所有目标已确认或待核对的来源，后续计划优先选择未推送内容。不同目标各自的过滤和真实回执仍独立。

多个固定时刻和重叠时间段在同一分钟只触发一次。按每个实际时刻分开记录状态，使用 `YYYY-MM-DDTHH:MM` 唯一键。电脑停止期间错过多个计划时仅补最近且仍在两小时窗口内的一轮；其有效期不跨过下一轮计划。较旧自动期刊停止后续内容，已进入原生发送流程的消息继续审计回执。

## Review Focus

- 同一天第二次、第三次必须真实触发，不受旧版每天一次的唯一键阻断。
- 固定时间与时间段重叠、重复规则只触发一次。
- 跨午夜时，星期和实际日期正确；间隔不整除时不额外生成终点。
- 停机恢复或从暂停恢复不堆积补发；旧期在下一轮到达后不继续排队。
- v0.3.16 数据、旧预览和已发送日记录保持兼容，不能误发同一轮。

## Task 1：计划编译与校验

Files: `xiaomao/news_schedule.py`、`xiaomao/news.py`、`xiaomao/news_store.py`、相应测试。

Interfaces:

- `normalize_schedule(value:dict) -> {time,times,timeRanges}`，负责新旧配置、严格字段类型和格式校验。
- `schedule_occurrences(config, start:float, end:float) -> list[float]`，返回闭区间内已去重排序的北京时间计划 timestamp，支持跨午夜和星期。
- `due_schedule(config, now:float, window=7200) -> dict|None`，只返回最新已到期计划，字段 `due,slot,legacyDay,time,deadline,nextAt`。当天已有到期但超过窗口时仍返回用于记录 missed；昨天只返回仍在窗口内的到期。deadline=min(due+window,nextAt)，没有下一轮则 due+window。
- `NewsStore.get_slot(account, subscription_id, slot) -> dict|None`，精确查询唯一计划，独立于历史展示上限。

- [x] 先写并观察失败测试，覆盖三时间点、范围端点、步长、重叠、跨日星期、边界类型、旧字段和恢复窗口。
- [x] 实现上述接口；保存规范化计划，读旧数据时不擅自修改版本或覆盖配置。

## Task 2：推送计划界面

Files: `desktop/src/news.tsx`、`news-api.ts`、`news.css`、`desktop/scripts/check-news.cjs`。

- [x] 支持添加 / 修改 / 删除多个固定时间；支持添加 / 删除时间段、开始结束和自定义分钟间隔。
- [x] 旧单时间自动显示为一条固定时间。保留星期和每期条数，动态展示计划摘要。规则可以组合。
- [x] 原生圆润控件、顺滑交互、草稿保护和字段级说明；手机宽度/高缩放不溢出。
- [x] 新旧快照签名规范化；保存和预览完整携带新字段，轮询不覆盖编辑。
- [x] 添加分类细分内容，支持自由输入、多主题及删除；切换分类保留草稿，预览显示对应主题。

## Task 3：后台与回归

Files: `xiaomao/news_runtime.py`、`tests/test_news_runtime.py`、文档与版本。

- [x] 使用 due_schedule 替换每天一次逻辑。每个时刻独立唯一键，识别旧日期键，避免升级重发。
- [x] 最近一期补推、下一期截止和旧期取消；保留全部发送意图与迟到回执审计。
- [x] 验证一天多次、重叠去重、跨日、暂停/重启、慢采集跨期和多目标隔离。
- [x] 完整测试、桌面 UI、正式安装版核对；源码、安装包及本机启动文件使用 v0.3.17。验证记录见同目录 news-multiple-plans-verification 文档，发布状态以 GitHub Release 为准。
