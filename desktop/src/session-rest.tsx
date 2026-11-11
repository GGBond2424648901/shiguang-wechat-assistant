import { Clock3, Coffee, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { errorMessage, request, type Session } from "./api";
import { Button, IconButton } from "./components";
import { reducesMotion } from "./motion";
import "./operations.css";

export function SessionRestControl({ session, disabled, notify, compact = false, active = true }: {
  session: Session; disabled: boolean; notify: (text: string, error?: boolean) => void; compact?: boolean; active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [minutes, setMinutes] = useState("10");
  const [custom, setCustom] = useState("10");
  const [busy, setBusy] = useState(false);
  const [rest, setRest] = useState(session.rest);
  const [now, setNow] = useState(Date.now());
  const sequence = useRef(0);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dialog = useRef<HTMLDialogElement>(null);
  const identity = useRef(session.id); identity.current = session.id;
  useEffect(() => { setRest(session.rest); }, [session.rest]);
  useEffect(() => { ++sequence.current; setOpen(false); setBusy(false); setRest(session.rest); return () => { ++sequence.current; }; }, [session.id]);
  useEffect(() => { if (!active) { ++sequence.current; setOpen(false); setBusy(false); } }, [active]);
  useEffect(() => { if (open) { dialog.current?.showModal(); setLeaving(false); } return () => { dialog.current?.close(); clearTimeout(closeTimer.current); }; }, [open]);
  useEffect(() => {
    if (!rest?.active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);
  }, [rest?.active]);
  const remaining = rest?.active && rest.until ? Math.max(0, Math.ceil(rest.until - now / 1000)) : 0;
  const countdown = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  const close = () => { if (leaving) return; setLeaving(true); closeTimer.current = setTimeout(() => setOpen(false), reducesMotion() ? 0 : 165); };
  const apply = async (cancel = false) => {
    if (busy || disabled) return;
    const value = Number(minutes === "custom" ? custom : minutes);
    if (!cancel && (!Number.isInteger(value) || value < 1 || value > 1440)) { notify("休息时长应为 1–1440 分钟", true); return; }
    const token = ++sequence.current, id = session.id;
    setBusy(true);
    try {
      const response = await request<{ rest: Session["rest"] }>(cancel ? "sessions.cancelRest" : "sessions.rest", { session: id, ...(cancel ? {} : { minutes: value }) });
      if (sequence.current !== token || identity.current !== id) return;
      setRest(response.rest); close(); setNow(Date.now());
      notify(cancel ? "临时休息已结束，已核对原运行状态" : `已安排休息 ${value} 分钟`);
    } catch (e) { if (sequence.current === token) notify(errorMessage(e), true); }
    finally { if (sequence.current === token) setBusy(false); }
  };
  return <div className={`session-rest ${compact ? "compact" : ""}`}>
    {compact ? <IconButton label={`临时休息 ${session.name}`} icon={<Coffee size={17} />} disabled={disabled || busy} onClick={() => setOpen(true)} />
      : <Button icon={<Coffee size={17} />} disabled={disabled || busy} onClick={() => setOpen(true)}>临时休息</Button>}
    {rest?.active && <span className="session-rest-countdown" title="到期前会再次核对账号、身份和手动操作"><Clock3 size={13} />{remaining ? countdown : "正在核对恢复"}</span>}
    {open && <dialog ref={dialog} className={`confirm-dialog session-rest-menu ${leaving ? "leaving" : ""}`} aria-label={`${session.name}临时休息设置`}
      onCancel={event => { event.preventDefault(); close(); }}>
      <div className="confirm-heading"><Coffee size={22} /><h2>临时休息</h2><IconButton label="关闭休息设置" icon={<X size={16} />} onClick={close} /></div>
      <b>{session.name}</b>
      {rest?.active ? <><p>休息剩余 {countdown}，到期核对后恢复原状态。</p><Button icon={<RotateCcw size={15} />} busy={busy} disabled={disabled} onClick={() => void apply(true)}>提前结束休息</Button></>
        : <><div className="flex rest-durations" role="group" aria-label="休息时长">{[["5", "5分钟"], ["10", "10分钟"], ["30", "30分钟"], ["60", "1小时"], ["custom", "自定义"]].map(([value, label]) => <Button key={value} variant={minutes === value ? "primary" : "ghost"} aria-pressed={minutes === value} onClick={() => setMinutes(value)}>{label}</Button>)}</div>
          {minutes === "custom" && <label className="field">分钟<input type="number" aria-label="自定义休息分钟" min="1" max="1440" value={custom} onChange={e => setCustom(e.target.value)} /></label>}
          <p>到期恢复本次休息前的模式。新的手动操作优先。</p><Button variant="primary" busy={busy} disabled={disabled} onClick={() => void apply()}>开始休息</Button></>}
      <Button variant="ghost" onClick={close}>收起</Button>
    </dialog>}
  </div>;
}
