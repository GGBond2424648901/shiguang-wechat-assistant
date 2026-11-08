import { ArrowLeft, Check, Crosshair, ExternalLink, RefreshCcw, Save, Undo2, X } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { errorMessage } from "./api";
import { Button, Empty, IconButton, Select, Switch } from "./components";

const regionKeys = ["header", "messages", "input", "send"] as const;
type Region = typeof regionKeys[number];
type Rectangle = [number, number, number, number];
type Regions = Record<Region, Rectangle>;
const labels = { header: "会话标题", messages: "消息区域", input: "输入区域", send: "发送按钮" };
type WindowList = { windows: { id: string; title: string; kind: "main" | "detached"; minimized: boolean }[];
  suggested: string; target: string };
type Preview = { token: string; target: string; kind: string; width: number; height: number; regions: Regions;
  image: string; observed: string; note: string; identified: boolean };
type Result = { target: string; applied: boolean; paused?: boolean; note: string };
const rpc = <T,>(method: string, params: Record<string, unknown> = {}) =>
  window.shiguang.calibration(method, params) as Promise<T>;
const clamp = (number: number) => Math.min(1, Math.max(0, number));

export function CalibrationWindow() {
  const [list, setList] = useState<WindowList>();
  const [windowId, setWindow] = useState("");
  const [picture, setPicture] = useState<Preview>();
  const [regions, setRegions] = useState<Regions>();
  const [active, setActive] = useState<Region>("header");
  const [undo, setUndo] = useState<Regions[]>([]);
  const [drag, setDrag] = useState<Rectangle>();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [saved, setSaved] = useState<Result>();
  const surface = useRef<HTMLDivElement>(null);
  const start = useRef<{ x: number; y: number }>(null);
  const running = useRef(false);

  function update(preview: Preview) {
    setPicture(preview);
    setRegions(preview.regions);
    setNote(preview.note);
    setConfirmed(false);
  }

  async function run(label: string, operation: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(label);
    setProblem("");
    try { await operation(); } catch (error) { setProblem(errorMessage(error)); }
    finally { setBusy(""); running.current = false; }
  }

  const discover = () => run("discover", async () => {
    const result = await rpc<WindowList>("discover");
    setList(result);
    setWindow(result.suggested || (result.windows.length === 1 ? result.windows[0].id : ""));
  });

  const close = () => void rpc("close");

  useEffect(() => {
    void discover();
    const key = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.defaultPrevented) close(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  const capture = () => run("capture", async () => {
    setUndo([]);
    update(await rpc<Preview>("capture", { window: windowId }));
  });
  const reselect = () => run("back", async () => {
    await rpc("cancel", { token: picture?.token });
    setPicture(undefined);
    setRegions(undefined);
    setConfirmed(false);
    const result = await rpc<WindowList>("discover");
    setList(result);
    setWindow(result.suggested);
  });
  const identify = (chooseObserved: boolean) => picture && regions && run("identify", async () => {
    setConfirmed(false);
    update(await rpc<Preview>("identify", { token: picture.token, regions, chooseObserved }));
  });
  const save = () => picture?.identified && regions && run("save", async () => {
    setSaved(await rpc<Result>("save", { token: picture.token, regions, confirmed }));
  });

  function locate(event: PointerEvent<HTMLDivElement>) {
    const box = surface.current!.getBoundingClientRect();
    return { x: clamp((event.clientX - box.left) / box.width), y: clamp((event.clientY - box.top) / box.height) };
  }

  function points(event: PointerEvent<HTMLDivElement>): Rectangle | undefined {
    if (!start.current) return;
    const end = locate(event);
    const { x, y } = start.current;
    return [Math.min(x, end.x), Math.min(y, end.y), Math.max(x, end.x), Math.max(y, end.y)];
  }

  function finish(event: PointerEvent<HTMLDivElement>) {
    const chosen = points(event);
    start.current = null;
    setDrag(undefined);
    if (chosen && picture && regions && (chosen[2] - chosen[0]) * picture.width >= 5 &&
        (chosen[3] - chosen[1]) * picture.height >= 5) {
      setUndo(previous => [...previous.slice(-19), regions]);
      setRegions({ ...regions, [active]: chosen });
      setConfirmed(false);
    }
  }

  return <div className="calibration-shell">
    <header className="close-chrome drag-region"><Crosshair size={19} /><b>校准微信窗口</b>
      <span className="calibration-target">{picture?.target || list?.target || "自动识别会话"}</span>
      <IconButton label="关闭校准" icon={<X size={16} />} onClick={close} disabled={busy === "save"} />
    </header>
    <main className="calibration-body">
      {saved ? <div className="calibration-result">
        <Empty icon={<Check size={30} />} title={`“${saved.target}”的校准已保存`}
          detail={saved.note || (saved.paused ? "可在工作台开启这个会话的回复。" : "后台已应用此次校准。")} />
      </div> : <>
        <p className="muted">识别所选窗口，并核对消息、输入与发送区域</p>
        {!picture ? <>
          <div className="calibration-controls">
            <Select label="目标窗口" value={windowId}
              options={(list?.windows ?? []).map(row => ({ value: row.id,
                label: `${row.kind === "main" ? "主窗口" : "独立聊天"} · ${row.title}${row.minimized ? "（已最小化）" : ""}` }))}
              onChange={setWindow} disabled={!!busy} />
            <Button icon={<RefreshCcw size={16} />} busy={busy === "discover"} disabled={!!busy} onClick={() => void discover()}>刷新</Button>
            <Button icon={<ExternalLink size={15} />} busy={busy === "open"} disabled={!!busy}
              onClick={() => void run("open", async () => {
                await rpc("openWechat");
                const next = await rpc<WindowList>("discover"); setList(next); setWindow(next.suggested);
              })}>打开微信</Button>
          </div>
          <div className="calibration-placeholder">
            <Empty icon={<Crosshair size={28} />} title={busy === "open" ? "正在打开微信" : busy ? "正在核对微信窗口" :
              !list?.windows.length ? "等待微信窗口" : "选择目标窗口"}
              detail={list?.windows.length ? "如需分别校准，先在微信把聊天拖成独立窗口。" :
                "若微信在托盘中，请点托盘图标显示微信。登录并进入目标会话后，刷新窗口列表。"} />
          </div>
        </> : regions && <>
          <div className="calibration-controls flex">
            <Button icon={<ArrowLeft size={15} />} disabled={!!busy} onClick={() => void reselect()}>重新选窗口</Button>
            <Button icon={<RefreshCcw size={15} />} busy={busy === "identify"} disabled={!!busy}
              onClick={() => void identify(false)}>重新识别</Button>
            {!picture.identified && !picture.observed && <Button icon={<ExternalLink size={15} />} busy={busy === "open"}
              disabled={!!busy} onClick={() => void run("open", async () => { await rpc("openWechat"); })}>打开微信</Button>}
            {picture.observed && picture.observed !== picture.target && <Button disabled={!!busy}
              onClick={() => void identify(true)}>使用窗口中的会话</Button>}
            <Button icon={<Undo2 size={15} />} disabled={!undo.length || !!busy} onClick={() => {
              setRegions(undo.at(-1)); setUndo(undo.slice(0, -1)); setConfirmed(false);
            }}>撤销调整</Button>
          </div>
          <div className="calibration-preview">
            <div ref={surface} className="calibration-surface" style={{
              aspectRatio: `${picture.width} / ${picture.height}`,
              width: `min(100%, ${Math.round(365 * picture.width / picture.height)}px)`,
            }} aria-label="微信截图校准区域" onPointerDown={event => {
              if (busy || event.button !== 0) return;
              start.current = locate(event); event.currentTarget.setPointerCapture(event.pointerId);
            }} onPointerMove={event => { if (start.current) setDrag(points(event)); }}
              onPointerUp={finish} onPointerCancel={() => { start.current = null; setDrag(undefined); }}>
              <img alt="当前微信窗口校准截图" src={picture.image} draggable={false} />
              {regionKeys.map((key, index) => {
                const [x1, y1, x2, y2] = key === active && drag ? drag : regions[key];
                return <div key={key} className={`region region-${key} ${active === key ? "active" : ""}`}
                  style={{ left: `${100 * x1}%`, top: `${100 * y1}%`,
                    width: `${100 * (x2 - x1)}%`, height: `${100 * (y2 - y1)}%` }}><span>{index + 1}</span></div>;
              })}
            </div>
          </div>
          <div className="calibration-regions" role="group" aria-label="选择要调整的区域">
            {regionKeys.map((key, index) => <button key={key} type="button" aria-pressed={active === key} onClick={() => setActive(key)}
              className={`region-choice region-${key}`} disabled={!!busy}>
              <span>{index + 1}</span><b>{labels[key]}</b>
              <small>{Math.round(regions[key][0] * picture.width)}, {Math.round(regions[key][1] * picture.height)} ·
                {" "}{Math.round((regions[key][2] - regions[key][0]) * picture.width)} × {Math.round((regions[key][3] - regions[key][1]) * picture.height)}</small>
            </button>)}
          </div>
          <div className="calibration-numeric">
            {(["左", "上", "右", "下"] as const).map((label, i) => {
              const limit = i % 2 ? picture.height : picture.width;
              return <label className="field" key={label}>{label}<input type="number" min={0} max={limit}
                disabled={!!busy} value={Math.round(regions[active][i] * limit)} onChange={event => {
                  const value = Number(event.target.value);
                  const box = [...regions[active]] as Rectangle;
                  box[i] = clamp(value / limit);
                  setUndo(previous => [...previous.slice(-19), regions]);
                  setRegions({ ...regions, [active]: box });
                  setConfirmed(false);
                }} /></label>;
            })}
          </div>
          {picture.observed && <p className="fine-print muted">窗口中识别到 {picture.observed}</p>}
          <p className="fine-print muted">{note}</p>
        </>}
        {problem && <p className="inline-error" role="alert">{problem}</p>}
      </>}
    </main>
    <footer className={`calibration-actions${picture && !saved ? " split" : ""}`}>
      {saved ? <Button variant="primary" onClick={close}>完成</Button> : !picture ? <>
        <Button onClick={close}>取消</Button>
        <Button variant="primary" icon={<Crosshair size={16} />} disabled={!windowId || !!busy}
          busy={busy === "capture"} onClick={() => void capture()}>唤起并校准</Button>
      </> : <>
        <Switch checked={confirmed} onChange={setConfirmed} disabled={!!busy || !picture.identified}>
          已核对会话、聊天底部和空白输入框
        </Switch>
        <Button variant="primary" icon={<Save size={16} />} busy={busy === "save"}
          disabled={!picture.identified || !confirmed || !!busy} onClick={() => void save()}>保存校准</Button>
      </>}
    </footer>
  </div>;
}
