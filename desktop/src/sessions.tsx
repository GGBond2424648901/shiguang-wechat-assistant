import {
  ArrowLeft, ChevronRight, CircleHelp, Crosshair, Globe, Info, Layers2, MessageCircle, Monitor, PauseCircle,
  Plus, RefreshCcw, Save, Search, ShieldCheck, Smile, Star, Trash2, UserRound, UsersRound, X,
} from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";
import { errorMessage, frequencies, request, ruleLabels, type Status } from "./api";
import { Button, Confirm, Empty, IconButton, PanelTitle, Select, StatusPill, Switch, useDraftGuard } from "./components";
import { reducesMotion } from "./motion";
import { rebaseSessions, rulesOf, sameOrder, sameRules, type Conversation, type DraftRule, type SavedSessions, type SessionDraft } from "./session-draft";

interface Contact {
  id: string; name: string; kind: Conversation["kind"];
  eligible: boolean; reason: string; saved: boolean; calibrated: boolean;
}
interface Directory {
  items: Contact[]; total: number; offset: number;
}
type Notify = (text: string, error?: boolean) => void;

const kindLabels = { group: "群聊", private: "私聊", service: "服务" };
const replyOptions = Object.entries(ruleLabels).map(([value, label]) => ({ value, label }));
const webSearchOptions = [
  { value: "", label: "继承默认" },
  { value: "true", label: "允许联网" },
  { value: "false", label: "关闭联网" },
];

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
  const [removeTarget, setRemoveTarget] = useState<{ item: Conversation; accountKey: string }>();
  const sessionRows = useRef<HTMLDivElement>(null);
  const accountDrafts = useRef(new Map<string, { base: SavedSessions; draft: SessionDraft; selected: string }>());
  const mutable = !!status && !status.readOnly && !busy;
  const row = order.find(item => item.id === selected) ?? order[0];
  const originalRow = base?.items.find(item => item.id === row?.id);
  const running = status?.sessions.find(item => item.id === row?.id);
  const rule = row ? rules[row.id] ?? rulesOf(row) : undefined;
  const changed = !!base && (!sameOrder(order, base.items) || monitor !== base.monitorAll || stickers !== base.stickersEnabled ||
    defaultMode !== base.defaultReplyMode);
  useDraftGuard("sessions", changed || !!Object.keys(rules).length || !!accountDrafts.current.size);
  const ruleChanged = !!row && !!rule && Object.hasOwn(rules, row.id) && !sameRules(rule, rulesOf(row));

  function accept(next: SavedSessions, preserveRules = false, committed: { range?: boolean; rule?: string } = {}) {
    const remainingRules = { ...rules };
    if (committed.rule) delete remainingRules[committed.rule];
    const currentDraft = committed.range && base ? {
      order: base.items, monitor: base.monitorAll, stickers: base.stickersEnabled,
      defaultMode: base.defaultReplyMode, rules: remainingRules,
    } : { order, monitor, stickers, defaultMode, rules: remainingRules };
    const switched = !!base && base.accountKey !== next.accountKey;
    if (preserveRules && switched) {
      const dirty = !sameOrder(currentDraft.order, base.items) || currentDraft.monitor !== base.monitorAll
        || currentDraft.stickers !== base.stickersEnabled || currentDraft.defaultMode !== base.defaultReplyMode
        || !!Object.keys(currentDraft.rules).length;
      if (dirty) accountDrafts.current.set(base.accountKey, { base, draft: currentDraft, selected });
      else accountDrafts.current.delete(base.accountKey);
    }
    const stored = preserveRules && switched ? accountDrafts.current.get(next.accountKey) : undefined;
    if (stored) accountDrafts.current.delete(next.accountKey);
    const draft = stored ? rebaseSessions(stored.base, stored.draft, next)
      : preserveRules && base ? rebaseSessions(base, currentDraft, next) : undefined;
    setBase(next);
    const items = draft?.order ?? next.items;
    setOrder(items);
    setMonitor(draft?.monitor ?? next.monitorAll);
    setStickers(draft?.stickers ?? next.stickersEnabled);
    setDefaultMode(draft?.defaultMode ?? next.defaultReplyMode);
    setSelected(current => items.find(item => item.id === (stored?.selected ?? current)
      || item.contactId === (stored?.selected ?? current))?.id ?? items[0]?.id ?? "");
    setRules(draft?.rules ?? {});
    if (switched) {
      setRemoveTarget(undefined);
      notify("微信账号已切换，会话草稿按账号分别保留");
    } else if (draft?.orderConflict) notify("监听列表在其他窗口发生变化，移除操作冲突，已载入最新列表供核对");
  }

  const updateFromWorker = useEffectEvent((next: SavedSessions) => {
    if (next.revision !== base?.revision) accept(next, true);
    setError("");
  });

  useEffect(() => {
    if (!active || busy) return;
    let alive = true;
    request<SavedSessions>("sessions.read").then(next => {
      if (alive) updateFromWorker(next);
    }).catch(e => { if (alive) setError(errorMessage(e)); });
    return () => { alive = false; };
  }, [active, status?.revision, busy]);

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
      if (sameRules(next, rulesOf(row))) delete updated[row.id];
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
    accept(next, true, { range: true });
    setSelected(next.items.find(item => item.name === selection)?.id ?? next.items[0]?.id ?? "");
    notify("会话范围已保存");
  });

  const saveRules = () => base && row && originalRow && rule && run("rules", async () => {
    const savedRule = rulesOf(originalRow);
    const next = await request<SavedSessions>("sessions.rules", {
      expected: base.revision, session: row.id, ...rule,
      before: { reply_mode: savedRule.replyMode, sticker_frequency: savedRule.stickerFrequency, web_search: savedRule.webSearch },
    });
    accept(next, true, { rule: row.id });
    notify(`“${row.name}”的独立回复规则已保存`);
  });

  const remove = (target: NonNullable<typeof removeTarget>) => base && run("remove", async () => {
    const { item, accountKey } = target;
    const saved = base.items.some(current => current.id === item.id);
    const next = saved ? await request<SavedSessions>("sessions.remove", { session: item.id, accountKey }) : undefined;
    const element = sessionRows.current?.querySelector<HTMLElement>(`[data-session-id="${CSS.escape(item.id)}"]`);
    if (element?.offsetHeight && !reducesMotion()) {
      const animation = element.animate([
        { height: `${element.offsetHeight}px`, opacity: 1, transform: "translateX(0)" },
        { height: "0", opacity: 0, transform: "translateX(8px)" },
      ], { duration: 175, easing: "cubic-bezier(.22,.75,.25,1)", fill: "forwards" });
      await animation.finished.catch(() => {});
    }
    if (next) accept(next, true);
    else {
      setOrder(current => current.filter(candidate => candidate.id !== item.id));
      setSelected(current => current === item.id ? "" : current);
    }
    notify(`已移出“${item.name}”`);
  });

  if (!base) return <div className="glass phase-note"><Empty
    title={error ? "会话设置未载入" : "正在读取会话设置"} detail={error || undefined} /></div>;
  if (directory) return <DirectoryPage key={base.accountKey} accountKey={base.accountKey} notify={notify} existing={order.filter(item => !item.placeholder)} close={() => setDirectory(false)}
    capture={() => void run("calibrate", async () => { await calibrate(); setDirectory(false); })}
    add={contacts => {
      const retained = order.filter(item => !item.placeholder);
      const additions = contacts.filter(item => !retained.some(row => row.id === item.id || row.contactId === item.id || row.name === item.name));
      if (retained.length + additions.length > 8) { notify("监听列表已更新，最多选择 8 个会话，请重新核对", true); return; }
      setOrder([...retained, ...additions.map(item => ({ ...item, paused: true, bound: true,
        replyMode: null, stickerFrequency: null, webSearch: null }))]);
      if (additions.length + retained.length > 1) setMonitor(true);
      setSelected(additions.at(-1)?.id ?? selected);
      setQuery("");
      setDirectory(false);
      notify(additions.length ? `已加入 ${additions.length} 个会话，保存后应用` : "所选会话已经加入监听列表");
    }} />;
  const pending = !originalRow;
  const paused = Boolean(row?.paused || (status?.mode !== "paused" && running?.mode === "paused"));
  const askToRemove = (item: Conversation) => setRemoveTarget({ item, accountKey: base.accountKey });
  const filtered = order.filter(item => !query || item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <div className="sessions-page">
    <section className="glass panel session-list">
      <PanelTitle icon={<Layers2 size={18} />} end={<Button variant="primary" icon={<Plus size={16} />}
        disabled={!mutable || order.length >= 8} onClick={() => setDirectory(true)}>添加会话</Button>}>监听对象</PanelTitle>
      <label className="persona-search"><Search size={15} /><input aria-label="搜索已选会话" placeholder="搜索群聊或联系人"
        value={query} onChange={event => setQuery(event.target.value)} /></label>
      <div className="session-rows" ref={sessionRows}>
        {filtered.map(item => {
          const current = status?.sessions.find(candidate => candidate.id === item.id);
          const chosen = row?.id === item.id;
          return <div className={`session-row ${chosen ? "active" : ""}`} key={item.id} data-session-id={item.id}>
            <button type="button" className="session-entry" aria-pressed={chosen}
              title={item.name} onClick={() => setSelected(item.id)}>
              <Avatar kind={item.kind} /><span className="session-entry-label"><b>{item.name}</b>
                <span><span className="tag">{kindLabels[item.kind]}</span>
                  <StatusPill compact mode={current?.mode ?? (item.paused ? "paused" : "observe")} /></span></span>
              <ChevronRight size={15} />
            </button>
            <IconButton variant="ghost" className="session-remove" label={`移出监听列表 ${item.name}`}
              icon={<Trash2 size={16} />} disabled={!mutable} onClick={() => askToRemove(item)} />
          </div>;
        })}
        {!filtered.length && <Empty title={order.length ? "没有匹配的会话" : "还没有监听会话"} />}
      </div>
      <div className="session-list-footer">
        <label className="session-monitor">
          <input type="checkbox" checked={monitor} disabled={!mutable || !order.length} onChange={event => setMonitor(event.target.checked)} />
          同时监听已选会话（{order.length} 个）
        </label>
        <Switch checked={stickers} disabled={!mutable} onChange={setStickers}>允许表情包回复</Switch>
        <label className="field">默认回复方式<Select label="默认回复方式" options={replyOptions}
          value={defaultMode} onChange={setDefaultMode} disabled={!mutable} /></label>
      </div>
    </section>
    <section className="glass panel session-detail">
      <div className="session-detail-scroll">
        {row && rule ? <>
        <div className="session-detail-title"><Avatar kind={row.kind} /><div>
          <h2>{row.name}</h2><div className="flex muted fine-print">
            <span>{kindLabels[row.kind]}</span>
            {row.id === order[0]?.id && <span>· 主要会话</span>}
            {base.accountLabel && <span>· {base.accountLabel}</span>}
          </div></div>
          <span className={row.bound ? "identity-label confirmed" : "identity-label"}><ShieldCheck size={15} />
            {row.bound ? "身份已核实" : "身份待核实"}</span>
          <div className="session-detail-icons flex">
            <IconButton label="设为主要会话" icon={<Star size={16} />} disabled={!mutable || row.id === order[0]?.id}
              onClick={() => setOrder([row, ...order.filter(item => item.id !== row.id)])} />
            <IconButton label="移出当前会话" icon={<Trash2 size={16} />} disabled={!mutable}
              onClick={() => askToRemove(row)} />
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
        <SessionBlock icon={<Globe size={21} />} title="联网回复" hint={base.webSearchEnabled
          ? "继承默认时允许联网" : "全局开关已关闭，开启后应用此设置"}
          controls={<Select label="联网回复" value={rule.webSearch === null ? "" : String(rule.webSearch)}
            options={webSearchOptions} onChange={value => editRule({ webSearch: value === "" ? null : value === "true" })}
            disabled={!mutable || pending || !row.bound} />} />
        <SessionBlock icon={<PauseCircle size={21} />} title="独立暂停" hint="只暂停这个会话，其他会话继续运行"
          controls={<Switch checked={paused} disabled={!mutable || !status?.connected || !running || !row.bound}
            onChange={value => void run("pause", async () => {
              await request("run.selected", { session: row.id, action: value ? "pause" : "enable" });
              const next = await request<SavedSessions>("sessions.read");
              accept(next, true);
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
        </> : <Empty icon={<MessageCircle size={24} />} title="没有已选会话" detail="添加一个群聊或联系人开始监听" />}
      </div>
      <footer className="session-detail-footer"><span><Info size={15} />{changed || Object.keys(rules).length
        ? "有未保存的修改" : `已选 ${order.length} 个会话`}</span>
        <div className="flex"><Button disabled={!mutable || (!changed && !Object.keys(rules).length)}
          onClick={() => accept(base)}>撤销</Button>
          {changed && <Button variant="primary" busy={busy === "save"} icon={<Save size={16} />} disabled={!mutable}
            onClick={() => void save()}>保存监听范围</Button>}
          {row && <Button variant={changed ? undefined : "primary"} busy={busy === "rules"} icon={<Save size={16} />}
            disabled={!mutable || !ruleChanged || pending} onClick={() => void saveRules()}>保存当前会话</Button>}</div>
      </footer>
    </section>
    {active && removeTarget && <Confirm title="移出监听列表" destructive
      detail={`移出“${removeTarget.item.name}”后会停止监听该会话。以后可以从通讯录重新添加。`}
      cancel={() => setRemoveTarget(undefined)}
      confirm={() => {
        setRemoveTarget(undefined);
        void remove(removeTarget);
      }} />}
  </div>;
}

function DirectoryPage({ accountKey, existing, notify, close, add, capture }: {
  accountKey: string; existing: Conversation[]; notify: Notify; close: () => void; add: (rows: Contact[]) => void; capture: () => void;
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
      request<Directory>("sessions.directory", { accountKey, query, kind, offset: page * 60, refresh: version !== refreshed.count })
        .then(next => { if (alive) { refreshed.count = version; setResult(next); setError(""); } })
        .catch(e => { if (alive) setError(errorMessage(e)); })
        .finally(() => { if (alive) setBusy(false); });
    }, 140);
    return () => { alive = false; clearTimeout(timer); };
  }, [accountKey, query, kind, page, version, refreshed]);
  const current = result?.items ?? [];
  const contains = (row: Contact) => existing.some(item => item.name === row.name || item.id === row.id || item.contactId === row.id) || row.saved;
  const pending = checked.filter(row => !contains(row));
  const toggle = (row: Contact) => {
    if (checked.some(item => item.id === row.id)) { setChecked(checked.filter(item => item.id !== row.id)); return; }
    if (!row.eligible || contains(row)) return;
    if (pending.length + existing.length >= 8) notify("最多保存 8 个监听会话", true);
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
        <PanelTitle icon={<Layers2 size={18} />} end={<span className="muted">{existing.length + pending.length} / 8</span>}>本次选择</PanelTitle>
        <div className="directory-picked">
          {pending.length ? pending.map(item => <div className="flex" key={item.id}>
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
      <Button variant="primary" disabled={!pending.length || pending.length + existing.length > 8} onClick={() => add(pending)}>
        加入监听列表 {pending.length} / {Math.max(0, 8 - existing.length)}</Button>
    </footer>
  </div>;
}
