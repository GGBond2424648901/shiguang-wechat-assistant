import { ArrowLeft, BookOpen, CircleHelp, FolderOpen, Images, Plus, RefreshCw, Save, Search, Send, Smile, Tags, X } from "lucide-react";
import { useDraftGuard } from "./components";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { errorMessage, frequencies, mediaUrl, priorities, request, tagsFromText, type Catalog, type Sticker, type StickerDetail } from "./api";
import { Button, Confirm, Empty, IconButton, PanelTitle, Select, Switch } from "./components";
import { ImportPanel, useImports } from "./sticker-import";

const BLOCK = 72;
const INITIAL = { width: 640, height: 440, row: 172 };
// A full viewport can load immediately while the bridge retains capacity for catalogue queries.
const GALLERY_IMAGE_SLOTS = 20;
const queuedImages = new Map<HTMLImageElement, string>();
const loadingImages = new Set<HTMLImageElement>();
type Notify = (message: string, error?: boolean) => void;
type Gallery = { key: string; meta: Catalog; blocks: Map<number, Sticker[]> };
type Subpage = "library" | "import" | "detail" | "guide";

function loadQueuedImages() {
  for (const [image, source] of queuedImages) {
    if (loadingImages.size >= GALLERY_IMAGE_SLOTS) break;
    if (loadingImages.has(image)) continue;
    queuedImages.delete(image);
    loadingImages.add(image);
    // Electron keeps serving already started requests even after their tiles unmount.
    const finish = () => {
      image.removeEventListener("load", finish);
      image.removeEventListener("error", finish);
      loadingImages.delete(image);
      loadQueuedImages();
    };
    image.addEventListener("load", finish);
    image.addEventListener("error", finish);
    image.src = source;
  }
}

const Tile = memo(function Tile({ item, active, open }: { item: Sticker; active: boolean; open: (item: Sticker) => void }) {
  const [hover, setHover] = useState(false);
  const [broken, setBroken] = useState(false);
  const image = useRef<HTMLImageElement>(null);
  const animate = active && hover && item.animated && document.documentElement.dataset.reduceMotion !== "true"
    && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const source = mediaUrl(item, animate);
  useLayoutEffect(() => {
    const node = image.current;
    if (!active || broken || !node || node.getAttribute("src") === source) return;
    queuedImages.set(node, source);
    loadQueuedImages();
    return () => { queuedImages.delete(node); };
  }, [active, broken, source]);

  return <button type="button" className="sticker-tile" onClick={() => open(item)}
    onPointerEnter={() => setHover(true)} onPointerLeave={() => setHover(false)}>
    <span className="tile-art">
      {broken ? <Images size={22} /> : <img ref={image} alt="" draggable={false} decoding="async"
        onError={() => setBroken(true)} />}
      {item.animated && <span className="gif-label">GIF</span>}
    </span>
    <span className="tile-caption"><b>{item.meaning}</b><small>{item.character}</small></span>
  </button>;
});

export function StickersPage({ active, readOnly, notify }: { active: boolean; readOnly: boolean; notify: Notify }) {
  const [sub, setSub] = useState<Subpage>("library");
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [reload, setReload] = useState(0);
  const [gallery, setGallery] = useState<Gallery>();
  const [error, setError] = useState("");
  const [measure, setMeasure] = useState(INITIAL);
  const [scroll, setScroll] = useState(0);
  const [detail, setDetail] = useState<Sticker>();
  const [guide, setGuide] = useState("");
  const [guideFrom, setGuideFrom] = useState<"library" | "import">("library");
  const grid = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const live = active && sub === "library";
  const key = JSON.stringify([category, debounced, reload]);
  const current = gallery?.key === key ? gallery : undefined;
  const meta = current?.meta;
  const columns = Math.max(1, Math.min(4, Math.floor((measure.width + 12) / 174)));
  const first = Math.max(0, Math.floor(scroll / measure.row) - 1) * columns;
  const end = Math.min(meta?.total ?? 0, (Math.ceil((scroll + measure.height) / measure.row) + 2) * columns);
  const imports = useImports(active, notify);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 160);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const node = grid.current;
    if (!live || !node) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const row = Number.parseFloat(getComputedStyle(node).getPropertyValue("--gallery-row")) || INITIAL.row;
      if (width > 0 && height > 0) setMeasure(previous =>
        previous.width === width && previous.height === height && previous.row === row ? previous : { width, height, row });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [live]);

  useEffect(() => {
    if (grid.current) grid.current.scrollTop = 0;
    setScroll(0);
  }, [category, debounced]);

  useEffect(() => {
    if (!live || current) return;
    let listening = true;
    setError("");
    request<Catalog>("stickers.query", { category, query: debounced, offset: 0, limit: BLOCK }).then(data => {
      if (listening) setGallery({ key, meta: data, blocks: new Map([[0, data.items]]) });
    }).catch(failure => { if (listening) setError(errorMessage(failure)); });
    return () => { listening = false; };
  }, [live, key, current, category, debounced]);

  const blockStart = Math.floor(first / BLOCK);
  const blockEnd = Math.floor(Math.max(0, end - 1) / BLOCK);
  useEffect(() => {
    if (!live || !current) return;
    let listening = true;
    for (let block = blockStart; block <= blockEnd; block += 1) {
      if (current.blocks.has(block)) continue;
      request<Catalog>("stickers.query", { category, query: debounced, offset: block * BLOCK, limit: BLOCK })
        .then(data => {
          if (!listening) return;
          if (data.revision !== current.meta.revision) { setReload(value => value + 1); return; }
          setGallery(previous => {
            if (previous?.key !== key) return previous;
            const blocks = new Map(previous.blocks).set(block, data.items);
            if (blocks.size > 22) {
              for (const cached of blocks.keys()) {
                if (cached !== 0 && (cached < blockStart || cached > blockEnd)) {
                  blocks.delete(cached);
                  break;
                }
              }
            }
            return { ...previous, blocks };
          });
        })
        .catch(failure => { if (listening) setError(errorMessage(failure)); });
    }
    return () => { listening = false; };
  }, [live, key, current, blockStart, blockEnd, category, debounced]);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const openDetail = useCallback((item: Sticker) => {
    setDetail(item);
    setSub("detail");
  }, []);
  const refresh = useCallback(() => setReload(value => value + 1), []);
  const categories = gallery?.meta.categories;
  const categoryOptions = useMemo(() => [{ value: "", label: "全部分类" }, ...(categories ?? [])
    .map(label => ({ label, value: label }))], [categories]);
  const viewGuide = async () => {
    setGuideFrom(sub === "import" ? "import" : "library");
    setSub("guide");
    if (guide) return;
    try { setGuide(await request<string>("stickers.guide")); }
    catch (failure) { notify(errorMessage(failure), true); }
  };
  return <div className="sticker-page" data-subpage={sub}>
    {sub === "library" && <div className="page-toolbar">
      <div className="segments" aria-label="表情包页面">
        <Button variant="primary" aria-current="page" onClick={() => setSub("library")}>素材库</Button>
        <Button variant="ghost" onClick={() => setSub("import")}>导入与识别</Button>
      </div>
      <div className="flex toolbar-actions">
        <Button variant="primary" icon={<Plus size={17} />} disabled={readOnly || imports.busy}
          onClick={() => { setSub("import"); void imports.pick(false); }}>导入素材</Button>
        <Button icon={<CircleHelp size={16} />} onClick={() => void viewGuide()}>收集说明</Button>
      </div>
    </div>}
    <div className="library-layout" hidden={sub !== "library"}>
      <section className="glass panel gallery-panel">
        <PanelTitle>
          素材库 <small>{meta ? `共 ${meta.total.toLocaleString()} 项` : ""}</small>
        </PanelTitle>
        <div className="gallery-filters">
          <label className="search-field"><Search size={17} /><input aria-label="搜索表情" value={query}
            onChange={event => setQuery(event.target.value)} placeholder="搜索用途、情绪或分类" maxLength={160} />
            {query && <IconButton label="清空搜索" icon={<X size={15} />} onClick={() => setQuery("")} />}</label>
          <Select label="素材分类" value={category} options={categoryOptions} onChange={setCategory} />
        </div>
        <div className="filter-chips">{categoryOptions.slice(0, 8).map(option =>
          <button type="button" key={option.value} aria-pressed={category === option.value}
            onClick={() => setCategory(option.value)}>{option.value ? option.label : "全部"}</button>)}</div>
        {error && <div className="inline-error" role="alert">{error}<Button onClick={refresh}>重试</Button></div>}
        <div className="gallery-scroll" ref={grid} data-testid="gallery-scroll"
          data-total={meta?.total ?? 0} onScroll={() => {
            if (frame.current) return;
            frame.current = requestAnimationFrame(() => { frame.current = 0; setScroll(grid.current?.scrollTop ?? 0); });
          }}>
          {!meta?.total && <Empty icon={<Smile size={28} />}
            title={!meta ? error ? "素材库暂不可读" : "正在加载素材库" : "没有匹配的表情"}
            detail={meta && !meta.total ? "试试其他关键词，或导入本地素材" : undefined} />}
          {Boolean(meta?.total) && <div className="gallery-space" style={{ height: Math.ceil(meta!.total / columns) * measure.row }}>
            <div className="gallery-grid" style={{
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
              transform: `translateY(${Math.floor(first / columns) * measure.row}px)`,
            }}>
              {Array.from({ length: Math.max(0, end - first) }, (_, offset) => {
                const index = first + offset;
                const item = current?.blocks.get(Math.floor(index / BLOCK))?.[index % BLOCK];
                return item ? <Tile key={item.id} item={item} active={live} open={openDetail} />
                  : <div className="sticker-skeleton" key={`loading-${index}`} />;
              })}
            </div>
          </div>}
        </div>
      </section>
      <StickerPreferences meta={gallery?.meta} readOnly={readOnly} notify={notify} refresh={refresh} />
    </div>
    {sub === "library" && <footer className="glass gallery-bottom">
      <span>{meta ? `已载入 ${meta.total.toLocaleString()} 个素材` : "正在载入"}</span>
      <span>滚轮浏览 · 点击查看详情 · 支持 PNG、JPG、WebP 和 GIF</span>
      <Button icon={<RefreshCw size={16} />} aria-label="刷新素材库" onClick={refresh}>刷新</Button>
    </footer>}
    {sub === "import" && <>
      <div className="page-toolbar subpage-toolbar"><div className="flex">
        <Button icon={<ArrowLeft size={16} />} onClick={() => setSub("library")}>返回素材库</Button>
        <span className="section-marker">导入与识别</span>
      </div><Button icon={<CircleHelp size={16} />} onClick={() => void viewGuide()}>收集说明</Button></div>
      <ImportPanel controller={imports} categories={categoryOptions.slice(1)} active={active}
        readOnly={readOnly} notify={notify} onSaved={refresh} />
    </>}
    {sub === "detail" && detail && <StickerInspector key={detail.id} item={detail} active={active}
      readOnly={readOnly} categories={categoryOptions.slice(1)} notify={notify}
      back={() => setSub("library")} onSaved={refresh} />}
    {sub === "guide" && <>
      <div className="page-toolbar subpage-toolbar"><div className="flex">
        <Button icon={<ArrowLeft size={16} />} onClick={() => setSub(guideFrom)}>{guideFrom === "import" ? "返回导入" : "返回素材库"}</Button>
        <span className="section-marker">收集说明</span>
      </div></div>
      <StickerGuide text={guide} />
    </>}
  </div>;
}

function StickerGuide({ text }: { text: string }) {
  const [intro, ...sections] = text.trim().split(/(?=^[一二三四五六七八九十]+、)/m);
  const icons = [Images, FolderOpen, Tags, Send, BookOpen];
  return <section className="glass panel subpage guide-panel">
    <PanelTitle>收集说明</PanelTitle>
    <p className="muted">{intro.trim() || "正在读取说明…"}</p>
    <div className="guide-text">
      {sections.map((section, index) => {
        const [title, ...lines] = section.trim().split(/\r?\n/);
        const Icon = icons[index] ?? CircleHelp;
        return <section className="guide-section" key={title}>
          <span className="guide-icon"><Icon size={26} /></span>
          <div><h3>{title}</h3><p>{lines.join("\n").trim()}</p></div>
        </section>;
      })}
    </div>
  </section>;
}

function StickerPreferences({ meta, readOnly, notify, refresh }: {
  meta?: Catalog; readOnly: boolean; notify: Notify; refresh: () => void;
}) {
  const [draft, setDraft] = useState<{ priorities: Record<string, string>; neutral: boolean; revision: number }>();
  const [frequency, setFrequency] = useState<{ value: string; revision: string }>();
  const [busy, setBusy] = useState("");
  const values = draft ?? meta;
  const staleFrequency = Boolean(frequency && frequency.revision !== meta?.configRevision);
  useDraftGuard("sticker-preferences", !!draft || !!frequency);
  const savePriorities = async () => {
    if (!values || busy) return;
    setBusy("priorities");
    try {
      await request("stickers.preferences", { expected: values.revision, priorities: values.priorities, neutral: values.neutral });
      setDraft(undefined);
      notify("分类优先级已保存");
      refresh();
    } catch (error) { notify(errorMessage(error), true); }
    finally { setBusy(""); }
  };
  const saveFrequency = async () => {
    if (!meta || !frequency || busy) return;
    setBusy("frequency");
    try {
      const result = await request<{ offline: boolean }>("stickers.frequency", { frequency: frequency.value, expected: frequency.revision });
      setFrequency(undefined);
      notify(result.offline ? "默认频率已保存，下次启动生效" : "默认表情频率已保存");
      refresh();
    } catch (error) { notify(errorMessage(error), true); }
    finally { setBusy(""); }
  };
  const selected = frequency?.value || meta?.frequency || "natural";
  return <section className="glass panel preference-panel">
    <PanelTitle>发送偏好</PanelTitle>
    <p className="muted">会话单独设置的偏好优先生效。</p>
    <div className="preference-scroll">
      <div className="field"><span>默认发送频率</span>
        <Select label="默认发送频率" options={frequencies} value={selected}
          onChange={value => meta && setFrequency(value === meta.frequency ? undefined : { value, revision: meta.configRevision })}
          disabled={!meta || readOnly} />
      </div>
      {frequency && (staleFrequency
        ? <Button className="wide" onClick={() => setFrequency(undefined)}>设置已更新，取消频率草稿</Button>
        : <Button className="wide" disabled={readOnly} busy={busy === "frequency"} onClick={() => void saveFrequency()}>保存默认频率</Button>)}
      <div className="form-divider" />
      <h3>分类优先级</h3>
      {meta?.categories.map(name => <div className="priority-row" key={name}><span>{name}</span>
        <Select label={`${name} 优先级`} options={priorities} value={values?.priorities[name] ?? "标准"}
          onChange={value => values && setDraft({ ...values, priorities: { ...values.priorities, [name]: value } })}
          disabled={readOnly} /></div>)}
      <Switch checked={values?.neutral ?? false} onChange={value => values && setDraft({ ...values, neutral: value })}
        disabled={!values || readOnly}>不偏好任何分类</Switch>
      <p className="muted fine-print">停用分类不会自动发送。备用分类只在缺少合适素材时使用。</p>
    </div>
    <Button variant="primary" className="wide" icon={<Send size={16} />} busy={busy === "priorities"}
      disabled={!draft || readOnly} onClick={() => void savePriorities()}>保存偏好</Button>
  </section>;
}

function StickerInspector({ item, active, readOnly, categories, notify, back, onSaved }: {
  item: Sticker; active: boolean; readOnly: boolean; categories: { label: string; value: string }[];
  notify: Notify; back: () => void; onSaved: () => void;
}) {
  const [record, setRecord] = useState<StickerDetail>();
  const [draft, setDraft] = useState({ category: item.character, meaning: item.meaning, tags: item.tags.join("、"), source: "" });
  const [busy, setBusy] = useState(false);
  const [broken, setBroken] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const dirty = !!record && (draft.category !== record.character || draft.meaning !== record.meaning ||
    draft.tags !== record.tags.join("、") || draft.source !== record.source);
  useDraftGuard("sticker-detail", dirty);
  useEffect(() => {
    let current = true;
    request<StickerDetail>("stickers.detail", { id: item.id }).then(data => {
      if (!current) return;
      setRecord(data);
      setDraft({ category: data.character, meaning: data.meaning, tags: data.tags.join("、"), source: data.source });
    }).catch(error => { if (current) notify(errorMessage(error), true); });
    return () => { current = false; };
  }, [item.id, notify]);
  const save = async () => {
    if (!record || busy) return;
    setBusy(true);
    try {
      await request("stickers.saveDetail", { id: item.id, expected: record.revision, ...draft, tags: tagsFromText(draft.tags) });
      onSaved();
      notify("素材标注已保存");
      back();
    } catch (error) { notify(errorMessage(error), true); }
    finally { setBusy(false); }
  };
  return <>
    <div className="page-toolbar detail-toolbar"><div className="flex">
      <Button icon={<ArrowLeft size={16} />} disabled={busy} onClick={() => dirty ? setLeaving(true) : back()}>返回素材库</Button>
      {record && <span className="identity-label confirmed">已入库</span>}
    </div></div>
    <section className="glass panel subpage detail-panel">
    <PanelTitle>素材详情</PanelTitle>
    <div className="detail-layout">
      <div className="detail-preview">
        <div className="detail-art">
          {active && !broken && <img src={mediaUrl(item, item.animated)} alt={draft.meaning} onError={() => setBroken(true)} />}
          {item.animated && <span className="gif-label">GIF</span>}
        </div>
        {broken && <Empty title="预览不可用" />}
        {record && <span>{record.extension.toUpperCase()} · {record.width} × {record.height} · {record.frames} 帧</span>}
      </div>
      <div className="glass form-stack detail-editor">
        <div className="form-stack sticker-editor-scroll">
          <label className="field">分类<input aria-label="分类" list="sticker-categories" value={draft.category}
            onChange={event => setDraft({ ...draft, category: event.target.value })} maxLength={20} disabled={readOnly} />
            <datalist id="sticker-categories">{categories.map(value => <option key={value.value} value={value.value} />)}</datalist>
          </label>
          {(["meaning", "tags"] as const).map((field, index) => <label className="field" key={field}>
            {["用途", "情绪标签"][index]}
            <input value={draft[field]} onChange={event => setDraft({ ...draft, [field]: event.target.value })}
              maxLength={[120, 180][index]} disabled={readOnly} />
          </label>)}
          <label className="field">来源<textarea value={draft.source} maxLength={1000} rows={3} disabled={readOnly}
            onChange={event => setDraft({ ...draft, source: event.target.value })} /></label>
        </div>
        <footer className="detail-actions sticker-editor-actions">
          <Button disabled={!record || busy} onClick={() => record && setDraft({
            category: record.character, meaning: record.meaning, tags: record.tags.join("、"), source: record.source,
          })}>撤销修改</Button>
          <Button variant="primary" icon={<Save size={16} />} disabled={readOnly || !record} busy={busy}
            onClick={() => void save()}>保存修改</Button>
        </footer>
      </div>
    </div>
    </section>
    {leaving && <Confirm title="放弃素材修改" detail="返回素材库会放弃这张表情未保存的修改。"
      cancel={() => setLeaving(false)} confirm={back} />}
  </>;
}
