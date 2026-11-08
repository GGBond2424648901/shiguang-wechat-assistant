const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("shiguang", {
  draft: (page, dirty) => ipcRenderer.invoke("shiguang:draft", page, dirty),
  request: (method, params = {}) => ipcRenderer.invoke("shiguang:request", method, params),
  watch: (listener) => {
    const handle = (_, value) => listener(value);
    ipcRenderer.on("shiguang:status", handle);
    return () => ipcRenderer.removeListener("shiguang:status", handle);
  },
  windowAction: (action) => ipcRenderer.invoke("shiguang:window", action),
  zoom: (percent) => ipcRenderer.invoke("shiguang:zoom", percent),
  windowState: (listener) => {
    const handle = (_, value) => listener(value);
    ipcRenderer.on("shiguang:window-state", handle);
    return () => ipcRenderer.removeListener("shiguang:window-state", handle);
  },
  appInfo: () => ipcRenderer.invoke("shiguang:info"),
  calibrate: (session) => ipcRenderer.invoke("shiguang:calibrate", session),
  openBackup: (action, session, name) => ipcRenderer.invoke("shiguang:backup-open", action, session, name),
  backupAction: (action, params = {}) => ipcRenderer.invoke("shiguang:backup-action", action, params),
  calibration: (method, params = {}) => ipcRenderer.invoke("shiguang:calibration", method, params),
  personaFile: (mode, params) => ipcRenderer.invoke("shiguang:persona-file", mode, params),
  pickStickers: (folder) => ipcRenderer.invoke("shiguang:stickers-pick", folder),
  watchImports: (listener) => {
    const handle = (_, value) => listener(value);
    ipcRenderer.on("shiguang:imports", handle);
    return () => ipcRenderer.removeListener("shiguang:imports", handle);
  },
});
