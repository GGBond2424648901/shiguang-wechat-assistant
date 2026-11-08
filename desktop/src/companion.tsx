import {
  Activity, ArrowDownToLine, ArrowLeft, BookOpen, CalendarDays, Check, Clock, Eye, FolderOpen, Heart, Info,
  Layers2, LockKeyhole, MessageCircle, Plus, RefreshCcw, Send, Trash2, Upload, X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { errorMessage, request, type Status } from "./api";
import { Button, Confirm, Empty, IconButton, PanelTitle, Select, Switch, useDraftGuard } from "./components";

type Notify = (text: string, error?: boolean) => void;
type Kind = "idle" | "followup" | "checkin" | "signoff";
interface Routine {
  id: string; time: string; weekdays: number[]; enabled: boolean; intent: string; prefer_sticker: boolean;
}
type Policy = Record<`${Kind}_${"min" | "max"}_minutes`, number> & {
  enabled: boolean; followup: boolean; idle: boolean; gentle_followups: boolean; daily_max: number;
  quiet_start: number; quiet_end: number; style: string; interests: string;
  dates: { date: string; label: string }[]; routines: Routine[]; idle_probability: number; followup_probability: number;
};
interface Preferences { session: string; bound: boolean; preferences: Policy; revision: string }
interface Live {
  connected: boolean; reason: string; mode?: string; nextAt?: number; nextStage?: string; once?: string;
  previewState?: string; preview?: string; pending?: number;
  routines: { id: string; time: string; intent: string; enabled: boolean; status: string; next_due_at: number }[];
  history: { day: string; status: string; routine_id: string; attempts: number }[];
}
interface Memory {
  messages: number; facts: number; diaries: number; summary: string;
  sources: { author: string; quote: string }[]; journal: { day: string; text: string }[];
}
const tabs = [["policy", "主动聊天"], ["routines", "定时任务"], ["memory", "会话记忆"]] as const;
const templates = [["morning", "早安"], ["noon", "午间"], ["afternoon", "午后"], ["evening", "晚安"]] as const;
const weekdays = ["一", "二", "三", "四", "五", "六", "日"];
const rangeLabels = { idle: "空闲话题", followup: "聊后续话", checkin: "无人回应追问", signoff: "开场后收尾" };
const statuses: Record<string, string> = {
  planned: "已排程", verified: "已确认", unknown: "结果未知", partial: "部分确认",
  missed: "已错过", candidate: "候选中", staged: "待发送", retry: "等待重试",
};
const fromTime = (time: string) => {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error("请填写有效的免打扰时间");
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
};
const toTime = (number: number) => `${String(Math.floor(number / 60)).padStart(2, "0")}:${String(number % 60).padStart(2, "0")}`;
const dateText = (time: number) => new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai",
}).format(time * 1000);
const datesToText = (policy: Policy) => policy.dates.map(row => row.date + " " + row.label).join("\n");

export function CompanionPage({ active, status, notify }: { active: boolean; status?: Status; notify: Notify }) {
  const [selected, setSelected] = useState("");
  const id = status?.sessions.some(row => row.id === selected) ? selected : status?.primary;
  const selection = status?.sessions.find(row => row.id === id);
  const [tab, setTab] = useState<typeof tabs[number][0]>("policy");
  const [base, setBase] = useState<Preferences>();
  const [draft, setDraft] = useState<Policy>();
  const [scopedLive, setLive] = useState<{ session: string; data: Live }>();
  const [scopedMemory, setMemory] = useState<{ session: string; data: Memory }>();
  const live = scopedLive?.session === id ? scopedLive?.data : undefined;
  const memory = scopedMemory?.session === id ? scopedMemory?.data : undefined;
  const [error, setError] = useState("");
  const [memoryError, setMemoryError] = useState("");
  const [busy, setBusy] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const [template, setTemplate] = useState("morning");
  const [dates, setDates] = useState("");
  const [quiet, setQuiet] = useState(["", ""]);
  const [confirm, setConfirm] = useState<"once" | string>();
  const scroll = useRef<HTMLDivElement>(null);
  const sequence = useRef(0);
  const localId = useRef(id);
  const readOnly = status?.readOnly ?? true;
  const policy = base?.session === id ? draft : undefined;
  const mutable = !readOnly && !!base?.bound && !!policy && !busy;
  const dirty = !!base && !!policy && (policy !== base.preferences || dates !== datesToText(base.preferences)
    || quiet[0] !== toTime(base.preferences.quiet_start) || quiet[1] !== toTime(base.preferences.quiet_end));
  useDraftGuard("companion", dirty);

  function accept(next: Preferences) {
    setBase(next);
    setDraft(next.preferences);
    setDates(datesToText(next.preferences));
    setQuiet([toTime(next.preferences.quiet_start), toTime(next.preferences.quiet_end)]);
  }

  useEffect(() => {
    localId.current = id;
    setLive(undefined);
    setMemory(undefined);
    setError("");
    setMemoryError("");
    setConfirm(undefined);
  }, [id]);

  useEffect(() => {
    if (!active || !id || dirty || busy) return;
    const at = ++sequence.current;
    request<Preferences>("companion.read", { session: id }).then(next => {
      if (at === sequence.current) { if (next.revision !== base?.revision) accept(next); setError(""); }
    }).catch(e => { if (at === sequence.current) setError(errorMessage(e)); });
    return () => { sequence.current++; };
  }, [active, id, refresh, dirty, busy, base?.revision]);

  useEffect(() => {
    if (!active || !id || tab === "memory") return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (!document.hidden) {
        try {
          const value = await request<Live>("companion.state", { session: id });
          if (alive) setLive(old => old?.session === id && JSON.stringify(old.data) === JSON.stringify(value)
            ? old : { session: id, data: value });
        } catch { if (alive) setLive(undefined); }
      }
      if (alive) timer = setTimeout(tick, 1500);
    };
    void tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [active, id, tab, refresh]);

  useEffect(() => {
    if (!active || !id || tab !== "memory") return;
    let alive = true;
    request<Memory>("companion.memory", { session: id }).then(next => {
      if (alive) { setMemory({ session: id, data: next }); setMemoryError(""); }
    }).catch(e => { if (alive) setMemoryError(errorMessage(e)); });
    return () => { alive = false; };
  }, [active, id, tab, refresh]);

  function field<K extends keyof Policy>(key: K, value: Policy[K]) {
    setDraft(before => before ? { ...before, [key]: value } : before);
  }

  async function perform(label: string, fn: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    try { await fn(); } catch (e) { notify(errorMessage(e), true); }
    finally { setBusy(""); }
  }

  const save = () => base && policy && id && perform("save", async () => {
    const dateRows = dates.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
      const match = /^(\d{2}-\d{2})\s+(.+)$/.exec(line);
      if (!match) throw new Error("重要日期请每行填写 MM-DD 和名称");
      return { date: match[1], label: match[2] };
    });
    const next = await request<Preferences>("companion.save", { session: id, expected: base.revision,
      preferences: { ...policy, quiet_start: fromTime(quiet[0]), quiet_end: fromTime(quiet[1]), dates: dateRows } });
    if (localId.current === id) { accept(next); setRefresh(value => value + 1); }
    notify("本会话的陪伴设置已保存");
  });

  const command = (action: "once" | "preview") => id && perform(action, async () => {
    await request("companion.command", { session: id, action });
    setRefresh(value => value + 1);
    notify(action === "once" ? "已安排一次主动聊天，发送结果见工作台" : "已提交预览请求");
  });
  const addRoutine = () => policy && perform("routine", async () => {
    const row = await request<Routine>("companion.preset", { name: template });
    field("routines", [...policy.routines, row]);
  });
  const editRoutine = (routine: Routine, changes: Partial<Routine>) => {
    if (policy) field("routines", policy.routines.map(row => row.id === routine.id ? { ...row, ...changes } : row));
  };
  const backup = (action: "import" | "export") => id && selection && perform("backup", async () => {
    const updated = await window.shiguang.openBackup(action, id, selection.name);
    if (updated) { setBase(undefined); setMemory(undefined); setRefresh(value => value + 1); }
  });
  const choose = (value: string) => {
    if (value === id) return;
    if (dirty) setConfirm(value);
    else { setSelected(value); setMemory(undefined); }
  };
  const openTab = (value: typeof tab) => {
    setTab(value);
    if (scroll.current) scroll.current.scrollTop = 0;
  };

  function range(kind: Kind) {
    return <div className="companion-line" key={kind}>
      <span><b>{rangeLabels[kind]}</b></span>
      <div className="range-pair">
        {(["min", "max"] as const).map((bound, index) => <label className="field" key={bound}>
          <input type="number" min={1} max={1440} disabled={!mutable}
            aria-label={rangeLabels[kind] + (index ? "最长" : "最短")}
            value={policy?.[`${kind}_${bound}_minutes`] ?? ""}
            onChange={event => field(`${kind}_${bound}_minutes`, Number(event.target.value))} />
        </label>)}<small>分钟</small>
      </div>
    </div>;
  }

  if (!id || !selection) return <div className="glass phase-note"><Empty title="请先添加监听会话" /></div>;
  const nextRun = (live?.routines ?? []).filter(row => row.next_due_at > 0).sort((a, b) => a.next_due_at - b.next_due_at)[0];
  return <div className="companion-page" data-tab={tab}>
    <div className="companion-scroll" ref={scroll}>
      <div className="companion-nav">
        <div className="flex glass companion-selector"><span>目标会话</span><Select label="陪伴会话"
          value={id} options={status?.sessions.map(row => ({ value: row.id, label: row.name })) ?? []}
          disabled={!!busy} onChange={choose} /></div>
        <div role="group" aria-label="陪伴功能" className="companion-tabs">{tabs.map(([value, label]) =>
          <Button key={value} variant={tab === value ? "primary" : "ghost"} aria-pressed={tab === value} onClick={() => openTab(value)}>{label}</Button>
        )}</div>
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {tab === "policy" && <div className="companion-columns">
        <section className="glass panel companion-policy">
          <PanelTitle icon={<MessageCircle size={20} />} end={<Switch checked={policy?.enabled ?? false} disabled={!mutable}
            onChange={value => field("enabled", value)}>启用主动聊天</Switch>}>主动聊天</PanelTitle>
          <p className="muted fine-print">按本会话的节奏和上下文安排话题</p>
          {range("idle")}{range("followup")}
          <div className="companion-line"><b>每日次数上限</b>
            <label className="field"><input type="number" min={1} max={24} disabled={!mutable}
              aria-label="每日次数上限" value={policy?.daily_max ?? ""} onChange={event => field("daily_max", Number(event.target.value))} /></label>
          </div>
          <div className="companion-line"><b>安静时段</b>
            <div className="range-pair">{quiet.map((value, index) => <label key={index} className="field">
              <input type="time" value={value} disabled={!mutable} aria-label={index ? "结束安静时段" : "开始安静时段"}
                onChange={event => setQuiet(values => values.map((time, at) => at === index ? event.target.value : time))} />
            </label>)}<small>北京时间</small></div>
          </div>
          <div className="flex companion-toggles">
            <Switch checked={policy?.idle ?? false} disabled={!mutable} onChange={value => field("idle", value)}>空闲话题</Switch>
            <Switch checked={policy?.followup ?? false} disabled={!mutable} onChange={value => field("followup", value)}>聊后续话</Switch>
            <Switch checked={policy?.gentle_followups ?? false} disabled={!mutable} onChange={value => field("gentle_followups", value)}>温和追问</Switch>
          </div>
          <Button className="advanced-button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>{advanced ? "收起高级设置" : "高级陪伴设置"}</Button>
          {advanced && <div className="form-stack companion-advanced">
            {range("checkin")}{range("signoff")}
            <div className="flex probability">
              {(["idle", "followup"] as const).map(kind => <label key={kind} className="field">{rangeLabels[kind]}概率 %
                <input type="number" min={0} max={100} step="any" disabled={!mutable}
                  value={policy ? Number((100 * policy[`${kind}_probability`]).toFixed(4)) : ""}
                  onChange={event => field(`${kind}_probability`, Number(event.target.value) / 100)} /></label>)}
            </div>
            <label className="field">陪伴语气<textarea rows={3} maxLength={2000} disabled={!mutable}
              value={policy?.style ?? ""} onChange={event => field("style", event.target.value)} /></label>
            <label className="field">关注的兴趣<input maxLength={500} disabled={!mutable}
              value={policy?.interests ?? ""} onChange={event => field("interests", event.target.value)} /></label>
            <label className="field">重要日期（每行 MM-DD 名称）<textarea rows={3} disabled={!mutable} value={dates}
              onChange={event => setDates(event.target.value)} /></label>
          </div>}
        </section>
        <div className="companion-side">
          <section className="glass panel companion-preview">
            <PanelTitle icon={<Send size={18} />} end={<small className="muted">{live?.nextAt
              ? `${dateText(live.nextAt)} · ${live.nextStage}`
              : live?.reason || (live?.connected ? "暂未安排" : "等待读取后台状态")}</small>}>下一次候选</PanelTitle>
            <p className="preview-text">{live?.preview || "尚未生成预览"}</p>
            {(live?.once || live?.previewState) && <small className="muted">{[live.once, live.previewState].filter(Boolean).join(" · ")}</small>}
            <div className="flex">
              <Button className="peach-outline" icon={<Eye size={16} />} busy={busy === "preview"} disabled={!mutable || !live?.connected}
                onClick={() => void command("preview")}>生成预览</Button>
              <Button variant="primary" icon={<CalendarDays size={16} />} disabled={!mutable || !live?.connected || !base?.preferences.enabled}
                busy={busy === "once"} onClick={() => setConfirm("once")}>安排一次</Button>
            </div>
          </section>
          <section className="glass panel companion-preview">
            <PanelTitle icon={<CalendarDays size={18} />} end={<Button className="peach-outline" icon={<Plus size={15} />}
              onClick={() => openTab("routines")}>管理任务</Button>}>定时任务</PanelTitle>
            {policy?.routines.length ? policy.routines.map(row => <div className="companion-summary-row" key={row.id}>
              <Clock size={15} /><b>{row.time}</b><span>{row.intent}</span></div>) : <Empty title="暂无定时关怀" detail="可按星期与时间配置问候。" />}
          </section>
        </div>
      </div>}
      {tab === "routines" && <div className="companion-columns">
        <section className="glass panel companion-policy">
          <PanelTitle icon={<CalendarDays size={19} />} end={<Button className="peach-outline" icon={<Plus size={15} />} disabled={!mutable || (policy?.routines.length ?? 0) >= 8}
            busy={busy === "routine"} onClick={() => void addRoutine()}>新建</Button>}>定时关怀</PanelTitle>
          <div className="flex companion-templates">{templates.map(([value, label]) => <Button key={value}
            variant={template === value ? "primary" : undefined} aria-pressed={template === value} onClick={() => setTemplate(value)}>{label}</Button>)}</div>
          {policy?.routines.map(row => <section key={row.id} className="routine-entry">
            <div className="flex routine-heading"><Clock size={16} />
              <label className="field"><input aria-label="任务时间" type="time" value={row.time} disabled={!mutable}
                onChange={event => editRoutine(row, { time: event.target.value })} /></label>
              <div className="routine-enabled">
                <Switch checked={row.enabled} disabled={!mutable} onChange={enabled => editRoutine(row, { enabled })}>启用</Switch>
              </div>
              <Switch checked={row.prefer_sticker} disabled={!mutable} onChange={prefer_sticker => editRoutine(row, { prefer_sticker })}>搭配表情</Switch>
              <IconButton label="删除任务" icon={<Trash2 size={16} />} disabled={!mutable}
                onClick={() => field("routines", policy.routines.filter(item => item.id !== row.id))} />
            </div>
            <div className="routine-days flex">{weekdays.map((day, index) => <label key={day}>
              <input type="checkbox" checked={row.weekdays.includes(index)} disabled={!mutable}
                onChange={event => editRoutine(row, { weekdays: event.target.checked
                  ? [...row.weekdays, index].sort((a, b) => a - b) : row.weekdays.filter(value => value !== index) })} />周{day}
            </label>)}</div>
            <label className="field">关怀意图<textarea rows={2} value={row.intent} maxLength={160} disabled={!mutable}
              onChange={event => editRoutine(row, { intent: event.target.value })} /></label>
          </section>)}
          {!policy?.routines.length && <Empty icon={<CalendarDays size={24} />} title="还没有定时任务" detail="选择一个模板后点新建。" />}
        </section>
        <section className="glass panel companion-preview">
          <PanelTitle icon={<Activity size={18} />}>执行记录</PanelTitle>
          <p className="routine-next"><Clock size={16} />{!base?.preferences.enabled ? "主动聊天未开启" : live?.mode === "paused" ?
            "此会话已暂停" : nextRun ? `下一次 · ${dateText(nextRun.next_due_at)}` : "暂无执行时间"}</p>
          {(live?.history ?? []).length ? <div className="routine-history">{[...live!.history].reverse().map((row, index) => <div
            className="companion-summary-row" key={row.routine_id + row.day + index}>
            <time>{row.day}</time><span>{base?.preferences.routines.find(item => item.id === row.routine_id)?.intent || "历史任务"}</span>
            <span className="tag">{statuses[row.status] || row.status}</span>
          </div>)}</div> : <Empty title="暂无执行记录" />}
        </section>
      </div>}
      {tab === "memory" && <>
        {memoryError && <p className="inline-error" role="alert">{memoryError}</p>}
        <div className="memory-metrics">
          {([[BookOpen, "原文记录", memory?.messages], [Layers2, "来源记忆", memory?.facts], [CalendarDays, "本机日记", memory?.diaries]] as const).map(
            ([Icon, label, value]) => <div key={label} className="glass memory-metric">
              <Icon size={21} /><b>{label}</b><strong>{value ?? "—"}</strong></div>)}
        </div>
        <div className="companion-columns">
          <section className="glass panel companion-preview memory-summary">
            <PanelTitle icon={<MessageCircle size={19} />}>最近会话摘要</PanelTitle>
            <p className="memory-entry">{memory?.summary || "暂无摘要"}</p>
            <h3>有来源的记忆</h3>
            {memory?.sources.length ? memory.sources.map((row, index) => <p className="memory-entry" key={index}><b>{row.author}</b>{row.quote}</p>)
              : <Empty title="暂无来源记忆" />}
          </section>
          <section className="glass panel companion-preview memory-journal">
            <PanelTitle icon={<CalendarDays size={19} />}>本机日记</PanelTitle>
            {memory?.journal.length ? memory.journal.map(row => <p className="memory-entry" key={row.day}><b>{row.day}</b>{row.text}</p>)
              : <Empty title="暂无日记" />}
          </section>
        </div>
      </>}
    </div>
    <footer className="companion-footer glass">
      <span className="fine-print muted"><Info size={15} />仅作用于 {selection.name}{dirty ? " · 有未保存的修改" : ""}</span>
      <div className="flex">
        {tab !== "memory" ? <>
          {tab === "routines" && <Button icon={<ArrowLeft size={15} />} onClick={() => openTab("policy")}>返回</Button>}
          {dirty && <Button disabled={!!busy} onClick={() => base && accept(base)}>撤销修改</Button>}
          <Button variant="primary" icon={<Check size={16} />} busy={busy === "save"} disabled={!mutable || !dirty}
            onClick={() => void save()}>{tab === "routines" ? "保存定时任务" : "保存陪伴设置"}</Button>
        </> : <>
          <Button className="peach-outline" icon={<RefreshCcw size={15} />} onClick={() => setRefresh(value => value + 1)}>刷新记忆</Button>
          <Button className="peach-outline" icon={<Upload size={15} />} disabled={!mutable} onClick={() => void backup("export")}>导出加密备份</Button>
          <Button className="peach-outline" icon={<ArrowDownToLine size={15} />} disabled={!mutable} onClick={() => void backup("import")}>导入备份</Button>
        </>}
      </div>
    </footer>
    {confirm && <Confirm title={confirm === "once" ? "安排一次主动聊天" : "切换陪伴会话"}
      detail={confirm === "once" ? `为“${selection.name}”安排一次实际微信发送。后台会等待发送条件满足。`
        : "当前陪伴设置有草稿。确认后放弃草稿，打开另一个会话。"}
      cancel={() => setConfirm(undefined)} confirm={() => {
        if (confirm === "once") void command("once");
        else { setSelected(confirm); setBase(undefined); setDraft(undefined); setMemory(undefined); }
        setConfirm(undefined);
      }} />}
  </div>;
}

interface BackupPreview { token: string; messages: number; facts: number; diaries: number; name: string }
export function BackupWindow() {
  const [context, setContext] = useState<{ action: "import" | "export"; name: string }>();
  const [file, setFile] = useState("");
  const [password, setPassword] = useState("");
  const [preview, setPreview] = useState<BackupPreview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const call = <T,>(action: string, values = {}) => window.shiguang.backupAction(action, values) as Promise<T>;
  const close = () => void call("close");
  useEffect(() => {
    call<typeof context>("context").then(setContext).catch(e => setError(errorMessage(e)));
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try { await operation(); } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  const importing = context?.action === "import";
  return <div className="calibration-shell backup-shell">
    <header className="close-chrome drag-region"><LockKeyhole size={18} />
      <b>{importing ? "导入会话备份" : "导出会话备份"}</b>
      <IconButton label="关闭备份窗口" icon={<X size={15} />} onClick={close} disabled={busy} /></header>
    <div className="calibration-body backup-body">
      <p className="muted">{context?.name}</p>
      {saved ? <Empty title={importing ? "备份已合并" : "加密备份已导出"}
        detail={importing ? "此会话的主动聊天已关闭。" : "请妥善保管备份口令。"} /> : <>
        {importing && <Button icon={<FolderOpen size={16} />} disabled={busy} onClick={() => void run(async () => {
          const name = await call<string | null>("pick");
          if (name) { setFile(name); setPreview(undefined); }
        })}>{file || "选择 .xlc 备份文件"}</Button>}
        <label className="field">备份口令<input type="password" value={password} disabled={busy} maxLength={1024}
          autoComplete="off" onChange={event => { setPassword(event.target.value); setPreview(undefined); }} /></label>
        <p className="muted fine-print">{importing ? "核对会话身份后预览，合并时保留本机较新内容。" : "使用至少八个字符的独立口令。"}</p>
        {preview && <div className="backup-metrics">
          <span>原文 <b>{preview.messages}</b></span><span>记忆 <b>{preview.facts}</b></span><span>日记 <b>{preview.diaries}</b></span>
        </div>}
        {error && <p className="inline-error" role="alert">{error}</p>}
      </>}
    </div>
    <footer className="calibration-actions">
      {saved ? <Button variant="primary" onClick={close}>完成</Button> : <>
        <Button disabled={busy} onClick={close}>取消</Button>
        <Button variant="primary" busy={busy} disabled={!context || password.length < 8 || (importing && !file)}
          onClick={() => void run(async () => {
            if (importing && !preview) { setPreview(await call("preview", { password })); return; }
            const success = await call<boolean>(importing ? "restore" : "export", { token: preview?.token, password });
            if (success) { setPassword(""); setSaved(true); }
          })}>{importing ? preview ? "确认合并" : "核对备份" : "导出加密备份"}</Button>
      </>}
    </footer>
  </div>;
}
