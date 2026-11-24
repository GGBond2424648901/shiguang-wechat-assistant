import { AudioLines, Cpu, Download, Mic, RefreshCw, Search, Square, Trash2, Volume2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import { Button, Confirm, Empty, IconButton, Select } from "./components";
import { fabricLanguageLabel, fabricRoleLabel, type FabricRole } from "./fabric-labels";
import "./fabric.css";

type Tab = "community" | "local" | "engine";
type Sort = "date" | "series" | "size";
type Pack = {
  id: string; release: string; name: string; series: string; group: string; author: string; authorUrl: string;
  family: string; version: string; size: number; date: string; license: string; licenseUrl: string;
  coverUrl: string; sourceUrl: string; description: string; staged: boolean; installed: boolean;
  roleId: string; updateAvailable: boolean;
};
type Reference = { id: string; text: string; language: string; preferred: boolean; recommended: boolean };
type ReferenceSelection = { roleId: string; options: Reference[]; value: string };
type Setup = {
  supported: boolean; version: string; installations: string[]; installation: string; modelPath: string; roleId: string;
  defaultModelPath: string; defaultInstallPath: string; backend: "cuda" | "vulkan";
};
type Variant = { id: string; label: string; size_bytes: number; installed: boolean };
type Engine = Setup & {
  engine?: { state: string; compute_backend: string; runtime_ready: boolean };
  runtime?: { installed_variant: string | null; recommended_variant: string; busy: boolean; variants: Variant[] };
  assets?: { engine_core_ready: boolean; engine_core_packs: { name: string; size_bytes: number }[];
    gsv_cpp_ready: boolean; gsv_cpp_size_bytes: number };
  extraAssets?: { key: string; label: string; size_bytes: number; installed: boolean }[];
  extraBusy?: boolean;
};
type Job = {
  id: string; name: string; state: "running" | "done" | "failed" | "cancelled" | "unknown";
  kind: string; phase: string; done: number; total: number; message: string; cancelable: boolean;
  audio?: string; roleId?: string;
};
type Catalog = { items: Pack[]; source: string; warning: string };
type Library = { roles: FabricRole[]; roleId: string; modelPath: string };
const tabs: { id: Tab; name: string }[] = [
  { id: "community", name: "社区音色" }, { id: "local", name: "本地音色" }, { id: "engine", name: "引擎与资源" },
];
const sorts: { value: Sort; label: string }[] = [
  { value: "date", label: "最近更新" }, { value: "series", label: "按系列" }, { value: "size", label: "按大小" },
];
const backends = [{ value: "cuda", label: "Fabric 推理环境" }, { value: "vulkan", label: "Vulkan" }];
const bytes = (size: number) => size >= 1e9 ? `${(size / 1e9).toFixed(2)} GB` : `${Math.round(size / 1e6)} MB`;
const keyOf = (pack: Pack) => `${pack.id}:${pack.release}`;

function PackCard({ pack, selected, disabled, act }: {
  pack: Pack; selected: boolean; disabled: boolean; act: (method: string, params: Record<string, string>) => void;
}) {
  const [coverFailed, setCoverFailed] = useState(false);
  const [coverReady, setCoverReady] = useState(false);
  const params = { id: pack.id, release: pack.release };
  return <article className="fabric-card">
    <div className="fabric-cover">
      {!coverReady && <AudioLines aria-hidden="true" size={42} />}
      {pack.coverUrl && !coverFailed && <img className={coverReady ? "ready" : ""} src={pack.coverUrl} alt=""
        loading="lazy" referrerPolicy="no-referrer" onLoad={() => setCoverReady(true)} onError={() => setCoverFailed(true)} />}
      {pack.family && <span>{pack.family}</span>}
    </div>
    <div className="fabric-card-body">
      <h3 title={pack.name}>{pack.name}</h3>
      <div className="fabric-meta">
        {pack.author && <span title={pack.authorUrl || undefined}>{pack.author}</span>}
        {(pack.series || pack.group) && <span>{[pack.series, pack.group].filter(Boolean).join(" · ")}</span>}
        {(pack.license || pack.size > 0) && <span className="fabric-license">
          {pack.license && <span title={pack.licenseUrl || undefined}>{pack.license}</span>}
          {pack.size > 0 && <span>{bytes(pack.size)}</span>}
        </span>}
      </div>
      <div className="fabric-card-actions">
        {pack.staged ? <>
          <Button variant="primary" disabled={disabled} onClick={() => act("fabric.install", params)}>安装</Button>
          <Button disabled={disabled} onClick={() => act("fabric.reveal", params)}>查看下载</Button>
        </> : pack.installed && !pack.updateAvailable ? <Button variant={selected ? "ghost" : "primary"}
          disabled={disabled || selected || !pack.roleId} onClick={() => act("fabric.selectRole", { roleId: pack.roleId })}>
          {selected ? "当前音色" : "设为默认"}</Button> : <Button variant="primary" disabled={disabled}
          icon={<Download size={15} />} onClick={() => act("fabric.download", params)}>{pack.updateAvailable ? "下载更新" : "下载"}</Button>}
      </div>
      {pack.sourceUrl && <small className="muted fabric-model-path" title={pack.sourceUrl}>来源 {pack.sourceUrl}</small>}
    </div>
  </article>;
}

export function FabricPage({ active, readOnly }: { active: boolean; readOnly: boolean }) {
  const [tab, setTab] = useState<Tab>("community");
  const [setup, setSetup] = useState<Setup>();
  const [engine, setEngine] = useState<Engine>();
  const [catalog, setCatalog] = useState<Catalog>();
  const [library, setLibrary] = useState<Library>();
  const [job, setJob] = useState<Job | null>(null);
  const [dismissedJobId, setDismissedJobId] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("date");
  const [onlyAvailable, setOnlyAvailable] = useState(false);
  const [count, setCount] = useState(24);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsStart, setNeedsStart] = useState(false);
  const [previewRoleId, setPreviewRoleId] = useState("");
  const [uninstallRole, setUninstallRole] = useState<FabricRole>();
  const [referenceSelection, setReferenceSelection] = useState<ReferenceSelection>();
  const [referencePlaying, setReferencePlaying] = useState(false);
  const [previewText, setPreviewText] = useState("");
  const [backend, setBackend] = useState("cuda");
  const [directory, setDirectory] = useState("");
  const referencePlayback = useRef<{ audio?: HTMLAudioElement } | null>(null);
  const generatedAudio = useRef<HTMLAudioElement>(null);
  const previewRole = library?.roles.find(role => role.id === previewRoleId);
  const openRoleId = previewRole?.id;
  const selectedReferences = referenceSelection?.roleId === openRoleId ? referenceSelection : undefined;
  const references = selectedReferences?.options ?? [];
  const reference = selectedReferences?.value ?? "";
  const running = job?.state === "running" ? job.id : "";
  const completedJobId = job?.state === "done" && !job.audio ? job.id : "";
  const jobError = job?.state === "failed" || job?.state === "unknown";
  const uninstallMeta = [uninstallRole?.author, uninstallRole?.version].filter(Boolean).join(" · ");

  const stopReference = useCallback(() => {
    const audio = referencePlayback.current?.audio;
    referencePlayback.current = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    setReferencePlaying(false);
  }, []);

  async function playReference(roleId: string, reference: string) {
    stopReference();
    setError("");
    const playback: { audio?: HTMLAudioElement } = {};
    referencePlayback.current = playback;
    setReferencePlaying(true);
    try {
      const { id } = await request<{ id: string }>("fabric.playReference", { roleId, reference });
      if (referencePlayback.current !== playback) return;
      generatedAudio.current?.pause();
      const audio = new Audio();
      playback.audio = audio;
      const failed = () => {
        if (referencePlayback.current !== playback) return;
        stopReference();
        setError("参考音频无法播放，请重新试听。");
      };
      audio.onended = stopReference;
      audio.onerror = failed;
      audio.src = `sg-media://voice/${id}`;
      await audio.play().catch(failed);
    } catch (cause) {
      if (referencePlayback.current !== playback) return;
      stopReference();
      setError(errorMessage(cause));
    }
  }

  const load = useCallback(async (page: Tab, refresh = false, signal?: AbortSignal) => {
    setError("");
    setLoading(true);
    try {
      const current = await request<Setup>("fabric.setup");
      if (signal?.aborted) return;
      setSetup(current);
      setNeedsStart(false);
      if (!current.installation) {
        setCatalog(undefined);
        setLibrary(undefined);
        setEngine(undefined);
        return;
      }
      if (page === "community") {
        const data = await request<Catalog>("fabric.catalog", { refresh });
        if (!signal?.aborted) setCatalog(data);
      } else if (page === "local") {
        const data = await request<Library>("fabric.roles");
        if (!signal?.aborted) setLibrary(data);
      } else {
        const data = await request<Engine>("fabric.engine");
        if (!signal?.aborted) {
          setEngine(data);
          setNeedsStart(!data.runtime);
        }
      }
    } catch (cause) {
      if (!signal?.aborted) {
        const message = errorMessage(cause);
        if (/尚未启动|无法连接 Fabric/.test(message)) setNeedsStart(true);
        else setError(message);
      }
    } finally { if (!signal?.aborted) setLoading(false); }
  }, []);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void load(tab, false, controller.signal);
    request<Job | null>("fabric.status").then(value => {
      if (!controller.signal.aborted) setJob(value);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [active, tab, load]);

  useEffect(() => {
    if (!active || tab !== "local" || !openRoleId) return;
    let current = true;
    setReferenceSelection(undefined);
    request<Reference[]>("fabric.references", { roleId: openRoleId }).then(options => {
      if (current) {
        const value = (options.find(item => item.preferred) ?? options.find(item => item.recommended) ?? options[0])?.id ?? "";
        setReferenceSelection({ roleId: openRoleId, options, value });
      }
    }).catch(cause => { if (current) setError(errorMessage(cause)); });
    return () => { current = false; stopReference(); };
  }, [active, tab, openRoleId, stopReference]);

  useEffect(() => {
    if (!active) setUninstallRole(undefined);
  }, [active]);

  useEffect(() => {
    if (!completedJobId || completedJobId === dismissedJobId) return;
    const timer = window.setTimeout(() => setDismissedJobId(completedJobId), 3500);
    return () => window.clearTimeout(timer);
  }, [completedJobId, dismissedJobId]);

  useEffect(() => {
    if (!active || !running) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      try {
        const next = await request<Job | null>("fabric.status");
        if (stopped) return;
        setJob(next);
        if (next?.state === "running") timer = window.setTimeout(poll, 650);
        else void load(tab);
      } catch (cause) { if (!stopped) setError(errorMessage(cause)); }
    };
    timer = window.setTimeout(poll, 400);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [active, running, tab, load]);

  const visible = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    const packs = (catalog?.items ?? []).filter(pack =>
      (!onlyAvailable || !pack.installed) && [pack.name, pack.author, pack.series, pack.group, pack.family]
        .some(value => value.toLocaleLowerCase().includes(term)));
    const recent = (a: Pack, b: Pack) => b.date.localeCompare(a.date);
    if (sort === "series") return packs.sort((a, b) => a.series.localeCompare(b.series, "zh-CN") || recent(a, b));
    if (sort === "size") return packs.sort((a, b) => a.size - b.size || recent(a, b));
    return packs.sort(recent);
  }, [catalog, query, sort, onlyAvailable]);

  async function act(method: string, params: Record<string, string> = {}) {
    setError("");
    setBusy(true);
    try {
      if (["fabric.download", "fabric.install", "fabric.cancel", "fabric.provision", "fabric.assets", "fabric.preview", "fabric.installEngine",
        "fabric.extraAssets"].includes(method)) {
        if (method === "fabric.preview") stopReference();
        const next = await request<Job>(method, params);
        setJob(next);
        if (next.state !== "running") await load(tab);
      } else if (method === "fabric.uninstall") {
        const roleId = params.roleId;
        if (previewRoleId === roleId) stopReference();
        if (job?.roleId === roleId) generatedAudio.current?.pause();
        let warning = "";
        const data = await request<Library>(method, params).catch(async cause => {
          warning = errorMessage(cause);
          return request<Library>("fabric.roles").catch(() => { throw cause; });
        });
        setLibrary(data);
        setSetup(current => current && { ...current, roleId: data.roleId });
        if (!data.roles.some(role => role.id === roleId)) {
          setCatalog(current => current && { ...current, items: current.items.map(pack => pack.roleId === roleId
            ? { ...pack, roleId: "", installed: false, updateAvailable: false } : pack) });
          setPreviewRoleId(current => current === roleId ? "" : current);
          setJob(current => current?.kind === "preview" && current.roleId === roleId ? null : current);
        }
        if (warning) setError(warning);
      } else {
        await request(method, params);
        if (method === "fabric.saveReference") {
          setReferenceSelection(current => current?.roleId === params.roleId ? { ...current,
            options: current.options.map(item => ({ ...item, preferred: item.id === params.reference })) } : current);
        }
        if (method === "fabric.modelDirectory" || method === "fabric.selectInstallation") setDirectory("");
        if (method !== "fabric.reveal") await load(tab, method === "fabric.start");
      }
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function browseDirectory() {
    try {
      const selected = await window.shiguang.pickModelDirectory();
      if (selected) setDirectory(selected);
    } catch (cause) { setError(errorMessage(cause)); }
  }

  const disabled = readOnly || busy || Boolean(running);
  const needsSetup = setup && (needsStart || !setup.installation);
  const total = job?.total ?? 0;
  return <div className="fabric-page glass panel">
    <div className="fabric-tabs" aria-label="语音与音色功能">
      {tabs.map(item => <button key={item.id} type="button" aria-pressed={tab === item.id}
        onClick={() => setTab(item.id)}>{item.name}</button>)}
    </div>
    <p className="muted fabric-attribution">语音合成与社区音色接入使用图灵镜（Turing Mirror）的 GPT-SoVITS Fabric。社区音色由各自的作者提供。</p>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {tab !== "engine" && needsSetup ? <div className="fabric-setup-empty">
      <Empty icon={<Mic size={29} />} title={needsStart ? "Fabric 尚未启动" : "先连接 Fabric 引擎"} />
      <Button variant="primary" disabled={readOnly || busy} onClick={() => needsStart ? void act("fabric.start") : setTab("engine")}>
        {needsStart ? "启动语音引擎" : "打开引擎与资源"}</Button>
    </div> : tab === "community" && <>
      <div className="fabric-toolbar">
        <label className="fabric-search"><Search size={18} aria-hidden="true" /><input aria-label="搜索社区音色"
          placeholder="搜索音色、系列或作者" value={query} onChange={event => { setQuery(event.target.value); setCount(24); }} /></label>
        <Select label="音色排序" value={sort} options={sorts} onChange={value => setSort(value as Sort)} />
        <label className="fabric-filter"><input type="checkbox" checked={onlyAvailable} onChange={event => {
          setOnlyAvailable(event.target.checked); setCount(24);
        }} />只看未安装</label>
        <IconButton label="刷新社区音色" disabled={loading} icon={<RefreshCw size={18} />} onClick={() => void load(tab, true)} />
      </div>
      {catalog?.warning && <p className="muted" role="status">社区刷新暂未完成。{catalog.warning}</p>}
      {visible.length ? <div className="fabric-grid">{visible.slice(0, count).map(pack => <PackCard key={keyOf(pack)}
        pack={pack} selected={Boolean(pack.roleId) && pack.roleId === setup?.roleId} disabled={disabled}
        act={(method, params) => void act(method, params)} />)}</div>
        : <Empty title={loading ? "正在读取社区音色" : query ? "没有匹配的音色" : "暂无可显示的音色"} />}
      {count < visible.length && <div className="fabric-more"><Button onClick={() => setCount(count + 24)}>加载更多</Button></div>}
    </>}
    {tab === "local" && !needsSetup && <>
      {library?.modelPath && <p className="muted fabric-model-path">当前模型目录 <code>{library.modelPath}</code></p>}
      {library?.roles.length ? <div className="fabric-role-list">{library.roles.map(role => <article key={role.id} className="fabric-role">
        <div className="fabric-role-icon"><Volume2 size={21} /></div>
        <div><h3>{fabricRoleLabel(role)}</h3><p className="muted">{[role.author, role.version]
          .filter(Boolean).join(" · ")}</p></div>
        <div className="fabric-role-actions"><Button disabled={busy} onClick={() => setPreviewRoleId(role.id)}>试听</Button>
          <Button variant={library.roleId === role.id ? "ghost" : "primary"} disabled={disabled || library.roleId === role.id}
            onClick={() => void act("fabric.selectRole", { roleId: role.id })}>{library.roleId === role.id ? "当前音色" : "设为默认"}</Button>
          <Button variant="danger" aria-label={`卸载 ${fabricRoleLabel(role)}`} disabled={disabled}
            icon={<Trash2 size={15} />} onClick={() => setUninstallRole(role)}>卸载</Button></div>
      </article>)}</div> : <Empty title={loading ? "正在读取本地音色" : "暂无已安装音色"}
        detail={loading ? undefined : "可以在社区音色页下载并安装角色包。"} />}
      {previewRole && <section className="fabric-engine-card fabric-preview">
        <div className="fabric-section-header"><h3>{fabricRoleLabel(previewRole)} · 试听</h3>
          <Button variant="ghost" onClick={() => setPreviewRoleId("")}>收起</Button></div>
        <label className="field"><span>合成文本</span><textarea value={previewText} rows={3} maxLength={1000}
          onChange={event => setPreviewText(event.target.value)} placeholder="输入要试听的内容" /></label>
        <div className="fabric-preview-fields">
          <div className="field"><span>参考音频对应文本</span><div className="fabric-reference-row">
            <Select label="参考音频对应文本" value={reference}
              options={references.map(item => ({ value: item.id, label: `${fabricLanguageLabel(item.language)} · ${item.text || item.id}` }))}
              disabled={busy || !references.length} onChange={value => {
                if (value === reference || !selectedReferences) return;
                setReferenceSelection({ ...selectedReferences, value });
                void playReference(previewRole.id, value);
              }} />
            <Button variant="ghost" disabled={!reference || (busy && !referencePlaying)}
              icon={referencePlaying ? <Square size={15} /> : <Volume2 size={16} />}
              onClick={() => referencePlaying ? stopReference() : void playReference(previewRole.id, reference)}>
              {referencePlaying ? "停止播放" : "播放参考音频"}
            </Button>
          </div></div>
          <div className="field"><span>合成引擎</span><Select label="试听合成引擎" value={backend} options={backends}
            disabled={disabled} onChange={setBackend} /></div>
        </div>
        <div className="fabric-role-actions">
          <Button variant="primary" disabled={disabled || !reference || !previewText.trim()} icon={<Volume2 size={16} />}
            onClick={() => void act("fabric.preview", { roleId: previewRole.id, text: previewText, reference, backend })}>生成试听</Button>
          <Button disabled={disabled || !reference || references.some(item => item.id === reference && item.preferred)}
            onClick={() => void act("fabric.saveReference", { roleId: previewRole.id, reference })}>保存默认参考音频</Button>
        </div>
      </section>}
    </>}
    {tab === "engine" && <div className="fabric-engine">
      <section className="fabric-engine-card">
        <div className="fabric-section-header"><h3><Cpu size={20} /> GPT-SoVITS Fabric {setup?.version}</h3>
          <IconButton label="刷新引擎状态" disabled={loading} icon={<RefreshCw size={18} />} onClick={() => void load(tab)} /></div>
        {setup?.installations.length ? <Select label="Fabric 安装目录" value={setup.installation}
          options={setup.installations.map(path => ({ value: path, label: path }))} disabled={disabled}
          onChange={path => void act("fabric.selectInstallation", { path })} /> : <p className="muted">尚未找到此版本的 Fabric 安装。</p>}
        {setup?.modelPath && <p className="muted fabric-model-path">当前模型目录 <code>{setup.modelPath}</code></p>}
        {needsStart && <Button variant="primary" disabled={disabled} onClick={() => void act("fabric.start")}>启动语音引擎</Button>}
        {setup && !setup.installations.length && <>
          <p className="muted fabric-model-path">默认安装目录 <code>{setup.defaultInstallPath}</code></p>
          <Button variant="primary" icon={<Download size={16} />} disabled={disabled || !setup.supported}
            onClick={() => void act("fabric.installEngine")}>下载并安装语音引擎</Button>
        </>}
      </section>
      {setup?.installation && <section className="fabric-engine-card fabric-storage"><h3>音色模型目录</h3>
        {needsStart ? <>
          <div className="fabric-directory"><label className="field"><span>模型下载与安装位置</span>
            <input value={directory} onChange={event => setDirectory(event.target.value)} placeholder={setup.modelPath} /></label>
            <Button disabled={disabled} onClick={() => void browseDirectory()}>选择文件夹</Button></div>
          <div className="flex">
            <Button variant="primary" disabled={disabled || !directory.trim() || directory.trim() === setup.modelPath}
              onClick={() => void act("fabric.modelDirectory", { path: directory.trim() })}>保存模型目录</Button>
            <Button disabled={disabled || setup.modelPath === setup.defaultModelPath}
              onClick={() => void act("fabric.modelDirectory", { path: "" })}>恢复默认目录</Button>
          </div>
          <p className="muted">可使用其他磁盘上的模型库。切换后，原目录里的音色仍可通过切回原目录使用。</p>
        </> : <>
          <p className="muted">退出 Fabric 后可以切换模型目录。</p>
          <Button disabled={disabled || engine?.runtime?.busy || loading}
            onClick={() => void act("fabric.stop")}>退出 Fabric 以切换目录</Button>
        </>}
      </section>}
      {engine?.runtime && <section className="fabric-engine-card">
        <h3>推理环境</h3>
        <div className="fabric-resource"><div><strong>微信语音使用</strong><span className="muted">
          {setup?.backend === "vulkan" ? "Vulkan" : engine.runtime.installed_variant === "nvidia" ? "NVIDIA CUDA" : "Fabric 推理环境"}
        </span></div><Select label="微信语音合成引擎" value={setup?.backend ?? "cuda"}
          options={engine.assets?.gsv_cpp_ready ? backends : backends.slice(0, 1)} disabled={disabled}
          onChange={value => void act("fabric.saveBackend", { backend: value })} /></div>
        {engine.runtime.variants.map(variant => <div className="fabric-resource" key={variant.id}><div>
          <strong>{variant.id === "nvidia" ? "NVIDIA CUDA" : variant.label || "CPU"}</strong>
          <span className="muted">{variant.size_bytes ? bytes(variant.size_bytes) : ""}
            {variant.id === engine.runtime?.recommended_variant ? " · 本机推荐" : ""}</span></div>
          <Button disabled={disabled || engine.runtime?.busy || variant.id === engine.runtime?.installed_variant}
            icon={<Download size={15} />} onClick={() => void act("fabric.provision", { variant: variant.id })}>
            {variant.id === engine.runtime?.installed_variant ? "当前环境" : variant.installed ? "切换" : "下载并安装"}</Button></div>)}
      </section>}
      {engine?.assets && <section className="fabric-engine-card"><h3>模型资源</h3>
        <div className="fabric-resource"><div><strong>基础合成模型</strong><span className="muted">
          {bytes(engine.assets.engine_core_packs.reduce((sum, pack) => sum + pack.size_bytes, 0))}</span></div>
          <Button disabled={disabled || engine.assets.engine_core_ready} icon={<Download size={15} />}
            onClick={() => void act("fabric.assets", { kind: "base" })}>{engine.assets.engine_core_ready ? "已就绪" : "下载并安装"}</Button></div>
        <div className="fabric-resource"><div><strong>Vulkan 推理资源</strong><span className="muted">
          {bytes(engine.assets.gsv_cpp_size_bytes)} · 支持 V5 Turbo 中日文</span></div>
          <Button disabled={disabled || engine.assets.gsv_cpp_ready} icon={<Download size={15} />}
            onClick={() => void act("fabric.assets", { kind: "vulkan" })}>{engine.assets.gsv_cpp_ready ? "已就绪" : "下载并安装"}</Button></div>
        {engine.extraAssets?.map(asset => <div className="fabric-resource" key={asset.key}>
          <div><strong>{asset.key === "gsv-v5" ? "V5 / V5 Turbo 合成底模" : asset.label}</strong>
            <span className="muted">{bytes(asset.size_bytes)}</span></div>
          <Button disabled={disabled || asset.installed || engine.extraBusy} icon={<Download size={15} />}
            onClick={() => void act("fabric.extraAssets", { key: asset.key })}>{asset.installed ? "已就绪" : "下载并安装"}</Button>
        </div>)}
      </section>}
    </div>}
    {job && (!completedJobId || completedJobId !== dismissedJobId) && <div key={`${job.id}:${jobError}`}
      className={`fabric-job ${jobError ? "inline-error" : ""}`} role={jobError ? "alert" : "status"}>
      <div><strong>{job.name}</strong><span>{job.message || "正在准备"}</span></div>
      {job.state === "running" && (total > 0 ? <progress max={total} value={job.done} aria-label="Fabric 任务进度" /> :
        <progress aria-label="Fabric 任务进度" />)}
      {job.cancelable && <Button disabled={readOnly || busy} onClick={() => void act("fabric.cancel", { id: job.id })}>
        {job.kind === "preview" ? "取消试听" : "取消下载"}</Button>}
      {job.audio && tab === "local" && <audio ref={generatedAudio} key={job.id} src={job.audio} controls preload="metadata"
        aria-label="合成试听" onPlay={stopReference} onError={() => setError("试听音频无法读取，请重新生成。")} />}
    </div>}
    {active && uninstallRole && <Confirm destructive confirmLabel="卸载" title={`卸载“${fabricRoleLabel(uninstallRole)}”？`}
      detail={uninstallMeta ? `将移除该音色的本地模型（${uninstallMeta}）。` : "将移除该音色的本地模型。"}
      cancel={() => setUninstallRole(undefined)} confirm={() => {
        setUninstallRole(undefined);
        void act("fabric.uninstall", { roleId: uninstallRole.id });
      }} />}
  </div>;
}
