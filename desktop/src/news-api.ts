import { request } from "./api";

export interface NewsCategory { id: string; label: string; query?: string }
export interface NewsTimeRange { start: string; end: string; intervalMinutes: number }
export interface NewsDraft {
  id?: string;
  name: string;
  enabled: boolean;
  selectionMode: "single" | "multiple";
  categories: string[];
  categoryTopics?: Record<string, string[]>;
  keywords: string;
  excludeKeywords: string;
  sourceDomains: string;
  time: string;
  // Optional on reads so pre-upgrade subscriptions and preview snapshots remain valid.
  times?: string[];
  timeRanges?: NewsTimeRange[];
  weekdays: number[];
  itemCount: number;
  lookbackHours: number;
  style: "brief" | "persona";
  includeLinks: boolean;
  recipients: string[];
}
export interface NewsSubscription extends NewsDraft {
  id: string;
  revision: number;
  targets?: { session: string; name: string; scope: string }[];
}
export interface NewsArticle {
  id: string; title: string; summary: string; url: string; source: string; category: string;
  topic?: string;
  publishedAt: number; timeLabel: string;
}
export interface NewsDelivery {
  session: string; name: string; scope: string; state: string; reason: string;
  parts: { requestId: string; taskId: string; state: string; reason: string }[];
}
export interface NewsEdition {
  id: string; subscriptionId: string; snapshot: NewsDraft; trigger: string; slot: string;
  createdAt: number; updatedAt: number; state: string; reason: string;
  scheduledAt?: number;
  articles: NewsArticle[]; parts: string[]; warnings: string[]; deliveries: NewsDelivery[];
}
export interface NewsRead {
  subscriptions: NewsSubscription[];
  categories: NewsCategory[];
  defaultSubscription: NewsDraft;
  accountReady: boolean;
}
export interface NewsState {
  connected: boolean;
  edition: NewsEdition | null;
  history: NewsEdition[];
  reason: string;
}

// Keep private target identity snapshots and revisions out of renderer writes.
export function editableSubscription(value: NewsDraft, existing = true): NewsDraft {
  const times = [...new Set(value.times === undefined ? [value.time] : value.times)].sort();
  const ranges = new Map((value.timeRanges ?? []).map(range => [
    JSON.stringify([range.start, range.end, range.intervalMinutes]),
    { start: range.start, end: range.end, intervalMinutes: range.intervalMinutes },
  ]));
  const timeRanges = [...ranges.values()].sort((a, b) => a.start.localeCompare(b.start)
    || a.end.localeCompare(b.end) || a.intervalMinutes - b.intervalMinutes);
  const categoryTopics = Object.fromEntries(Object.entries(value.categoryTopics ?? {})
    .filter(([, topics]) => topics.length > 0).sort(([a], [b]) => a.localeCompare(b))
    .map(([category, topics]) => [category, [...new Set(topics.map(topic => topic.trim()))].sort()]));
  return {
    ...(existing && value.id ? { id: value.id } : {}),
    name: value.name,
    enabled: value.enabled,
    selectionMode: value.selectionMode,
    categories: [...value.categories],
    categoryTopics,
    keywords: value.keywords,
    excludeKeywords: value.excludeKeywords,
    sourceDomains: value.sourceDomains,
    time: times[0] ?? timeRanges[0]?.start ?? "",
    times,
    timeRanges,
    weekdays: [...value.weekdays],
    itemCount: value.itemCount,
    lookbackHours: value.lookbackHours,
    style: value.style,
    includeLinks: value.includeLinks,
    recipients: [...value.recipients],
  };
}

export function subscriptionSignature(value: NewsDraft) {
  const clean = editableSubscription(value, false);
  return JSON.stringify({ ...clean, categories: [...clean.categories].sort(), recipients: [...clean.recipients].sort(),
    weekdays: [...clean.weekdays].sort((a, b) => a - b) });
}

export function safeNewsUrl(value: string) {
  try {
    const url = new URL(value);
    if ((url.protocol !== "https:" && url.protocol !== "http:") || !url.hostname || url.username || url.password) return undefined;
    return url.href;
  } catch { return undefined; }
}

export const readNews = () => request<NewsRead>("news.read");
export const readNewsState = (subscription: string) => request<NewsState>("news.state", { subscription });
export const saveNews = (subscription: NewsDraft, expected: number | null) =>
  request<NewsSubscription>("news.save", { subscription: editableSubscription(subscription, expected !== null), expected });
export const deleteNews = (subscription: string, expected: number) =>
  request<{ deleted: true }>("news.delete", { subscription, expected });
export const commandNews = (subscription: string, action: "preview" | "push" | "cancel", edition?: string) =>
  request<{ accepted: true; edition?: string }>("news.command", { subscription, action, ...(edition ? { edition } : {}) });
