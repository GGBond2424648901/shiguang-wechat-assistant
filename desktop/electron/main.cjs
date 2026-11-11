const { app, BrowserWindow, dialog, ipcMain, Menu, protocol, screen, shell } = require("electron");
const { publicWebUrl } = require("./public-url.cjs");
const { createHash } = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const { Broker } = require("./broker.cjs");
const { prepareTransparentWindow, isWindowFullscreen } = require("./window-shape.cjs");

const packaged = app.isPackaged && !process.defaultApp;
const project = packaged ? path.resolve(path.dirname(process.execPath), "..") : path.resolve(__dirname, "../..");
const flag = (name) => process.argv.find((value) => value.startsWith("--" + name + "="))?.slice(name.length + 3);
const defaultProfile = packaged ? path.join(process.env.LOCALAPPDATA || path.resolve(app.getPath("appData"), "../Local"), "XiaomaoAssistant") : project;
const profile = path.resolve(flag("profile") || defaultProfile);
const managed = process.argv.includes("--managed") || packaged;
const wallpaper = path.resolve(!managed && flag("wallpaper") || path.join(profile, "runtime", "ui-background.png"));
const readOnly = !managed && !process.argv.includes("--writable");
const browserProfile = createHash("sha256").update(profile.toLowerCase() + (readOnly ? ":preview" : "")).digest("hex").slice(0, 20);
const bundle = path.join(__dirname, "../dist/index.html");
const icon = packaged ? path.join(__dirname, "../icon.ico") : path.join(project, "xiaomao/ui_assets/app.ico");
const title = "拾光微信聊天助手";

app.setAppUserModelId("local.shiguang.assistant");
app.setPath("userData", path.join(app.getPath("appData"), "ShiguangDesktop", browserProfile));
protocol.registerSchemesAsPrivileged([{ scheme: "sg-media", privileges: { standard: true, secure: true } }]);

let main, prompt, broker, timer, status, signature, stopping = false, quitting = false, released = false;
let statusBusy = false, writes = 0, painted = false, retryBridge;
let failures = [];
const drafts = new Set();
const draftPages = new Set(["persona", "companion", "news", "sessions", "settings", "appearance", "sticker-preferences", "sticker-detail", "sticker-import", "usage-pricing"]);
let trayExitRequested = false, bridgeReady = !managed, residentStart = false;
let calibration, calibrationTarget, calibrationSaving = false, backup;
let reducedMotion = false;
const windowShapes = new WeakMap();
const allowed = new Set([
  "view.status", "view.queue", "run.start", "run.mode", "run.selected", "run.recover",
  "stickers.query", "stickers.detail", "stickers.guide", "stickers.preferences",
  "stickers.saveDetail", "stickers.frequency",
  "imports.list", "imports.remove", "imports.analyze", "imports.cancel", "imports.commit",
  "appearance.read", "appearance.save", "settings.read", "settings.save", "settings.pacing",
  "settings.credentials", "settings.removeKey", "settings.testModel",
  "persona.read", "persona.effective", "persona.save", "persona.saveDocument", "persona.importText",
  "sessions.read", "sessions.directory", "sessions.save", "sessions.rules", "sessions.remove",
  "companion.read", "companion.state", "companion.memory", "companion.save", "companion.command", "companion.preset",
  "news.read", "news.state", "news.save", "news.delete", "news.command",
  "news.feedback", "news.feedbackList", "news.clearFeedback",
  "tasks.overview", "tasks.history", "tasks.usage", "tasks.savePrices", "diagnostics.run",
  "sessions.rest", "sessions.cancelRest",
  "studio.chat", "studio.summary", "studio.state", "studio.cancel", "studio.sendSummary",
]);
const mutatingMethods = new Set([
  "tasks.savePrices", "sessions.rest", "sessions.cancelRest", "news.feedback", "news.clearFeedback", "news.delete",
  "studio.chat", "studio.summary", "studio.cancel", "studio.sendSummary",
]);

function secureWindow(options) {
  const window = new BrowserWindow({
    // The Windows resize hit area is inside the client on frameless Electron 45 windows.
    show: false, frame: false, thickFrame: false, transparent: true, roundedCorners: false, autoHideMenuBar: true, icon,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"), sandbox: true, contextIsolation: true,
      nodeIntegration: false, webSecurity: true, zoomMode: "isolated",
    },
    ...options,
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    const safeUrl = publicWebUrl(url);
    if (safeUrl) shell.openExternal(safeUrl).catch(() => {});
    return { action: "deny" };
  });
  window.on("page-title-updated", (event) => event.preventDefault());
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(false));
  window.webContents.session.setPermissionCheckHandler(() => false);
  if (process.platform === "win32") windowShapes.set(window, prepareTransparentWindow(window));
  return window;
}

function showShaped(window) {
  windowShapes.get(window)?.();
  if (!window.isDestroyed()) window.show();
}

function popupView(name) {
  return { hash: name, query: { motion: reducedMotion ? "reduced" : "normal" } };
}

function identify(event, promptAllowed = false) {
  const from = BrowserWindow.fromWebContents(event.sender);
  if (event.senderFrame !== event.sender.mainFrame || (from !== main && (!promptAllowed || from !== prompt))) {
    throw new Error("无效界面来源");
  }
  return from;
}

async function refreshStatus() {
  if (statusBusy || stopping || !main?.isVisible() || main.isMinimized()) return;
  statusBusy = true;
  let fresh;
  try {
    fresh = await broker.call("view.status");
  } catch {
    fresh = { sessions: [], primary: "", accountLabel: "", revision: "", tray: false,
      stats: { pending: 0, verified: 0, unknown: 0, failed: 0, skipped: 0 },
      ...status, connected: false, readOnly, mode: "disconnected", reason: "本机界面服务暂未连接" };
  } finally {
    statusBusy = false;
  }
  if (!main || main.isDestroyed() || !main.isVisible() || main.isMinimized()) return;
  const next = JSON.stringify(fresh);
  status = fresh;
  if (next !== signature) {
    signature = next;
    main.webContents.send("shiguang:status", status);
  }
}

function openClose() {
  if (prompt && !prompt.isDestroyed()) {
    prompt.focus();
    return;
  }
  const bounds = main.getBounds();
  const area = screen.getDisplayMatching(bounds).workArea;
  const width = Math.min(470, area.width);
  const height = Math.min(312, area.height);
  const x = Math.max(area.x, Math.min(area.x + area.width - width, Math.round(bounds.x + (bounds.width - width) / 2)));
  const y = Math.max(area.y, Math.min(area.y + area.height - height, Math.round(bounds.y + (bounds.height - height) / 2)));
  prompt = secureWindow({
    parent: main, modal: true, skipTaskbar: true, resizable: false, maximizable: false, minimizable: false,
    width, height, x, y, title: "关闭助手",
  });
  const dialogWindow = prompt;
  dialogWindow.once("ready-to-show", () => void showShaped(dialogWindow));
  prompt.on("closed", () => {
    prompt = undefined;
    if (trayExitRequested && !quitting) void acknowledgeTrayExit(false);
  });
  prompt.loadFile(bundle, popupView("close"));
}

async function acknowledgeTrayExit(accepted) {
  if (!trayExitRequested) return;
  trayExitRequested = false;
  try { await broker.call("panel.exitAck", { accepted }); } catch {}
}

async function stopResident() {
  if (stopping || readOnly) throw new Error("当前界面不能停止后台");
  stopping = true;
  try {
    await broker.call("run.stop", {}, 58_000);
    await acknowledgeTrayExit(true);
    quitting = true;
    app.quit();
  } finally {
    stopping = false;
  }
}

function registerIpc() {
  ipcMain.handle("shiguang:zoom", (event, percent) => {
    const from = identify(event);
    if (!Number.isInteger(percent) || percent < 75 || percent > 200) throw new Error("界面缩放应为 75–200%");
    from.webContents.setZoomFactor(percent / 100);
  });
  ipcMain.handle("shiguang:draft", (event, page, dirty) => {
    identify(event);
    if (!draftPages.has(page) || typeof dirty !== "boolean") throw new Error("无效的编辑状态");
    if (dirty) drafts.add(page);
    else drafts.delete(page);
  });
  ipcMain.handle("shiguang:request", async (event, method, params) => {
    identify(event);
    if (stopping || !allowed.has(method) || !params || typeof params !== "object" || Array.isArray(params)) {
      throw new Error("不支持此界面操作");
    }
    if (JSON.stringify(params).length > 1_500_000) throw new Error("请求内容过大");
    const long = method === "settings.testModel" || method.startsWith("sessions.") || method === "run.start";
    const changing = mutatingMethods.has(method) || /\.(?:start|mode|selected|recover|save|saveDocument|pacing|credentials|remove|removeKey|preferences|saveDetail|frequency|importText|commit|rules|command)$/.test(method);
    if (changing) writes++;
    if (method === "run.start" && broker.closed) {
      clearTimeout(retryBridge);
      connectBridge();
    }
    try {
      const result = await broker.call(method, params, long ? 80_000 : undefined);
      if (method === "view.status") status = result;
      if (method === "appearance.read" || method === "appearance.save") reducedMotion = result.reduceMotion;
      if (method.startsWith("run.") || method === "sessions.remove") void refreshStatus();
      return result;
    } finally {
      if (changing) writes--;
    }
  });
  ipcMain.handle("shiguang:info", async (event) => {
    const from = identify(event, true);
    const reduceMotion = from === prompt
      ? Boolean((await broker.call("appearance.read").catch(() => null))?.reduceMotion) : false;
    return { readOnly, tray: Boolean(status?.tray), connected: Boolean(status?.connected),
             hasWallpaper: Boolean(await fs.stat(wallpaper).catch(() => null)), reduceMotion, trayExit: trayExitRequested,
             dirty: drafts.size > 0, maximized: from.isMaximized() || isWindowFullscreen(from) };
  });
  ipcMain.handle("shiguang:window", async (event, action) => {
    const from = identify(event, true);
    if (from === prompt) {
      if (action === "cancel") prompt.close();
      else if ((action === "background" && status?.tray && !stopping) || (action === "dismissPreview" && readOnly)) {
        if (drafts.size || writes) throw new Error("请先保存修改或等待操作结束");
        await acknowledgeTrayExit(false);
        quitting = true;
        app.quit();
      } else if (action === "exit" || action === "discardExit") {
        if (writes) throw new Error("请等待当前操作结束");
        if (drafts.size && action === "exit") throw new Error("仍有未保存的修改，请重新打开关闭选项");
        await stopResident();
      } else throw new Error("暂时无法关闭助手");
      return;
    }
    if (action === "minimize") main.minimize();
    else if (action === "maximize") {
      if (isWindowFullscreen(main)) main.setFullScreen(false);
      else main.isMaximized() ? main.unmaximize() : main.maximize();
    }
    else if (action === "close") main.close();
    else throw new Error("不支持此窗口操作");
  });
  ipcMain.handle("shiguang:calibrate", async (event, target = "") => {
    identify(event);
    if (stopping || readOnly || typeof target !== "string" || target.length > 520)
      throw new Error("当前界面不能校准微信窗口");
    if (calibration && !calibration.isDestroyed()) { calibration.focus(); return; }
    const parent = main.getBounds();
    const area = screen.getDisplayMatching(parent).workArea;
    const width = Math.min(area.width, 618);
    const height = Math.min(area.height, 774);
    const x = Math.max(area.x, Math.min(area.x + area.width - width, Math.round(parent.x + (parent.width - width) / 2)));
    const y = Math.max(area.y, Math.min(area.y + area.height - height, Math.round(parent.y + (parent.height - height) / 2)));
    const editor = secureWindow({ parent: main, modal: true, skipTaskbar: true, title: "校准微信窗口",
      width, height, x, y, minWidth: Math.min(520, width), minHeight: Math.min(475, height) });
    calibration = editor;
    calibrationTarget = target;
    editor.once("ready-to-show", () => void showShaped(editor));
    const closed = new Promise(resolve => editor.once("closed", async () => {
      try { await broker.call("calibration.cancel"); }
      catch {}
      if (calibration === editor) calibration = undefined;
      resolve();
      void refreshStatus();
    }));
    editor.on("close", event => {
      if (calibrationSaving && !quitting) event.preventDefault();
    });
    editor.loadFile(bundle, popupView("calibration")).catch(() => editor.destroy());
    await closed;
  });
  ipcMain.handle("shiguang:calibration", async (event, action, params) => {
    const from = BrowserWindow.fromWebContents(event.sender);
    if (from !== calibration || event.senderFrame !== event.sender.mainFrame || readOnly || stopping
        || !params || typeof params !== "object" || JSON.stringify(params).length > 16_000) {
      throw new Error("无效的校准界面请求");
    }
    if (action === "close") { calibration.close(); return; }
    if (!["discover", "openWechat", "capture", "identify", "save", "cancel"].includes(action))
      throw new Error("不支持此校准操作");
    const session = calibrationTarget;
    const data = action === "discover" ? { session } : action === "capture" ? { session, window: params.window, deferIdentify: true } : params;
    if (action === "capture") from.hide();
    if (action === "save") calibrationSaving = true;
    try {
      return await broker.call("calibration." + action, data, 90_000);
    } finally {
      if (action === "capture" && !from.isDestroyed()) from.show();
      if (action === "save") calibrationSaving = false;
    }
  });
  ipcMain.handle("shiguang:backup-open", async (event, action, session, name) => {
    identify(event);
    if (readOnly || stopping || !["import", "export"].includes(action) || typeof session !== "string"
        || !session || session.length > 520 || typeof name !== "string" || name.length > 80) {
      throw new Error("当前不能操作会话备份");
    }
    if (backup && !backup.window.isDestroyed()) { backup.window.focus(); return false; }
    const owner = main.getBounds();
    const area = screen.getDisplayMatching(owner).workArea;
    const width = Math.min(473, area.width);
    const height = Math.min(472, area.height);
    const x = Math.max(area.x, Math.min(area.x + area.width - width, Math.round(owner.x + (owner.width - width) / 2)));
    const y = Math.max(area.y, Math.min(area.y + area.height - height, Math.round(owner.y + (owner.height - height) / 2)));
    const child = secureWindow({ parent: main, modal: true, skipTaskbar: true, width, height, x, y, resizable: false,
      title: action === "import" ? "导入会话备份" : "导出会话备份" });
    const data = { window: child, action, session, name, busy: false, restored: false, file: undefined };
    backup = data;
    child.on("close", event => { if (data.busy && !quitting) event.preventDefault(); });
    child.once("ready-to-show", () => void showShaped(child));
    const closed = new Promise(resolve => child.once("closed", () => {
      if (backup === data) backup = undefined;
      resolve(data.restored);
    }));
    child.loadFile(bundle, popupView("backup")).catch(() => child.destroy());
    return closed;
  });
  ipcMain.handle("shiguang:backup-action", async (event, action, params) => {
    const current = backup;
    if (!current || readOnly || stopping || event.sender !== current.window.webContents
        || event.senderFrame !== event.sender.mainFrame || !params || typeof params !== "object") {
      throw new Error("无效的备份界面来源");
    }
    if (action === "context") return { action: current.action, name: current.name };
    if (action === "close") { current.window.close(); return; }
    if (current.busy) throw new Error("备份正在处理");
    current.busy = true;
    try {
      const filters = [{ name: "加密会话备份", extensions: ["xlc"] }];
      if (action === "pick" && current.action === "import") {
        const picked = await dialog.showOpenDialog(current.window, { filters, properties: ["openFile"] });
        if (picked.canceled) return null;
        current.file = picked.filePaths[0];
        return path.basename(current.file);
      }
      const { password, token } = params;
      if (typeof password !== "string" || password.length < 8 || password.length > 1024)
        throw new Error("备份口令须为 8 至 1024 个字符");
      const fields = { session: current.session, password };
      if (action === "preview" && current.action === "import" && current.file)
        return await broker.call("companion.backupPreview", { ...fields, path: current.file });
      if (action === "restore" && current.action === "import" && typeof token === "string" && /^[a-f0-9]{32}$/.test(token)) {
        await broker.call("companion.restore", { ...fields, token }, 95_000);
        current.restored = true;
        return true;
      }
      if (action === "export" && current.action === "export") {
        const picked = await dialog.showSaveDialog(current.window, { filters, defaultPath: "拾光-会话备份.xlc" });
        if (picked.canceled) return false;
        await broker.call("companion.export", { ...fields, path: picked.filePath }, 35_000);
        return true;
      }
      throw new Error("不支持的备份操作");
    } finally {
      current.busy = false;
    }
  });
  ipcMain.handle("shiguang:persona-file", async (event, mode, params) => {
    identify(event);
    if (stopping || !params || typeof params !== "object" || JSON.stringify(params).length > 1_500_000)
      throw new Error("无效的人格文件操作");
    if (mode === "import") {
      if (readOnly) throw new Error("当前为只读预览");
      const { canceled, filePaths } = await dialog.showOpenDialog(main, {
        title: "导入人格文档",
        properties: ["openFile", "multiSelections"],
        filters: [{ name: "人格文件", extensions: ["json", "md", "markdown", "txt"] }],
      });
      return canceled ? null : broker.call("persona.importFiles", {
        paths: filePaths, expected: params.expected, category: params.category,
      });
    }
    if (mode !== "export") throw new Error("无效的人格文件操作");
    if (readOnly) throw new Error("当前为只读预览");
    const { canceled, filePath } = await dialog.showSaveDialog(main, {
      title: "导出完整人格",
      defaultPath: "拾光-人格备份.json",
      filters: [{ name: "完整 JSON 备份", extensions: ["json"] }, { name: "Markdown 阅读版", extensions: ["md"] }],
    });
    return canceled ? null : broker.call("persona.export", { path: filePath, title: params.title, modules: params.modules });
  });
  ipcMain.handle("shiguang:stickers-pick", async (event, folder) => {
    identify(event);
    if (readOnly || stopping || typeof folder !== "boolean") throw new Error("当前不能导入素材");
    const selection = await dialog.showOpenDialog(main, {
      title: folder ? "选择表情包文件夹" : "选择表情包原件",
      properties: folder ? ["openDirectory"] : ["openFile", "multiSelections"],
      filters: folder ? [] : [{ name: "表情图片", extensions: ["png", "jpg", "jpeg", "webp", "gif"] }],
    });
    return selection.canceled ? null
      : broker.call("imports.pick", { paths: selection.filePaths, folder }, 120_000);
  });
}

function registerMedia() {
  protocol.handle("sg-media", async (request) => {
    if (request.method !== "GET") return new Response(null, { status: 405 });
    const url = new URL(request.url);
    if (url.host === "wallpaper" && url.pathname === "/selected") {
      try {
        const info = await fs.stat(wallpaper);
        if (!info.isFile() || info.size > 24 * 1024 * 1024) return new Response(null, { status: 404 });
        const bytes = await fs.readFile(wallpaper);
        if (!bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return new Response(null, { status: 415 });
        return new Response(bytes, { headers: { "Content-Type": "image/png", "Cache-Control": "private, max-age=3600" } });
      } catch {
        return new Response(null, { status: 404 });
      }
    }
    if (!["sticker", "import"].includes(url.host) || !/^\/[A-Za-z0-9_-]{1,80}$/.test(url.pathname)) {
      return new Response(null, { status: 404 });
    }
    const variant = url.searchParams.get("variant") === "original" ? "original" : "thumbnail";
    try {
      const id = url.pathname.slice(1);
      const image = url.host === "sticker" ? await broker.call("stickers.image", { id, variant })
        : await broker.call("imports.preview", { id, variant });
      return new Response(Buffer.from(image.data, "base64"), { headers: {
        "Content-Type": image.mime,
        "Cache-Control": variant === "original" || url.host === "import" ? "no-store" : "private, max-age=1800",
      } });
    } catch {
      return new Response(null, { status: 404 });
    }
  });
}

function showMain() {
  if (!main || main.isDestroyed() || !painted) return;
  if (main.isMinimized()) main.restore();
  main.show();
  main.focus();
  void refreshStatus();
}

async function startResident() {
  if (!managed || residentStart || stopping) return;
  residentStart = true;
  try {
    const existing = await broker.call("view.status");
    if (!existing.connected && existing.mode !== "starting") await broker.call("run.start", {}, 40_000);
  } catch {}
  finally { void refreshStatus(); }
}

function connectBridge() {
  const python = packaged ? path.join(project, "XiaomaoCLI.exe")
    : process.env.SHIGUANG_PYTHON || path.join(project, ".venv/Scripts/python.exe");
  const args = packaged ? ["--root", profile, "desktop-bridge"] : ["-m", "xiaomao.desktop_bridge", "--root", profile];
  if (managed) args.push("--managed");
  if (readOnly) args.push("--read-only");
  const current = broker = new Broker(python, args, project);
  current.onEvent = (topic, value) => {
    if (broker !== current || quitting) return;
    if (topic === "panel") {
      if (value === "ready") {
        bridgeReady = true;
        if (painted && !main?.isVisible()) showMain();
        void startResident();
        void refreshStatus();
      } else if (value === "show") showMain();
      else if (value === "exit") {
        trayExitRequested = true;
        if (drafts.size || writes || calibration || backup) { showMain(); openClose(); }
        else void stopResident().catch(() => { showMain(); openClose(); });
      } else if (value === "delegated" || value === "occupied") {
        quitting = true;
        app.quit();
      }
    }
    if (topic === "imports" && main && !main.isDestroyed() && main.isVisible() && !main.isMinimized()) {
      main.webContents.send("shiguang:imports", value);
    }
  };
  current.exited.then(() => {
    if (quitting || broker !== current) return;
    if (!bridgeReady && painted) showMain();
    if (!bridgeReady) bridgeReady = true;
    void refreshStatus();
    const now = Date.now();
    failures = failures.filter(at => now - at < 60_000);
    failures.push(now);
    if (failures.length <= 3) {
      retryBridge = setTimeout(() => {
        if (!quitting && broker === current) connectBridge();
      }, failures.length * 1000);
    }
  });
  return current;
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", showMain);
  app.whenReady().then(() => {
    // Keep native text-editing shortcuts. Zoom goes through the saved appearance state.
    Menu.setApplicationMenu(Menu.buildFromTemplate([{ role: "editMenu" }]));
    connectBridge();
    registerIpc();
    registerMedia();
    const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
    main = secureWindow({
      title, width: Math.min(1488, area.width), height: Math.min(960, area.height),
      minWidth: Math.min(820, area.width), minHeight: Math.min(560, area.height),
    });
    main.once("ready-to-show", () => {
      windowShapes.get(main)?.();
      if (main.isDestroyed()) return;
      painted = true;
      if (bridgeReady) showMain();
    });
    main.on("close", (event) => {
      if (!quitting) {
        event.preventDefault();
        openClose();
      }
    });
    main.webContents.on("before-input-event", (event, input) => {
      if (input.type === "keyDown" && input.key === "F11" && !input.isAutoRepeat) {
        event.preventDefault();
        main.setFullScreen(!isWindowFullscreen(main));
      }
    });
    for (const event of ["maximize", "unmaximize", "restore", "enter-full-screen", "leave-full-screen"]) {
      main.on(event, () => main.webContents.send("shiguang:window-state", { maximized: main.isMaximized() || isWindowFullscreen(main) }));
    }
    main.loadFile(bundle).catch(() => {
      dialog.showErrorBox(title, "界面资源无法加载，请重新构建桌面应用。");
      quitting = true;
      app.quit();
    });
    timer = setInterval(refreshStatus, 1000);
  });
  app.on("before-quit", (event) => {
    quitting = true;
    clearInterval(timer);
    clearTimeout(retryBridge);
    if (released || !broker) return;
    event.preventDefault();
    if (broker.releasing) return;
    broker.releasing = true;
    broker.close().finally(() => {
      released = true;
      app.quit();
    });
  });
}
