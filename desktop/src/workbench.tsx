import { Check, CircleCheck, Eye, Headphones, Pause, Play, RefreshCw, Send, Settings2, UsersRound, UserRound } from "lucide-react";
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
const TASK_PAGE = 40;
const RECEIPT_PAGE = 4;
type QueuePosition = { scope: string; offset: number; receiptOffset: number };

function SessionIcon({ session }: { session: Session }) {
  return <span className="glyph">{session.kind === "private" ? <UserRound size={19} /> : <UsersRound size={19} />}</span>;
}

export function Workbench({ active, status, notify, navigate }: {
  active: boolean; status?: Status;
  notify: (message: string, error?: boolean) => void; navigate: (page: PageKey) => void;
}) {
  const [selected, setSelected] = useState("");
  const [queue, setQueue] = useState<QueuePosition & { value: Queue }>();
  const [refresh, setRefresh] = useState(0);
  const [position, setPosition] = useState<QueuePosition>();
  const [working, setWorking] = useState("");
  const [recover, setRecover] = useState<Session>();
  const scope = status?.sessions.some(item => item.id === selected) ? selected : "";
  const primary = status?.sessions.find(item => item.id === status.primary);
  const row = status?.sessions.find(item => item.id === scope) ?? primary;
  const stats = status?.stats;
  const scopeKey = `${status?.revision ?? ""}:${scope}`;
  const offset = position?.scope === scopeKey ? position.offset : 0;
  const receiptOffset = position?.scope === scopeKey ? position.receiptOffset : 0;
  const shown = status?.connected && queue?.scope === scopeKey ? queue : undefined;
  const data = shown?.value;
  const loadingPage = shown?.offset !== offset || shown?.receiptOffset !== receiptOffset;
  const shownReceiptOffset = shown?.receiptOffset ?? 0;
  const receiptTotal = data?.receiptTotal ?? 0;
  const names = useMemo(() => [{ value: "", label: "全部会话" }, ...(status?.sessions ?? []).map(
    item => ({ value: item.id, label: item.name }))], [status?.sessions]);

  useEffect(() => {
    if (!active || !status?.connected) return;
    let valid = true;
    let reading = false;
    const load = () => {
      if (document.hidden || reading) return;
      reading = true;
      request<Queue>("view.queue", { session: scope, offset, limit: TASK_PAGE, receipt_offset: receiptOffset }).then(value => {
        if (valid) setQueue(old => old?.scope === scopeKey && old.offset === offset && old.receiptOffset === receiptOffset
          && JSON.stringify(old.value) === JSON.stringify(value)
          ? old : { scope: scopeKey, offset, receiptOffset, value });
      }).catch(error => {
        if (valid) setQueue({ scope: scopeKey, offset, receiptOffset,
          value: { tasks: [], receipts: [], total: 0, receiptTotal: 0, note: errorMessage(error) } });
      }).finally(() => { reading = false; });
    };
    load();
    const timer = stats?.pending ? window.setInterval(load, 1000) : undefined;
    return () => { valid = false; window.clearInterval(timer); };
  }, [active, status?.connected, stats?.pending, stats?.verified, stats?.unknown, stats?.failed, scope, offset, receiptOffset, scopeKey, refresh]);

  const act = async (key: string, method: string, params = {}) => {
    if (working) return;
    setWorking(key);
    try {
      await request(method, params);
      setRefresh(value => value + 1);
      notify(key === "start" ? "后台已连接" : "操作已应用");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setWorking("");
    }
  };
  const updateScope = (id: string) => { setSelected(id); setPosition(undefined); };
  const pageTo = (tasks: number, receipts: number) => setPosition({ scope: scopeKey, offset: tasks, receiptOffset: receipts });
  const toggle = (session: Session) => act(session.id, "run.selected",
    { session: session.id, action: session.mode === "auto" ? "pause" : "enable" });
  const controlsDisabled = !status?.connected || status.readOnly || Boolean(working);
  const canPauseAll = status?.connected && status.mode !== "paused";
  const canEnableAll = status?.mode !== "auto" || status.sessions.some(session => session.mode !== "auto");
  return <div className="workbench page-grid">
    <section className="glass active-session">
      <span className="current-label">当前会话</span>
      {row && <>
        <SessionIcon session={row} />
        <div className="session-title"><strong>{row.name}</strong>
          <span className="subpill">{row.kind === "private" ? "私聊" : "群聊"} · {ruleLabels[row.replyMode]}</span>
        </div>
        <StatusPill mode={row.mode} compact />
      </>}
      <div className="session-actions">
        {status?.connected && row
          ? <Button variant="primary" busy={working === row.id} disabled={controlsDisabled || !row.bound}
              icon={row.mode === "auto" ? <Pause size={15} /> : <Play size={15} />} onClick={() => void toggle(row)}>
              {row.mode === "auto" ? "暂停此会话" : "开启此会话"}
            </Button>
          : <Button variant="primary" disabled={status?.readOnly || !status} busy={working === "start"}
              onClick={() => void act("start", "run.start")}>连接后台</Button>}
        <Button icon={<Settings2 size={16} />} onClick={() => navigate("sessions")}>管理会话</Button>
      </div>
    </section>
    <div className="metrics">
      {[
        { label: "监听范围", value: status?.sessions.length, icon: <Headphones />, tone: "lavender" },
        { label: "待发送", value: stats?.pending, icon: <Send />, tone: "peach" },
        { label: "已确认", value: stats?.verified, icon: <CircleCheck />, tone: "mint" },
      ].map(item => <section className="glass metric" key={item.label}>
        <div className={`metric-icon ${item.tone}`}>{item.icon}</div>
        <div><span>{item.label}</span><strong>{item.value === undefined ? "—" : number.format(item.value)}</strong></div>
      </section>)}
    </div>
    <div className="dashboard-columns">
      <section className="glass panel queue-panel">
        <PanelTitle icon={<Send size={20} />} end={<>
          <Select label="发送队列会话" options={names} value={scope} onChange={updateScope} />
          <IconButton label="刷新队列" icon={<RefreshCw size={17} />} onClick={() => setRefresh(value => value + 1)} />
        </>}>发送队列</PanelTitle>
        <div className="queue-list">
          {data?.tasks.map(task => <article className="queue-row" key={task.id}>
            <span className="glyph peach"><Send size={18} /></span>
            <div className="queue-body">
              <b>{task.group} <span>· {task.sender}</span></b><p>{task.text}</p>
              {task.reply && <p className="queue-reply">{task.reply}</p>}
              {task.progress && <small>{task.progress}</small>}
            </div>
            <span className={`task-state ${task.state}`}>{queueState[task.state] ?? task.state}</span>
          </article>)}
          {!data?.tasks.length && !data?.receipts.length && <Empty icon={<Send size={26} />}
            title={status?.connected ? data ? "当前没有待回复消息" : "正在读取队列" : "等待后台连接"}
            detail={data?.note || status?.reason || "新消息和已确认回执会显示在这里"} />}
          {(receiptTotal > RECEIPT_PAGE || shownReceiptOffset > 0) && <nav className="receipt-pagination flex" aria-label="已确认回执分页">
            <span className="muted" aria-live="polite">已确认回执 {data?.receipts.length
              ? `${shownReceiptOffset + 1}–${shownReceiptOffset + data.receipts.length} / ${receiptTotal}`
              : `共 ${receiptTotal} 条`}</span>
            <Button variant="ghost" disabled={loadingPage || shownReceiptOffset === 0}
              onClick={() => pageTo(offset, Math.max(0, receiptOffset - RECEIPT_PAGE))}>较新</Button>
            <Button variant="ghost" disabled={loadingPage || shownReceiptOffset + RECEIPT_PAGE >= receiptTotal}
              onClick={() => pageTo(offset, receiptOffset + RECEIPT_PAGE)}>更早</Button>
          </nav>}
          {data?.receipts.map((entry, index) => <article className="queue-row" key={entry.at + "-" + index}>
            <span className="glyph mint"><Check size={20} /></span>
            <div className="queue-body"><b>{entry.group} <span>· 已确认</span></b><p>{entry.text}</p></div>
            <time>{time.format(new Date(entry.at * 1000))}</time>
          </article>)}
          {data?.note && (data.tasks.length > 0 || data.receipts.length > 0) && <p className="muted">{data.note}</p>}
        </div>
        <div className="panel-foot">
          <Button variant="ghost" icon={<Eye size={16} />} disabled={controlsDisabled}
            onClick={() => void act("observe", "run.mode", { action: "observe" })}>只观察</Button>
          {scope === "" || scope === primary?.id
            ? <Button variant="ghost" disabled={controlsDisabled || !primary} busy={working === "recover"}
                onClick={() => setRecover(primary)}>处理最新点名</Button>
            : <span />}
          {(data?.total ?? 0) > TASK_PAGE && <div className="flex">
            <Button disabled={loadingPage || offset === 0} onClick={() => pageTo(Math.max(0, offset - TASK_PAGE), receiptOffset)}>上一页</Button>
            <Button disabled={loadingPage || offset + TASK_PAGE >= (data?.total ?? 0)} onClick={() => pageTo(offset + TASK_PAGE, receiptOffset)}>下一页</Button>
          </div>}
        </div>
      </section>
      <section className="glass panel watch-panel">
        <PanelTitle icon={<Headphones size={21} />} end={<IconButton label="管理监听" icon={<Settings2 size={17} />}
          onClick={() => navigate("sessions")} />}>监听范围</PanelTitle>
        <div className="watch-list">
          {status?.sessions.map(session => <div key={session.id} className={`watch-row ${scope === session.id ? "chosen" : ""}`}>
            <button className="watch-target" type="button" aria-pressed={scope === session.id}
              title={ruleLabels[session.replyMode]}
              onClick={() => updateScope(session.id)}>
              <SessionIcon session={session} /><span><b>{session.name}</b></span>
            </button>
            <StatusPill mode={session.mode} compact />
            <Button variant="primary" icon={session.mode === "auto" ? <Pause size={15} /> : <Play size={15} />}
              disabled={controlsDisabled || !session.bound} busy={working === session.id}
              onClick={() => void toggle(session)} aria-label={`${session.mode === "auto" ? "暂停" : "开启"} ${session.name}`}>
              {session.mode === "auto" ? "暂停" : "开启"}
            </Button>
          </div>)}
          {!status?.sessions.length && <Empty title="还没有监听会话" detail="可以在会话页添加群聊或联系人" />}
        </div>
        {row?.lastResult && <div className="last-result"><b>{row.lastResult.summary}</b><small>{row.lastResult.reason}</small></div>}
        <div className="watch-foot">
          {canEnableAll && <Button variant={canPauseAll ? undefined : "primary"} icon={<Play size={15} />}
            disabled={controlsDisabled} busy={working === "allEnable"}
            onClick={() => void act("allEnable", "run.mode", { action: "enable" })}>全部开启</Button>}
          {canPauseAll && <Button variant="primary" icon={<Pause size={15} />} disabled={controlsDisabled} busy={working === "allPause"}
            onClick={() => void act("allPause", "run.mode", { action: "pause" })}>全部暂停</Button>}
        </div>
      </section>
    </div>
    {recover && active && <Confirm title="处理最新点名" detail={`核对“${recover.name}”中最新的点名消息，符合发送条件时进入回复队列。`}
      cancel={() => setRecover(undefined)} confirm={() => {
        setRecover(undefined);
        void act("recover", "run.recover", { session: recover.id });
      }} />}
  </div>;
}
