export type PageKey = "workbench" | "sessions" | "persona" | "companion" | "news" | "tasks" | "stickers" | "settings";
export type RunMode = "auto" | "observe" | "paused" | "starting" | "stopped" | "disconnected";

export interface Session {
  id: string;
  name: string;
  kind: "group" | "private";
  bound: boolean;
  mode: RunMode;
  replyMode: string;
  stickerFrequency: string;
  reason: string;
  calibrated: boolean;
  windowState: string;
  pending: number;
  rest?: { active: boolean; until: number | null; remainingSeconds: number; minutes: number; priorMode: string | null };
  lastResult: { state: string; summary: string; reason: string; at?: number } | null;
}

export interface Status {
  connected: boolean;
  readOnly: boolean;
  mode: RunMode;
  reason: string;
  primary: string;
  accountLabel: string;
  accountScope?: string;
  sessions: Session[];
  tray: boolean;
  revision: string;
  stats: { pending: number; verified: number; unknown: number; failed: number; skipped: number };
}

export interface Queue {
  total: number;
  receiptTotal: number;
  note: string;
  tasks: { id: string; group: string; sender: string; text: string; reply: string; state: string; progress: string }[];
  receipts: { id: string; at: number; group: string; text: string; kind: "text" | "emoji" | "image"; trigger: string; linked: boolean }[];
}

export interface Sticker {
  id: string;
  character: string;
  meaning: string;
  tags: string[];
  animated: boolean;
  priority: string;
  version: string;
}

export interface Catalog {
  items: Sticker[];
  total: number;
  offset: number;
  categories: string[];
  priorities: Record<string, string>;
  neutral: boolean;
  revision: number;
  configRevision: string;
  frequency: string;
}

export interface StickerDetail extends Sticker {
  source: string;
  size: number;
  width: number;
  height: number;
  frames: number;
  extension: string;
  revision: number;
}

export type WindowAction = "minimize" | "maximize" | "close" | "background" | "exit" | "discardExit" | "cancel" | "dismissPreview";
export interface Info { readOnly: boolean; tray: boolean; connected: boolean; hasWallpaper: boolean; reduceMotion: boolean; trayExit: boolean; dirty: boolean; maximized: boolean }
export interface Appearance { transparency: number; reduceMotion: boolean; zoomPercent: number; revision: string | null }
export const defaultAppearance: Appearance = { transparency: 21, reduceMotion: false, zoomPercent: 100, revision: null };
const zoomSteps = [75, 80, 90, 100, 110, 125, 150, 175, 200];
export function stepZoom(percent: number, direction: -1 | 1) {
  const next = direction < 0 ? zoomSteps.filter(value => value < percent).at(-1) : zoomSteps.find(value => value > percent);
  return next ?? percent;
}

export interface StickerAnnotation { category: string; meaning: string; tags: string[]; source: string }
export interface ImportRow extends StickerAnnotation {
  id: string; name: string; status: string; width: number; height: number; frames: number; size: number;
}
export interface ImportSnapshot {
  rows: ImportRow[];
  job: { id: string; state: "running" | "done" | "cancelled" | "failed"; completed: number; total: number } | null;
}

declare global {
  interface Window {
    shiguang: {
      draft: (page: string, dirty: boolean) => Promise<void>;
      request: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
      watch: (listener: (value: Status) => void) => () => void;
      windowAction: (action: WindowAction) => Promise<void>;
      zoom: (percent: number) => Promise<void>;
      windowState: (listener: (value: { maximized: boolean }) => void) => () => void;
      appInfo: () => Promise<Info>;
      calibrate: (session?: string) => Promise<void>;
      openBackup: (action: "import" | "export", session: string, name: string) => Promise<boolean>;
      backupAction: (action: string, params?: Record<string, unknown>) => Promise<unknown>;
      calibration: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
      personaFile: (mode: 'import' | 'export', params: Record<string, unknown>) => Promise<unknown>;
      pickStickers: (folder: boolean) => Promise<ImportSnapshot | null>;
      watchImports: (listener: (value: ImportSnapshot) => void) => () => void;
    };
  }
}

export function request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  return window.shiguang.request(method, params) as Promise<T>;
}

export function errorMessage(error: unknown) {
  const text = error instanceof Error ? error.message : String(error);
  return text.replace(/^Error invoking remote method '[^']+': (Error: )?/, "");
}

export const runLabels: Record<RunMode, string> = {
  auto: "运行中", observe: "只观察", paused: "已暂停",
  starting: "连接中", stopped: "已停止", disconnected: "未连接",
};
export const ruleLabels: Record<string, string> = {
  natural: "自然参与", mentions: "仅 @ 或点名", all: "逐条回复",
};
export const frequencies = [
  { value: "requested", label: "仅按需" }, { value: "occasional", label: "偶尔" },
  { value: "natural", label: "自然" }, { value: "expressive", label: "爱用表情" },
];
export const priorities = ["优先", "标准", "备用", "停用"].map(value => ({ value, label: value }));

export function mediaUrl(item: Sticker, animated = false) {
  return `sg-media://sticker/${encodeURIComponent(item.id)}?v=${item.version}&variant=${animated ? "original" : "thumbnail"}`;
}

export function tagsFromText(text: string) {
  return text.split(/[、,，;；\s]+/).filter(Boolean);
}
