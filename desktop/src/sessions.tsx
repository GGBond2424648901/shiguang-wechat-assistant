import {
  ArrowLeft, ChevronRight, CircleHelp, Crosshair, Info, Layers2, MessageCircle, Monitor, PauseCircle,
  Plus, RefreshCcw, Save, Search, ShieldCheck, Smile, Star, Trash2, UserRound, UsersRound, X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { errorMessage, frequencies, request, ruleLabels, type Status } from "./api";
import { Button, Empty, IconButton, PanelTitle, Select, StatusPill, Switch, useDraftGuard } from "./components";

interface Conversation {
  id: string; name: string; kind: "group" | "private" | "service";
  bound: boolean; paused: boolean; calibrated: boolean;
  placeholder?: boolean;
  windowKind?: string | null; windowSize?: [number, number] | null;
  replyMode?: string | null; stickerFrequency?: string | null;
}
interface SavedSessions {
  items: Conversation[]; monitorAll: boolean; stickersEnabled: boolean;
  defaultReplyMode: string; defaultStickerFrequency: string; accountLabel: string; revision: string;
}
interface Contact {
  id: string; name: string; kind: Conversation["kind"];
  eligible: boolean; reason: string; saved: boolean; calibrated: boolean;
}
interface Directory {
  items: Contact[]; total: number; offset: number;
}
type DraftRule = { replyMode: string | null; stickerFrequency: string | null };
type Notify = (text: string, error?: boolean) => void;

const kindLabels = { group: "群聊", private: "私聊", service: "服务" };
const replyOptions = Object.entries(ruleLabels).map(([value, label]) => ({ value, label }));
const rulesOf = (row: Conversation): DraftRule => ({ replyMode: row.replyMode ?? null, stickerFrequency: row.stickerFrequency ?? null });

function Avatar({ kind }: { kind: Conversation["kind"] }) {
  const Icon = kind === "group" ? UsersRound : kind === "private" ? UserRound : MessageCircle;
  return <span className="conversation-avatar"><Icon size={19} /></span>;
}

function SessionBlock({ icon, title, hint, controls, children }: {
  icon: ReactNode; title: string; hint: string; controls?: ReactNode; children?: ReactNode;
}) {
  return <section className="session-block">
    <div className="session-block-header"><span className="block-icon">{icon}</span>
      <div><h3>{title}</h3><p>{hint}</p></div>{controls && <div className="session-block-controls">{controls}</div>}
    </div>{children}
  </section>;
}

export function SessionsPage({ active, status, notify, calibrate, directory, setDirectory }: {
  active: boolean; status?: Status; notify: Notify; calibrate: (session?: string) => Promise<void>;
  directory: boolean; setDirectory: (open: boolean) => void;
}) {
  const [base, setBase] = useState<SavedSessions>();
  const [order, setOrder] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState("");
  const [monitor, setMonitor] = useState(false);
  const [stickers, setStickers] = useState(true);
  const [defaultMode, setDefaultMode] = useState("natural");
  const [rules, setRules] = useState<Record<string, DraftRule>>({});
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const mutable = !!status && !status.readOnly && !busy;
  const row = order.find(item => item.id === selected) ?? order[0];
  const originalRow = base?.items.find(item => item.id === row?.id);
  const running = status?.sessions.find(item => item.id === row?.id);
  const rule = row ? rules[row.id] ?? rulesOf(row) : undefined;
  const changed = !!base && (order !== base.items || monitor !== base.monitorAll || stickers !== base.stickersEnabled ||
    defaultMode !== base.defaultReplyMode);
  useDraftGuard("sessions", changed || !!Object.keys(rules).length);
  const ruleChanged = !!row && !!rule && (rule.replyMode !== row.replyMode || rule.stickerFrequency !== row.stickerFrequency)
    && Object.hasOwn(rules, row.id);

  function accept(next: SavedSessions, preserveRules = false) {
    setBase(next);
    setOrder(next.items);
    setMonitor(next.monitorAll);
    setStickers(next.stickersEnabled);
    setDefaultMode(next.defaultReplyMode);
    setSelected(current => next.items.some(item => item.id === current) ? current : next.items[0]?.id ?? "");
    if (!preserveRules) setRules({});
  }

  useEffect(() => {
    if (!active || busy || changed || Object.keys(rules).length) return;
    let alive = true;
    request<SavedSessions>("sessions.read").then(next => {
      if (alive && next.revision !== base?.revision) accept(next);
      if (alive) setError("");
    }).catch(e => { if (alive) setError(errorMessage(e)); });
    return () => { alive = false; };
  }, [active, status?.revision, busy, changed, base?.revision, rules]);

  async function run(action: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(action);
    try { await operation(); } catch (e) { notify(errorMessage(e), true); }
    finally { setBusy(""); }
  }

  const editRule = (fields: Partial<DraftRule>) => {
    if (!row || !rule) return;
    const next = { ...rule, ...fields };
    setRules(previous => {
      const updated = { ...previous };
      if (next.replyMode === (row.replyMode ?? null) && next.stickerFrequency === (row.stickerFrequency ?? null)) delete updated[row.id];
      else updated[row.id] = next;
      return updated;
    });
  };

  const save = () => base && run("save", async () => {
    const selection = row?.name;
    const next = await request<SavedSessions>("sessions.save", {
      expected: base.revision, order: order.map(item => item.id), monitorAll: monitor,
      stickersEnabled: stickers, defaultReplyMode: defaultMode,
    });
    accept(next, true);
    setSelected(next.items.find(item => item.name === selection)?.id ?? next.items[0]?.id ?? "");
    notify("会话范围已保存");
  });

  const saveRules = () => base && row && rule && run("rules", async () => {
    const next = await request<SavedSessions>("sessions.rules", { expected: base.revision, session: row.id, ...rule });
    if (changed) setBase(next);
    else accept(next, true);
    setRules(previous => {
      const updated = { ...previous };
      delete updated[row.id];
      return updated;
    });
    notify(`“${row.name}”的独立回复规则已保存`);
  });

  if (!base || !row || !rule) return <div className="glass phase-note"><Empty
    title={error ? "会话设置未载入" : "正在读取会话设置"} detail={error || undefined} /></div>;
  if (directory) return <DirectoryPage notify={notify} existing={order.filter(item => !item.placeholder)} close={() => setDirectory(false)}
    capture={() => void run("calibrate", async () => { await calibrate(); setDirectory(false); })}
    add={contacts => {
      const retained = order.filter(item => !item.placeholder);
      setOrder([...retained, ...contacts.map(item => ({ ...item, paused: true, bound: true,
        replyMode: null, stickerFrequency: null }))]);
      if (contacts.length + retained.length > 1) setMonitor(true);
      setSelected(contacts.at(-1)?.id ?? selected);
      setDirectory(false);
      notify(`已加入 ${contacts.length} 个会话，保存后应用`);
    }} />;
  const pending = !originalRow;
  const paused = row.paused || (status?.mode !== "paused" && running?.mode === "paused");
  const filtered = order.filter(item => !query || item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <div className="sessions-page">
    <section className="glass panel session-list">
      <PanelTitle icon={<Layers2 size={18} />} end={<Button variant="primary" icon={<Plus size={16} />}
        disabled={!mutable || order.length >= 8} onClick={() => setDirectory(true)}>添加会话</Button>}>监听对象</PanelTitle>
      <label className="persona-search"><Search size={15} /><input aria-label="搜索已选会话" placeholder="搜索群聊或联系人"
        value={query} onChange={event => setQuery(event.target.value)} /></label>
      <div className="session-rows">
        {filtered.map(item => {
          const current = status?.sessions.find(candidate => candidate.id === item.id);
          return <button type="button" className={row.id === item.id ? "session-entry active" : "session-entry"} key={item.id}
            aria-pressed={row.id === item.id} onClick={() => setSelected(item.id)}>
            <Avatar kind={item.kind} /><span className="session-entry-label"><b>{item.name}</b>
              <span><span className="tag">{kindLabels[item.kind]}</span>
                <StatusPill compact mode={current?.mode ?? (item.paused ? "paused" : "observe")} /></span></span>
            <ChevronRight size={15} />
          </button>;
        })}
        {!filtered.length && <Empty title="没有匹配的会话" />}
      </div>
      <div className="session-list-footer">
        <label className="session-monitor">
          <input type="checkbox" checked={monitor} disabled={!mutable} onChange={event => setMonitor(event.target.checked)} />
          同时监听已选会话（{order.length} 个）
        </label>
        <Switch checked={stickers} disabled={!mutable} onChange={setStickers}>允许表情包回复</Switch>
        <label className="field">默认回复方式<Select label="默认回复方式" options={replyOptions}
          value={defaultMode} onChange={setDefaultMode} disabled={!mutable} /></label>
      </div>
    </section>
    <section className="glass panel session-detail">
      <div className="session-detail-scroll">
        <div className="session-detail-title"><Avatar kind={row.kind} /><div>
          <h2>{row.name}</h2><div className="flex muted fine-print">
            <span>{kindLabels[row.kind]}</span>
            {row.id === order[0].id && <span>· 主要会话</span>}
            {base.accountLabel && <span>· {base.accountLabel}</span>}
          </div></div>
          <span className={row.bound ? "identity-label confirmed" : "identity-label"}><ShieldCheck size={15} />
            {row.bound ? "身份已核实" : "身份待核实"}</span>
          <div className="session-detail-icons flex">
            <IconButton label="设为主要会话" icon={<Star size={16} />} disabled={!mutable || row.id === order[0].id}
              onClick={() => setOrder([row, ...order.filter(item => item.id !== row.id)])} />
            <IconButton label="移出监听列表" icon={<Trash2 size={16} />} disabled={!mutable || order.length <= 1}
              onClick={() => { setOrder(order.filter(item => item.id !== row.id)); setSelected(""); }} />
          </div>
        </div>
        <SessionBlock icon={<MessageCircle size={21} />} title="回复方式" hint="独立设置该会话如何回复"
          controls={<div className="flex session-rule-group" role="group" aria-label="回复方式">
            {[{ value: "", label: "继承默认" }, ...replyOptions].map(option => <Button key={option.value}
              variant={rule.replyMode === (option.value || null) ? "primary" : undefined}
              aria-pressed={rule.replyMode === (option.value || null)} disabled={!mutable || pending || !row.bound}
              onClick={() => editRule({ replyMode: option.value || null })}>{option.label}</Button>)}
          </div>} />
        <SessionBlock icon={<Smile size={21} />} title="表情偏好" hint="控制回复表情的使用频率"
          controls={<Select label="会话表情偏好" value={rule.stickerFrequency ?? ""}
            options={[{ value: "", label: `继承默认 · ${frequencies.find(option => option.value === base.defaultStickerFrequency)?.label ?? ""}` }, ...frequencies]}
            onChange={stickerFrequency => editRule({ stickerFrequency: stickerFrequency || null })}
            disabled={!mutable || pending || !row.bound} />} />
        <SessionBlock icon={<PauseCircle size={21} />} title="独立暂停" hint="只暂停这个会话，其他会话继续运行"
          controls={<Switch checked={paused} disabled={!mutable || !status?.connected || !running || !row.bound}
            onChange={value => void run("pause", async () => {
              await request("run.selected", { session: row.id, action: value ? "pause" : "enable" });
              const next = await request<SavedSessions>("sessions.read");
              if (changed) setBase(next);
              else accept(next, true);
              notify(value ? "会话已暂停" : "会话已开启");
            })}>暂停此会话</Switch>} />
        <SessionBlock icon={<Monitor size={21} />} title="窗口绑定" hint="逐个校准主窗口或独立聊天窗口"
          controls={<div className="flex"><span className={row.calibrated ? "identity-label confirmed" : "identity-label"}>
            {row.calibrated ? "已校准" : "待校准"}</span>
            <Button className="peach-outline" disabled={!mutable || pending} icon={<Crosshair size={15} />} onClick={() =>
              void run("calibrate", () => calibrate(row.id))}>{row.calibrated ? "重新校准" : "校准此会话"}</Button></div>}>
          <dl className="session-binding"><dt>窗口类型</dt><dd>{row.windowKind === "detached" ? "独立聊天窗口"
            : row.windowKind === "main" ? "微信主窗口" : "等待校准"}</dd>
            <dt>当前状态</dt><dd>{running?.reason || (pending ? "先保存会话再校准" : row.windowSize?.join(" × ") || "尚无绑定")}</dd></dl>
        </SessionBlock>
      </div>
      <footer className="session-detail-footer"><span><Info size={15} />{changed || Object.keys(rules).length
        ? "有未保存的修改" : `已选 ${order.length} 个会话`}</span>
        <div className="flex"><Button disabled={!mutable || (!changed && !Object.keys(rules).length)}
          onClick={() => accept(base)}>撤销</Button>
          {changed && <Button variant="primary" busy={busy === "save"} icon={<Save size={16} />} disabled={!mutable}
            onClick={() => void save()}>保存监听范围</Button>}
          <Button variant={changed ? undefined : "primary"} busy={busy === "rules"} icon={<Save size={16} />}
            disabled={!mutable || !ruleChanged || pending} onClick={() => void saveRules()}>保存当前会话</Button></div>
      </footer>
    </section>
  </div>;
}

function DirectoryPage({ existing, notify, close, add, capture }: {
  existing: Conversation[]; notify: Notify; close: () => void; add: (rows: Contact[]) => void; capture: () => void;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(0);
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Directory>();
  const [checked, setChecked] = useState<Contact[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const refreshed = useState(() => ({ count: 0 }))[0];
  useEffect(() => {
    let alive = true;
    setBusy(true);
    const timer = setTimeout(() => {
      request<Directory>("sessions.directory", { query, kind, offset: page * 60, refresh: version !== refreshed.count })
        .then(next => { if (alive) { refreshed.count = version; setResult(next); setError(""); } })
        .catch(e => { if (alive) setError(errorMessage(e)); })
        .finally(() => { if (alive) setBusy(false); });
    }, 140);
    return () => { alive = false; clearTimeout(timer); };
  }, [query, kind, page, version, refreshed]);
  const current = result?.items ?? [];
  const contains = (row: Contact) => existing.some(item => item.name === row.name) || row.saved;
  const toggle = (row: Contact) => {
    if (!row.eligible || contains(row)) return;
    if (checked.some(item => item.id === row.id)) setChecked(checked.filter(item => item.id !== row.id));
    else if (checked.length + existing.length >= 8) notify("最多保存 8 个监听会话", true);
    else setChecked([...checked, row]);
  };
  return <div className="glass directory-page">
    <div className="directory-toolbar">
      <Button className="directory-return" aria-label="返回会话" title="返回会话" icon={<ArrowLeft size={16} />}
        onClick={close}><span>返回会话</span></Button>
      <label className="persona-search"><Search size={16} /><input aria-label="搜索通讯录" placeholder="搜索群聊或联系人"
        value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} /></label>
      <div className="flex" role="group" aria-label="会话类型">
        {[["all", "全部"], ["group", "群聊"], ["private", "私聊"]].map(([value, label]) => <Button key={value}
          variant={kind === value ? "primary" : "ghost"} aria-pressed={kind === value}
          onClick={() => { setKind(value); setPage(0); }}>{label}</Button>)}
      </div>
      <Button className="directory-refresh peach-outline" aria-label="刷新目录" title="刷新目录" icon={<RefreshCcw size={16} />} busy={busy}
        onClick={() => { setVersion(version + 1); setPage(0); }}><span>刷新目录</span></Button>
    </div>
    <div className="directory-body">
      <section className="glass panel directory-list">
        <PanelTitle icon={<MessageCircle size={18} />} end={<span className="fine-print muted">共 {result?.total ?? 0} 个会话</span>}>选择会话</PanelTitle>
        <div className="directory-rows" aria-busy={busy}>
          {error ? <Empty title="通讯录暂不可用" detail={error} /> : busy && !result ? <Empty title="正在读取微信通讯录" />
            : current.length ? current.map(item => <label className="directory-entry" key={item.id}>
              <input type="checkbox" aria-label={item.name} disabled={!item.eligible || contains(item) || busy}
                checked={contains(item) || checked.some(row => row.id === item.id)} onChange={() => toggle(item)} />
              <Avatar kind={item.kind} /><b>{item.name}</b><span className="tag">{kindLabels[item.kind]}</span>
              <small className={item.calibrated ? "verified" : "muted"}>{item.reason || (contains(item)
                ? item.calibrated ? "已校准" : "已加入" : "可选择")}</small>
            </label>) : <Empty title="没有匹配的联系人" />}
        </div>
        {!!result && result.total > 60 && <div className="flex">
          <Button disabled={!page || busy} onClick={() => setPage(page - 1)}>上一页</Button>
          <span className="fine-print muted">第 {page + 1} 页</span>
          <Button disabled={(page + 1) * 60 >= result.total || busy} onClick={() => setPage(page + 1)}>下一页</Button>
        </div>}
      </section>
      <section className="glass panel directory-selected">
        <PanelTitle icon={<Layers2 size={18} />} end={<span className="muted">{existing.length + checked.length} / 8</span>}>本次选择</PanelTitle>
        <div className="directory-picked">
          {checked.length ? checked.map(item => <div className="flex" key={item.id}>
            <Avatar kind={item.kind} /><b>{item.name}</b>
            <IconButton label={`移除 ${item.name}`} icon={<X size={15} />} onClick={() => toggle(item)} />
          </div>) : <Empty icon={<CircleHelp size={23} />} title="勾选左侧群聊或联系人" detail="支持一次加入多个会话。" />}
        </div>
        <div className="session-binding directory-hint"><Info size={16} />
          <span>选中的会话先加入草稿。返回会话页保存范围，再校准并开启自动回复。</span></div>
      </section>
    </div>
    <footer className="directory-footer flex">
      <Button className="directory-capture" aria-label="识别当前微信会话" title="识别当前微信会话" icon={<Crosshair size={15} />}
        onClick={capture}><span>识别当前微信会话</span></Button>
      <Button onClick={close}>取消</Button>
      <Button variant="primary" disabled={!checked.length} onClick={() => add(checked)}>
        加入监听列表 {checked.length} / {8 - existing.length}</Button>
    </footer>
  </div>;
}
