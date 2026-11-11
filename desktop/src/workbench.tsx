import { Check, CircleCheck, Crosshair, Eye, Headphones, Image, MessageCircle, Pause, Play, RefreshCw, Send, Settings2, UsersRound, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { errorMessage, request, ruleLabels, type PageKey, type Queue, type Session, type Status } from "./api";
import { Button, Confirm, Empty, IconButton, PanelTitle, Select, StatusPill } from "./components";

const queueState: Record<string, string> = {
  candidate: "正在生成", generated: "待发送", staged: "核对输入",
  sending: "发送中", awaiting_receipt: "等待回执", unknown: "结果未知",
  verified: "已确认", failed: "未发送", skipped: "已跳过",
};
const number = new Intl.NumberFormat("zh-CN");
const time = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
const detailTime = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const dateAndTime = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
const RECEIPT_PAGE = 4;
type QueuePosition = { scope: string; receiptOffset: number };
type QueueView = "tasks" | "receipts";
type Receipt = Queue["receipts"][number];
type Task = Queue["tasks"][number];

function SessionIcon({ session }: { session?: Session }) {
  return <span className="glyph">{session?.kind === "private" ? <UserRound size={19} /> : <UsersRound size={19} />}</span>;
}

function sessionRule(session: Session) {
  return [session.kind === "private" ? "私聊" : "群聊", ruleLabels[session.replyMode]].filter(Boolean).join(" · ");
}

function ReceiptTime({ at, detail = false }: { at: number; detail?: boolean }) {
  const date = new Date(at * 1000);
  const today = date.toDateString() === new Date().toDateString();
  return <time dateTime={date.toISOString()} title={date.toLocaleString("zh-CN")}>
    {(today ? detail ? detailTime : time : dateAndTime).format(date)}
  </time>;
}

function ReplyDetail({ session, receipt, task }: { session?: Session; receipt?: Receipt; task?: Task }) {
  const source = receipt?.trigger || task?.text;
  const media = receipt?.kind === "emoji" ? "已发送表情包" : receipt?.kind === "image" ? "已发送图片" : "";
  const body = receipt?.text || task?.reply;
  return <>
    <div className="reply-meta">
      <SessionIcon session={session} />
      <div className="reply-conversation">
        <strong>{receipt?.group ?? task?.group}</strong>
        {session && <small>{sessionRule(session)}</small>}
      </div>
      {receipt ? <>
        <span className="task-state verified"><Check size={15} />已确认</span>
        <ReceiptTime at={receipt.at} detail />
      </> : task && <span className={`task-state ${task.state}`}>{queueState[task.state] ?? task.state}</span>}
    </div>
    <div className="reply-messages">
      <section className="reply-message">
        <h3><MessageCircle size={17} />触发消息</h3>
        <div className="reply-message-body">{source || (receipt?.linked ? "触发原文未保留" : "无关联的触发消息")}</div>
      </section>
      <section className="reply-message">
        <h3>{media ? <Image size={17} /> : <MessageCircle size={17} />}{receipt ? "实际回复" : "拟发送回复"}</h3>
        <div className="reply-message-body">{media && <strong>{media}</strong>}{body || "尚未生成回复"}</div>
      </section>
    </div>
    <div className="reply-foot">
      <span>{receipt ? receipt.linked ? "触发来源 · 会话消息" : "触发来源 · 无关联消息" : task?.sender}</span>
      {receipt ? <span><Check size={15} />微信回执已核对</span> : task?.progress && <span>{task.progress}</span>}
    </div>
  </>;
}

export function Workbench({ active, status, notify, navigate, calibrate }: {
  active: boolean; status?: Status;
  notify: (message: string, error?: boolean) => void; navigate: (page: PageKey) => void;
  calibrate: (session: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState("");
  const [queue, setQueue] = useState<QueuePosition & { value: Queue }>();
  const [refresh, setRefresh] = useState(0);
  const [position, setPosition] = useState<QueuePosition>();
  const [working, setWorking] = useState("");
  const [recover, setRecover] = useState<Session>();
  const [view, setView] = useState<QueueView>();
  const [picked, setPicked] = useState("");
  const scope = status?.sessions.some(item => item.id === selected) ? selected : "";
  const primary = status?.sessions.find(item => item.id === status.primary);
  const stats = status?.stats;
  const revision = status?.revision;
  const scopeKey = `${revision ?? ""}:${scope}`;
  const pagingScope = JSON.stringify([status?.primary, scope, status?.sessions.map(item => item.id)]);
  const receiptOffset = position?.scope === pagingScope ? position.receiptOffset : 0;
  const shown = status?.connected && queue?.scope === scopeKey ? queue : undefined;
  const data = shown?.value;
  const loadingPage = shown?.receiptOffset !== receiptOffset;
  const shownReceiptOffset = shown?.receiptOffset ?? 0;
  const receiptTotal = data?.receiptTotal ?? 0;
  const activeView = view ?? (data?.total ? "tasks" : "receipts");
  const tasksVisible = activeView === "tasks";
  const receipt = tasksVisible ? undefined : data?.receipts.find(item => item.id === picked) ?? data?.receipts[0];
  const task = tasksVisible ? data?.tasks.find(item => item.id === picked) ?? data?.tasks[0] : undefined;
  const detailGroup = receipt?.group ?? task?.group;
  const detailSession = status?.sessions.find(item => item.name === detailGroup);
  const running = status?.sessions.filter(item => item.mode === "auto").length ?? 0;
  const names = useMemo(() => [{ value: "", label: "全部会话" }, ...(status?.sessions ?? []).map(
    item => ({ value: item.id, label: item.name }))], [status?.sessions]);

  useEffect(() => {
    if (!active || !status?.connected) return;
    let valid = true;
    let reading = false;
    const load = () => {
      if (document.hidden || reading) return;
      reading = true;
      request<Queue>("view.queue", { session: scope, limit: null, receipt_offset: receiptOffset, revision }).then(value => {
        if (valid) setQueue(old => old?.scope === scopeKey && old.receiptOffset === receiptOffset
          && JSON.stringify(old.value) === JSON.stringify(value)
          ? old : { scope: scopeKey, receiptOffset, value });
      }).catch(error => {
        if (valid) setQueue({ scope: scopeKey, receiptOffset,
          value: { tasks: [], receipts: [], total: 0, receiptTotal: 0, note: errorMessage(error) } });
      }).finally(() => { reading = false; });
    };
    load();
    const timer = stats?.pending ? window.setInterval(load, 1000) : undefined;
    return () => { valid = false; window.clearInterval(timer); };
  }, [active, status?.connected, stats?.pending, stats?.verified, stats?.unknown, stats?.failed, scope, receiptOffset, revision, scopeKey, refresh]);

  const act = async (key: string, method: string, params = {}) => {
    if (working) return;
    setWorking(key);
    try {
      const result = await request<Status>(method, params);
      setRefresh(value => value + 1);
      if (key !== "start" || result.connected) {
        notify(key === "start" ? "后台已连接" : "操作已应用");
      }
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setWorking("");
    }
  };
  const updateScope = (id: string) => {
    if (id === scope) return;
    setSelected(id);
    setPicked("");
    setPosition(undefined);
  };
  const switchView = (next: QueueView) => {
    setView(next);
    if (next !== activeView) setPicked("");
  };
  const pageTo = (receiptOffset: number) => {
    setPicked("");
    setPosition({ scope: pagingScope, receiptOffset });
  };
  const toggle = (session: Session) => act(session.id, "run.selected",
    { session: session.id, action: session.mode === "auto" ? "pause" : "enable" });
  const calibrateSession = async (session: Session) => {
    if (working) return;
    setWorking(`calibrate:${session.id}`);
    try {
      await calibrate(session.id);
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setWorking("");
    }
  };
  const controlsDisabled = !status?.connected || status.readOnly || Boolean(working);
  const canPauseAll = status?.connected && status.sessions.length > 0 && status.mode !== "paused";
  const canEnableAll = status && status.sessions.length > 0
    && (status.mode !== "auto" || status.sessions.some(session => session.mode !== "auto"));
  const queueEmpty = tasksVisible ? !data?.tasks.length : !data?.receipts.length;
  const emptyTitle = !status?.connected ? "等待后台连接" : !data ? "正在读取队列"
    : tasksVisible ? "当前没有待回复消息" : "当前没有已确认回执";

  return <div className="workbench">
    <section className="glass panel reply-panel">
      <PanelTitle icon={<MessageCircle size={20} />}>回复详情
        {receipt && <small className="subpill">{shownReceiptOffset === 0 && receipt.id === data?.receipts[0]?.id ? "最新回执" : "已选回执"}</small>}
      </PanelTitle>
      {receipt || task ? <ReplyDetail session={detailSession} receipt={receipt} task={task} />
        : <div className="reply-empty">
          <Empty title="暂无回复详情" detail={status?.connected ? "从发送队列选择一条消息" : status?.reason} icon={<MessageCircle size={25} />} />
          {status && !status.connected && <Button variant="primary" disabled={status.readOnly || Boolean(working)}
            busy={working === "start" || status.mode === "starting"}
            onClick={() => void act("start", "run.start")}>{status.mode === "starting" ? "连接中" : "连接后台"}</Button>}
        </div>}
    </section>
    <section className="glass panel queue-panel">
      <header className="panel-title queue-toolbar">
        <h2><Send size={20} />发送队列</h2>
        <div className="queue-tabs" role="group" aria-label="队列状态">
          <Button aria-pressed={tasksVisible} variant={tasksVisible ? "primary" : "ghost"} onClick={() => switchView("tasks")}>
            待发送 <span>{data ? number.format(data.total) : "—"}</span>
          </Button>
          <Button aria-pressed={!tasksVisible} variant={!tasksVisible ? "primary" : "ghost"} onClick={() => switchView("receipts")}>
            已确认 <span>{data ? number.format(receiptTotal) : "—"}</span>
          </Button>
        </div>
        <div className="panel-end">
          <Select label="发送队列会话" options={names} value={scope} onChange={updateScope} />
          <IconButton label="刷新队列" icon={<RefreshCw size={17} />} onClick={() => setRefresh(value => value + 1)} />
        </div>
      </header>
      <div className="queue-list" aria-busy={loadingPage && status?.connected}>
        {tasksVisible ? data?.tasks.map(item => <button type="button" className="queue-row"
          key={item.id} aria-pressed={item.id === task?.id} onClick={() => setPicked(item.id)}>
          <span className="queue-icon peach"><Send size={22} /></span>
          <span className="queue-body"><b>{item.group}</b><span>{item.reply || item.text || item.progress}</span></span>
          <span className={`task-state ${item.state}`}>{queueState[item.state] ?? item.state}</span>
        </button>) : data?.receipts.map(item => <button type="button" className="queue-row"
          key={item.id} aria-pressed={item.id === receipt?.id} onClick={() => setPicked(item.id)}>
          <span className="queue-icon mint"><CircleCheck size={24} /></span>
          <span className="queue-body"><b>{item.group}</b><span>{item.text}</span></span>
          <ReceiptTime at={item.at} />
        </button>)}
        {queueEmpty && <Empty icon={<Send size={24} />} title={emptyTitle} detail={data?.note || status?.reason} />}
        {!queueEmpty && data?.note && <p className="queue-note muted">{data.note}</p>}
      </div>
      <div className="queue-foot">
        {!tasksVisible && (receiptTotal > 0 || shownReceiptOffset > 0) && <nav className="flex" aria-label="已确认回执分页">
          <span className="muted" aria-live="polite">{data?.receipts.length
            ? `第 ${shownReceiptOffset + 1}–${shownReceiptOffset + data.receipts.length} 条` : "没有更多回执"}</span>
          <Button variant="ghost" disabled={loadingPage || shownReceiptOffset === 0}
            onClick={() => pageTo(Math.max(0, receiptOffset - RECEIPT_PAGE))}>较新</Button>
          <Button variant="ghost" disabled={loadingPage || shownReceiptOffset + RECEIPT_PAGE >= receiptTotal}
            onClick={() => pageTo(receiptOffset + RECEIPT_PAGE)}>更早</Button>
        </nav>}
        <div className="queue-actions">
          <Button variant="ghost" icon={<Eye size={16} />} disabled={controlsDisabled}
            onClick={() => void act("observe", "run.mode", { action: "observe" })}>只观察</Button>
          {(scope === "" || scope === primary?.id) &&
            <Button variant="ghost" disabled={controlsDisabled || !primary} busy={working === "recover"}
              onClick={() => setRecover(primary)}>处理最新点名</Button>}
        </div>
      </div>
    </section>
    <section className="glass panel watch-panel">
      <PanelTitle icon={<Headphones size={21} />} end={<Button variant="ghost" icon={<Settings2 size={16} />}
        onClick={() => navigate("sessions")}>管理会话</Button>}>监听范围</PanelTitle>
      <p className="watch-summary">共 {status?.sessions.length ?? 0} 个会话 · {running} 个运行中</p>
      <div className="watch-list">
        {status?.sessions.map(session => <div key={session.id} className={`watch-row ${scope === session.id ? "chosen" : ""}`}>
          <button className="watch-target" type="button" aria-pressed={scope === session.id}
            title={session.name} onClick={() => updateScope(session.id)}>
            <SessionIcon session={session} /><span><b>{session.name}</b><small>{sessionRule(session)}</small></span>
          </button>
          <StatusPill mode={session.mode} compact />
          <IconButton className="watch-calibrate" label={`${session.calibrated ? "重新校准" : "校准"} ${session.name}`}
            icon={<Crosshair size={17} />} disabled={status?.readOnly !== false || Boolean(working)}
            busy={working === `calibrate:${session.id}`} onClick={() => void calibrateSession(session)} />
          <Button variant="primary" icon={session.mode === "auto" ? <Pause size={15} /> : <Play size={15} />}
            disabled={controlsDisabled || !session.bound} busy={working === session.id}
            onClick={() => void toggle(session)} aria-label={`${session.mode === "auto" ? "暂停" : "开启"} ${session.name}`}>
            {session.mode === "auto" ? "暂停" : "开启"}
          </Button>
        </div>)}
        {!status?.sessions.length && <Empty title="还没有监听会话" detail="可以在会话页添加群聊或联系人" />}
      </div>
      <div className="watch-foot">
        <Button icon={<Play size={15} />} disabled={controlsDisabled || !canEnableAll} busy={working === "allEnable"}
          onClick={() => void act("allEnable", "run.mode", { action: "enable" })}>全部开启</Button>
        <Button variant="primary" icon={<Pause size={15} />} disabled={controlsDisabled || !canPauseAll} busy={working === "allPause"}
          onClick={() => void act("allPause", "run.mode", { action: "pause" })}>全部暂停</Button>
      </div>
    </section>
    {recover && active && <Confirm title="处理最新点名" detail={`核对“${recover.name}”中最新的点名消息，符合发送条件时进入回复队列。`}
      cancel={() => setRecover(undefined)} confirm={() => {
        setRecover(undefined);
        void act("recover", "run.recover", { session: recover.id });
      }} />}
  </div>;
}
