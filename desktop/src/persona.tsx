import {
  ArrowDown, ArrowLeft, ArrowUp, BookOpen, Box, Check, Download, Eye, FilePlus, Pencil, Save, Search, Undo2, Upload,
} from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Button, Confirm, Empty, IconButton, PanelTitle, Select, Switch, useDraftGuard } from "./components";
import { errorMessage, request } from "./api";

interface Module {
  id: string; title: string; text: string; enabled: boolean; origin: string;
  category?: string; digest?: string;
}
interface Persona {
  format: "legacy" | "modules"; title: string; revision: string; modules: Module[];
  defaultText?: string; categories?: Record<string, string>;
}
interface ImportedPersona { modules?: Module[]; snapshot?: Persona; selected?: string; count: number }
interface EffectivePersona { text: string; details: { backend: string } }

const modelModule = ({ id, title, text, enabled, origin }: Module) => ({ id, title, text, enabled, origin });

export function PersonaPage({ active, readOnly, notify }: {
  active: boolean; readOnly: boolean; notify: (text: string, error?: boolean) => void;
}) {
  const [base, setBase] = useState<Persona>();
  const [draft, setDraft] = useState<Module[]>([]);
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [chapter, setChapter] = useState("");
  const [preview, setPreview] = useState<EffectivePersona>();
  const [busy, setBusy] = useState("");
  const [failed, setFailed] = useState("");
  const [discard, setDiscard] = useState(false);
  const [paste, setPaste] = useState<{ title: string; text: string; category: string }>();
  const positions = useRef(new Map<string, number>());
  const textarea = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const row = draft.find(item => item.id === selected);
  const index = draft.findIndex(item => item.id === selected);
  const modules = base?.format === "modules";
  const dirty = !!base && draft !== base.modules;
  useDraftGuard("persona", !readOnly && (dirty || !!paste?.text));
  const changed = row && row !== base?.modules.find(item => item.id === selected);
  const editText = useDeferredValue(row?.text ?? "");
  const chapters = useMemo(() => [...editText.matchAll(/^(?:#{1,6}[ \t]+([^\n\r]+)|【([^\n\r]+)】)[ \t]*\r?$/gm)]
    .map(m => ({ value: String(m.index), label: m[1] || m[2] })), [editText]);
  const shown = draft.filter(item => !query || item.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const categories = Object.entries(base?.categories ?? {}).map(([value, label]) => ({ value, label }));

  function accept(next: Persona, id = selected, keepDraft = false) {
    setBase(next);
    setDraft(previous => {
      if (!keepDraft || !base) return next.modules;
      const original = new Map(base.modules.map(item => [item.id, item]));
      let kept = false;
      const rows = next.modules.map(item => {
        const local = previous.find(candidate => candidate.id === item.id);
        const old = original.get(item.id);
        if (!local || !old || local === old || item.id === id) return item;
        kept = true;
        return { ...item, title: local.title !== old.title ? local.title : item.title,
          text: local.text !== old.text ? local.text : item.text,
          enabled: local.enabled !== old.enabled ? local.enabled : item.enabled,
          category: local.category !== old.category ? local.category : item.category };
      });
      return kept ? rows : next.modules;
    });
    setSelected(next.modules.some(item => item.id === id) ? id : next.modules[0]?.id ?? "");
    setChapter("");
  }

  useEffect(() => {
    if (!active || dirty || busy || paste) return;
    let alive = true;
    request<Persona>("persona.read").then(next => {
      if (alive && base?.revision !== next.revision) accept(next);
      if (alive) setFailed("");
    }).catch(error => { if (alive) setFailed(errorMessage(error)); });
    return () => { alive = false; };
  }, [active, dirty, busy, base?.revision, paste]);

  useEffect(() => {
    setChapter("");
    if (textarea.current) textarea.current.scrollTop = preview ? 0 : positions.current.get(selected) ?? 0;
  }, [selected, preview]);

  function edit(id: string, fields: Partial<Module>) {
    setDraft(previous => previous.map(item => item.id === id ? { ...item, ...fields } : item));
  }

  function setBody(text: string) {
    if (!row) return;
    const restored = /\r\n/.test(row.text) && !/(?<!\r)\n/.test(row.text) ? text.replace(/\n/g, "\r\n") : text;
    edit(row.id, { text: restored });
  }

  function order(step: number) {
    const to = index + step;
    if (index < 0 || to < 0 || to >= draft.length) return;
    const next = [...draft];
    [next[index], next[to]] = [next[to], next[index]];
    setDraft(next);
  }

  function jump(value: string) {
    setChapter(value);
    const editor = textarea.current;
    const ruler = mirror.current;
    if (!editor || !ruler) return;
    const offset = (row?.text.slice(0, Number(value)) ?? "").replace(/\r\n?/g, "\n").length;
    ruler.style.width = editor.clientWidth + "px";
    ruler.textContent = editor.value.slice(0, offset) + "\u200b";
    editor.focus({ preventScroll: true });
    editor.setSelectionRange(offset, offset);
    editor.scrollTop = Math.max(0, ruler.scrollHeight - 30);
    ruler.textContent = "";
  }

  async function perform(label: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    try { await operation(); } catch (error) { notify(errorMessage(error), true); }
    finally { setBusy(""); }
  }

  async function save() {
    if (!base || !row || readOnly || !dirty || (!modules && !changed)) return;
    await perform("save", async () => {
      const next = modules
        ? await request<Persona>("persona.save", { expected: base.revision, title: base.title, modules: draft.map(modelModule) })
        : await request<Persona>("persona.saveDocument", { expected: base.revision, id: row.id, title: row.title,
            text: row.text, enabled: row.enabled, category: row.category });
      accept(next, selected, !modules);
      notify("人格已保存，下一次生成回复时生效");
    });
  }

  useEffect(() => {
    if (!active) return;
    const shortcut = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!preview && !paste) void save();
      }
    };
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  });

  const importFiles = () => base && perform("import", async () => {
    const result = await window.shiguang.personaFile("import", { expected: base.revision, category: row?.category });
    if (!result) return;
    const incoming = result as ImportedPersona;
    if (incoming.modules?.length) {
      const combined = [...draft, ...incoming.modules];
      if (combined.length > 48 || combined.reduce((count, item) => count + item.text.length, 0) > 200000)
        throw new Error("加上当前草稿后超过 48 个模块或 200000 字，请先调整草稿");
      setDraft(combined);
      setSelected(incoming.modules.at(-1)!.id);
      setPreview(undefined);
      notify(`已载入 ${incoming.count} 个模块，保存后应用`);
    } else if (incoming.snapshot) {
      accept(incoming.snapshot, incoming.selected, true);
      notify(`已导入 ${incoming.count} 份补充稿，选择参与回复后保存`);
    }
  });

  const submitPaste = () => base && paste && perform("paste", async () => {
    const result = await request<Required<Pick<ImportedPersona, "snapshot" | "selected">>>("persona.importText",
      { expected: base.revision, ...paste });
    accept(result.snapshot, result.selected, true);
    setPaste(undefined);
    notify("补充稿已导入，可单独启用");
  });

  const exportFiles = () => base && perform("export", async () => {
    const result = await window.shiguang.personaFile("export", { title: base.title, modules: draft.map(modelModule) });
    if (result) notify("完整人格已导出，包含原文与启用状态");
  });

  if (failed && !base) return <div className="glass phase-note"><Empty title="无法加载人格" detail={failed} /></div>;
  if (!base || !row) return <div className="glass phase-note" aria-busy="true"><Empty title="正在加载人格" /></div>;
  const mutable = !readOnly && !busy && !preview;
  const enabledCount = draft.filter(item => item.enabled).length;
  const activeSupplement = base.modules.find(item => item.id !== "main" && item.enabled);
  return <div className="persona-page">
    <div className="glass persona-toolbar">
      <div className="flex">
        <Button icon={<Download size={16} />} busy={busy === "import"} disabled={!mutable} onClick={() => void importFiles()}>
          {modules ? "导入模块" : "导入文档"}
        </Button>
        {modules ? <Button icon={<Upload size={16} />} busy={busy === "export"} disabled={!!busy || readOnly} onClick={() => void exportFiles()}>导出整套</Button>
          : <Button icon={<FilePlus size={16} />} disabled={!mutable} onClick={() => setPaste({ title: "", text: "", category: "custom" })}>粘贴新文档</Button>}
        <Button icon={preview ? <ArrowLeft size={16} /> : <Eye size={16} />} busy={busy === "preview"} disabled={!!busy}
          onClick={() => preview ? setPreview(undefined) : void perform("preview", async () => setPreview(await request("persona.effective")))}>
          {preview ? "返回编辑" : "预览生效稿"}
        </Button>
      </div>
      <div className="flex">
        {dirty && <Button icon={<Undo2 size={15} />} disabled={!!busy} onClick={() => setDiscard(true)}>撤销修改</Button>}
        <Button variant="primary" icon={<Save size={16} />} busy={busy === "save"} disabled={!mutable || !dirty || (!modules && !changed)}
          onClick={() => void save()}>保存并应用</Button>
      </div>
    </div>
    <div className="persona-layout">
      <section className="glass panel persona-list">
        <PanelTitle icon={<Box size={18} />} end={<span className="muted fine-print">{enabledCount} / {draft.length}</span>}>
          {modules ? "人格模块" : "人格文档"}
        </PanelTitle>
        <label className="persona-search"><Search size={15} /><input aria-label="搜索模块" placeholder="搜索名称" value={query}
          onChange={event => setQuery(event.target.value)} /></label>
        <div className="persona-module-list" aria-label="人格模块列表">
          {shown.map(item => <div key={item.id} className={item.id === selected ? "persona-item active" : "persona-item"}>
            <button type="button" aria-current={item.id === selected ? "true" : undefined} onClick={() => {
              setSelected(item.id); setPreview(undefined);
            }}>
              <span className="persona-index">{draft.indexOf(item) + 1}</span>
              <span><b>{item.title}</b><small>{item.origin}</small></span>
            </button>
            {modules && <Switch checked={item.enabled} disabled={!mutable}
              onChange={enabled => edit(item.id, { enabled })}>{item.title}启用</Switch>}
          </div>)}
        </div>
        {modules && <div className="persona-list-footer flex">
          <IconButton label="模块上移" icon={<ArrowUp size={16} />} disabled={!mutable || index <= 0} onClick={() => order(-1)} />
          <IconButton label="模块下移" icon={<ArrowDown size={16} />} disabled={!mutable || index >= draft.length - 1} onClick={() => order(1)} />
          <span className="muted fine-print">按列表顺序生效</span>
        </div>}
      </section>
      <section className="glass panel persona-content">
        {preview ? <PanelTitle icon={<BookOpen size={18} />} end={<span className="fine-print muted">
          {preview.details.backend === "deepseek-harness" ? "DeepSeek Harness" : "兼容编译"}
        </span>}>当前生效稿</PanelTitle>
          : <div className="persona-meta">
            <label className="field">人格名称<input value={row.title} aria-label="人格名称" maxLength={100}
              disabled={!mutable || row.id === "main"} onChange={event => edit(row.id, { title: event.target.value })} /></label>
            {row.id !== "main" && <Switch checked={row.enabled} disabled={!mutable}
              onChange={enabled => edit(row.id, { enabled })}>参与回复</Switch>}
            <div className="persona-chapters"><Select label="章节" value={chapter}
              options={[{ value: "", label: "完整人设" }, ...chapters]} onChange={jump} /></div>
          </div>}
        {!modules && !preview && <div className="flex persona-legacy">
          <span className="muted fine-print">{activeSupplement ? `生效中 · 基础人格 + ${activeSupplement.title}` : "生效中 · 仅基础人格"}</span>
          {row.id !== "main" && <Select label="文档分类" value={row.category ?? "custom"} options={categories}
            disabled={!mutable} onChange={category => edit(row.id, { category })} />}
          {row.id === "main" && <Button disabled={!mutable} onClick={() => edit(row.id, { text: base.defaultText ?? row.text })}>载入默认稿</Button>}
        </div>}
        <label className="persona-editor field">{preview ? "编译原文" : "人格提示词"}
          <textarea ref={textarea} spellCheck={false} value={preview?.text ?? row.text} readOnly={!mutable}
            onScroll={event => { if (!preview) positions.current.set(selected, event.currentTarget.scrollTop); }}
            onChange={event => setBody(event.target.value)} />
        </label>
        <div className="persona-mirror" ref={mirror} aria-hidden="true" />
        <div className="persona-detail"><span className={dirty ? "draft-note" : ""}>
          {dirty ? <Pencil size={15} /> : <Check size={15} />}{dirty ? "有未保存的修改" : "当前版本已生效"}
        </span><span>{(preview?.text ?? row.text).length.toLocaleString()} 字 · {chapters.length} 个章节</span></div>
      </section>
    </div>
    {discard && <Confirm title="撤销未保存的修改" detail="重新载入当前生效的人格版本。" cancel={() => setDiscard(false)}
      confirm={() => { setDiscard(false); void perform("discard", async () => accept(await request("persona.read"))); }} />}
    {paste && <section className="glass panel persona-paste">
      <PanelTitle icon={<FilePlus size={18} />}>粘贴补充稿</PanelTitle>
      <label className="field">文档名称<input value={paste.title} onChange={event => setPaste({ ...paste, title: event.target.value })} /></label>
      <Select label="文档分类" options={categories} value={paste.category} onChange={category => setPaste({ ...paste, category })} />
      <label className="field">文档原文<textarea rows={7} value={paste.text}
        onChange={event => setPaste({ ...paste, text: event.target.value })} /></label>
      <div className="flex"><Button disabled={!!busy} onClick={() => setPaste(undefined)}>返回编辑</Button>
        <Button variant="primary" busy={busy === "paste"} disabled={!paste.title.trim() || !paste.text.trim()}
          onClick={() => void submitPaste()}>导入补充稿</Button></div>
    </section>}
  </div>;
}
