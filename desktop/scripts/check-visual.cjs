/* Capture real render output at the exact generated-artboard size. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { _electron } = require("playwright");
const { fixture } = require("./check-desktop.cjs");

const desktop = path.resolve(__dirname, "..");
const project = path.dirname(desktop);
const python = process.env.SHIGUANG_PYTHON || path.join(project, ".venv/Scripts/python.exe");
const destination = path.resolve(process.argv[2] || path.join(project, "build/ui-audit/reproduction/current"));
const sizes = { workbench: [1562, 1007], compact: [1120, 760], wide: [1586, 992], tall: [1487, 1058], detail: [1585, 992], guide: [1561, 1008] };

async function main() {
  await fs.mkdir(destination, { recursive: true });
  const test = fixture(destination, true);
  let app;
  try {
    await test.ready;
    const profile = path.join(destination, "profile");
    const local = process.env.SHIGUANG_QA_WALLPAPER;
    if (local) await fs.copyFile(local, path.join(profile, "runtime/ui-background.png"));
    const installed = process.env.SHIGUANG_PACKAGED;
    const executablePath = installed || process.env.SHIGUANG_ELECTRON;
    const env = { ...process.env, SHIGUANG_PYTHON: python };
    delete env.ELECTRON_RUN_AS_NODE;
    if (!installed) {
      const adapter = path.join(destination, "fixture-adapter");
      await fs.mkdir(adapter, { recursive: true });
      await fs.copyFile(path.join(project, "scripts/qa_desktop_adapter.py"), path.join(adapter, "sitecustomize.py"));
      env.SHIGUANG_UI_FIXTURE_PROFILE = profile;
      env.PYTHONPATH = [adapter, project, env.PYTHONPATH].filter(Boolean).join(path.delimiter);
    }
    app = await _electron.launch({ args: [...(installed ? [] : [desktop]), "--profile=" + profile, "--writable"],
      env, ...(executablePath ? { executablePath } : {}), timeout: 45000 });
    const page = await app.firstWindow();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    const report = { wallpaper: null, pages: {} };
    if (local) {
      await page.waitForFunction(() => document.documentElement.dataset.wallpaper === "local");
      const decoded = await page.evaluate(async () => {
        const image = new Image();
        image.src = "sg-media://wallpaper/selected";
        await image.decode();
        return [image.naturalWidth, image.naturalHeight];
      });
      const original = await fs.readFile(local);
      const digest = data => crypto.createHash("sha256").update(data).digest("hex");
      assert.deepEqual(decoded, [original.readUInt32BE(16), original.readUInt32BE(20)]);
      assert.equal(digest(original), digest(await fs.readFile(path.join(profile, "runtime/ui-background.png"))));
      report.wallpaper = { size: decoded, sha256: digest(original) };
    }
    const click = name => page.getByRole("button", { name, exact: true }).click();
    const child = async (label, check) => {
      const [popup] = await Promise.all([app.waitForEvent("window"), click(label)]);
      popup.on("pageerror", e => errors.push(e.message));
      await check(popup);
      assert.equal(page.isClosed(), false, "closing a child must leave the main window visible");
    };
    const closeChild = async (popup, label) => {
      await Promise.all([popup.waitForEvent("close"), popup.getByRole("button", { name: label, exact: true }).click().catch(error => {
        if (!popup.isClosed()) throw error;
      })]);
    };
    const chooseFiles = filePaths => app.evaluate(({ dialog }, paths) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: paths });
    }, filePaths);
    const capture = async (name, size, selectors) => {
      const [width, height] = sizes[size];
      await page.setViewportSize({ width, height });
      await page.locator(selectors.at(-1)).last().waitFor({ state: "visible" });
      await page.waitForFunction(() => document.fonts.status === "loaded");
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const image = await page.screenshot({ path: path.join(destination, name + ".png"), scale: "css", animations: "disabled", omitBackground: true });
      assert.deepEqual([image.readUInt32BE(16), image.readUInt32BE(20)], [width, height]);
      report.pages[name] = await page.evaluate(items => {
        return Object.fromEntries([".shell", ".rail", ".brand", ".nav-item", ".chrome", ".heading h1", ".routes", ".route:not([hidden])", ...items].map(selector => {
          const el = [...document.querySelectorAll(selector)].find(node => node.getClientRects().length);
          if (!el) return [selector, null];
          const {x, y, width, height} = el.getBoundingClientRect();
          return [selector, [x, y, width, height].map(Math.round)];
        }));
      }, selectors);
      assert.equal(report.pages[name][".rail"][2], 232, name + " changed the desktop rail width");
    };
    await page.getByText("这周有约拍吗？", { exact: true }).waitFor();
    await click("已确认 4");
    await page.locator(".reply-panel").getByText("@拾光 发个摸鱼表情包吧", { exact: true }).waitFor();
    assert.equal(await page.locator(".watch-row").count(), 5);
    await capture("workbench", "workbench", [".reply-panel", ".queue-panel", ".watch-panel", ".footer"]);
    await click("会话");
    await capture("sessions", "tall", [".session-list", ".session-detail"]);
    if (!installed) {
      await click("添加会话");
      await page.getByRole("heading", { name: "选择会话", exact: true }).first().waitFor();
      for (const name of ["阿云", "海边印相室", "周末城市漫步"]) {
        await page.locator(".directory-entry").filter({ hasText: name }).getByRole("checkbox").check();
      }
      await capture("directory", "wide", [".directory-page", ".directory-toolbar", ".directory-list", ".directory-selected", ".directory-footer"]);
      await click("返回会话");
      await child("重新校准", async popup => {
        await popup.getByRole("button", { name: "唤起并校准" }).click();
        await popup.getByAltText("当前微信窗口校准截图").waitFor();
        assert.equal(await popup.locator(".region").count(), 4);
        await popup.screenshot({ path: path.join(destination, "calibration.png"), scale: "css", animations: "disabled", omitBackground: true });
        const region = popup.getByRole("spinbutton", { name: "左", exact: true });
        await region.fill("245");
        await popup.getByRole("button", { name: "撤销调整" }).click();
        assert.equal(await region.inputValue(), "239");
        await closeChild(popup, "关闭校准");
      });
    }
    await click("人格");
    await capture("persona", "wide", [".persona-toolbar", ".persona-list", ".persona-content"]);
    await click("记忆与陪伴");
    await page.getByText("周末要是没下雨", { exact: false }).waitFor();
    await capture("companion", "wide", [".companion-selector", ".companion-tabs", ".companion-policy", ".companion-side > :first-child", ".companion-side > :last-child"]);
    await click("定时任务");
    await capture("companion-schedules", "wide", [".companion-policy", ".companion-preview"]);
    await click("会话记忆");
    await page.getByText("摄影群在约周末日落。天气好就去桥下找机位，拍完分享照片。", { exact: true }).waitFor();
    await capture("companion-memory", "wide", [".memory-metrics", ".memory-summary", ".memory-journal"]);
    const backup = path.join(destination, "示例会话备份.xlc");
    const password = "sample-backup-test";
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ canceled: false, filePath });
    }, backup);
    await child("导出加密备份", async popup => {
      await popup.getByLabel("备份口令").fill(password);
      await popup.getByRole("button", { name: "导出加密备份", exact: true }).click();
      await popup.getByText("加密备份已导出", { exact: true }).waitFor();
      assert.ok((await fs.readFile(backup)).subarray(0, 13).equals(Buffer.from("XLCOMPANION1\n")));
      await closeChild(popup, "完成");
    });
    await chooseFiles([backup]);
    await child("导入备份", async popup => {
      await popup.getByRole("button", { name: "选择 .xlc 备份文件", exact: true }).click();
      await popup.getByLabel("备份口令").fill(password);
      await popup.getByRole("button", { name: "核对备份", exact: true }).click();
      await popup.getByRole("button", { name: "确认合并", exact: true }).waitFor();
      await popup.screenshot({ path: path.join(destination, "backup.png"), scale: "css", animations: "disabled", omitBackground: true });
      await closeChild(popup, "取消");
    });
    await click("表情包");
    await page.waitForFunction(() => document.querySelector(".sticker-tile img")?.naturalWidth);
    await capture("stickers", "wide", [".sticker-page > .page-toolbar", ".gallery-panel", ".preference-panel", ".gallery-bottom"]);
    await page.locator(".sticker-tile").first().click();
    await capture("detail", "detail", [".detail-panel", ".detail-preview", ".detail-editor"]);
    await click("返回素材库");
    await click("导入与识别");
    const stickerSource = path.join(profile, "assets/sticker_library");
    await chooseFiles(["fixture-0000.gif", "fixture-0001.gif", "fixture-0003.png", "fixture-0004.png"].map(file => path.join(stickerSource, file)));
    await click("选择文件");
    await page.waitForFunction(() => Array.from(document.querySelectorAll(".import-row > small")).filter(row => row.textContent === "已有标注").length === 4);
    const importedAnimation = page.locator(".import-preview img");
    await importedAnimation.waitFor();
    assert.match(await importedAnimation.getAttribute("src"), /[?]variant=original$/);
    await page.waitForFunction(() => document.querySelector(".import-preview img")?.naturalWidth);
    await capture("import", "detail", [".import-panel", ".import-queue", ".import-editor"]);
    await click("收集说明");
    await capture("guide", "guide", [".guide-panel", ".guide-text", ".guide-section:last-child"]);
    await click("返回导入");
    assert.equal(await page.locator(".import-row input:checked").count(), 4);
    await click("确认入库");
    await page.getByText("已入库或更新 4 张", { exact: true }).waitFor();
    await click("关闭提示");
    const meaning = page.locator(".import-editor").getByRole("textbox", { name: "用途", exact: true });
    await meaning.fill("再次编辑后的用途");
    await child("关闭助手", async popup => {
      await popup.getByText("请先保存修改，或选择完全退出", { exact: true }).waitFor();
      assert.equal(await popup.getByRole("button", { name: /收起到托盘/ }).isDisabled(), true);
      await closeChild(popup, "取消");
    });
    await click("确认入库");
    await page.getByText("已入库或更新 4 张", { exact: true }).waitFor();
    await click("关闭提示");
    await click("返回素材库");
    await click("运行设置");
    await capture("settings", "wide", [".credentials-panel", ".wechat-settings", ".pacing-panel", ".settings-footer"]);
    await click("界面外观");
    await capture("appearance", "wide", [".appearance-pane", ".appearance-options", ".appearance-preview"]);
    await click("工作台");
    await capture("workbench-compact", "compact", [".reply-panel", ".queue-panel", ".watch-panel"]);
    assert.ok(await page.locator(".watch-target b").first().evaluate(name => name.clientWidth >= 90),
      "compact two-column layout clipped the conversation name");
    await page.setViewportSize({ width: 520, height: 475 });
    assert.ok(await page.locator(".routes").evaluate(route => route.scrollWidth <= route.clientWidth),
      "minimum-width layout scrolled horizontally");
    await page.setViewportSize({ width: 1562, height: 1007 });
    const [popup] = await Promise.all([app.waitForEvent("window"), click("关闭助手")]);
    await popup.getByRole("button", { name: "取消关闭" }).waitFor();
    await popup.screenshot({ path: path.join(destination, "close-dialog.png"), scale: "css", animations: "disabled", omitBackground: true });
    await Promise.all([popup.waitForEvent("close"), popup.keyboard.press("Escape")]);
    if (process.env.SHIGUANG_DEMO_FFMPEG) {
      const record = require("./record-preview.cjs");
      console.log(await record(page, destination, process.env.SHIGUANG_DEMO_FFMPEG));
    }
    const shellSignature = name => {
      const geometry = report.pages[name];
      const [routeX, routeY, routeWidth] = geometry[".route:not([hidden])"];
      const [railX, , railWidth] = geometry[".rail"];
      const [headingX, headingY] = geometry[".heading h1"];
      return [routeX - railX - railWidth, geometry[".shell"][2] - 1 - routeX - routeWidth,
        routeY, headingX, headingY];
    };
    // Packaged runs deliberately omit the development-only contacts adapter.
    for (const name of ["sessions", ...(!installed ? ["directory"] : []), "persona", "companion", "stickers", "settings"]) {
      const baseline = shellSignature("workbench");
      assert.ok(shellSignature(name).every((value, index) => Math.abs(value - baseline[index]) <= 1),
        name + " shared shell drifted " + JSON.stringify(shellSignature(name)));
    }
    assert.equal(await page.locator(".heading p").count(), 0);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(destination, "geometry.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ captures: (await fs.readdir(destination)).filter(file => file.endsWith(".png")).length,
      wallpaper: report.wallpaper, errors }));
  } finally {
    if (app) await app.close();
    test.child.stdin.end();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
