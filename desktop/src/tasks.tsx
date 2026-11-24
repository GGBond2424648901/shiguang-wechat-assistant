import { Activity, BarChart3, CalendarClock, Check, Clock3, Edit3, History, Info, MessageCircle, Newspaper, RefreshCw, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { errorMessage, request, type PageKey, type Status } from "./api";
import { Button, Empty, IconButton, PanelTitle, Select, useDraftGuard } from "./components";
import type { PlannedTask, TaskDestination, TaskHistory, TaskOverview, TaskRecord, UsagePrices, UsageReport } from "./tasks-api";
import "./tasks.css";

const labels: Record<string, string> = {
  scheduled: "待执行", conditional: "条件触发", waiting: "等待条件", paused: "已暂停", disabled: "未启用", collecting: "采集中",
  ready: "已准备", publishing: "发送中", verified: "已确认", completed: "已完成", sent: "已发送",
  failed: "未完成", unknown: "待核对", partial: "部分确认", cancelled: "已取消", missed: "已错过",
  empty: "无新增内容", pending: "待处理", queued: "已排队", dispatching: "发送中", skipped: "已跳过",
};
const kinds = { news: "资讯推送", routine: "定时关怀", proactive: "主动聊天" };
const clock = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
const fullTime = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
const dayLabel = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "short" });
const validStamp = (at?: number | null) => typeof at === "number" && Number.isFinite(at) && at > 0;
const fmt = (at?: number | null, full = false) => validStamp(at) ? (full ? fullTime : clock).format(at! * 1000) : "—";
const stateName = (state: string) => labels[state] || "等待核对";
const amount = (n?: number) => Number.isFinite(n) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(n!) : "—";
function KindIcon({ kind, size = 20 }: { kind: string; size?: number }) {
  return kind === "news" ? <Newspaper size={size} /> : kind === "routine" ? <CalendarClock size={size} /> : <MessageCircle size={size} />;
}

type TaskRead<T> = { key: string; data?: T; error?: string; pending: boolean };

function useTaskRead<T>(active: boolean, scope: string, refresh: number, method: string, parameter: string, value: number) {
  const key = `${scope}:${value}`;
  const [result, setResult] = useState<TaskRead<T>>();
  useEffect(() => {
    if (!active) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      setResult(before => before?.key === key ? { ...before, pending: true } : { key, pending: true });
      try {
        const data = await request<T>(method, { [parameter]: value });
        if (current) setResult({ key, data, pending: false });
      } catch (error) {
        if (current) setResult(before => ({
          key, data: before?.key === key ? before.data : undefined, error: errorMessage(error), pending: false,
        }));
      } finally {
        if (current) timer = setTimeout(() => void load(), 10000);
      }
    }
    void load();
    return () => { current = false; clearTimeout(timer); };
  }, [active, key, method, parameter, refresh, value]);
  return result?.key === key ? result : { key, pending: active };
}

export function TasksPage({ active, status, notify, navigate, edit }: {
  active: boolean; status?: Status; notify: (message: string, error?: boolean) => void;
  navigate: (page: PageKey) => void; edit: (value: TaskDestination) => void;
}) {
  const [tab, setTab] = useState("plans");
  const [hours, setHours] = useState("24");
  const [days, setDays] = useState("7");
  const [kind, setKind] = useState("all");
  const [session, setSession] = useState("all");
  const [version, setVersion] = useState(0);
  const scope = `${status?.accountScope ?? status?.accountLabel ?? ""}:${status?.revision ?? ""}`;
  const plans = useTaskRead<TaskOverview>(active && tab === "plans", scope, version, "tasks.overview", "hours", Number(hours));
  const records = useTaskRead<TaskHistory>(active && tab !== "usage", scope, version, "tasks.history", "limit", 80);
  const usage = useTaskRead<UsageReport>(active && tab === "usage", scope, version, "tasks.usage", "days", Number(days));
  const current = tab === "usage" ? usage : tab === "history" ? records : plans;
  const currentName = tab === "usage" ? "用量统计" : tab === "history" ? "执行记录" : "计划总览";
  const busy = current.pending || (tab === "plans" && records.pending);
  const sourceRows = tab === "history" ? records.data?.rows : plans.data?.rows;
  const sessions = useMemo(() => {
    const names = new Map<string, string>();
    for (const row of sourceRows ?? []) if (row.session) names.set(row.session, row.name);
    return [...names];
  }, [sourceRows]);
  const selectedSession = sessions.some(([id]) => id === session) ? session : "all";
  const rows = (plans.data?.rows ?? []).filter(row => (kind === "all" || row.kind === kind) && (selectedSession === "all" || row.session === selectedSession));
  const groups = new Map<string, PlannedTask[]>();
  for (const row of rows) {
    const group = row.conditional ? "条件触发" : validStamp(row.at) ? dayLabel.format(row.at! * 1000) : "尚未安排";
    groups.set(group, [...(groups.get(group) ?? []), row]);
  }
  const history = (records.data?.rows ?? []).filter(row => (kind === "all" || row.kind === kind) && (selectedSession === "all" || row.session === selectedSession));
  const refreshing = () => setVersion(v => v + 1);
  const tabControls = <div className="segments tasks-tabs" role="group" aria-label="任务中心页面">
    {[["plans", "计划总览", CalendarClock], ["history", "执行记录", History], ["usage", "用量统计", BarChart3]].map(([value, label, Icon]) => {
      const Glyph = Icon as typeof CalendarClock;
      return <Button key={String(value)} variant={tab === value ? "primary" : "ghost"} aria-pressed={tab === value}
        icon={<Glyph size={18} />} onClick={() => setTab(String(value))}>{String(label)}</Button>;
    })}
  </div>;
  return <div className="tasks-page">
    <div className="glass tasks-toolbar">{tabControls}<div className="tasks-toolbar-end">
      {tab === "usage" ? <Select label="统计范围" value={days} options={[{ value: "1", label: "今天" }, { value: "7", label: "最近7天" }, { value: "30", label: "最近30天" }]} onChange={setDays} />
        : tab === "plans" && <Select label="计划范围" value={hours} options={[24, 48, 72].map(h => ({ value: String(h), label: `未来${h}小时` }))} onChange={setHours} />}
      <IconButton label="刷新任务中心" icon={<RefreshCw size={21} />} busy={busy} onClick={refreshing} />
    </div></div>
    {current.error && <div className="inline-error tasks-error" role="status"><span>{currentName}读取失败。{current.error}</span>
      <Button disabled={current.pending} onClick={refreshing}>重试</Button></div>}
    <div className="tasks-usage-host" hidden={tab !== "usage"}><UsagePanel key={scope} report={usage.data} failed={!!usage.error} readOnly={status?.readOnly !== false} notify={notify} refresh={refreshing} /></div>
    <div className={`tasks-columns ${tab === "history" ? "history-mode" : ""}`} hidden={tab === "usage"}>
        <section className="glass panel tasks-agenda">
          <PanelTitle icon={tab === "plans" ? <CalendarClock size={23} /> : <History size={23} />} end={current.data && <span className="tag">共 {tab === "plans" ? rows.length : history.length} 项</span>}>
            {tab === "plans" ? "未来计划" : "执行记录"}</PanelTitle>
          <div className="tasks-filters"><Select label="任务类型" value={kind} onChange={setKind} options={[{ value: "all", label: "全部类型" }, ...Object.entries(kinds).map(([value, label]) => ({ value, label }))]} />
            <Select label="任务会话" value={selectedSession} onChange={setSession} options={[{ value: "all", label: "全部会话" }, ...sessions.map(([value, label]) => ({ value, label }))]} /></div>
          {tab === "plans" && plans.data && !plans.data.connected && <p className="tasks-caption" role="status">{plans.data.reason || "后台尚未连接"}；当前仅显示已保存计划。</p>}
          <div className="tasks-agenda-scroll" aria-busy={current.pending && !current.data}>
            {!current.data ? <Empty title={current.error ? `${currentName}暂不可用` : `正在读取${currentName}`} />
              : tab === "plans" ? rows.length ? [...groups].map(([day, entries]) => <section className="tasks-day" key={day}>
                <h3><i />{day}<small>{entries.length} 项</small></h3>
                {entries.map(row => <div className="tasks-agenda-row" key={row.id}>
                  <time className={row.conditional ? "conditional" : ""} title={validStamp(row.at) ? fmt(row.at, true) : "由空闲、概率和会话状态共同决定"}>{row.conditional ? "条件触发" : fmt(row.at)}</time>
                  <span className="tasks-kind"><KindIcon kind={row.kind} /></span>
                  <div className="tasks-row-copy"><b>{row.title}</b><span>{row.name || kinds[row.kind]}</span>
                    {row.reason && <small>{row.reason}</small>}
                    {row.conditional && validStamp(row.windowStart) && <small>预计评估窗口 {fmt(row.windowStart)}{validStamp(row.windowEnd) && `–${fmt(row.windowEnd)}`}</small>}</div>
                  <span className={`tasks-state ${row.state}`}>{stateName(row.state)}</span>
                  <Button className="tasks-edit" icon={<Edit3 size={14} />} aria-label={`编辑 ${row.title}`} onClick={() => edit(row)}>编辑</Button>
                </div>)}
              </section>) : <Empty icon={<CalendarClock size={32} />} title="这个范围没有计划" detail="可在每日资讯或记忆与陪伴中安排任务" />
                : history.length ? history.map(row => <HistoryRow key={row.id} row={row} edit={edit} />) : <Empty title="还没有执行记录" detail="真实任务执行后会在这里显示结果" />}
          </div>
          {tab === "plans" && <p className="tasks-caption"><Info size={15} />计划按北京时间显示，实际执行需满足当时的发送条件。{plans.data?.truncated ? "计划较多，当前展示已达到上限。" : "主动聊天仅为条件评估窗口。"}</p>}
        </section>
        <aside className="glass panel tasks-activity"><PanelTitle icon={<Activity size={23} />}>执行动态</PanelTitle>
          <div className="tasks-activity-scroll">{(records.data?.rows ?? []).slice(0, 6).map(row => <HistoryRow key={row.id} row={row} edit={edit} compact />)}
            {records.data && !records.data.rows.length && <Empty title="暂时没有执行动态" detail="推送与定时关怀的结果会在这里汇总" />}
            {tab === "plans" && records.error && <p className="tasks-caption" role="status">执行动态读取失败。{records.error}</p>}
            {!records.data && !records.error && <Empty title="正在读取执行动态" />}</div>
          <div className="tasks-hint"><Clock3 size={36} /><div><b>安排一目了然</b><p>点击编辑可回到原页面调整。暂停和只观察状态会继续遵守。</p>
            <Button variant="ghost" onClick={() => navigate("settings")}>查看运行设置</Button></div></div>
        </aside>
      </div>
  </div>;
}

function HistoryRow({ row, edit, compact = false }: { row: TaskRecord; edit: (value: TaskDestination) => void; compact?: boolean }) {
  return <div className={`tasks-history-row ${compact ? "compact" : ""}`}>
    <span className="tasks-kind"><KindIcon kind={row.kind} /></span><div className="tasks-row-copy"><b>{row.title}</b>
      <span>{row.name || (kinds as Record<string, string>)[row.kind] || "任务"}</span><small>{row.reason || stateName(row.state)}</small></div>
    <div className="tasks-record-end"><time>{fmt(row.at, !compact)}</time><span className={`tasks-state ${row.state}`}>{stateName(row.state)}</span>
      {!compact && <Button className="tasks-edit" icon={<Edit3 size={14} />} onClick={() => edit(row)}>查看设置</Button>}</div>
  </div>;
}

function UsagePanel({ report, failed, readOnly, notify, refresh }: { report?: UsageReport; failed: boolean; readOnly: boolean;
  notify: (message: string, error?: boolean) => void; refresh: () => void }) {
  const [base, setBase] = useState<UsagePrices>();
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [saving, setSaving] = useState(false);
  const dirty = !!base && (input !== String(base.inputPerMillion) || output !== String(base.outputPerMillion));
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  useDraftGuard("usage-pricing", dirty && !readOnly);
  useEffect(() => {
    if (report && !dirtyRef.current) { setBase(report.pricing); setInput(String(report.pricing.inputPerMillion)); setOutput(String(report.pricing.outputPerMillion)); }
  }, [report]);
  const save = async () => {
    if (!base || readOnly || saving) return;
    const prices = { inputPerMillion: Number(input), outputPerMillion: Number(output) };
    if (!input.trim() || !output.trim() || [prices.inputPerMillion, prices.outputPerMillion].some(n => !Number.isFinite(n) || n < 0 || n > 100000)) {
      notify("请输入 0–100000 之间的有效单价", true); return;
    }
    setSaving(true);
    try { await request("tasks.savePrices", { prices, expected: base.revision }); dirtyRef.current = false; setBase(undefined); notify("估算单价已保存"); refresh(); }
    catch (e) { notify(errorMessage(e), true); } finally { setSaving(false); }
  };
  if (!report) return <section className="glass panel tasks-usage"><Empty title={failed ? "用量统计暂不可用" : "正在读取用量统计"} /></section>;
  const totals = report.totals;
  return <div className="tasks-usage-grid">
    <section className="glass panel tasks-usage"><PanelTitle icon={<BarChart3 size={23} />}>用量概览</PanelTitle>
      <div className="tasks-metrics">{[["模型调用", totals.calls, "次"], ["检索 / 订阅抓取", totals.searches, "次"], ["输入 tokens", totals.inputTokens, ""], ["输出 tokens", totals.outputTokens, ""], ["累计请求耗时", totals.durationMs / 1000, "秒"], ["失败请求", totals.errors, "次"]].map(([label, value, unit]) =>
        <div key={String(label)}><span>{label}</span><b>{amount(Number(value))}<small>{unit}</small></b></div>)}</div>
      {report.queue && <p className="tasks-queue-summary"><Clock3 size={16} />{!report.queue.connected ? "连接后台后显示当前队列等待情况"
        : report.queue.pending === 0 ? "当前队列为空"
          : `当前待处理 ${report.queue.pending ?? "待确认"} 条 · 最早等待 ${report.queue.oldestSeconds === null ? "暂不可核实" : `${amount(report.queue.oldestSeconds)} 秒`}`}</p>}
      <div className="tasks-usage-scroll"><table className="tasks-usage-table"><thead><tr><th>日期</th><th>模型</th><th>检索</th><th>tokens</th><th>失败</th></tr></thead><tbody>
        {report.daily.map(row => <tr key={row.day}><td>{row.day}</td><td>{amount(row.calls)}</td><td>{amount(row.searches)}</td><td>{amount(row.inputTokens + row.outputTokens)}</td><td>{amount(row.errors)}</td></tr>)}
      </tbody></table>{!report.daily.length && <Empty title="暂无用量记录" detail="实际调用后开始累计" />}</div>
      {!!report.notes.length && <div className="tasks-caption-list">{report.notes.map(note => <p key={note}><Info size={14} />{note}</p>)}</div>}
    </section>
    <section className="glass panel tasks-pricing"><PanelTitle icon={<Save size={22} />}>费用估算</PanelTitle>
      <div className="tasks-cost"><span>按自定义单价估算</span><b>{report.estimatedCost === null ? "暂不可估算" : `¥ ${report.estimatedCost.toFixed(4)}`}</b></div>
      <p className="muted fine-print">单价单位：元 / 百万 tokens。请填写所用模型的实际价格；估算不等同于服务商账单。</p>
      <label className="field">输入单价<input type="number" min="0" max="100000" step="0.01" aria-label="每百万输入tokens单价" value={input} disabled={readOnly || saving} onChange={e => setInput(e.target.value)} /></label>
      <label className="field">输出单价<input type="number" min="0" max="100000" step="0.01" aria-label="每百万输出tokens单价" value={output} disabled={readOnly || saving} onChange={e => setOutput(e.target.value)} /></label>
      <Button variant="primary" icon={<Check size={17} />} disabled={readOnly || !dirty || !base} busy={saving} onClick={() => void save()}>保存估算单价</Button>
      {!!totals.unknownTokenCalls && <p className="tasks-caption">有 {totals.unknownTokenCalls} 次调用未返回完整 token 用量，不能计为免费调用。</p>}
    </section>
  </div>;
}
