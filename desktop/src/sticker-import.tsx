import { FilePlus, FolderOpen, ImagePlus, Save, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, request, tagsFromText, type ImportRow, type ImportSnapshot } from "./api";
import { Button, Empty, PanelTitle, Select, useDraftGuard, type Option } from "./components";

type Notify = (message: string, error?: boolean) => void;
type Edit = { category: string; meaning: string; tags: string; source: string };
const committed = (row: ImportRow) => row.status === "已入库" || row.status === "已更新";

export function useImports(active: boolean, notify: Notify) {
  const [snapshot, setSnapshot] = useState<ImportSnapshot>({ rows: [], job: null });
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Partial<Edit>>>({});
  const [category, setCategory] = useState("");
  const loaded = useRef(false);
  useDraftGuard("sticker-import", snapshot.rows.some(row => !committed(row) || Boolean(drafts[row.id])));
  const adopt = useCallback((value: ImportSnapshot) => {
    setSnapshot(previous => {
      const old = previous.job;
      const next = value.job;
      if (old && next?.id === old.id && next.state === "running"
          && (old.state !== "running" || old.completed > next.completed)) return previous;
      return value;
    });
  }, []);
  useEffect(() => {
    if (!active) return;
    const off = window.shiguang.watchImports(adopt);
    if (loaded.current) request<ImportSnapshot>("imports.list").then(adopt).catch(() => {});
    return off;
  }, [active, adopt]);

  const pick = async (folder: boolean) => {
    if (pending || snapshot.job?.state === "running") return;
    setPending(true);
    loaded.current = true;
    try {
      const next = await window.shiguang.pickStickers(folder);
      if (!next) return;
      adopt(next);
      const before = new Set(snapshot.rows.map(item => item.id));
      const fresh = next.rows.filter(row => !before.has(row.id) && row.frames > 0).map(row => row.id);
      setSelected(fresh);
      if (fresh.length) adopt(await request<ImportSnapshot>("imports.analyze", { ids: fresh, category }));
    } catch (error) { notify(errorMessage(error), true); }
    finally { setPending(false); }
  };
  const values = (row: ImportRow): Edit => ({
    category: row.category || category, meaning: row.meaning, tags: row.tags.join("、"), source: row.source,
    ...drafts[row.id],
  });
  const edit = (id: string, key: keyof Edit, value: string) => setDrafts(previous => ({
    ...previous, [id]: { ...previous[id], [key]: value },
  }));
  const perform = async (operation: string, params: Record<string, unknown>) => {
    if (pending) return;
    setPending(true);
    try {
      const response = await request<ImportSnapshot & { saved?: number; failed?: number }>(operation, params);
      adopt(response);
      if (operation === "imports.remove" || operation === "imports.commit") {
        const kept = new Set(response.rows.filter(row => operation === "imports.remove" ||
          !committed(row) || !Object.hasOwn(params.selected as object, row.id)).map(row => row.id));
        setDrafts(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => kept.has(id))));
      }
      return response;
    } catch (error) { notify(errorMessage(error), true); }
    finally { setPending(false); }
  };
  return {
    snapshot, pending, selected, setSelected, category, setCategory, pick, values, edit, perform,
    busy: pending || snapshot.job?.state === "running",
  };
}

export function ImportPanel({ controller: c, categories, active, readOnly, notify, onSaved }: {
  controller: ReturnType<typeof useImports>; categories: Option[]; readOnly: boolean; notify: Notify;
  active: boolean; onSaved: () => void;
}) {
  const [focus, setFocus] = useState("");
  const anchor = useRef("");
  const rows = c.snapshot.rows;
  const row = rows.find(item => item.id === focus) ?? rows.find(item => c.selected.includes(item.id)) ?? rows[0];
  const values = row ? c.values(row) : undefined;
  const running = c.snapshot.job?.state === "running";
  const animate = active && document.documentElement.dataset.reduceMotion !== "true"
    && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  function select(id: string, checked: boolean, extend: boolean) {
    const start = rows.findIndex(item => item.id === anchor.current);
    const end = rows.findIndex(item => item.id === id);
    const ids = extend && start >= 0 ? rows.slice(Math.min(start, end), Math.max(start, end) + 1).map(item => item.id) : [id];
    c.setSelected(old => checked ? [...new Set([...old, ...ids])] : old.filter(key => !ids.includes(key)));
    anchor.current = id;
  }
  const commit = async () => {
    const selected = Object.fromEntries(rows.filter(item => c.selected.includes(item.id)).map(item => {
      const values = c.values(item);
      return [item.id, { ...values, tags: tagsFromText(values.tags) }];
    }));
    const result = await c.perform("imports.commit", { selected });
    if (result) {
      if (result.saved) onSaved();
      notify(`已入库或更新 ${result.saved ?? 0} 张${result.failed ? `，${result.failed} 张待核对` : ""}`, !!result.failed);
    }
  };
  return <section className="glass panel subpage import-panel">
    <PanelTitle>导入与识别</PanelTitle>
    <div className="import-layout">
      <div className="glass panel import-queue">
        <PanelTitle>上传队列</PanelTitle>
        <div className="flex import-pickers">
          <Button variant="primary" icon={<FilePlus size={16} />} onClick={() => void c.pick(false)} disabled={c.busy || readOnly}>选择文件</Button>
          <Button icon={<FolderOpen size={16} />} onClick={() => void c.pick(true)} disabled={c.busy || readOnly}>选择文件夹</Button>
        </div>
        <div className="flex import-controls">
          <span>分类</span>
          <Select label="识别分类" value={c.category} options={[{ label: "自动判断", value: "" }, ...categories]}
            onChange={c.setCategory} disabled={c.busy || readOnly} />
          <Button variant="primary" icon={<Sparkles size={15} />} disabled={c.pending || readOnly || !rows.length} onClick={() => {
            if (running) void c.perform("imports.cancel", {});
            else void c.perform("imports.analyze", { ids: c.selected.length ? c.selected : rows.map(item => item.id), category: c.category });
          }}>{running ? "停止识别" : "AI 识别"}</Button>
        </div>
        <p className="import-format">支持 PNG、JPG、WebP 和 GIF · 每批最多 100 张</p>
        {running && <progress max={c.snapshot.job!.total} value={c.snapshot.job!.completed} aria-label="识别进度" />}
        <div className="import-list" aria-label="上传队列">
          {!rows.length && <Empty icon={<ImagePlus size={34} />} title="选择表情图片开始导入"
            detail="支持 PNG、JPG、WebP 和 GIF，每批最多 100 张" />}
          {rows.map(item => <div className={`import-row ${row?.id === item.id ? "selected" : ""}`} key={item.id}>
            <input type="checkbox" checked={c.selected.includes(item.id)} aria-label={`选择 ${item.name}`}
              onChange={event => select(item.id, event.target.checked,
                event.nativeEvent instanceof MouseEvent && event.nativeEvent.shiftKey)} />
            <button type="button" onClick={() => setFocus(item.id)}>
              {item.frames > 0 ? <img alt="" loading="lazy" decoding="async" src={`sg-media://import/${item.id}`} /> : <ImagePlus size={24} />}
              <span><b>{item.name}</b><small>{item.frames > 0 ? `${item.width} × ${item.height} · ${Math.ceil(item.size / 1024)} KiB` : "无法读取"}</small></span>
            </button>
            <small className={["已入库", "已更新", "已有标注", "已识别"].includes(item.status) ? "mint" : "muted"}>{item.status}</small>
          </div>)}
        </div>
        <div className="flex import-foot">
          <span>{rows.length} 张 · 选中 {c.selected.length} 张</span>
          <Button icon={<Trash2 size={15} />} disabled={c.busy || readOnly || !c.selected.length} onClick={async () => {
            if (await c.perform("imports.remove", { ids: c.selected })) c.setSelected([]);
          }}>移出选中</Button>
        </div>
      </div>
      <div className="glass panel form-stack import-editor">
        <PanelTitle>素材详情</PanelTitle>
        {row && values ? <>
          <div className="import-preview">{row.frames > 0
            ? <img src={`sg-media://import/${row.id}${row.frames > 1 && animate ? "?variant=original" : ""}`} alt="" />
            : <Empty title="图片预览不可用" />}
          </div>
          <span className="muted">{row.width} × {row.height} · {row.frames} 帧 · {Math.ceil(row.size / 1024)} KiB</span>
          {(["category", "meaning", "tags"] as const).map((field, index) =>
            <label key={field} className="field">{["分类", "用途", "情绪标签"][index]}
              <input value={values[field]} onChange={event => c.edit(row.id, field, event.target.value)}
                disabled={readOnly || c.busy} maxLength={[20, 120, 180][index]} />
            </label>)}
          <label className="field">来源<textarea rows={2} value={values.source} onChange={event => c.edit(row.id, "source", event.target.value)}
            disabled={readOnly || c.busy} maxLength={1000} /></label>
          <Button className="wide" variant="primary" icon={<Save size={16} />} disabled={readOnly || c.busy || !c.selected.length}
            onClick={() => void commit()}>确认入库</Button>
        </> : <Empty title="选中一张素材查看详情" />}
      </div>
    </div>
  </section>;
}
