export interface Conversation {
  id: string; contactId?: string; name: string; kind: "group" | "private" | "service";
  bound: boolean; paused: boolean; calibrated: boolean; placeholder?: boolean;
  windowKind?: string | null; windowSize?: [number, number] | null;
  replyMode?: string | null; stickerFrequency?: string | null; webSearch?: boolean | null;
}
export interface SavedSessions {
  items: Conversation[]; monitorAll: boolean; stickersEnabled: boolean; webSearchEnabled: boolean;
  defaultReplyMode: string; defaultStickerFrequency: string; accountLabel: string; accountKey: string; revision: string;
}
export type DraftRule = { replyMode: string | null; stickerFrequency: string | null; webSearch: boolean | null };
export interface SessionDraft {
  order: Conversation[]; monitor: boolean; stickers: boolean; defaultMode: string;
  rules: Record<string, DraftRule>;
  orderConflict?: boolean;
}
export const rulesOf = (row: Conversation): DraftRule => ({
  replyMode: row.replyMode ?? null, stickerFrequency: row.stickerFrequency ?? null, webSearch: row.webSearch ?? null,
});
const ruleFields = ["replyMode", "stickerFrequency", "webSearch"] as const;
export const sameRules = (a: DraftRule, b: DraftRule) => ruleFields.every(key => a[key] === b[key]);
export const sameOrder = (a: Conversation[], b: Conversation[]) =>
  a.length === b.length && a.every((row, index) => row.id === b[index].id);

export function rebaseSessions(base: SavedSessions, draft: SessionDraft, next: SavedSessions): SessionDraft {
  if (base.accountKey !== next.accountKey) return {
    order: next.items, monitor: next.monitorAll, stickers: next.stickersEnabled,
    defaultMode: next.defaultReplyMode, rules: {},
  };
  const previous = new Map(base.items.map(row => [row.id, row]));
  const resolve = (id: string) => next.items.find(row => row.id === id || row.contactId === id);
  const retained = draft.order.filter(row => previous.has(row.id));
  const lastOld = draft.order.reduce((last, row, index) => previous.has(row.id) ? index : last, -1);
  const reordered = !sameOrder(retained, base.items.filter(row => retained.some(item => item.id === row.id)))
    || draft.order.some((row, index) => !previous.has(row.id) && index < lastOld);
  const edited = draft.order.flatMap(row => {
    const current = resolve(row.id);
    if (current) return [current];
    if (previous.has(row.id) || row.placeholder && next.items.some(item => !item.placeholder)) return [];
    return [row];
  });
  const order = reordered ? edited : [
    ...next.items.filter(row => !previous.has(row.id) || edited.some(item => item.id === row.id)),
    ...edited.filter(row => !next.items.some(item => item.id === row.id)),
  ];
  for (const row of next.items) {
    if (!previous.has(row.id) && !order.some(item => item.id === row.id)) order.push(row);
  }
  const orderConflict = !order.length && !!next.items.length;
  const unique = orderConflict ? next.items : order.filter((row, index) => order.findIndex(item => item.id === row.id) === index);
  const rules: Record<string, DraftRule> = {};
  for (const [id, edited] of Object.entries(draft.rules)) {
    const current = resolve(id);
    if (!current || !unique.some(row => row.id === current.id)) continue;
    const before = previous.get(id);
    const saved = rulesOf(current);
    const merged = { ...saved };
    const preserveEdit = <K extends keyof DraftRule>(key: K) => {
      if (!before || edited[key] !== (before[key] ?? null)) merged[key] = edited[key];
    };
    ruleFields.forEach(preserveEdit);
    if (!sameRules(merged, saved)) rules[current.id] = merged;
  }
  return {
    order: unique, rules, orderConflict,
    monitor: draft.monitor === base.monitorAll ? next.monitorAll : draft.monitor,
    stickers: draft.stickers === base.stickersEnabled ? next.stickersEnabled : draft.stickers,
    defaultMode: draft.defaultMode === base.defaultReplyMode ? next.defaultReplyMode : draft.defaultMode,
  };
}
