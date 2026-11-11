# 每日资讯实现契约

本文件为模块间接口约定。所有 JSON 字段使用 camelCase。

## 配置与目录

`xiaomao/news.py`：`CATEGORIES` 为 `[{id,label,query}]`；`default_subscription()` 返回下面配置；`validate_subscription(value)` 返回干净配置或中文 ValueError。收件人仅保存有界的会话 ID，实际身份由宿主保存时解析并写入额外 `targets` 快照，不接受网页传入的 targets。

```
id: UUID hex（新建时由后端补齐）
name: 1..40 字
enabled: false
selectionMode: multiple | single
categories: 分类 id 数组，最多 20
keywords: ""（公开关键词，最多 180 字）
excludeKeywords: ""（逗号分隔，最多 120 字）
sourceDomains: ""（逗号分隔，最多 5 个公开域名）
time: "09:00"
weekdays: [0,1,2,3,4,5,6]（周一为 0）
itemCount: 5（1..12）
lookbackHours: 24（24 / 48 / 72）
style: brief | persona
includeLinks: true
recipients: []（最多 8 个现有 session id）
```

至少选择一个分类或填写公开关键词；停用草稿允许空收件人。`revision` 由存储返回，不属于编辑配置。`targets` 是宿主解析后的 `[{session,name,scope}]`，不作为公开查询内容。

## 存储接口

`NewsStore(runtime)` 使用本机私有 SQLite 文件，锁和事务覆盖跨线程读改写；账号必须显式传入。提供：

```
list_subscriptions(account) -> [config + revision + targets]
get_subscription(account, id) -> dict | None
save_subscription(account, config, expected_revision=None) -> dict
delete_subscription(account, id, expected_revision) -> None
create_edition(account, subscription_id, snapshot, trigger, slot, now) -> dict | None
get_edition(account, id) -> dict | None
update_edition(account, id, **changes) -> dict
history(account, subscription_id, limit=20) -> [dict] newest first
seen_urls(account, recipient_scope) -> set[str]
mark_seen(account, recipient_scope, urls, now) -> None
close()
```

存储必须按账号隔离；保存用 revision 乐观锁。删除订阅会将未完成期刊取消，历史可保留。`slot` 非空时 `(account,subscription_id,slot)` 唯一，重复创建返回 None。`create_edition` 初值：`id, subscriptionId, snapshot, trigger, slot, createdAt, updatedAt, state="collecting", reason="", articles=[], parts=[], warnings=[], deliveries=[]`。update 在事务内合并，禁止复活已取消期刊。

## 联网采集接口

`NewsResearch(model)`；`collect(subscription, now, cancelled=lambda:False) -> {articles,parts,warnings}`。使用当前 model 的连接池、凭据和既有 `search_public`，不使用聊天上下文。最多 3 个并行查询。每个 article 为 `{id,title,summary,url,source,category,publishedAt,timeLabel}`；`parts` 是待发文本数组，每段 <=1000 字、总条数有界，包含简报日期和实际来源，生成的 URL 必须来自检索结果。没有可用来源返回空列表并给出原因；失败不能虚构结果。`publishedAt=0` 表示未知，timeLabel 明示“发布时间未核实”。默认简洁摘要，persona 风格只在总结环节使用当前人格。

## 宿主与页面 API

`news.read()` -> `{subscriptions,categories,defaultSubscription,accountReady}`

`news.state(subscription: string)` -> `{connected,edition: dict|null,history: [],reason: string}`。edition 除上述字段含 deliveries：`[{session,name,scope,parts:[{requestId,taskId,state,reason}],state,reason}]`。state 和 history 从后台共享持久化存储读取，不等待联网。

`news.save(subscription: object, expected: number|null)` -> 已保存配置；宿主重新解析 recipients 的有效身份和 scope。

`news.delete(subscription: string, expected: number)` -> `{deleted:true}`。

`news.command(subscription: string, action: "preview"|"push"|"cancel", edition?: string)` -> `{accepted:true,edition?:string}`。preview 仅采集；push 仅对指定且仍匹配最新设置的就绪期刊发送；cancel 取消尚未发送的内容。重复 push 幂等。

页面文件 `news.tsx / news.css / news-api.ts`，组件 `NewsPage({active,status,notify,manageSessions})`。复用 request、Button、Select、Toggle、PanelTitle、useDraftGuard，页面自身不修改 main.tsx/api.ts/共享 CSS。主协调者负责中央 RPC、导航、Resident、ControlClient/Server。

## 发送恢复约定

后台每次只给每个目标排一段，使用稳定 requestId，以数据库唯一指纹避免双发；收到 verified 后持久化下一段。普通回复优先，暂停时等待；一个目标未校准不影响其他目标。已经实际尝试且回执未知的内容不得自动重发，标记待核对。重启时未尝试的中断段可以从日志恢复；切换账号、移除监听范围、scope 改变都阻断旧计划。过了推送窗口不继续补发旧期刊。
