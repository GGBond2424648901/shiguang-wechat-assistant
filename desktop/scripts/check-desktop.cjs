/* Exercises the built desktop app against a separate, synthetic profile. */
const assert = require("node:assert/strict");
const { execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const { _electron } = require("playwright");

const desktop = path.resolve(__dirname, "..");
const project = path.dirname(desktop);
const python = process.env.SHIGUANG_PYTHON || path.join(project, ".venv/Scripts/python.exe");
const output = path.resolve(process.argv[2] || path.join(project, "build/ui-audit/electron"));
const percentile = (samples, p) => [...samples].sort((a, b) => a - b)[Math.ceil(samples.length * p) - 1];

function fixture(root = output, visual = false) {
  const child = spawn(python, [path.join(project, "scripts/qa_ui_baseline.py"), root, "--serve",
    ...(visual ? ["--visual"] : [])], {
    cwd: project, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONUTF8: "1" },
  });
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("离线测试配置未启动")), 45_000);
    child.stdout.on("data", (buffer) => {
      if (buffer.toString().includes("READY")) {
        clearTimeout(timer);
        resolve();
      }
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error("离线测试服务提前退出: " + code));
    });
    child.once("error", reject);
    child.stderr.on("data", (buffer) => process.stderr.write(buffer));
  });
  return { child, ready };
}

async function appMetrics(electron) {
  return electron.evaluate(({ app }) => app.getAppMetrics().reduce((sum, process) =>
    sum + (process.memory?.workingSetSize || 0) / 1024, 0));
}

async function readFrames(page, operation) {
  await page.evaluate(() => {
    window.__qaFrameTimes = [];
    window.__qaCollect = true;
    const tick = (time) => {
      window.__qaFrameTimes.push(time);
      if (window.__qaCollect) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await operation();
  return page.evaluate(() => {
    window.__qaCollect = false;
    const times = window.__qaFrameTimes;
    const gaps = times.slice(1).map((value, i) => value - times[i]);
    return { frames: times.length, fps: 1000 * (times.length - 1) / (times.at(-1) - times[0]),
      maxGapMs: Math.max(...gaps), longGaps: gaps.filter(value => value > 200) };
  });
}

async function soak(electron, page, seconds, errors) {
  const started = performance.now();
  const rows = [];
  while (true) {
    const sampled = await page.evaluate(async () => {
      const nav = [...document.querySelectorAll(".nav-item")];
      const start = performance.now();
      const status = await window.shiguang.request("view.status");
      const latency = performance.now() - start;
      const at = nav.findIndex(button => button.getAttribute("aria-current") === "page");
      const next = at === 5 ? 0 : (at + 1) % nav.length;
      nav[next].click();
      await new Promise(resolve => setTimeout(resolve, 50));
      return { connected: status.connected, latency, switched: nav[next].getAttribute("aria-current") === "page" };
    });
    const elapsed = Math.round((performance.now() - started) / 1000);
    rows.push({ elapsed, ...sampled, workingSetMiB: await appMetrics(electron) });
    await fs.writeFile(path.join(output, "soak.json"), JSON.stringify({ targetSeconds: seconds, elapsed, rows, errors }, null, 2));
    assert.ok(sampled.connected && sampled.switched && !errors.length, "soak UI or resident connection failed");
    if (elapsed >= seconds) break;
    await new Promise(resolve => setTimeout(resolve, Math.min(30, seconds - elapsed) * 1000));
  }
  assert.ok(percentile(rows.map(row => row.latency), .95) < 400, "soak status IPC is too slow");
  assert.ok(rows.at(-1).workingSetMiB <= rows[0].workingSetMiB + 50, "soak working set grew by more than 50 MiB");
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const test = fixture();
  let electron;
  try {
    await test.ready;
    if (process.env.SHIGUANG_QA_WALLPAPER) {
      await fs.copyFile(process.env.SHIGUANG_QA_WALLPAPER, path.join(output, "profile/runtime/ui-background.png"));
    }
    const env = { ...process.env, SHIGUANG_PYTHON: python };
    delete env.ELECTRON_RUN_AS_NODE;
    const start = performance.now();
    const installed = process.env.SHIGUANG_PACKAGED;
    electron = await _electron.launch({
      args: [...(installed ? [] : [desktop]), "--profile=" + path.join(output, "profile"), "--writable"],
      ...((installed || process.env.SHIGUANG_ELECTRON) ? { executablePath: installed || process.env.SHIGUANG_ELECTRON } : {}),
      env, timeout: 45_000,
    });
    const page = await electron.firstWindow();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.getByRole("heading", { name: "工作台", exact: true }).waitFor();
    await page.getByText("这周有约拍吗？", { exact: true }).waitFor();
    const firstReadyMs = performance.now() - start;
    await page.screenshot({ path: path.join(output, "workbench.png"), animations: "disabled" });

    const scope = page.getByRole("combobox", { name: "发送队列会话" });
    await scope.click();
    await scope.press("End");
    await scope.press("Enter");
    await page.getByText("当前没有待回复消息", { exact: true }).waitFor();
    assert.equal(await page.getByText("这周有约拍吗？", { exact: true }).count(), 0);
    await scope.click();
    await scope.press("Home");
    await scope.press("Enter");
    await page.getByText("这周有约拍吗？", { exact: true }).waitFor();

    await page.getByRole("button", { name: "表情包", exact: true }).click();
    await page.getByText("共 5,000 项", { exact: true }).waitFor();
    const gallery = page.getByTestId("gallery-scroll");
    await page.locator(".sticker-tile img").first().waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll(".sticker-tile img")].some(img => img.naturalWidth));
    assert.equal(await gallery.getAttribute("data-total"), "5000");
    assert.ok(await page.locator(".sticker-tile").count() <= 45, "gallery must virtualize the catalogue");
    await page.screenshot({ path: path.join(output, "stickers.png"), animations: "disabled" });
    await page.locator(".sticker-tile").first().click();
    await page.getByRole("heading", { name: "素材详情", exact: true }).waitFor();
    await page.getByText("GIF · 260 × 220 · 2 帧", { exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, "sticker-detail.png"), animations: "disabled" });
    const annotation = page.getByRole("textbox", { name: "用途", exact: true });
    await annotation.fill("需要保留的表情草稿");
    await page.getByRole("button", { name: "返回素材库", exact: true }).click();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    assert.equal(await annotation.inputValue(), "需要保留的表情草稿");
    await page.getByRole("button", { name: "返回素材库", exact: true }).click();
    await page.getByRole("button", { name: "确认", exact: true }).click();
    const search = page.getByRole("textbox", { name: "搜索表情" });
    await search.fill("0017");
    await page.getByText("共 1 项", { exact: true }).waitFor();
    await search.fill("");
    await page.getByText("共 5,000 项", { exact: true }).waitFor();
    await gallery.evaluate(element => { element.scrollTop = 17_200; });
    await page.waitForFunction(() => Array.from(document.querySelectorAll(".sticker-tile b")).some(
      element => Number(element.textContent.match(/\d+$/)?.[0]) > 200));
    await gallery.evaluate(element => { element.scrollTop = 0; });
    await page.locator(".sticker-tile").first().waitFor();

    await page.getByRole("button", { name: "运行设置", exact: true }).click();
    await page.getByRole("heading", { name: "模型与密钥", exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, "settings.png"), animations: "disabled" });
    await page.getByRole("button", { name: "添加密钥", exact: true }).click();
    await page.getByLabel("新密钥 1", { exact: true }).fill("sk-fixture-invalid-A091");
    await page.getByRole("button", { name: "增加输入栏", exact: true }).click();
    await page.getByLabel("新密钥 2", { exact: true }).fill("sk-fixture-invalid-B092");
    await page.getByRole("button", { name: "追加密钥", exact: true }).click();
    await page.getByText("已加密保存 2 个", { exact: true }).waitFor();
    const credential = await fs.readFile(path.join(output, "profile/runtime/deepseek.dpapi"));
    assert.ok(credential.length > 32 && !credential.includes("sk-fixture-invalid"));
    await page.getByRole("button", { name: "删除密钥 2", exact: true }).click();
    await page.getByRole("button", { name: "确认", exact: true }).click();
    await page.getByText("已加密保存 1 个", { exact: true }).waitFor();
    assert.ok(await page.getByRole("button", { name: "删除密钥 1", exact: true }).isDisabled());

    const nickname = page.getByRole("textbox", { name: "群内昵称", exact: true });
    await nickname.fill("第一个账号的未保存昵称");
    const rotateProfile = [
      "from dataclasses import replace",
      "from pathlib import Path",
      "from xiaomao.config import Settings",
      "import sys",
      "settings=Settings.load(Path(sys.argv[1]))",
      "rows={name:{**row,'account':'fixture-account-b'} for name,row in settings.conversation_targets.items()}",
      "replace(settings,conversation_targets=rows,self_display_name='第二个账号').save(expected=settings)",
    ].join("\n");
    execFileSync(python, ["-X", "utf8", "-c", rotateProfile, path.join(output, "profile")], { cwd: project, windowsHide: true });
    await page.getByRole("button", { name: "添加密钥", exact: true }).click();
    await page.getByLabel("新密钥 1", { exact: true }).fill("sk-fixture-invalid-C093");
    await page.getByRole("button", { name: "追加密钥", exact: true }).click();
    await page.getByText("已加密保存 2 个", { exact: true }).waitFor();
    assert.ok(await page.getByRole("button", { name: "保存设置", exact: true }).isDisabled(),
      "editing the global key list must not attach the new account's revision to an old nickname");
    assert.equal(await nickname.inputValue(), "第一个账号的未保存昵称");
    await page.getByRole("button", { name: "读取最新设置", exact: true }).click();
    await page.getByRole("button", { name: "确认", exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll(".wechat-settings .field input")].some(input => input.value === "第二个账号"));
    await page.getByRole("button", { name: "界面外观", exact: true }).click();
    const transparency = page.getByRole("slider", { name: "玻璃透明度" });
    await transparency.press("End");
    await transparency.press("ArrowLeft");
    assert.equal(await transparency.inputValue(), "47");
    assert.ok(await page.evaluate(() => Math.abs(Number(document.documentElement.style.getPropertyValue("--glass-alpha")) - .53) < .001));
    await page.getByRole("switch", { name: "减少动态效果" }).check();
    await page.getByRole("button", { name: "保存外观", exact: true }).click();
    await page.getByText("界面外观已保存", { exact: true }).waitFor();
    await page.reload();
    await page.getByRole("button", { name: "运行设置", exact: true }).click();
    await page.getByRole("button", { name: "界面外观", exact: true }).click();
    assert.equal(await transparency.inputValue(), "47");
    assert.equal(await page.getByRole("switch", { name: "减少动态效果" }).isChecked(), true);
    await page.getByRole("button", { name: "恢复默认", exact: true }).click();
    await page.getByRole("button", { name: "保存外观", exact: true }).click();
    await page.getByText("界面外观已保存", { exact: true }).waitFor();
    await page.getByRole("button", { name: "关闭提示" }).click();
    await page.screenshot({ path: path.join(output, "appearance.png"), animations: "disabled" });

    await page.getByRole("button", { name: "会话", exact: true }).click();
    await page.getByRole("heading", { name: "晨光摄影群", exact: true }).waitFor();
    await page.getByRole("button", { name: /小禾/ }).click();
    await page.getByRole("heading", { name: "小禾", exact: true }).waitFor();
    await page.getByRole("button", { name: "逐条回复", exact: true }).click();
    await page.getByText("有未保存的修改", { exact: true }).waitFor();
    await page.getByRole("button", { name: "撤销", exact: true }).click();
    await page.getByRole("button", { name: /晨光摄影群/ }).click();
    await page.screenshot({ path: path.join(output, "sessions.png"), animations: "disabled" });
    await page.evaluate(() => {
      const nav = document.querySelectorAll(".nav-item");
      nav[0].click(); nav[4].click(); nav[1].click();
    });
    assert.equal(await page.getByRole("button", { name: "会话", exact: true }).getAttribute("aria-current"), "page");

    await page.getByRole("button", { name: "人格", exact: true }).click();
    await page.getByRole("heading", { name: "人格模块", exact: true }).waitFor();
    const original = await page.getByRole("textbox", { name: "人格提示词" }).inputValue();
    assert.ok(original.length > 1000);
    await page.screenshot({ path: path.join(output, "persona.png"), animations: "disabled" });
    await page.getByRole("button", { name: /聊天示例/ }).click();
    const editor = page.getByRole("textbox", { name: "人格提示词" });
    const examples = await editor.inputValue();
    assert.ok(examples.length > 6000);
    await editor.fill(examples + "追加验收段落\n");
    await page.getByRole("button", { name: "工作台", exact: true }).click();
    await page.getByRole("button", { name: "人格", exact: true }).click();
    assert.equal(await editor.inputValue(), examples + "追加验收段落\n");
    await page.getByRole("button", { name: "模块上移" }).click();
    await page.getByRole("switch", { name: "参与回复", exact: true }).uncheck();
    await page.getByRole("button", { name: "保存并应用", exact: true }).click();
    await page.getByText("人格已保存，下一次生成回复时生效", { exact: true }).waitFor();
    const store = path.join(output, "profile/config/persona_modules.json");
    const persisted = JSON.parse(await fs.readFile(store, "utf-8"));
    assert.equal(persisted.modules[0].text, examples + "追加验收段落\n");
    assert.equal(persisted.modules[0].enabled, false);
    assert.equal(persisted.modules[1].text, original);
    await page.getByRole("button", { name: "预览生效稿", exact: true }).click();
    await page.getByRole("heading", { name: "当前生效稿", exact: true }).waitFor();
    assert.ok(!(await page.getByRole("textbox", { name: "编译原文" }).inputValue()).includes("追加验收段落"));
    await page.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("switch", { name: "参与回复", exact: true }).check();
    await page.getByRole("button", { name: "模块下移" }).click();
    await editor.fill(examples);
    await page.getByRole("button", { name: "保存并应用", exact: true }).click();
    await page.getByText("人格已保存，下一次生成回复时生效", { exact: true }).waitFor();
    await page.getByRole("button", { name: "关闭提示" }).click();
    await page.getByRole("button", { name: "表情包", exact: true }).click();
    await page.getByText("共 5,000 项", { exact: true }).waitFor();
    await page.locator(".sticker-tile").first().waitFor();

    await page.getByRole("button", { name: "记忆与陪伴", exact: true }).click();
    await page.getByRole("heading", { name: "主动聊天", exact: true }).waitFor();
    await page.getByRole("spinbutton", { name: "每日次数上限" }).waitFor();
    await page.waitForFunction(() => document.querySelector('input[aria-label="每日次数上限"]').value === "2");
    await page.screenshot({ path: path.join(output, "companion.png"), animations: "disabled" });
    await page.getByRole("button", { name: "定时任务", exact: true }).click();
    await page.getByRole("button", { name: "新建", exact: true }).click();
    await page.getByRole("textbox", { name: "关怀意图" }).waitFor();
    assert.equal(await page.locator('input[type="time"]').inputValue(), "09:00");
    await page.screenshot({ path: path.join(output, "routines.png"), animations: "disabled" });
    const [dirtyPrompt] = await Promise.all([
      electron.waitForEvent("window"),
      page.getByRole("button", { name: "关闭助手", exact: true }).click(),
    ]);
    await dirtyPrompt.getByText("请先保存修改，或选择完全退出", { exact: true }).waitFor();
    assert.equal(await dirtyPrompt.getByRole("button", { name: /收起到托盘/ }).isDisabled(), true);
    await Promise.all([dirtyPrompt.waitForEvent("close"), dirtyPrompt.keyboard.press("Escape")]);
    await page.getByRole("button", { name: "撤销修改", exact: true }).click();
    await page.getByRole("button", { name: "会话记忆", exact: true }).click();
    await page.getByRole("heading", { name: "最近会话摘要" }).waitFor();
    await page.screenshot({ path: path.join(output, "memory.png"), animations: "disabled" });
    await page.getByRole("button", { name: "表情包", exact: true }).click();

    const [owned] = await Promise.all([
      electron.waitForEvent("window"),
      page.getByRole("button", { name: "关闭助手", exact: true }).click(),
    ]);
    await owned.getByRole("button", { name: "取消关闭", exact: true }).waitFor();
    const modal = await electron.evaluate(({ BrowserWindow }) => {
      const popup = BrowserWindow.getAllWindows().find(window => window.title === "关闭助手");
      return popup && { parentVisible: popup.getParentWindow()?.isVisible(), bounds: popup.getBounds(),
        contentWidth: popup.getContentBounds().width,
        parentBounds: popup.getParentWindow()?.getBounds(), modal: popup.isModal() };
    });
    assert.ok(modal?.parentVisible && modal.modal && modal.contentWidth <= 480 &&
      modal.bounds.width < modal.parentBounds.width / 2, JSON.stringify(modal));
    await owned.screenshot({ path: path.join(output, "close-popup.png"), animations: "disabled" });
    await Promise.all([owned.waitForEvent("close"), owned.keyboard.press("Escape")]);
    assert.equal(electron.windows().length, 1);

    const memoryBefore = await appMetrics(electron);
    const samples = await page.evaluate(async () => {
      const tabs = [...document.querySelectorAll(".nav-item")];
      const results = [];
      const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
      for (let i = 0; i < 100; i += 1) {
        const button = tabs[i % 2 ? 4 : 0];
        const start = performance.now();
        button.click();
        await frame();
        await frame();
        results.push(performance.now() - start);
        if (button.getAttribute("aria-current") !== "page") throw new Error("切页没有更新选中状态");
      }
      return results;
    });
    const scroll = await readFrames(page, () => gallery.evaluate(async element => {
      const until = performance.now() + 2500;
      do {
        element.scrollTop += 41;
        await new Promise(resolve => requestAnimationFrame(resolve));
      } while (performance.now() < until);
    }));
    const resize = await readFrames(page, () => electron.evaluate(async ({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0];
      const original = win.getBounds();
      for (let index = 0; index < 110; index += 1) {
        win.setSize(960 + Math.round(150 * (1 + Math.sin(index / 16))), 690);
        await new Promise(resolve => setTimeout(resolve, 17));
      }
      win.setBounds(original);
    }));
    await gallery.evaluate(element => { element.scrollTop = 0; });
    await page.getByRole("button", { name: "工作台", exact: true }).click();
    await page.waitForTimeout(1200);
    const memoryAfter = await appMetrics(electron);
    const zoomed = [];
    const cssPages = ["工作台", "会话", "人格", "记忆与陪伴", "表情包", "运行设置"];
    for (const scale of [1, 1.25, 1.5, 2]) {
      await electron.evaluate(({ BrowserWindow }, factor) => {
        BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(factor);
      }, scale);
      for (const title of cssPages) {
        await page.getByRole("button", { name: title, exact: true }).click();
        const bounds = await page.evaluate(() => {
          const host = document.querySelector(".routes");
          const close = document.querySelector(".window-close").getBoundingClientRect();
          return { width: window.innerWidth, client: host.clientWidth, scroll: host.scrollWidth, close: close.right,
            rail: Math.round(document.querySelector(".rail").getBoundingClientRect().width) };
        });
        assert.ok(bounds.close <= bounds.width + 1, `window controls clipped at ${scale} ${title}`);
        assert.ok(bounds.scroll <= bounds.client + 3, `horizontal overflow at ${scale} ${title}: ${JSON.stringify(bounds)}`);
        assert.equal(bounds.rail, bounds.width <= 625 ? 56 : 232, "sidebar must not follow page content");
        zoomed.push({ scale, title, width: bounds.width, rail: bounds.rail });
      }
    }
    await electron.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1));
    assert.deepEqual(errors, []);
    const report = {
      firstReadyMs, navP95Ms: percentile(samples, .95), navMaxMs: Math.max(...samples),
      scroll, resize, memoryBeforeMiB: memoryBefore, memoryAfterMiB: memoryAfter,
      memoryIncreaseMiB: memoryAfter - memoryBefore,
      sampleCount: samples.length, catalogueSize: 5000, rendererErrors: errors, modal, zoomed,
      measurements: "Two-frame renderer interaction latency and animation-frame pacing. Real Electron production build, synthetic resident.",
    };
    await fs.writeFile(path.join(output, "results.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    assert.ok(report.navP95Ms <= 100, "click and warm navigation P95 must be within 100 ms");
    assert.ok(scroll.fps >= 55 && resize.fps >= 55, "scroll and resize pacing must stay above 55 frames/s");
    assert.deepEqual([...scroll.longGaps, ...resize.longGaps], []);
    assert.ok(report.memoryIncreaseMiB <= 50, "working set growth must stay within 50 MiB");
    if (process.env.SHIGUANG_DEMO_FFMPEG) {
      const recordPreview = require("./record-preview.cjs");
      console.log(await recordPreview(page, output, process.env.SHIGUANG_DEMO_FFMPEG));
    }
    if (!process.env.SHIGUANG_SOAK_SECONDS) {
      const bridgeScript = [
        "import os,psutil,sys",
        "root,profile,mode=sys.argv[1:]",
        "profile=os.path.normcase(os.path.normpath(profile))",
        "rows=[]",
        "for child in psutil.Process(int(root)).children(recursive=True):",
        "    try: args=child.cmdline()",
        "    except (psutil.NoSuchProcess,psutil.AccessDenied): continue",
        "    if any(a in ('xiaomao.desktop_bridge','desktop-bridge') for a in args) and profile in [os.path.normcase(os.path.normpath(a)) for a in args]:",
        "        rows.append(child)",
        "pids={row.pid for row in rows}",
        "parents={row.ppid() for row in rows} & pids",
        "rows=[row for row in rows if row.pid not in parents]",
        "if len(rows)!=1: sys.exit(2)",
        "print(rows[0].pid)",
        "if mode=='stop': rows[0].terminate()",
      ].join("\n");
      const bridgePid = (mode) => execFileSync(python, ["-c", bridgeScript, String(electron.process().pid),
        path.join(output, "profile"), mode], { windowsHide: true, encoding: "utf8" }).trim();
      const previous = bridgePid("stop");
      let replacement;
      const deadline = performance.now() + 12_000;
      while (performance.now() < deadline) {
        try {
          const current = bridgePid("read");
          if (current !== previous) { replacement = current; break; }
        } catch (error) {
          if (error.status !== 2) throw error;
        }
        await new Promise(resolve => setTimeout(resolve, 180));
      }
      assert.ok(replacement, "the bridge process must restart without reopening the panel");
      await page.waitForFunction(async () => {
        try { return (await window.shiguang.request("view.status")).connected; }
        catch { return false; }
      }, undefined, { timeout: 12_000 });
      await fs.writeFile(path.join(output, "bridge-reconnect.json"), JSON.stringify({
        previous: Number(previous), replacement: Number(replacement), connected: true,
      }));
    }
    const seconds = Number(process.env.SHIGUANG_SOAK_SECONDS || 0);
    if (seconds > 0) await soak(electron, page, seconds, errors);
  } finally {
    if (electron) await electron.close();
    test.child.stdin.end();
  }
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { fixture };
