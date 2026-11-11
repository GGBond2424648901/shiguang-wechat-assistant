/* Exercises the real NewsPage against an isolated desktop RPC fixture. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { _electron } = require("playwright");

const desktop = path.resolve(__dirname, "..");
const output = path.resolve(process.argv[2] || path.join(os.tmpdir(), "shiguang-news-qa"));
const categories = [
  ["game", "游戏"], ["anime", "动漫"], ["music", "音乐"], ["esports", "电竞"], ["food", "美食"], ["world", "国际"],
  ["technology", "科技"], ["ai", "人工智能"], ["digital", "数码"], ["film", "影视"], ["finance", "财经"], ["sports", "体育"],
  ["travel", "旅游"], ["life", "生活"], ["education", "教育"], ["science", "科学"], ["auto", "汽车"], ["health", "健康"],
].map(([id, label]) => ({ id, label, query: label }));
const initial = {
  id: "backend-template-id", name: "我的每日早报", enabled: false, selectionMode: "multiple", categories: ["technology"], categoryTopics: {},
  keywords: "", excludeKeywords: "", sourceDomains: "", time: "09:00", times: ["09:00"], timeRanges: [], weekdays: [0, 1, 2, 3, 4, 5, 6], itemCount: 5,
  lookbackHours: 24, style: "brief", includeLinks: true, recipients: [], targets: [{ session: "untrusted-target" }],
};
const status = {
  connected: true, readOnly: false, mode: "auto", reason: "", primary: "first", accountLabel: "测试账号", tray: false,
  revision: "first", accountScope: "account-one", stats: { pending: 0, verified: 0, unknown: 0, failed: 0, skipped: 0 },
  sessions: [
    { id: "first", name: "文件传输助手", kind: "private", bound: true, mode: "auto", calibrated: true },
    { id: "second", name: "资讯测试群", kind: "group", bound: true, mode: "auto", calibrated: true },
    { id: "unbound", name: "身份未确认", kind: "group", bound: false, mode: "auto", calibrated: true },
  ].map(row => ({ ...row, replyMode: "natural", stickerFrequency: "requested", reason: "", windowState: "", pending: 0, lastResult: null })),
};

function entry() {
  return `import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { NewsPage } from "/src/news.tsx";
import { editableSubscription, subscriptionSignature } from "/src/news-api.ts";
import { NoticeToast } from "/src/components.tsx";
import "/src/styles.css";
import "/src/fixed-actions.css";
const initial = ${JSON.stringify(initial)};
const categories = ${JSON.stringify(categories)};
window.newsCalls = [];
window.newsApi = { editableSubscription, subscriptionSignature };
window.newsSubscriptions = [];
window.newsEdition = null;
window.newsHistory = [];
window.newsPolls = 0;
window.newsConnection = { connected: true, reason: "" };
window.shiguang = {
  draft: async (page, dirty) => { window.newsDirty = dirty; },
  request: async (method, params = {}) => {
    window.newsCalls.push({ method, params: structuredClone(params) });
    if (method === "news.read") return { subscriptions: structuredClone(window.newsSubscriptions), categories, defaultSubscription: initial, accountReady: true };
    if (method === "news.save") {
      const row = { ...structuredClone(params.subscription), id: params.subscription.id || "saved-news", revision: (params.expected || 0) + 1,
        targets: params.subscription.recipients.map(session => ({ session, name: session === "first" ? "文件传输助手" : "资讯测试群", scope: "checked-" + session })) };
      window.newsSubscriptions = [...window.newsSubscriptions.filter(item => item.id !== row.id), row];
      return structuredClone(row);
    }
    if (method === "news.delete") { window.newsSubscriptions = window.newsSubscriptions.filter(item => item.id !== params.subscription); window.newsEdition = null; return { deleted: true }; }
    if (method === "news.state") { window.newsPolls++; return { ...window.newsConnection, edition: structuredClone(window.newsEdition), history: structuredClone(window.newsHistory) }; }
    if (method === "news.command" && params.action === "preview") {
      const snapshot = structuredClone(window.newsSubscriptions.find(item => item.id === params.subscription));
      window.newsEdition = { id: "edition-1", subscriptionId: params.subscription, snapshot, trigger: "preview", slot: "", createdAt: 1794358800,
        updatedAt: 1794358800, state: "collecting", reason: "", articles: [], parts: [], warnings: [], deliveries: [] };
      setTimeout(() => { if (window.newsEdition?.state !== "collecting") return; window.newsEdition = { ...window.newsEdition, state: "ready", articles: [
        { id: "real-source", title: "已检索的公开资讯", summary: "真实结果由后端返回；此处仅用于页面边界测试。", url: "https://example.org/news/1", source: "公开来源", category: "game", topic: "三角洲", publishedAt: 0, timeLabel: "发布时间未核实" },
        { id: "unsafe-source", title: "危险链接不会成为可点击来源", summary: "验证链接协议过滤。", url: "javascript:alert(1)", source: "无效来源", category: "game", publishedAt: 0, timeLabel: "发布时间未核实" },
      ], parts: ["测试简报"], warnings: ["部分方向暂无可靠来源"] }; }, 100);
      return { accepted: true, edition: "edition-1" };
    }
    if (method === "news.command" && params.action === "push") {
      window.newsEdition = { ...window.newsEdition, state: "partial", deliveries: [
        { session: "first", name: "文件传输助手", scope: "checked-first", state: "verified", reason: "", parts: [{ requestId: "request-1", taskId: "task-1", state: "verified", reason: "微信回执已确认" }] },
        { session: "second", name: "资讯测试群", scope: "checked-second", state: "unknown", reason: "回执待核对", parts: [{ requestId: "request-2", taskId: "task-2", state: "unknown", reason: "回执待核对" }] },
      ] };
      window.newsHistory = [structuredClone(window.newsEdition)];
      return { accepted: true, edition: "edition-1" };
    }
    if (method === "news.command" && params.action === "cancel") { window.newsEdition = { ...window.newsEdition, state: "cancelled", reason: "用户取消" }; return { accepted: true }; }
    throw new Error("未支持测试方法 " + method);
  },
};
function TestApp() {
  const [active, setActive] = useState(true);
  const [status, setStatus] = useState(${JSON.stringify(status)});
  const [notice, setNotice] = useState("");
  window.newsSetActive = setActive;
  window.newsSetStatus = setStatus;
  return <div className="shell"><aside className="rail">拾光</aside><main className="main-pane" data-page="news">
    <div className="chrome"><h1>每日资讯</h1></div><div className="routes"><div className="route">
      <NewsPage active={active} status={status} notify={(text, error) => setNotice((error ? "错误：" : "") + text)} manageSessions={() => { window.managedSessions = true; }} />
    </div></div><div className="footer">共享页脚</div>
  </main>{notice && <NoticeToast message={notice} error={notice.startsWith("错误：")} leaving={false} close={() => setNotice("")} />}</div>;
}
createRoot(document.getElementById("root")).render(<TestApp />);`;
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const { createServer } = await import("vite");
  const react = (await import("@vitejs/plugin-react")).default;
  const virtualId = path.join(desktop, "__news-entry.tsx").replaceAll("\\", "/");
  const server = await createServer({ configFile: false, root: desktop, plugins: [react(), {
    name: "news-qa-entry", resolveId(id) { if (id === "/__news-entry.tsx" || id === virtualId) return virtualId; },
    load(id) { if (id === virtualId) return entry(); },
    configureServer(server) { server.middlewares.use(async (req, res, next) => {
      if (req.url !== "/__news-test") return next();
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      res.end(await server.transformIndexHtml(req.url, '<!doctype html><html lang="zh-CN"><head><title>每日资讯页面验证</title></head><body><div id="root"></div><script type="module" src="/__news-entry.tsx"></script></body></html>'));
    }); },
  }], server: { host: "127.0.0.1", port: 0 }, logLevel: "error" });
  await server.listen();
  const url = `http://127.0.0.1:${server.httpServer.address().port}/__news-test`;
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "shiguang-news-electron-"));
  const mainFile = path.join(temp, "main.cjs");
  await fs.writeFile(mainFile, `const {app,BrowserWindow}=require("electron");
app.whenReady().then(()=>{const win=new BrowserWindow({width:1562,height:1007,webPreferences:{sandbox:true,contextIsolation:true}});win.loadURL(${JSON.stringify(url)});});`);
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  let electron;
  try {
    electron = await _electron.launch({ args: [mainFile], env, timeout: 45000 });
    const page = await electron.firstWindow();
    const errors = [];
    const consoleErrors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") { consoleErrors.push(message.text()); console.error("renderer:", message.text()); } });
    page.on("requestfailed", request => console.error("request:", request.url(), request.failure()));
    const form = page.locator(".news-page");
    try { await form.waitFor({ timeout: 6000 }); }
    catch (error) { console.error({ errors, html: await page.locator("body").innerText() }); throw error; }
    assert.equal(await form.isVisible(), true, "Daily news page must render its native controls");
    await page.getByRole("heading", { name: "订阅设置", exact: true }).waitFor();
    assert.equal(await page.title(), "每日资讯页面验证");
    assert.equal(await page.locator("vite-error-overlay").count(), 0);
    assert.equal(await form.locator(".news-category").count(), 18);
    await page.getByText("还没有生成简报", { exact: true }).waitFor();
    assert.equal(await form.locator(".news-article").count(), 0);
    assert.equal(await page.getByText("细化关注内容", { exact: true }).isVisible(), true,
      "Each chosen category must expose independent editable topic controls");
    assert.equal(await page.getByRole("button", { name: "添加固定时间", exact: true }).isVisible(), true,
      "A subscription must support several fixed daily push times");
    assert.equal(await page.getByRole("button", { name: "删除固定时间 1", exact: true }).isDisabled(), true,
      "The last schedule rule must remain until another rule is present");
    const compatibility = await page.evaluate(initial => {
      const old = { ...window.newsApi.editableSubscription(window.newsSubscriptions[0] || initial, false), time: "06:45" };
      delete old.times; delete old.timeRanges; delete old.categoryTopics;
      const migrated = window.newsApi.editableSubscription(old, false);
      const mixed = { ...old, time: "00:01", times: ["18:00", "08:30", "08:30"], timeRanges: [
        { start: "23:00", end: "01:00", intervalMinutes: 45 }, { start: "10:00", end: "12:00", intervalMinutes: 30 },
        { start: "10:00", end: "12:00", intervalMinutes: 30 },
      ] };
      const canonical = { ...old, time: "08:30", times: ["08:30", "18:00"], timeRanges: [
        { start: "10:00", end: "12:00", intervalMinutes: 30 }, { start: "23:00", end: "01:00", intervalMinutes: 45 },
      ] };
      const topicMap = { ...old, categoryTopics: { sports: [" 张雪摩托 ", "张雪摩托"], game: ["鸣潮", "三角洲"] } };
      return { migrated, mixed: window.newsApi.editableSubscription(mixed, false),
        same: window.newsApi.subscriptionSignature(mixed) === window.newsApi.subscriptionSignature(canonical),
        oldMatchesNew: window.newsApi.subscriptionSignature(old) === window.newsApi.subscriptionSignature({ ...old, times: ["06:45"], timeRanges: [] }),
        rangeOnly: window.newsApi.editableSubscription({ ...old, times: [], timeRanges: [{ start: "10:00", end: "12:00", intervalMinutes: 30 }] }, false),
        topics: window.newsApi.editableSubscription(topicMap, false).categoryTopics,
        topicsMatch: window.newsApi.subscriptionSignature(topicMap) === window.newsApi.subscriptionSignature({ ...old, categoryTopics: { game: ["三角洲", "鸣潮"], sports: ["张雪摩托"] } }),
        emptyTopicsMatch: window.newsApi.subscriptionSignature(old) === window.newsApi.subscriptionSignature({ ...old, categoryTopics: { game: [] } }) };
    }, initial);
    assert.deepEqual(compatibility.migrated.times, ["06:45"], "Old single-time subscriptions must migrate on read");
    assert.deepEqual(compatibility.migrated.timeRanges, []);
    assert.deepEqual(compatibility.migrated.categoryTopics, {}, "Missing pre-upgrade topic maps must normalize to an empty object");
    assert.deepEqual(compatibility.mixed.times, ["08:30", "18:00"]);
    assert.equal(compatibility.mixed.time, "08:30", "New schedule fields must override stale legacy time");
    assert.equal(compatibility.mixed.timeRanges.length, 2);
    assert.equal(compatibility.same, true, "Schedule order and duplicate rules must not dirty an equivalent snapshot");
    assert.equal(compatibility.oldMatchesNew, true, "Old preview snapshots must match their migrated subscription");
    assert.deepEqual(compatibility.rangeOnly.times, [], "An explicit empty fixed-time list must stay range-only");
    assert.equal(compatibility.rangeOnly.time, "10:00");
    assert.deepEqual(compatibility.topics, { game: ["三角洲", "鸣潮"], sports: ["张雪摩托"] });
    assert.equal(compatibility.topicsMatch, true, "Topic map order, surrounding whitespace and duplicates must normalize consistently");
    assert.equal(compatibility.emptyTopicsMatch, true, "Deleting the final topic must restore a broad category without a false dirty flag");
    await page.screenshot({ path: path.join(output, "empty-desktop.png") });

    await page.getByRole("textbox", { name: "订阅名称", exact: true }).fill("我的公开早报");
    await page.getByRole("button", { name: "单选", exact: true }).click();
    const game = page.getByRole("button", { name: "游戏", exact: true });
    await game.click();
    await game.click();
    assert.equal(await game.getAttribute("aria-pressed"), "true", "Clicking a chosen single category must retain its selection");
    await page.getByRole("button", { name: "多选", exact: true }).click();
    await page.getByRole("button", { name: "科技", exact: true }).click();
    await page.getByRole("button", { name: "体育", exact: true }).click();
    await page.getByRole("button", { name: "添加游戏主题", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "生成预览", exact: true }).isDisabled(), true,
      "A newly added empty topic must be completed or removed before collection");
    await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).fill("三角洲");
    await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).fill("游".repeat(61));
    assert.equal(await page.getByRole("button", { name: "生成预览", exact: true }).isDisabled(), true);
    await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).fill("三角洲");
    await page.getByRole("button", { name: "添加游戏主题", exact: true }).click();
    await page.getByRole("textbox", { name: "游戏细分主题 2", exact: true }).fill("临时游戏主题");
    await page.getByRole("textbox", { name: "游戏细分主题 2", exact: true }).fill("已编辑主题");
    await page.getByRole("button", { name: "删除游戏主题 2", exact: true }).click();
    assert.equal(await page.getByRole("textbox", { name: "游戏细分主题 2", exact: true }).count(), 0);
    await page.getByRole("button", { name: "添加游戏主题", exact: true }).click();
    await page.getByRole("textbox", { name: "游戏细分主题 2", exact: true }).fill("鸣潮");
    await page.getByRole("button", { name: "添加体育主题", exact: true }).click();
    await page.getByRole("textbox", { name: "体育细分主题 1", exact: true }).fill("张雪摩托");
    await game.click();
    assert.equal(await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).count(), 0);
    assert.equal(await page.getByRole("textbox", { name: "体育细分主题 1", exact: true }).inputValue(), "张雪摩托");
    await game.click();
    assert.equal(await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).inputValue(), "三角洲",
      "Deselecting a direction must retain its own topics for reselection");
    await page.getByRole("button", { name: "体育", exact: true }).click();
    assert.equal(await page.getByRole("textbox", { name: "体育细分主题 1", exact: true }).count(), 0);
    assert.match(await page.getByRole("status", { name: "细分主题数量", exact: true }).innerText(), /3\s*\/\s*24/);
    await form.locator(".news-category-topic-settings").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "category-topics-desktop.png") });
    await page.getByRole("button", { name: "添加目标", exact: true }).click();
    await page.getByRole("button", { name: "文件传输助手", exact: true }).click();
    await page.getByRole("button", { name: "资讯测试群", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "身份未确认", exact: true }).count(), 0);
    await page.getByRole("button", { name: "完成", exact: true }).click();
    const enabled = page.getByRole("switch", { name: "定时推送", exact: true });
    assert.equal(await enabled.isChecked(), false, "Existing accounts must never silently enable a new news subscription");
    await enabled.check();
    await page.getByRole("textbox", { name: "固定时间 1", exact: true }).fill("08:30");
    for (const [index, time] of ["12:15", "18:45", "22:00", "07:00", "06:10"].entries()) {
      await page.getByRole("button", { name: "添加固定时间", exact: true }).click();
      await page.getByRole("textbox", { name: `固定时间 ${index + 2}`, exact: true }).fill(time);
    }
    await page.getByRole("button", { name: "删除固定时间 4", exact: true }).click();
    assert.equal(await form.locator(".news-fixed-time-row").count(), 5, "Fixed-time rows must be removable independently");
    await page.getByRole("button", { name: "添加时间段", exact: true }).click();
    await page.getByRole("textbox", { name: "时间段 1 开始时间", exact: true }).fill("10:00");
    await page.getByRole("textbox", { name: "时间段 1 结束时间", exact: true }).fill("12:00");
    await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill("30");
    await page.getByRole("button", { name: "添加时间段", exact: true }).click();
    await page.getByRole("textbox", { name: "时间段 2 开始时间", exact: true }).fill("23:00");
    await page.getByRole("textbox", { name: "时间段 2 结束时间", exact: true }).fill("01:00");
    await page.getByRole("spinbutton", { name: "时间段 2 间隔分钟", exact: true }).fill("45");
    await page.getByRole("button", { name: "添加时间段", exact: true }).click();
    await page.getByRole("button", { name: "删除时间段 3", exact: true }).click();
    assert.equal(await form.locator(".news-time-range-row").count(), 2);
    assert.match(await page.getByRole("status", { name: "推送计划摘要", exact: true }).innerText(), /5 个固定时间.*2 个时间段/);
    await page.getByText(/跨午夜.*开始日/).first().waitFor();
    await page.getByRole("button", { name: "星期日", exact: true }).click();
    await page.getByRole("combobox", { name: "资讯条数", exact: true }).click();
    await page.getByRole("option", { name: "7 条", exact: true }).click();
    await page.getByRole("radio", { name: "当前人格", exact: true }).check();
    await page.getByRole("switch", { name: "附上来源链接", exact: true }).uncheck();
    await page.getByRole("button", { name: "高级筛选", exact: true }).click();
    await page.getByRole("textbox", { name: "排除关键词", exact: true }).fill("营销");
    await page.getByRole("textbox", { name: "来源域名", exact: true }).fill("example.org");
    const lookback = page.getByRole("combobox", { name: "资讯回看范围", exact: true });
    await lookback.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    await lookback.click();
    await page.getByRole("option", { name: "最近 48 小时", exact: true }).click();
    await page.getByRole("button", { name: "高级筛选", exact: true }).click();
    await form.locator(".news-schedule").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "multiple-plan-desktop.png") });
    await page.getByRole("button", { name: "保存订阅", exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions.length === 1 && window.newsDirty === false);
    const saved = await page.evaluate(() => window.newsCalls.find(call => call.method === "news.save"));
    assert.equal(saved.params.expected, null);
    assert.equal(saved.params.subscription.name, "我的公开早报");
    assert.equal(saved.params.subscription.enabled, true);
    assert.equal(saved.params.subscription.selectionMode, "multiple");
    assert.deepEqual([...saved.params.subscription.categories].sort(), ["game", "technology"]);
    assert.deepEqual(saved.params.subscription.categoryTopics, { game: ["三角洲", "鸣潮"], sports: ["张雪摩托"] },
      "Inactive direction topics must be retained in writes without becoming global keywords");
    assert.equal(saved.params.subscription.time, "06:10");
    assert.deepEqual(saved.params.subscription.times, ["06:10", "07:00", "08:30", "12:15", "18:45"]);
    assert.deepEqual(saved.params.subscription.timeRanges, [
      { start: "10:00", end: "12:00", intervalMinutes: 30 }, { start: "23:00", end: "01:00", intervalMinutes: 45 },
    ]);
    assert.deepEqual(saved.params.subscription.weekdays, [0, 1, 2, 3, 4, 5]);
    assert.equal(saved.params.subscription.itemCount, 7);
    assert.equal(saved.params.subscription.style, "persona");
    assert.equal(saved.params.subscription.includeLinks, false);
    assert.equal(saved.params.subscription.excludeKeywords, "营销");
    assert.equal(saved.params.subscription.sourceDomains, "example.org");
    assert.equal(saved.params.subscription.lookbackHours, 48);
    assert.deepEqual(saved.params.subscription.recipients, ["first", "second"]);
    assert.equal("targets" in saved.params.subscription, false, "The renderer must not submit backend identity snapshots");
    assert.equal("revision" in saved.params.subscription, false);
    assert.equal("id" in saved.params.subscription, false, "New subscriptions must not reuse the backend template ID");

    await page.getByRole("textbox", { name: "关注关键词", exact: true }).fill("公开产品发布");
    await page.getByRole("button", { name: "生成预览", exact: true }).click();
    await page.getByRole("heading", { name: "已检索的公开资讯", exact: true }).waitFor({ timeout: 10000 });
    const calls = await page.evaluate(() => window.newsCalls.filter(call => call.method === "news.save" || call.method === "news.command"));
    assert.deepEqual(calls.map(call => call.method), ["news.save", "news.save", "news.command"], "Generating a dirty draft must save before collecting");
    assert.equal(calls[1].params.expected, 1);
    assert.deepEqual(calls[1].params.subscription.categoryTopics, { game: ["三角洲", "鸣潮"], sports: ["张雪摩托"] });
    assert.deepEqual(calls[1].params.subscription.times, ["06:10", "07:00", "08:30", "12:15", "18:45"]);
    assert.deepEqual(calls[1].params.subscription.timeRanges, [
      { start: "10:00", end: "12:00", intervalMinutes: 30 }, { start: "23:00", end: "01:00", intervalMinutes: 45 },
    ], "Generating a preview must save every schedule field");
    assert.equal(calls[2].params.action, "preview");
    assert.equal(await form.locator('a[href="https://example.org/news/1"]').count(), 1);
    assert.equal(await form.locator(".news-article-category").filter({ hasText: "游戏 · 三角洲" }).count(), 1,
      "Preview provenance must show the article's own category and topic");
    assert.equal(await form.locator('a[href^="javascript:"]').count(), 0);
    await page.getByText("部分方向暂无可靠来源", { exact: true }).waitFor();
    await form.locator(".news-scroll").evaluate(element => { element.scrollTop = 0; });
    await page.screenshot({ path: path.join(output, "preview-desktop.png") });
    await page.evaluate(() => { window.newsConnection = { connected: false, reason: "后台正在切换微信账号" }; });
    await page.getByText("后台正在切换微信账号", { exact: true }).first().waitFor();
    assert.equal(await page.getByRole("button", { name: "立即推送", exact: true }).isDisabled(), true);
    await page.evaluate(() => { window.newsConnection = { connected: true, reason: "" }; });
    await page.waitForFunction(() => !document.querySelector('.news-footer .button.primary').disabled);

    await page.getByRole("textbox", { name: "订阅名称", exact: true }).fill("尚未保存的名字");
    await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).fill("三角洲前瞻");
    await page.waitForTimeout(1800);
    assert.equal(await page.getByRole("textbox", { name: "订阅名称", exact: true }).inputValue(), "尚未保存的名字", "Status polling must preserve a draft");
    assert.equal(await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).inputValue(), "三角洲前瞻", "Polling must preserve topic edits");
    await page.getByRole("textbox", { name: "固定时间 1", exact: true }).fill("");
    await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill("");
    await page.waitForTimeout(1800);
    assert.equal(await page.getByRole("textbox", { name: "固定时间 1", exact: true }).inputValue(), "", "Polling must preserve an incomplete fixed-time edit");
    assert.equal(await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).inputValue(), "", "Polling must preserve an incomplete interval edit");
    assert.equal(await page.getByRole("button", { name: "生成预览", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "立即推送", exact: true }).isDisabled(), true);
    await page.getByRole("button", { name: "新建订阅", exact: true }).click();
    await page.getByRole("button", { name: "保留草稿", exact: true }).click();
    assert.equal(await page.getByRole("textbox", { name: "订阅名称", exact: true }).inputValue(), "尚未保存的名字");
    await page.evaluate(value => window.newsSetStatus({ ...value, accountScope: "account-two" }), status);
    await page.getByText("微信账号已切换，请读取最新设置后核对", { exact: true }).first().waitFor();
    assert.equal(await form.locator(".news-article").count(), 0, "The old account preview must disappear even when the display label is unchanged");
    assert.equal(await page.getByRole("textbox", { name: "订阅名称", exact: true }).inputValue(), "尚未保存的名字");
    assert.equal(await page.getByRole("button", { name: "保存订阅", exact: true }).isDisabled(), true);
    await page.evaluate(value => window.newsSetStatus(value), status);
    await page.getByRole("button", { name: "撤销修改", exact: true }).click();
    await page.getByRole("button", { name: "立即推送", exact: true }).click();
    await page.getByText("回执待核对", { exact: true }).first().waitFor();
    await page.evaluate(() => { window.newsHistory[0] = { ...window.newsHistory[0], trigger: "schedule", scheduledAt: 1794357000 }; });
    await page.waitForTimeout(1800);
    await page.getByRole("button", { name: "推送记录", exact: true }).click();
    assert.match(await page.locator(".news-history-item > summary").first().innerText(), /11\/11 08:30/,
      "Recovered scheduled editions must show their planned slot time instead of collection creation time");
    await page.locator(".news-history-item > summary").first().click();
    await page.getByText("文件传输助手", { exact: true }).last().waitFor();
    await page.getByText("资讯测试群", { exact: true }).last().waitFor();

    await page.evaluate(value => window.newsSetStatus({ ...value, mode: "paused" }), status);
    await page.getByText(/全局已暂停/).first().waitFor();
    assert.equal(await page.getByRole("button", { name: "立即推送", exact: true }).isDisabled(), true);
    await page.evaluate(value => window.newsSetStatus({ ...value, mode: "auto", readOnly: true }), status);
    await page.getByText(/当前为只读模式/).first().waitFor();
    assert.equal(await page.getByRole("textbox", { name: "订阅名称", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("textbox", { name: "游戏细分主题 1", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "添加固定时间", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "添加时间段", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("textbox", { name: "时间段 1 开始时间", exact: true }).isDisabled(), true);
    await page.evaluate(value => window.newsSetStatus(value), status);

    await page.setViewportSize({ width: 860, height: 680 });
    await page.screenshot({ path: path.join(output, "compact.png") });
    const geometry = await page.evaluate(() => {
      const panel = document.querySelector(".news-footer").getBoundingClientRect();
      return { footerBottom: panel.bottom, height: innerHeight, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
    });
    assert.equal(geometry.overflow, false, "High zoom layout must not create horizontal document scrolling");
    assert.ok(geometry.footerBottom <= geometry.height + 1, "Actions must remain visible at high zoom");
    await page.evaluate(() => window.newsSetActive(false));
    await page.waitForTimeout(150);
    const polls = await page.evaluate(() => window.newsPolls);
    await page.waitForTimeout(1800);
    assert.equal(await page.evaluate(() => window.newsPolls), polls, "Inactive pages must stop polling");
    await page.evaluate(() => window.newsSetActive(true));
    await page.getByRole("button", { name: "删除订阅", exact: true }).click();
    await page.getByRole("button", { name: "保留订阅", exact: true }).click();
    assert.equal(await page.evaluate(() => window.newsSubscriptions.length), 1);
    await page.getByRole("button", { name: "删除订阅", exact: true }).click();
    await page.getByRole("button", { name: "确认删除", exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions.length === 0);
    const deletion = await page.evaluate(() => window.newsCalls.find(call => call.method === "news.delete"));
    assert.deepEqual(deletion.params, { subscription: "saved-news", expected: 2 });

    // Read a pre-upgrade subscription and preview without mutating private storage.
    const savesBeforeLegacy = await page.evaluate(() => window.newsCalls.filter(call => call.method === "news.save").length);
    await page.evaluate(initial => {
      const legacy = { ...initial, id: "legacy-news", revision: 4, time: "06:45", recipients: ["first"], targets: [] };
      delete legacy.times; delete legacy.timeRanges; delete legacy.categoryTopics;
      const previous = window.newsHistory[0];
      window.newsSubscriptions = [legacy];
      window.newsEdition = { ...previous, id: "legacy-preview", subscriptionId: legacy.id, snapshot: legacy, state: "ready", deliveries: [] };
      window.newsHistory = [];
    }, initial);
    await page.getByRole("button", { name: "读取最新订阅", exact: true }).click();
    await page.waitForFunction(() => document.querySelector('input[aria-label="固定时间 1"]')?.value === "06:45");
    assert.equal(await form.locator(".news-fixed-time-row").count(), 1);
    assert.equal(await form.locator(".news-time-range-row").count(), 0);
    assert.equal(await form.locator(".news-category-topic-row").count(), 0, "Old subscriptions must not gain unsolicited fine-grained topics");
    await page.waitForFunction(() => !document.querySelector('.news-footer .button.primary').disabled);
    assert.equal(await page.evaluate(() => window.newsCalls.filter(call => call.method === "news.save").length), savesBeforeLegacy,
      "Reading a legacy schedule must not save or enable the subscription");
    assert.equal(await enabled.isChecked(), false);

    // Range-only rules keep an explicit empty times list through save and preview.
    await page.getByRole("button", { name: "添加时间段", exact: true }).click();
    await page.getByRole("button", { name: "删除固定时间 1", exact: true }).click();
    assert.equal(await form.locator(".news-fixed-time-row").count(), 0);
    assert.equal(await page.getByRole("button", { name: "删除时间段 1", exact: true }).isDisabled(), true);
    await page.getByRole("textbox", { name: "时间段 1 开始时间", exact: true }).fill("21:30");
    await page.getByRole("textbox", { name: "时间段 1 结束时间", exact: true }).fill("21:30");
    assert.equal(await page.getByRole("button", { name: "生成预览", exact: true }).isDisabled(), true);
    await page.getByText(/开始和结束时间不能相同/).first().waitFor();
    await page.getByRole("textbox", { name: "时间段 1 结束时间", exact: true }).fill("23:10");
    for (const invalidInterval of ["0", "1441", "1.5"]) {
      await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill(invalidInterval);
      assert.equal(await page.getByRole("button", { name: "生成预览", exact: true }).isDisabled(), true,
        "Invalid intervals must not reach collection or backend writes");
    }
    await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill("20");
    await page.getByRole("button", { name: "保存订阅", exact: true }).click();
    await page.waitForFunction(() => window.newsDirty === false && window.newsSubscriptions[0]?.revision === 5);
    const rangeOnlySave = await page.evaluate(() => window.newsCalls.filter(call => call.method === "news.save").at(-1));
    assert.deepEqual(rangeOnlySave.params.subscription.times, []);
    assert.deepEqual(rangeOnlySave.params.subscription.timeRanges, [{ start: "21:30", end: "23:10", intervalMinutes: 20 }]);
    assert.equal(rangeOnlySave.params.subscription.time, "21:30");
    await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill("");
    await page.waitForFunction(() => window.newsDirty === true);
    const reads = await page.evaluate(() => window.newsCalls.filter(call => call.method === "news.read").length);
    await page.evaluate(() => window.newsSetActive(false));
    await page.waitForTimeout(150);
    await page.evaluate(() => window.newsSetActive(true));
    await page.waitForFunction(reads => window.newsCalls.filter(call => call.method === "news.read").length > reads, reads);
    assert.equal(await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).inputValue(), "",
      "An incomplete interval alone must remain dirty and survive configuration re-entry");
    await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill("20");
    await page.waitForFunction(() => window.newsDirty === false);
    await page.getByRole("textbox", { name: "时间段 1 结束时间", exact: true }).fill("00:10");
    await page.getByRole("spinbutton", { name: "时间段 1 间隔分钟", exact: true }).fill("45");
    await page.getByRole("button", { name: "生成预览", exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions[0]?.revision === 6 && window.newsEdition?.state === "ready");
    const rangeOnlyPreview = await page.evaluate(() => window.newsCalls.filter(call => call.method === "news.save").at(-1));
    assert.deepEqual(rangeOnlyPreview.params.subscription.times, []);
    assert.deepEqual(rangeOnlyPreview.params.subscription.timeRanges, [{ start: "21:30", end: "00:10", intervalMinutes: 45 }]);

    await page.setViewportSize({ width: 430, height: 820 });
    await form.locator(".news-schedule").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "range-only-mobile.png") });
    const mobile = await page.evaluate(() => {
      const footer = document.querySelector(".news-footer").getBoundingClientRect();
      const scroll = document.querySelector(".news-scroll");
      const settings = document.querySelector(".news-settings").getBoundingClientRect();
      return { footerBottom: footer.bottom, height: innerHeight, overflow: document.documentElement.scrollWidth > innerWidth + 1,
        scrollOverflow: scroll.scrollWidth > scroll.clientWidth + 1, fieldsVisible: [...document.querySelectorAll('.news-schedule input')].every(input => {
          const field = input.getBoundingClientRect(); return field.left >= settings.left && field.right <= settings.right;
        }) };
    });
    assert.equal(mobile.overflow, false, "Mobile schedule must not cause document scrolling sideways");
    assert.equal(mobile.scrollOverflow, false, "Mobile schedule controls must fit the scrolling content");
    assert.equal(mobile.fieldsVisible, true, "Mobile range controls must stay inside their panel");
    assert.ok(mobile.footerBottom <= mobile.height + 1, "Mobile actions must remain visible");

    await page.setViewportSize({ width: 1240, height: 860 });
    await electron.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1.5));
    await form.locator(".news-schedule").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "high-zoom.png") });
    const highZoom = await page.evaluate(() => {
      const footer = document.querySelector(".news-footer").getBoundingClientRect();
      const scroll = document.querySelector(".news-scroll");
      return { width: innerWidth, height: innerHeight, scale: devicePixelRatio, footerBottom: footer.bottom,
        overflow: document.documentElement.scrollWidth > innerWidth + 1, scrollOverflow: scroll.scrollWidth > scroll.clientWidth + 1 };
    });
    assert.equal(highZoom.overflow, false);
    assert.equal(highZoom.scrollOverflow, false);
    assert.ok(highZoom.footerBottom <= highZoom.height + 1, "150% zoom must keep actions on screen");
    await electron.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1));

    // Maximum-size rules remain editable and cannot grow beyond the backend contract.
    await page.evaluate(() => {
      window.newsSubscriptions[0] = { ...window.newsSubscriptions[0], revision: 7,
        categories: ["game", "sports", "technology", "anime"], categoryTopics: Object.fromEntries(["game", "sports", "technology", "anime"].map(category =>
          [category, Array.from({ length: category === "anime" ? 1 : category === "technology" ? 7 : 8 }, (_, index) => category + "主题" + (index + 1))])),
        times: Array.from({ length: 48 }, (_, index) => String(Math.floor(index / 2)).padStart(2, "0") + (index % 2 ? ":30" : ":00")),
        timeRanges: Array.from({ length: 8 }, (_, index) => ({ start: String(index + 1).padStart(2, "0") + ":00", end: String(index + 2).padStart(2, "0") + ":00", intervalMinutes: 30 })),
      };
    });
    await page.getByRole("button", { name: "读取最新订阅", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.news-fixed-time-row').length === 48);
    assert.equal(await page.getByRole("button", { name: "添加固定时间", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "添加时间段", exact: true }).isDisabled(), true);
    await page.getByRole("button", { name: "删除固定时间 48", exact: true }).click();
    await page.getByRole("button", { name: "删除时间段 8", exact: true }).click();
    assert.equal(await form.locator(".news-fixed-time-row").count(), 47);
    assert.equal(await form.locator(".news-time-range-row").count(), 7);
    assert.equal(await page.getByRole("button", { name: "添加固定时间", exact: true }).isDisabled(), false);
    assert.equal(await page.getByRole("button", { name: "添加时间段", exact: true }).isDisabled(), false);
    assert.equal(await page.getByRole("button", { name: "添加游戏主题", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "添加体育主题", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "添加科技主题", exact: true }).isDisabled(), true,
      "A category below eight topics must still respect the global total of 24");
    await page.getByRole("button", { name: "删除游戏主题 8", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "添加游戏主题", exact: true }).isDisabled(), false);
    assert.equal(await page.getByRole("button", { name: "添加体育主题", exact: true }).isDisabled(), true,
      "Per-category limits must still apply after a different category frees a global topic slot");
    assert.equal(await page.getByRole("button", { name: "添加科技主题", exact: true }).isDisabled(), false);
    assert.match(await page.getByRole("status", { name: "细分主题数量", exact: true }).innerText(), /23\s*\/\s*24/);
    await page.getByRole("button", { name: "添加科技主题", exact: true }).click();
    await page.getByRole("textbox", { name: "科技细分主题 8", exact: true }).fill("科技新主题");
    assert.equal(await page.getByRole("button", { name: "添加动漫主题", exact: true }).isDisabled(), true);
    await page.setViewportSize({ width: 430, height: 820 });
    await form.locator(".news-category-topic-settings").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "category-topics-mobile.png") });
    const topicGeometry = await page.evaluate(() => {
      const footer = document.querySelector(".news-footer").getBoundingClientRect();
      const scroll = document.querySelector(".news-scroll");
      return { footerBottom: footer.bottom, height: innerHeight, scrollOverflow: scroll.scrollWidth > scroll.clientWidth + 1 };
    });
    assert.equal(topicGeometry.scrollOverflow, false, "Many category topic rows must fit the mobile scroll surface");
    assert.ok(topicGeometry.footerBottom <= topicGeometry.height + 1, "Many category topics must not push footer actions off screen");
    assert.equal(await page.locator("vite-error-overlay").count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    console.log(JSON.stringify({ passed: true, url, screenshots: output, viewports: ["1562×1007", "860×680", "430×820", "150% zoom"], errors, consoleErrors, geometry, mobile, highZoom }));
  } finally {
    await electron?.close();
    await server.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
