export type TaskDestination = { page: "news" | "companion"; subscription?: string; session?: string };
export interface PlannedTask extends TaskDestination {
  id: string; kind: "news" | "routine" | "proactive"; title: string; at: number | null;
  windowStart?: number; windowEnd?: number; conditional: boolean; session: string; name: string;
  state: string; reason: string;
}
export interface TaskOverview {
  now: number; timezone: string; connected: boolean; reason?: string; rows: PlannedTask[]; truncated: boolean;
}
export interface TaskRecord extends TaskDestination {
  id: string; kind: string; title: string; at: number; state: string; reason: string; name: string;
}
export interface TaskHistory { rows: TaskRecord[]; connected: boolean; reason?: string }
export interface UsageCounters {
  calls: number; searches: number; inputTokens: number; outputTokens: number;
  durationMs: number; errors: number; unknownTokenCalls?: number;
}
export interface UsagePrices { inputPerMillion: number; outputPerMillion: number; currency: string; revision: string }
export interface UsageReport {
  from: number; to: number; totals: UsageCounters; daily: (UsageCounters & { day: string })[];
  pricing: UsagePrices; estimatedCost: number | null; notes: string[];
  queue?: { connected: boolean; pending: number | null; oldestSeconds: number | null };
}
export interface DiagnosticReport {
  at: number; network: boolean;
  items: { code: string; label: string; state: "ok" | "warn" | "error" | "unknown"; detail: string; action?: "settings" | "sessions" }[];
}
