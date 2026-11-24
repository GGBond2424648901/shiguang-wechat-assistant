/* Exercise the built Fabric interface with a synthetic desktop service in headless Chromium. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const dist = path.resolve(__dirname, "../dist");
const origin = "http://fabric-desktop.test";
const sampleId = "a".repeat(32);

function sampleWave() {
  const rate = 8_000;
  const size = rate * 2 * 2;
  const wave = Buffer.alloc(44 + size);
  wave.write("RIFF", 0);
  wave.writeUInt32LE(36 + size, 4);
  wave.write("WAVEfmt ", 8);
  wave.writeUInt32LE(16, 16);
  wave.writeUInt16LE(1, 20);
  wave.writeUInt16LE(1, 22);
  wave.writeUInt32LE(rate, 24);
  wave.writeUInt32LE(rate * 2, 28);
  wave.writeUInt16LE(2, 32);
  wave.writeUInt16LE(16, 34);
  wave.write("data", 36);
  wave.writeUInt32LE(size, 40);
  return wave;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
    page.setDefaultTimeout(5_000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    await page.route(origin + "/**", async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === "/sample.wav") {
        assert.equal(new URL(route.request().url()).searchParams.get("id"), sampleId);
        return route.fulfill({ contentType: "audio/wav", body: sampleWave() });
      }
      if (pathname === "/") {
        const html = await fs.readFile(path.join(dist, "index.html"), "utf8");
        return route.fulfill({ contentType: "text/html", body: html.replace("media-src sg-media:", "media-src sg-media: 'self'") });
      }
      await route.fulfill({ path: path.join(dist, pathname) });
    });
    await page.addInitScript(({ sampleId }) => {
      const calls = [];
      const roles = [
        { id: "tachibana", name: "橘望", version: "1.2", author: "Sample Studio", languages: ["ja", "zh"] },
        { id: "hifumi", name: "阿慈谷日富美（中文）", version: "1.0", author: "Sample Studio", languages: ["zh"] },
      ];
      const setup = { supported: true, version: "1.0.1", installations: ["X:/Fabric"], installation: "X:/Fabric",
        modelPath: "X:/Fabric/Models", roleId: "tachibana", defaultModelPath: "X:/Fabric/Models",
        defaultInstallPath: "X:/Fabric", backend: "cuda" };
      const pack = { id: "tachibana-pack", release: "tachibana-12", name: "橘望", series: "", group: "", author: "Sample Studio",
        authorUrl: "https://publisher.example/author", family: "V5 Turbo", version: "1.2", size: 1_000_000,
        date: "2026-11-22", license: "Sample License", licenseUrl: "https://publisher.example/license",
        coverUrl: "", sourceUrl: "https://publisher.example/voice", description: "", staged: false,
        installed: true, roleId: "tachibana", updateAvailable: false };
      const library = () => ({ roles: roles.slice(), roleId: setup.roleId, modelPath: setup.modelPath });
      const assets = { engine_core_ready: false, engine_core_packs: [], gsv_cpp_ready: false, gsv_cpp_size_bytes: 100_000_000 };
      let job = { id: "previous-resource", state: "done", kind: "assets", name: "基础合成模型",
        phase: "download", done: 100, total: 100, message: "上次的资源下载已完成", cancelable: false };
      const references = [
        { id: "0:1", text: "今天过得怎么样？", language: "zh", preferred: true, recommended: true },
        { id: "1:1", text: "おはよう", language: "ja", preferred: false, recommended: false },
      ];
      const originalAudio = window.Audio;
      const source = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src");
      const setAttribute = HTMLMediaElement.prototype.setAttribute;
      // Chromium lacks Electron's custom voice protocol. Serve the fixture WAV over the test origin.
      const sampleUrl = value => value.replace("sg-media://voice/", "/sample.wav?id=");
      HTMLMediaElement.prototype.setAttribute = function (name, value) {
        return setAttribute.call(this, name, name === "src" ? sampleUrl(value) : value);
      };
      const test = window.fabricFixture = { calls };
      window.Audio = function (url) {
        const audio = new originalAudio();
        Object.defineProperty(audio, "src", {
          get: () => source.get.call(audio),
          set: value => source.set.call(audio, sampleUrl(value)),
        });
        if (url) audio.src = url;
        test.audio = audio;
        return audio;
      };
      test.finishAsset = () => {
        assets.gsv_cpp_ready = true;
        job = { ...job, state: "done", done: 100, message: "Fabric 操作已完成", cancelable: false };
      };
      window.shiguang = {
        watch: () => () => {}, windowState: () => () => {}, draft: async () => {}, zoom: async () => {},
        appInfo: async () => ({ readOnly: false, connected: true, hasWallpaper: false, maximized: false }),
        request: async (method, params) => {
          calls.push({ method, params });
          switch (method) {
            case "view.status": return { connected: true, readOnly: false, mode: "observe", reason: "",
              primary: "", accountLabel: "", sessions: [], tray: false, revision: "1",
              stats: { pending: 0, verified: 0, unknown: 0, failed: 0, skipped: 0 } };
            case "view.queue": return { tasks: [], receipts: [], total: 0, receiptTotal: 0, note: "" };
            case "appearance.read": return { transparency: 21, reduceMotion: true, zoomPercent: 100, revision: null };
            case "sessions.read": return { items: [{ id: "fixture", name: "试听会话", kind: "private", bound: true,
              paused: false, calibrated: true, voiceEnabled: true, voiceRoleId: "tachibana" }], monitorAll: false,
              stickersEnabled: true, webSearchEnabled: false, defaultReplyMode: "natural", defaultStickerFrequency: "natural",
              accountLabel: "示例账号", accountKey: "fixture", revision: "1" };
            case "fabric.setup": return { ...setup };
            case "fabric.catalog": return { items: [{ ...pack }], source: "fixture", warning: "" };
            case "fabric.roles": return library();
            case "fabric.selectRole":
              setup.roleId = params.roleId;
              return { roleId: setup.roleId };
            case "fabric.references": return params.roleId === "tachibana" ? references : [];
            case "fabric.playReference": return { id: sampleId };
            case "fabric.saveReference":
              references.forEach(item => { item.preferred = item.id === params.reference; });
              return { reference: params.reference };
            case "fabric.preview":
              job = { id: "synthesized", roleId: params.roleId, state: "done", kind: "preview", name: "橘望",
                phase: "synthesis", done: 0, total: 0, message: "试听已生成", cancelable: false, audio: "sg-media://voice/" + sampleId };
              return job;
            case "fabric.uninstall": {
              const index = roles.findIndex(role => role.id === params.roleId);
              if (index < 0) throw new Error("Wrong role to uninstall");
              roles.splice(index, 1);
              if (setup.roleId === params.roleId) setup.roleId = "";
              if (pack.roleId === params.roleId) {
                pack.installed = false;
                pack.roleId = "";
              }
              if (job?.roleId === params.roleId) job = null;
              if (test.failAfterUninstall) throw new Error("音色已卸载，未能保存音色设置");
              return library();
            }
            case "fabric.status": return job;
            case "fabric.engine": return { ...setup, runtime: { installed_variant: "nvidia", recommended_variant: "nvidia",
              busy: false, variants: [] }, assets: { ...assets } };
            case "fabric.assets":
              job = { id: params.kind, state: "running", kind: "assets", name: "Vulkan 推理资源",
                phase: "download", done: 40, total: 100, message: "正在下载", cancelable: true };
              if (params.kind === "base") {
                job = { ...job, state: "failed", name: "基础合成模型", message: "模型下载失败", cancelable: false };
              }
              return job;
            case "fabric.cancel":
              job = { ...job, state: "cancelled", message: "已取消下载", cancelable: false };
              return job;
            default: throw new Error("Unexpected fixture method: " + method);
          }
        },
      };
    }, { sampleId });
    await page.goto(origin);
    await page.getByRole("button", { name: "语音与音色", exact: true }).click();
    const fabric = page.locator(".fabric-page");
    await fabric.getByText(/图灵镜（Turing Mirror）的 GPT-SoVITS Fabric/).waitFor();
    await fabric.getByText("Sample Studio", { exact: true }).waitFor();
    await fabric.getByText("Sample License", { exact: true }).waitFor();
    await fabric.getByText("来源 https://publisher.example/voice", { exact: true }).waitFor();
    assert.equal(await fabric.getByRole("link").count(), 0);
    await page.locator(".fabric-card").getByRole("heading", { name: "橘望", exact: true }).waitFor();
    const previous = page.getByRole("status").filter({ hasText: "上次的资源下载已完成" });
    await previous.waitFor();
    await previous.waitFor({ state: "detached", timeout: 6_000 });
    await page.getByRole("button", { name: "工作台", exact: true }).click();
    await page.getByRole("button", { name: "语音与音色", exact: true }).click();
    await page.waitForFunction(() => window.fabricFixture.calls.filter(call => call.method === "fabric.status").length > 1);
    assert.equal(await previous.count(), 0);
    await page.getByRole("button", { name: "本地音色", exact: true }).click();
    assert.equal(await fabric.getByRole("button", { name: /打开 Fabric/ }).count(), 0);
    const tachibana = page.locator(".fabric-role").filter({ hasText: "橘望" });
    await tachibana.waitFor();
    assert.equal(await tachibana.getByRole("heading").textContent(), "橘望（中文/日语）");
    assert.match(await tachibana.textContent(), /Sample Studio · 1\.2/);
    const hifumi = page.locator(".fabric-role").filter({ hasText: "阿慈谷日富美" });
    assert.equal(await hifumi.getByRole("heading").textContent(), "阿慈谷日富美（中文）");
    await tachibana.getByRole("button", { name: "试听", exact: true }).click();
    const reference = page.getByRole("combobox", { name: "参考音频对应文本", exact: true });
    await reference.click();
    await page.getByRole("option", { name: "日语 · おはよう", exact: true }).click();
    await page.waitForFunction(() => window.fabricFixture.audio?.currentTime > 0);
    assert.deepEqual(await page.evaluate(() => window.fabricFixture.calls.filter(
      item => /fabric\.(playReference|saveReference|preview)$/.test(item.method))),
    [{ method: "fabric.playReference", params: { roleId: "tachibana", reference: "1:1" } }]);
    await page.getByRole("button", { name: "停止播放", exact: true }).click();
    await page.waitForFunction(() => window.fabricFixture.audio.paused);
    await page.getByRole("button", { name: "播放参考音频", exact: true }).click();
    await page.waitForFunction(() => window.fabricFixture.audio?.currentTime > 0);

    const savedReference = page.getByRole("button", { name: "保存默认参考音频", exact: true });
    await savedReference.click();
    await page.waitForFunction(() => [...document.querySelectorAll(".fabric-preview button")]
      .find(button => button.textContent === "保存默认参考音频")?.disabled);
    await page.getByRole("textbox", { name: "合成文本", exact: true }).fill("试听这句话。");
    await page.getByRole("button", { name: "生成试听", exact: true }).click();
    const synthesized = page.getByLabel("合成试听", { exact: true });
    await synthesized.waitFor();
    await page.waitForFunction(() => document.querySelector('audio[aria-label="合成试听"]')?.duration > 0);
    assert.deepEqual(await page.evaluate(() => window.fabricFixture.calls.filter(
      item => /fabric\.(saveReference|preview)$/.test(item.method))),
    [
      { method: "fabric.saveReference", params: { roleId: "tachibana", reference: "1:1" } },
      { method: "fabric.preview", params: { roleId: "tachibana", reference: "1:1", text: "试听这句话。", backend: "cuda" } },
    ]);
    await page.getByRole("button", { name: "播放参考音频", exact: true }).click();
    await page.waitForFunction(() => window.fabricFixture.audio?.currentTime > 0);

    const uninstall = tachibana.getByRole("button", { name: /卸载/ });
    await uninstall.click();
    const dialog = page.getByRole("dialog", { name: "卸载“橘望（中文/日语）”？" });
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => window.fabricFixture.calls.some(call => call.method === "fabric.uninstall")), false);
    await uninstall.click();
    await dialog.getByRole("button", { name: "卸载", exact: true }).click();
    await tachibana.waitFor({ state: "detached" });
    await page.locator(".fabric-preview").waitFor({ state: "detached" });
    await synthesized.waitFor({ state: "detached" });
    await page.waitForFunction(() => window.fabricFixture.audio.paused);
    await page.getByRole("button", { name: "社区音色", exact: true }).click();
    await page.locator(".fabric-card").getByRole("button", { name: "下载", exact: true }).waitFor();

    await page.getByRole("button", { name: "引擎与资源", exact: true }).click();
    assert.equal(await fabric.getByRole("button", { name: /打开 Fabric/ }).count(), 0);
    const asset = page.locator(".fabric-resource").filter({ hasText: "Vulkan 推理资源" });
    const download = asset.getByRole("button", { name: "下载并安装", exact: true });
    const progress = page.getByRole("progressbar", { name: "Fabric 任务进度" });
    await download.click();
    await progress.waitFor();
    await page.getByRole("button", { name: "取消下载", exact: true }).click();
    await page.getByRole("status").filter({ hasText: "已取消下载" }).waitFor();
    await download.click();
    await progress.waitFor();
    await page.evaluate(() => window.fabricFixture.finishAsset());
    const complete = page.getByRole("status").filter({ hasText: "Fabric 操作已完成" });
    await complete.waitFor();
    await asset.getByRole("button", { name: "已就绪", exact: true }).waitFor();
    await complete.waitFor({ state: "detached", timeout: 6_000 });
    await page.getByRole("button", { name: "本地音色", exact: true }).click();
    await page.getByRole("button", { name: "引擎与资源", exact: true }).click();
    assert.equal(await complete.count(), 0);
    await asset.getByRole("button", { name: "已就绪", exact: true }).waitFor();
    await page.locator(".fabric-resource").filter({ hasText: "基础合成模型" })
      .getByRole("button", { name: "下载并安装", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "模型下载失败" }).waitFor();
    await page.getByRole("button", { name: "本地音色", exact: true }).click();
    await hifumi.getByRole("button", { name: "设为默认", exact: true }).click();
    await hifumi.getByRole("button", { name: "当前音色", exact: true }).waitFor();
    await page.getByRole("button", { name: "会话", exact: true }).click();
    const sessionVoice = page.getByRole("combobox", { name: "会话音色", exact: true });
    await sessionVoice.getByText("原音色暂不可用 · 将使用默认音色", { exact: true }).waitFor();
    await sessionVoice.click();
    await page.getByRole("option", { name: "默认音色 · 阿慈谷日富美（中文）", exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "保存当前会话", exact: true }).isDisabled(), true);

    await sessionVoice.press("Escape");
    await page.getByRole("button", { name: "语音与音色", exact: true }).click();
    await page.evaluate(() => { window.fabricFixture.failAfterUninstall = true; });
    await hifumi.getByRole("button", { name: /卸载/ }).click();
    await page.getByRole("dialog", { name: "卸载“阿慈谷日富美（中文）”？" })
      .getByRole("button", { name: "卸载", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "音色已卸载，未能保存音色设置" }).waitFor();
    await hifumi.waitFor({ state: "detached" });
    assert.deepEqual(errors, []);
    console.log("Fabric desktop: localized labels, reference playback and explicit save/synthesis, confirmed uninstall and state reconciliation, progress/cancellation, transient success and accessible failure.");
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
