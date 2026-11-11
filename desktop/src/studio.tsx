import { BookOpen, MessageCircle, RotateCcw, Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import { Button, Empty, PanelTitle } from "./components";
import "./studio.css";

type Delivery = { queued?: boolean; duplicate?: boolean; id?: string; state: string; submittedText?: string };
type Job<T> = { job: string; kind: string; conversation?: string; status: string; result?: T; error?: string; delivery?: Delivery };
type ChatResult = { text: string; turns: number; contextLimit: number; enabledModules: string[] };
type SummaryResult = { highlights: string[]; tasks: string[]; dates: string[]; sendText: string;
  messageCount: number; availableCount: number; coveredStart: number | null; coveredEnd: number | null;
  truncated: boolean; insufficient: boolean; storedOnly: boolean };
type DraftModule = { id: string; title: string; text: string; enabled: boolean; origin: string; category?: string };
const running = (status?: string) => status === "queued" || status === "running";
const states: Record<string, string> = { queued: "等待模型", running: "模型生成中", completed: "预览已生成", canceled: "已取消",
  invalidated: "账号、会话或人格已变化，请重新生成", timed_out: "生成超时，请重试", failed: "生成失败" };
const deliveryStates: Record<string, string> = { queued: "已加入发送队列，等待回执", generated: "已加入发送队列，等待回执",
  submitting: "正在提交原正文，请等待核对", unresolved: "提交结果未确认，原正文已锁定；可核对同一发送请求",
  duplicate: "此预览已经提交，请在工作台查看回执", verified: "微信发送已验证", sent: "等待微信回执", sending: "正在发送，等待回执",
  unconfirmed: "发送结果未确认，请核对微信", unknown: "回执状态暂不可用，请查看工作台", failed: "发送失败，请查看工作台",
  skipped: "发送已跳过，请查看工作台", canceled: "发送已取消" };
const dateText = (value: number | null) => value ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(value * 1000) : "—";

/** One active view owns polling. Generation and navigation fence every RPC continuation. */
function useStudioJob<T>(active: boolean, scope: string) {
  const [job, setJob] = useState<Job<T>>();
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const pendingStart = useRef(false);
  const current = useRef<Job<T> | undefined>(undefined);
  const epoch = useRef(0);
  function clear() {
    epoch.current++;
    const old = current.current;
    current.current = undefined;
    pendingStart.current = false;
    setJob(undefined); setError(""); setStarting(false);
    if (old) void request("studio.cancel", { job: old.job }).catch(() => undefined);
  }
  useEffect(() => { clear(); return clear; }, [active, scope]);
  useEffect(() => {
    if (!active || !job?.job) return;
    const generation = epoch.current;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const next = await request<Job<T>>("studio.state", { job: job!.job });
        if (alive && generation === epoch.current) { current.current = next; setJob(next); }
      } catch (e) { if (alive && generation === epoch.current) setError(errorMessage(e)); }
      if (alive && generation === epoch.current) timer = setTimeout(tick, 1000);
    }
    void tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [active, job?.job]);
  async function start(method: string, params: Record<string, unknown>) {
    if (pendingStart.current || running(current.current?.status)) return;
    const generation = ++epoch.current;
    pendingStart.current = true;
    current.current = undefined;
    setJob(undefined); setStarting(true); setError("");
    try {
      const next = await request<Job<T>>(method, params);
      if (generation !== epoch.current) {
        void request("studio.cancel", { job: next.job }).catch(() => undefined);
        return;
      }
      current.current = next; setJob(next);
    } catch (e) {
      if (generation === epoch.current) { setError(errorMessage(e)); return "rejected" as const; }
    }
    finally { if (generation === epoch.current) { pendingStart.current = false; setStarting(false); } }
  }
  async function cancel() {
    const old = current.current;
    const generation = ++epoch.current;
    pendingStart.current = false;
    setStarting(false);
    if (!old) return;
    try {
      const next = await request<Job<T>>("studio.cancel", { job: old.job });
      if (generation === epoch.current) { current.current = next; setJob(next); }
    } catch (e) { if (generation === epoch.current) setError(errorMessage(e)); }
  }
  return { job, error, setError, start, clear, cancel, busy: starting || running(job?.status) };
}

export function PersonaTrial({ active, readOnly, accountRevision, expected, title, modules }: {
  active: boolean; readOnly: boolean; accountRevision: string; expected: string; title: string; modules: DraftModule[];
}) {
  const scope = JSON.stringify([accountRevision, expected, title, modules]);
  const trial = useStudioJob<ChatResult>(active, scope);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<{ role: string; text: string }[]>([]);
  const [conversation, setConversation] = useState("");
  const displayed = useRef("");
  const pendingText = useRef<string | undefined>(undefined);
  const end = useRef<HTMLDivElement>(null);
  function restorePending() {
    const message = pendingText.current;
    if (message === undefined) return;
    pendingText.current = undefined;
    setTurns(old => old.at(-1)?.role === "user" && old.at(-1)?.text === message ? old.slice(0, -1) : old);
    setText(old => old || message);
  }
  useEffect(() => { setTurns([]); setConversation(""); displayed.current = ""; pendingText.current = undefined; setText(""); }, [active, scope]);
  useEffect(() => {
    const job = trial.job;
    if (job?.status === "completed" && job.result && displayed.current !== job.job) {
      displayed.current = job.job;
      pendingText.current = undefined;
      setConversation(job.conversation ?? "");
      setTurns(old => [...old, { role: "assistant", text: job.result!.text }].slice(-24));
    }
    if (job?.status === "invalidated") { pendingText.current = undefined; setTurns([]); setConversation(""); }
    if (["canceled", "failed", "timed_out"].includes(job?.status ?? "")) restorePending();
  }, [trial.job]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest", behavior: "auto" }); }, [turns]);
  const enabled = modules.filter(row => row.enabled);
  async function submit() {
    if (!text.trim() || trial.busy || readOnly || pendingText.current !== undefined) return;
    const message = text;
    pendingText.current = message;
    // Trim only after commitment so a rejected turn cannot evict committed history.
    setTurns(old => [...old, { role: "user", text: message }]); setText("");
    const outcome = await trial.start("studio.chat", { expected, title, modules, conversation, text: message, request_id: crypto.randomUUID() });
    if (outcome === "rejected" && pendingText.current === message) restorePending();
  }
  return <section className="glass panel studio-trial">
    <PanelTitle icon={<MessageCircle size={18} />} end={<Button icon={<RotateCcw size={15} />} disabled={readOnly}
      onClick={() => { trial.clear(); pendingText.current = undefined; setTurns([]); setConversation(""); }}>清空试聊</Button>}>人格试聊</PanelTitle>
    <p className="muted fine-print">使用当前启用草稿 · 独立试聊记忆 · 不会发送微信或写入正式记忆</p>
    <div className="studio-module-overview" aria-label="试聊启用模块">{enabled.map(row => <span className="tag" key={row.id}>{row.title}</span>)}</div>
    <div className="studio-chat-log" role="log" aria-label="人格试聊记录" aria-live="polite">
      {!turns.length && <Empty title="试试现在的人格" detail="草稿调整后会开启新的试聊；上下文最多保留 24 条消息。" />}
      {turns.map((turn, index) => <div className={`studio-bubble ${turn.role}`} key={index}>
        <small>{turn.role === "user" ? "我" : "拾光"}</small><p>{turn.text}</p></div>)}<div ref={end} />
    </div>
    {(trial.error || trial.job?.error) && <p className="inline-error" role="alert">{trial.error || trial.job?.error}</p>}
    <p className="studio-job-note muted fine-print" role="status">{states[trial.job?.status ?? ""] ?? ""}</p>
    <label className="field">试聊消息<textarea rows={3} maxLength={4000} value={text} disabled={readOnly || trial.busy}
      placeholder="聊点什么，看看这版人格如何回应…" onChange={event => setText(event.target.value)}
      onKeyDown={event => { if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); submit(); } }} /></label>
    <div className="studio-actions"><small className="muted">Ctrl + Enter 发送 · {text.length} / 4000</small><div className="flex">
      {trial.busy && <Button icon={<Square size={14} />} onClick={() => { if (!trial.job) restorePending(); void trial.cancel(); }}>取消生成</Button>}
      <Button variant="primary" icon={<Send size={15} />} busy={trial.busy} disabled={readOnly || !text.trim() || !enabled.length}
        onClick={submit}>发送试聊</Button></div></div>
  </section>;
}

function localBeijing(date: Date) { return new Date(date.getTime() + 8 * 3600000).toISOString().slice(0, 16); }
export function ConversationSummary({ active, session, name, revision, readOnly, bound, canSend }: {
  active: boolean; session: string; name: string; revision: string; readOnly: boolean; bound: boolean; canSend: boolean;
}) {
  const [start, setStart] = useState(() => localBeijing(new Date(Date.now() - 86400000)));
  const [end, setEnd] = useState(() => localBeijing(new Date()));
  const summary = useStudioJob<SummaryResult>(active, JSON.stringify([session, revision, start, end]));
  const [editor, setEditor] = useState("");
  const [delivery, setDelivery] = useState<Delivery>();
  const [sending, setSending] = useState(false);
  const generation = useRef(0);
  const result = summary.job?.status === "completed" ? summary.job.result : undefined;
  useEffect(() => { generation.current++; setEditor(""); setDelivery(undefined); setSending(false); }, [active, session, revision, start, end]);
  useEffect(() => { if (result) setEditor(result.sendText); }, [summary.job?.job, !!result]);
  useEffect(() => { if (summary.job?.delivery) {
    setDelivery(summary.job.delivery);
    if (summary.job.delivery.submittedText !== undefined) setEditor(summary.job.delivery.submittedText);
  } }, [summary.job?.delivery]);
  async function send() {
    if (sending || !summary.job || !canSend || !result || readOnly) return;
    const at = generation.current;
    const submittedText = delivery?.submittedText ?? editor;
    setDelivery({ state: "submitting", submittedText });
    setSending(true); summary.setError("");
    try {
      const response = await request<Delivery>("studio.sendSummary", { job: summary.job.job, text: submittedText });
      if (at === generation.current) setDelivery(response);
    } catch (e) {
      if (at === generation.current) {
        setDelivery({ state: "unresolved", submittedText });
        summary.setError(errorMessage(e));
        try {
          const next = await request<Job<SummaryResult>>("studio.state", { job: summary.job.job });
          if (at === generation.current) setDelivery(next.delivery);
        } catch { /* Keep the frozen payload while the bridge cannot confirm acceptance. */ }
      }
    }
    finally { if (at === generation.current) setSending(false); }
  }
  return <div className="studio-summary">
    <section className="glass panel studio-summary-range"><PanelTitle icon={<BookOpen size={18} />}>会话总结</PanelTitle>
      <p className="muted">仅总结「{name}」本机实际保留的原文，不会自动记忆或替换已有事实。</p>
      <div className="studio-range-fields"><label className="field">开始时间 · 北京时间<input type="datetime-local" value={start}
        disabled={readOnly || sending} onChange={event => setStart(event.target.value)} /></label>
        <label className="field">结束时间 · 北京时间<input type="datetime-local" value={end} disabled={readOnly || sending}
          onChange={event => setEnd(event.target.value)} /></label></div>
      <div className="flex"><Button variant="primary" busy={summary.busy} disabled={readOnly || !bound || !start || !end || start >= end || sending}
        onClick={() => { setDelivery(undefined); setEditor(""); void summary.start("studio.summary", { session, start, end, request_id: crypto.randomUUID() }); }}>生成总结预览</Button>
        {summary.busy && <Button onClick={() => void summary.cancel()}>取消生成</Button>}
        <span className="muted fine-print" role="status">{states[summary.job?.status ?? ""] ?? (!bound ? "请先确认会话身份" : "")}</span></div>
      {(summary.error || summary.job?.error) && <p className="inline-error" role="alert">{summary.error || summary.job?.error}</p>}
    </section>
    {result && <section className="glass panel studio-summary-preview"><PanelTitle>总结预览</PanelTitle>
      <p className="fine-print muted">已用 {result.messageCount} / {result.availableCount} 条现存记录 · 实际覆盖 {dateText(result.coveredStart)} — {dateText(result.coveredEnd)}（北京时间）</p>
      {(result.truncated || result.insufficient) && <p className="studio-source-note" role="status">{result.truncated
        ? "记录超过本轮上限，仅使用范围内最近的部分原文。" : "可用原文不足，总结可能无法覆盖这段时间。"}</p>}
      <div className="studio-summary-sections">{([["highlights", "讨论要点"], ["tasks", "行动事项"], ["dates", "约定日期"]] as const).map(([key, label]) =>
        <section key={key}><h3>{label}</h3>{result[key].length ? <ul>{result[key].map((text, index) => <li key={index}>{text}</li>)}</ul>
          : <p className="muted">没有原文可确认的{label}</p>}</section>)}</div>
      <label className="field">发送正文（可编辑）<textarea rows={6} value={editor} disabled={readOnly || sending || !!delivery}
        onChange={event => setEditor(event.target.value)} /></label>
      <div className="studio-actions"><span className={editor.length > 400 ? "inline-error fine-print" : "muted fine-print"}>{editor.length} / 400 字 · 将按此处正文发送给「{name}」</span>
        <Button variant="primary" icon={<Send size={15} />} busy={sending}
          disabled={readOnly || !canSend || !editor.trim() || editor.length > 400 || !!delivery && delivery.state !== "unresolved" || sending}
          onClick={() => void send()}>{delivery?.state === "unresolved" ? "核对原发送请求" : "发送总结到所选会话"}</Button></div>
      {editor.length > 400 && <p className="muted fine-print">请将发送正文编辑至 400 字以内，预览原文会完整保留。</p>}
      {!canSend && <p className="muted fine-print">当前会话未满足自动发送条件；预览可以保留，核对会话状态后再发送。</p>}
      {delivery && <p className="studio-source-note" role="status">{deliveryStates[delivery.state] ?? `队列状态：${delivery.state}`}</p>}
    </section>}
  </div>;
}
