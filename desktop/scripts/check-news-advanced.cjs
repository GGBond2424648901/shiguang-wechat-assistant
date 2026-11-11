/* Exercises the real NewsPage against an isolated desktop RPC fixture. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { _electron } = require("playwright");

const desktop = path.resolve(__dirname, "..");
const output = path.resolve(process.argv[2] || path.join(os.tmpdir(), "shiguang-news-advanced-qa"));
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
window.newsPolls = 0; window.newsFeedback = [];
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
    if (method === "news.state") { window.newsPolls++; return { ...window.newsConnection, edition: structuredClone(window.newsEdition), history: structuredClone(window.newsHistory), feedback: structuredClone(window.newsFeedback) }; }
    if (method === "news.command" && params.action === "preview") {
      const snapshot = structuredClone(window.newsSubscriptions.find(item => item.id === params.subscription));
      window.newsEdition = { id: "edition-1", subscriptionId: params.subscription, snapshot, trigger: "preview", slot: "", createdAt: 1794358800,
        updatedAt: 1794358800, state: "collecting", reason: "", articles: [], parts: [], warnings: [], deliveries: [] };
      setTimeout(() => { if (window.newsEdition?.state !== "collecting") return; window.newsEdition = { ...window.newsEdition, state: "ready", articles: [
        { id: "real-source", title: "已检索的公开资讯", summary: "真实结果由后端返回；此处仅用于页面边界测试。", url: "https://example.org/news/1", source: "公开来源", category: "game", topic: "三角洲", sourceType: "feed", collectedVia: ["search", "feed"], sources: [
          { url: "https://example.org/news/1", source: "example.org", title: "原始来源" },
          { url: "https://repost.example.org/news/1", source: "repost.example.org", title: "同事件转载" }
        ], merge: { confidence: "high", reason: "标题、正文摘录和数字一致" }, publishedAt: 0, timeLabel: "发布时间未核实" },
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
    if (method === "news.feedback") {
      const found = window.newsEdition.articles.find(row => row.url === params.article.url);
      const target = params.scope === "article" ? found.url : params.scope === "source" ? found.source : found.category + "\\n" + found.topic;
      const row = { ...found, id: params.scope + target, scope: params.scope, value: params.value, target, updatedAt: 1794358800 };
      window.newsFeedback = [...window.newsFeedback.filter(item => item.id !== row.id), row];
      return structuredClone(row);
    }
    if (method === "news.clearFeedback") { window.newsFeedback = window.newsFeedback.filter(row => row.id !== params.feedbackId); return { cleared: true }; }
    if (method === "news.feedbackList") return { preferences: structuredClone(window.newsFeedback) };
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
    const combo = async (name, option) => {
      const trigger = page.getByRole('combobox', { name, exact: true });
      await trigger.scrollIntoViewIfNeeded();
      await trigger.focus();
      await trigger.press('ArrowDown');
      const options = await page.getByRole('listbox', { name, exact: true }).getByRole('option').allTextContents();
      const index = options.indexOf(option);
      assert.ok(index >= 0, `missing option ${option} for ${name}: ${options}`);
      await trigger.press('Home');
      for (let at = 0; at < index; at++) await trigger.press('ArrowDown');
      await trigger.press('Enter');
    };
    await page.getByRole('button', { name: '科技', exact: true }).click();
    await page.getByRole('button', { name: '游戏', exact: true }).click();
    await page.getByRole('button', { name: '添加游戏主题', exact: true }).click();
    await page.getByRole('textbox', { name: '游戏细分主题 1', exact: true }).fill('三角洲');
    await page.getByRole('button', { name: '高级筛选', exact: true }).click();
    await page.getByText('每个主题的检索条件', { exact: true }).click();
    await page.getByRole('textbox', { name: '三角洲主题别名', exact: true }).fill('Delta Force, DeltaForce');
    await page.getByRole('textbox', { name: '三角洲主题关注词', exact: true }).fill('新地图, 更新');
    await page.getByRole('textbox', { name: '三角洲主题排除词', exact: true }).fill('赛事');
    await page.getByText('来源与公共 RSS / Atom', { exact: true }).click();
    await combo('采集来源模式', '公开检索 + 订阅源');
    await page.getByRole('button', { name: '添加公共订阅源', exact: true }).click();
    await page.getByRole('textbox', { name: '订阅源 1 名称', exact: true }).fill('官方公开源');
    await page.getByRole('textbox', { name: '订阅源 1 地址', exact: true }).fill('https://news.example.org/rss.xml');
    await combo('订阅源 1 绑定方向', '游戏');
    await combo('订阅源 1 绑定主题', '三角洲');
    await page.getByRole('spinbutton', { name: '订阅源 1 优先级', exact: true }).fill('8');
    await page.getByRole('button', { name: '添加公共订阅源', exact: true }).click();
    await page.getByRole('textbox', { name: '订阅源 2 名称', exact: true }).fill('备用源');
    await page.getByRole('textbox', { name: '订阅源 2 地址', exact: true }).fill('https://backup.example.org/feed');
    await page.getByText('事件合并与偏好管理', { exact: true }).click();
    await page.getByRole('switch', { name: '保守合并同事件来源', exact: true }).check();
    await page.getByRole('button', { name: '保存订阅', exact: true }).click();
    await page.getByText('订阅已保存', { exact: true }).first().waitFor();
    const saved = await page.evaluate(() => structuredClone(window.newsSubscriptions[0]));
    assert.deepEqual(saved.topicSettings.game['三角洲'], { aliases: ['Delta Force', 'DeltaForce'], focusTerms: ['新地图', '更新'], exclusions: ['赛事'] });
    assert.equal(saved.sourceMode, 'both');
    assert.equal(saved.groupSameEvent, true);
    assert.equal(saved.feeds.length, 2);
    assert.equal(saved.feeds[0].priority, 8);
    assert.equal(saved.feeds[0].topic, '三角洲');
    assert.equal('targets' in (await page.evaluate(() => window.newsCalls.findLast(row => row.method === 'news.save').params.subscription)), false);
    // Existing selected topics remain canonical and dormant condition maps survive selection changes.
    await page.getByRole('button', { name: '游戏', exact: true }).click();
    await page.getByRole('button', { name: '科技', exact: true }).click();
    await page.getByRole('button', { name: '保存订阅', exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions[0].revision === 2);
    assert.deepEqual(await page.evaluate(() => window.newsSubscriptions[0].topicSettings.game['三角洲'].aliases), ['Delta Force', 'DeltaForce']);
    await page.getByRole('button', { name: '科技', exact: true }).click();
    await page.getByRole('button', { name: '游戏', exact: true }).click();
    await page.getByRole('switch', { name: '启用此来源', exact: true }).nth(1).uncheck();
    await page.getByRole('button', { name: '保存订阅', exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions[0].revision === 3);
    assert.equal(await page.evaluate(() => window.newsSubscriptions[0].feeds[1].enabled), false);
    await page.getByRole('button', { name: '移除来源 2', exact: true }).click();
    await page.getByRole('button', { name: '保存订阅', exact: true }).click();
    await page.waitForFunction(() => window.newsSubscriptions[0].revision === 4);
    assert.equal(await page.evaluate(() => window.newsSubscriptions[0].feeds.length), 1);
    await page.getByRole('button', { name: '生成预览', exact: true }).click();
    await page.getByRole('heading', { name: '已检索的公开资讯', exact: true }).waitFor();
    await page.getByText('关联来源 · 2 个', { exact: true }).click();
    assert.equal(await page.getByRole('link', { name: 'repost.example.org · 同事件转载', exact: true }).isVisible(), true);
    assert.equal(await page.getByText('公开检索 + RSS / Atom', { exact: true }).isVisible(), true);
    await page.locator('.news-article').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'associated-sources-desktop.png') });
    const article = page.getByRole('group', { name: '已检索的公开资讯反馈', exact: true });
    await article.getByRole('button', { name: '喜欢', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.news-preference'));
    assert.equal(await article.getByRole('button', { name: '喜欢', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.locator('.news-preference').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'saved-preferences-desktop.png') });
    await combo('修改反馈 已检索的公开资讯', '不相关 / 隐藏');
    await page.waitForFunction(() => window.newsFeedback[0].value === 'irrelevant');
    await page.getByRole('button', { name: '撤销反馈', exact: true }).click();
    await page.waitForFunction(() => window.newsFeedback.length === 0 && !document.querySelector('.news-preference'));
    await combo('已检索的公开资讯反馈范围', '这个来源 · 轻量排序');
    await article.getByRole('button', { name: '减少推荐', exact: true }).click();
    await page.waitForFunction(() => window.newsFeedback[0]?.scope === 'source');
    assert.equal(await article.getByRole('button', { name: '重复', exact: true }).count(), 0);
    await page.getByRole('button', { name: '撤销反馈', exact: true }).click();
    await page.waitForFunction(() => window.newsFeedback.length === 0 && !document.querySelector('.news-preference'));
    // External task-center navigation uses the exact same dirty guard.
    await page.evaluate(() => window.newsSubscriptions.push({ ...structuredClone(window.newsSubscriptions[0]), id: 'another-subscription', name: '另一个订阅' }));
    await page.getByRole('button', { name: '读取最新订阅', exact: true }).click();
    await page.getByRole('textbox', { name: '订阅名称', exact: true }).fill('保留此草稿');
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('shiguang:task-edit', { detail: { page: 'news', subscription: 'another-subscription' } })));
    await page.getByText('有未保存的订阅设置', { exact: true }).waitFor();
    await page.getByRole('button', { name: '保留草稿', exact: true }).click();
    assert.equal(await page.getByRole('textbox', { name: '订阅名称', exact: true }).inputValue(), '保留此草稿');
    await page.getByRole('button', { name: '撤销修改', exact: true }).click();
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('shiguang:task-edit', { detail: { page: 'news', subscription: 'another-subscription' } })));
    await page.waitForFunction(() => document.querySelector('[aria-label="订阅名称"]').value === '另一个订阅');
    await page.locator('.news-advanced').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'advanced-desktop.png') });
    await page.setViewportSize({ width: 430, height: 820 });
    await page.screenshot({ path: path.join(output, 'advanced-mobile.png') });
    const geometry = await page.evaluate(() => ({
      overflow: document.querySelector('.news-scroll').scrollWidth > document.querySelector('.news-scroll').clientWidth + 1,
      footerBottom: document.querySelector('.news-footer').getBoundingClientRect().bottom, height: innerHeight,
    }));
    assert.equal(geometry.overflow, false);
    assert.ok(geometry.footerBottom <= geometry.height + 1);
    // Read-only and account fences apply to every advanced writer.
    await page.evaluate(() => window.newsSetStatus(before => ({ ...before, readOnly: true })));
    await page.waitForFunction(() => document.querySelector('[aria-label="订阅名称"]').disabled);
    assert.equal(await page.getByRole('button', { name: '添加公共订阅源', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('switch', { name: '保守合并同事件来源', exact: true }).isDisabled(), true);
    assert.equal(await page.locator('vite-error-overlay').count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    const report = { passed: true, url, browser: 'Browser plugin not available; existing Playwright Electron harness',
      screenshots: output, viewports: ['1562x1007', '430x820'], geometry, errors, consoleErrors,
      interactions: ['topic conditions saved + inactive retention', 'feed mode/binding/priority/toggle/remove saved', 'merge metadata source links',
        'article feedback saved/edited/cleared', 'explicit source preference and duplicate scope fence', 'task-center dirty guard', 'read-only advanced controls'] };
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await electron?.close(); await server.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
