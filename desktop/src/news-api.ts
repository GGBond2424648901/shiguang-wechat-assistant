import { request } from "./api";

export interface NewsCategory { id: string; label: string; query?: string }
export interface NewsDraft {
  id?: string;
  name: string;
  enabled: boolean;
  selectionMode: "single" | "multiple";
  categories: string[];
  keywords: string;
  excludeKeywords: string;
  sourceDomains: string;
  time: string;
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
  publishedAt: number; timeLabel: string;
}
export interface NewsDelivery {
  session: string; name: string; scope: string; state: string; reason: string;
  parts: { requestId: string; taskId: string; state: string; reason: string }[];
}
export interface NewsEdition {
  id: string; subscriptionId: string; snapshot: NewsDraft; trigger: string; slot: string;
  createdAt: number; updatedAt: number; state: string; reason: string;
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
  return {
    ...(existing && value.id ? { id: value.id } : {}),
    name: value.name,
    enabled: value.enabled,
    selectionMode: value.selectionMode,
    categories: [...value.categories],
    keywords: value.keywords,
    excludeKeywords: value.excludeKeywords,
    sourceDomains: value.sourceDomains,
    time: value.time,
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
