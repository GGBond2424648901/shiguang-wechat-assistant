import { Check, ChevronDown, ChevronUp, Copy, Crosshair, KeyRound, Layers3, Link2, MessageCircle, Minus, Monitor, Plus, RotateCcw, Save, Send, Trash2, Zap } from "lucide-react";
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { defaultAppearance, errorMessage, request, stepZoom, type Appearance, type PageKey, type Status } from "./api";
import { Button, Confirm, Empty, IconButton, PanelTitle, Select, Switch, useDraftGuard } from "./components";

type Notify = (message: string, error?: boolean) => void;
type General = {
  model_name: string; auto_on_launch: boolean; split_replies: boolean; idle_wake: boolean;
  web_search_enabled: boolean;
  self_display_name: string; self_name_unique_confirmed: boolean; owner_confirmed: boolean;
};
type Settings = General & {
  account: string; scopeRevision: string; current: string; receive: string; owner_available: boolean; revision: string;
  pacing: [number, number, number, number]; continuationInterval: number; models: string[];
  pacingPresets: { label: string; values: number[] }[];
  keys: { identifier: string; suffix: string }[]; credentialError: string;
};
type Confirmation = { title: string; detail: string; run: () => void };
type Draft<T> = { original: T; value: T; revision: string; scope: string };
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
const changed = <T,>(draft?: Draft<T>) => Boolean(draft && !same(draft.original, draft.value));
function updateDraft<T>(previous: Draft<T> | undefined, value: T, next: Settings, replace: boolean, rebase: boolean): Draft<T> {
  if (!previous || replace || !changed(previous)) return {
    original: value, value, revision: next.revision, scope: next.scopeRevision,
  };
  if (rebase && previous.scope === next.scopeRevision && same(previous.original, value))
    return { ...previous, revision: next.revision };
  return previous;
}
const fieldsOf = (state: Settings): General => ({
  model_name: state.model_name, auto_on_launch: state.auto_on_launch, web_search_enabled: state.web_search_enabled === true,
  split_replies: state.split_replies, idle_wake: state.idle_wake, self_display_name: state.self_display_name,
  self_name_unique_confirmed: state.self_name_unique_confirmed, owner_confirmed: state.owner_confirmed,
});
const rateLabels = [
  { name: "最短间隔（秒）", min: 3, max: 3600, step: "any" },
  { name: "每小时（条）", min: 1, max: 3600, step: 1 },
  { name: "每分钟（条）", min: 1, max: 60, step: 1 },
  { name: "随机加时（秒）", min: 0, max: 30, step: "any" },
  { name: "同轮续发间隔（秒）", min: 0.5, max: 30, step: "any" },
];

export function SettingsPage({ active, status, appearance, setAppearance, notify, navigate }: {
  active: boolean; status?: Status; appearance: Appearance; setAppearance: (value: Appearance) => void;
  notify: Notify; navigate: (page: PageKey) => void;
}) {
  const [tab, setTab] = useState("general");
  const [data, setData] = useState<Settings>();
  const [general, setGeneral] = useState<Draft<General>>();
  const [pacing, setPacing] = useState<Draft<string[]>>();
  const [keys, setKeys] = useState([""]);
  const [editingKeys, setEditingKeys] = useState(false);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<Confirmation>();
  const readOnly = status?.readOnly ?? true;
  const draft = general?.value;
  const rate = pacing?.value ?? [];
  const rateInvalid = rateLabels.some(({ min, max, step }, index) => {
    const value = Number(rate[index]);
    return !rate[index] || !Number.isFinite(value) || value < min || value > max
      || (step === 1 && !Number.isInteger(value));
  });
  const configurationDirty = changed(general) || changed(pacing);
  const dirty = configurationDirty || keys.some(key => !!key.trim());
  const configChanged = Boolean(data && status?.revision && data.revision !== status.revision);
  const generalStale = configChanged || general?.revision !== data?.revision;
  const rateStale = configChanged || pacing?.revision !== data?.revision;
  const editingDisabled = readOnly || !!busy;
  useDraftGuard("settings", !readOnly && dirty);

  const accept = useCallback((next: Settings, operation = "") => {
    setData(next);
    setGeneral(previous => updateDraft(previous, fieldsOf(next), next, operation === "general" || operation === "reload", operation === "rate"));
    setPacing(previous => updateDraft(previous, [...next.pacing, next.continuationInterval].map(String), next,
      operation === "rate" || operation === "reload", operation === "general"));
    setError("");
  }, []);
  useEffect(() => {
    if (!active || busy || data?.revision && !configChanged) return;
    let current = true;
    request<Settings>("settings.read").then(value => {
      if (current) accept(value);
    }).catch(error => { if (current) setError(errorMessage(error)); });
    return () => { current = false; };
  }, [active, busy, configChanged, data?.revision, status?.revision, accept]);

  const perform = async (key: string, method: string, params: Record<string, unknown>, message: string) => {
    if (busy) return;
    setBusy(key);
    try {
      const next = await request<Settings>(method, params);
      accept(next, key);
      if (key === "append" || key === "replace") {
        setKeys([""]);
        setEditingKeys(false);
      }
      notify(message);
    } catch (error) { notify(errorMessage(error), true); }
    finally { setBusy(""); }
  };

  const reload = () => {
    const run = () => void perform("reload", "settings.read", {}, "已读取最新设置");
    if (configurationDirty) setConfirm({ title: "读取最新设置", detail: "读取后将放弃当前未保存的运行设置和节奏草稿。", run });
    else run();
  };
  const credentials = (mode: "append" | "replace") => {
    const values = keys.map(key => key.trim()).filter(Boolean);
    if (!values.length) { notify("请输入要保存的密钥", true); return; }
    const run = () => void perform(mode, "settings.credentials", { mode, values }, "加密密钥已保存");
    if (mode === "replace" && data?.keys.length) {
      setConfirm({ title: "替换已保存的密钥", detail: "使用当前输入替换整个密钥列表。", run });
    } else run();
  };
  const test = async () => {
    if (busy) return;
    setBusy("test");
    try { notify((await request<{ message: string }>("settings.testModel")).message); }
    catch (error) { notify(errorMessage(error), true); }
    finally { setBusy(""); }
  };
  const set = <K extends keyof General>(key: K, value: General[K]) =>
    setGeneral(previous => previous && ({ ...previous, value: { ...previous.value, [key]: value } }));
  const setRate = (value: string[] | ((previous: string[]) => string[])) =>
    setPacing(previous => previous && ({ ...previous, value: typeof value === "function" ? value(previous.value) : value }));
  const rateField = (index: number) => {
    const { name, min, max, step } = rateLabels[index];
    return <label key={index} className="field">
      {name}<input type="number" step={step} min={min} max={max}
        value={rate[index] ?? ""} disabled={editingDisabled}
        onChange={event => setRate(values => values.map((value, at) => at === index ? event.target.value : value))} />
    </label>;
  };
  const targetOptions = status?.sessions.map(session => ({ value: session.id, label: session.name })) ?? [];
  const selected = status?.sessions.find(session => session.id === target)
    ?? status?.sessions.find(session => session.id === status.primary);
  const tabs = <div className="segments settings-tabs" role="group" aria-label="设置页面">
    <Button variant={tab === "general" ? "primary" : "ghost"} aria-pressed={tab === "general"} onClick={() => setTab("general")}>常规</Button>
    <Button variant={tab === "appearance" ? "primary" : "ghost"} aria-pressed={tab === "appearance"} onClick={() => setTab("appearance")}>界面外观</Button>
  </div>;
  return <div className="settings-page page-grid" data-tab={tab}>
    <div className="settings-general" hidden={tab !== "general"}>
      {data && draft ? <>
        <div className="settings-scroll">
          {(generalStale || rateStale) && <div className="inline-error" role="status">
            <span>账号或设置已更新，当前草稿需核对后再保存。</span>
            <Button busy={busy === "reload"} disabled={!!busy} onClick={reload}>读取最新设置</Button>
          </div>}
          <div className="settings-columns">
            <section className="glass panel credentials-panel">
              <PanelTitle icon={<Layers3 size={20} />} description="选择对话模型。访问密钥加密保存在本机。">模型与密钥</PanelTitle>
              <div className="form-stack settings-form">
                <div className="field settings-model"><span>对话模型</span><Select label="对话模型" value={draft.model_name}
                  options={data.models.map(value => ({ value, label: value }))} onChange={value => set("model_name", value)} disabled={editingDisabled} /></div>
                <div className="row-heading"><strong>密钥管理</strong><span>已加密保存 {data.keys.length} 个</span></div>
                {data.credentialError && <p className="muted">{data.credentialError}</p>}
                <div className="credential-list">{data.keys.map((key, index) => <div className="credential-row" key={key.identifier}>
                  <KeyRound size={15} /><span>密钥 {index + 1} · •••• {key.suffix}</span>
                  <span className="identity-label confirmed credential-saved">已保存</span>
                  <IconButton label={`删除密钥 ${index + 1}`} icon={<Trash2 size={15} />} disabled={data.keys.length <= 1 || readOnly || !!busy}
                    onClick={() => setConfirm({ title: "删除此密钥", detail: `移除末四位为 ${key.suffix} 的密钥。`,
                      run: () => void perform("remove", "settings.removeKey", { id: key.identifier }, "密钥已删除") })} />
                </div>)}</div>
                {!data.keys.length && !data.credentialError && <p className="muted">尚未添加密钥</p>}
                {editingKeys && <><div className="key-inputs">{keys.map((value, index) => <label key={index} className="field">
                  <input type="password" autoComplete="off" spellCheck={false} aria-label={`新密钥 ${index + 1}`}
                    placeholder={`新密钥 ${index + 1}`} value={value} maxLength={256} disabled={readOnly || !!busy}
                    onChange={event => setKeys(values => values.map((key, at) => at === index ? event.target.value : key))} />
                </label>)}</div>
                <div className="flex key-tools">
                  <Button disabled={readOnly || keys.length >= 8 || !!busy} onClick={() => setKeys(previous => [...previous, ""])}>增加输入栏</Button>
                  <Button disabled={readOnly || !!busy} onClick={() => { setKeys([""]); setEditingKeys(false); }}>取消输入</Button>
                </div>
                <div className="flex key-actions">
                  <Button variant="primary" icon={<KeyRound size={16} />} disabled={readOnly || !!busy}
                    onClick={() => credentials("append")}>追加密钥</Button>
                  <Button disabled={readOnly || !!busy} onClick={() => credentials("replace")}>替换密钥</Button>
                </div></>}
                <div className="flex key-actions">
                  {!editingKeys && <Button variant="primary" icon={<Plus size={16} />} disabled={readOnly || !!busy}
                    onClick={() => setEditingKeys(true)}>添加密钥</Button>}
                  <Button icon={<Link2 size={15} />} busy={busy === "test"} disabled={readOnly || !!busy} onClick={() => void test()}>测试连接</Button>
                </div>
              </div>
            </section>
            <section className="glass panel wechat-settings">
              <PanelTitle icon={<MessageCircle size={20} />} end={tabs} description="核对微信窗口，设置自动回复和发送方式。">微信与启动</PanelTitle>
              <div className="form-stack settings-form">
                <div className="row-heading route-status">
                  <strong>{selected?.calibrated && <span className="route-check"><Check size={20} /></span>}{selected?.calibrated ? "微信窗口已校准" : "微信窗口待校准"}</strong>
                  <span>{data.account || "微信账号待核对"}</span></div>
                <div className="flex route-settings">
                  <Select label="校准对象" options={targetOptions} value={selected?.id ?? ""} onChange={setTarget} />
                  <Button variant="primary" icon={<Crosshair size={15} />} disabled={readOnly} onClick={() =>
                    void window.shiguang.calibrate(selected?.id).catch(error => notify(errorMessage(error), true))}>校准所选窗口</Button>
                  <Button onClick={() => navigate("sessions")}>管理监听</Button>
                </div>
                {selected?.windowState && <p className="muted">{selected.windowState}</p>}
                <Switch checked={draft.auto_on_launch} onChange={value => set("auto_on_launch", value)} disabled={editingDisabled}>启动时自动回复</Switch>
                <Switch checked={draft.web_search_enabled} onChange={value => set("web_search_enabled", value)} disabled={editingDisabled}>启用联网回复</Switch>
                <Switch checked={draft.split_replies} onChange={value => set("split_replies", value)} disabled={editingDisabled}>分气泡发送</Switch>
                <Switch checked={draft.idle_wake} onChange={value => set("idle_wake", value)} disabled={editingDisabled}>空闲时唤起微信发送</Switch>
                <label className="field">群内昵称<input value={draft.self_display_name} disabled={editingDisabled} maxLength={80}
                  onChange={event => set("self_display_name", event.target.value)} /></label>
                <Switch checked={draft.self_name_unique_confirmed} onChange={value => set("self_name_unique_confirmed", value)}
                  disabled={editingDisabled}>已确认群内昵称唯一</Switch>
                <Switch checked={draft.owner_confirmed} onChange={value => set("owner_confirmed", value)}
                  disabled={editingDisabled || !data.owner_available}>可称呼当前私聊为“主人”</Switch>
              </div>
            </section>
          </div>
          <section className="glass panel pacing-panel">
            <PanelTitle icon={<Send size={19} />} end={<div className="pacing-presets flex">
              <span className="muted fine-print">节奏预设</span>
              <Select label="节奏预设" value={data.pacingPresets.find(preset => preset.values.join(",") === rate.slice(0, 4).join(","))?.label || ""}
                options={[{ value: "", label: "自定义节奏" }, ...data.pacingPresets.map(({ label }) => ({ value: label, label }))]}
                onChange={label => {
                  const preset = data.pacingPresets.find(item => item.label === label);
                  if (preset) setRate(values => [...preset.values.map(String), values[4]]);
                }} disabled={readOnly || !!busy} />
            </div>}>发送节奏</PanelTitle>
            <div className="rate-fields">{[0, 3, 2, 1].map(rateField)}</div>
            <div className="pacing-followup">
              {rateField(4)}
              <span className="muted fine-print">同一轮从第 2 条起生效，仍逐条确认微信回执。</span>
              <Button variant="primary" icon={<Save size={16} />} disabled={editingDisabled || rateStale || rateInvalid}
                onClick={() => void perform("rate", "settings.pacing", {
                  expected: pacing?.revision, minimum: Number(rate[0]), hourly: Number(rate[1]),
                  minute: Number(rate[2]), jitter: Number(rate[3]), continuation: Number(rate[4]),
                }, "发送节奏已保存")}>保存节奏</Button>
            </div>
          </section>
        </div>
        <footer className="settings-footer flex glass">
          <span>{data.receive === "database" ? "数据库监听 · 微信最小化时也可接收" : "可见窗口监听"}</span>
          <Button onClick={() => navigate("workbench")}>查看队列</Button>
          <Button onClick={() => void window.shiguang.windowAction("close")}>关闭选项</Button>
          <Button variant="primary" icon={<Save size={16} />} disabled={editingDisabled || generalStale} busy={busy === "general"}
            onClick={() => void perform("general", "settings.save", { expected: general?.revision, fields: draft }, "运行设置已保存")}>保存设置</Button>
        </footer>
      </> : <section className="glass panel">
        <PanelTitle end={tabs}>运行设置</PanelTitle><Empty title={error || "正在读取运行设置"} />
        {error && <Button onClick={reload} disabled={!!busy}>读取最新设置</Button>}
      </section>}
    </div>
    <div className="settings-appearance" hidden={tab !== "appearance"}><AppearancePage appearance={appearance} onChange={setAppearance}
      readOnly={readOnly} notify={notify} tabs={tabs} /></div>
    {confirm && <Confirm title={confirm.title} detail={confirm.detail} cancel={() => setConfirm(undefined)}
      confirm={() => { const run = confirm.run; setConfirm(undefined); run(); }} />}
  </div>;
}

function AppearancePage({ appearance, onChange, readOnly, notify, tabs }: {
  appearance: Appearance; onChange: (value: Appearance) => void; readOnly: boolean; notify: Notify;
  tabs: ReactNode;
}) {
  const [sample, setSample] = useState("浅色文字");
  const [segment, setSegment] = useState(0);
  const [saving, setSaving] = useState(false);
  const smaller = stepZoom(appearance.zoomPercent, -1);
  const larger = stepZoom(appearance.zoomPercent, 1);
  const setTransparency = (value: number) => onChange({ ...appearance, transparency: Math.min(100, Math.max(0, value)) });
  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      onChange(await request<Appearance>("appearance.save", {
        transparency: appearance.transparency, reduceMotion: appearance.reduceMotion,
        zoomPercent: appearance.zoomPercent, expected: appearance.revision,
      }));
      notify("界面外观已保存");
    } catch (error) { notify(errorMessage(error), true); }
    finally { setSaving(false); }
  };
  return <section className="glass panel appearance-pane">
    <PanelTitle icon={<Layers3 size={20} />} end={tabs}
      description="设置玻璃透明度、界面缩放和过渡动效，实时预览文字与控件。">外观</PanelTitle>
    <div className="settings-columns appearance-columns">
      <section className="glass panel appearance-options">
        <div className="appearance-options-scroll">
          <div className="appearance-section">
            <h3><Copy size={25} /> 玻璃透明度</h3>
            <div className="opacity-control">
              <div>
                <input type="range" aria-label="玻璃透明度" min={0} max={100} disabled={saving}
                  style={{ "--opacity-progress": `${appearance.transparency}%` } as CSSProperties}
                  value={appearance.transparency} onChange={event => setTransparency(Number(event.target.value))} />
                <div className="opacity-labels"><span>0%</span><span>100%</span></div>
              </div>
              <div className="opacity-number">
                <input type="number" aria-label="透明度百分比" min={0} max={100} step={1} disabled={saving}
                  value={appearance.transparency} onChange={event => setTransparency(Math.trunc(Number(event.target.value)))} />
                <span aria-hidden="true">%</span>
                <div className="opacity-stepper">
                  <IconButton label="提高透明度" icon={<ChevronUp size={15} />} disabled={saving || appearance.transparency === 100}
                    onClick={() => setTransparency(appearance.transparency + 1)} />
                  <IconButton label="降低透明度" icon={<ChevronDown size={15} />} disabled={saving || appearance.transparency === 0}
                    onClick={() => setTransparency(appearance.transparency - 1)} />
                </div>
              </div>
            </div>
            <p className="muted">面板与导航同步透光。</p>
          </div>
          <div className="appearance-section">
            <div className="appearance-setting-row">
              <h3><Monitor size={23} /> 界面缩放</h3>
              <div className="zoom-stepper" role="group" aria-label="界面缩放">
                <IconButton label="缩小界面" icon={<Minus size={17} />} disabled={saving || smaller === appearance.zoomPercent}
                  onClick={() => onChange({ ...appearance, zoomPercent: smaller })} />
                <output aria-label="当前界面缩放" aria-live="polite">{appearance.zoomPercent}%</output>
                <IconButton label="放大界面" icon={<Plus size={17} />} disabled={saving || larger === appearance.zoomPercent}
                  onClick={() => onChange({ ...appearance, zoomPercent: larger })} />
              </div>
            </div>
            <p className="muted">窗口大小保持，界面内容即时缩放。</p>
          </div>
          <div className="appearance-section motion-option">
            <Zap size={23} aria-hidden="true" />
            <div>
              <Switch checked={appearance.reduceMotion} disabled={saving}
                onChange={value => onChange({ ...appearance, reduceMotion: value })}>减少动态效果</Switch>
              <p className="muted">关闭过渡动效，减少界面位移。</p>
            </div>
          </div>
        </div>
        <footer className="flex appearance-actions">
          <Button icon={<RotateCcw size={15} />} disabled={saving}
            onClick={() => onChange({ ...defaultAppearance, revision: appearance.revision })}>恢复默认</Button>
          <Button variant="primary" icon={<Check size={16} />} busy={saving} disabled={readOnly} onClick={() => void save()}>保存外观</Button>
        </footer>
      </section>
      <section className="glass panel appearance-preview">
        <h3><Monitor size={17} /> 界面预览</h3><p className="muted">预览卡片、下拉框和文字对比度。</p>
        <div className="appearance-sample glass">
          <b><Layers3 size={20} /> 示例卡片 A</b>
          <p className="muted">展示卡片容器、下拉选择与分段导航。</p>
          <div className="flex">
            <Select label="示例选择" value={sample} options={["浅色文字", "次要文字"].map(value => ({ value, label: value }))} onChange={setSample} />
            <div className="segments" aria-label="分段导航预览">{["选项一", "选项二", "选项三"].map((label, index) =>
              <Button key={label} variant={segment === index ? "primary" : "ghost"} aria-pressed={segment === index}
                onClick={() => setSegment(index)}>{label}</Button>)}</div>
          </div>
        </div>
        <div className="appearance-sample glass">
          <b><MessageCircle size={20} /> 示例卡片 B</b>
          <p className="muted">展示输入焦点与文本对比效果。</p>
          <div className="appearance-text-example">
            <input aria-label="文本输入预览" placeholder="点击查看焦点边缘" />
            <div><strong>高对比文本</strong><p className="muted">次要说明文字</p></div>
          </div>
        </div>
      </section>
    </div>
  </section>;
}
