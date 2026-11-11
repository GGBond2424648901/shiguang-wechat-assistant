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
  id: "backend-template-id", name: "我的每日早报", enabled: false, selectionMode: "multiple", categories: ["technology"],
  keywords: "", excludeKeywords: "", sourceDomains: "", time: "09:00", weekdays: [0, 1, 2, 3, 4, 5, 6], itemCount: 5,
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
import { NoticeToast } from "/src/components.tsx";
import "/src/styles.css";
import "/src/fixed-actions.css";
const initial = ${JSON.stringify(initial)};
const categories = ${JSON.stringify(categories)};
window.newsCalls = [];
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
        { id: "real-source", title: "已检索的公开资讯", summary: "真实结果由后端返回；此处仅用于页面边界测试。", url: "https://example.org/news/1", source: "公开来源", category: "technology", publishedAt: 0, timeLabel: "发布时间未核实" },
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
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") console.error("renderer:", message.text()); });
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
    await page.screenshot({ path: path.join(output, "empty-desktop.png") });

    await page.getByRole("textbox", { name: "订阅名称", exact: true }).fill("我的公开早报");
    await page.getByRole("button", { name: "单选", exact: true }).click();
    const game = page.getByRole("button", { name: "游戏", exact: true });
    await game.click();
    await game.click();
    assert.equal(await game.getAttribute("aria-pressed"), "true", "Clicking a chosen single category must retain its selection");
    await page.getByRole("button", { name: "多选", exact: true }).click();
    await page.getByRole("button", { name: "科技", exact: true }).click();
    await page.getByRole("button", { name: "添加目标", exact: true }).click();
    await page.getByRole("button", { name: "文件传输助手", exact: true }).click();
    await page.getByRole("button", { name: "资讯测试群", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "身份未确认", exact: true }).count(), 0);
    await page.getByRole("button", { name: "完成", exact: true }).click();
    const enabled = page.getByRole("switch", { name: "定时推送", exact: true });
    assert.equal(await enabled.isChecked(), false, "Existing accounts must never silently enable a new news subscription");
    await enabled.check();
    await page.getByRole("textbox", { name: "每天推送时间", exact: true }).fill("08:30");
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
    await page.getByRole("button", { name: "保存订阅", exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions.length === 1 && window.newsDirty === false);
    const saved = await page.evaluate(() => window.newsCalls.find(call => call.method === "news.save"));
    assert.equal(saved.params.expected, null);
    assert.equal(saved.params.subscription.name, "我的公开早报");
    assert.equal(saved.params.subscription.enabled, true);
    assert.equal(saved.params.subscription.selectionMode, "multiple");
    assert.deepEqual(saved.params.subscription.categories, ["game", "technology"]);
    assert.equal(saved.params.subscription.time, "08:30");
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
    assert.equal(calls[2].params.action, "preview");
    assert.equal(await form.locator('a[href="https://example.org/news/1"]').count(), 1);
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
    await page.waitForTimeout(1800);
    assert.equal(await page.getByRole("textbox", { name: "订阅名称", exact: true }).inputValue(), "尚未保存的名字", "Status polling must preserve a draft");
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
    await page.getByRole("button", { name: "推送记录", exact: true }).click();
    await page.locator(".news-history-item > summary").first().click();
    await page.getByText("文件传输助手", { exact: true }).last().waitFor();
    await page.getByText("资讯测试群", { exact: true }).last().waitFor();

    await page.evaluate(value => window.newsSetStatus({ ...value, mode: "paused" }), status);
    await page.getByText(/全局已暂停/).first().waitFor();
    assert.equal(await page.getByRole("button", { name: "立即推送", exact: true }).isDisabled(), true);
    await page.evaluate(value => window.newsSetStatus({ ...value, mode: "auto", readOnly: true }), status);
    await page.getByText(/当前为只读模式/).first().waitFor();
    assert.equal(await page.getByRole("textbox", { name: "订阅名称", exact: true }).isDisabled(), true);
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
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, url, screenshots: output, viewports: ["1562×1007", "860×680"], errors, geometry }));
  } finally {
    await electron?.close();
    await server.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
