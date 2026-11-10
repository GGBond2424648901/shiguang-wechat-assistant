import { ChevronDown, Check, CircleAlert, CircleHelp, LoaderCircle, Trash2, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { runLabels, type RunMode } from "./api";
import { reducesMotion } from "./motion";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
  busy?: boolean;
  icon?: ReactNode;
};
export function Button({ variant, busy, icon, className = "", children, ...props }: ButtonProps) {
  return <button type="button" {...props} disabled={props.disabled || busy}
    className={`button ${variant || "default"} ${className}`} aria-busy={busy || undefined}>
    {busy ? <LoaderCircle className="spinner" size={16} /> : icon}{children}
  </button>;
}

export function IconButton({ label, icon, ...props }: ButtonProps & { label: string }) {
  return <Button aria-label={label} title={label} icon={icon} {...props}
    className={`icon-button ${props.className ?? ""}`} />;
}

export function StatusPill({ mode, compact = false }: { mode: RunMode; compact?: boolean }) {
  return <span className={`status-pill ${mode} ${compact ? "compact" : ""}`}>
    <span aria-hidden="true" />{runLabels[mode] ?? mode}
  </span>;
}

export function PanelTitle({ icon, children, end, description }: {
  icon?: ReactNode; children: ReactNode; end?: ReactNode; description?: string;
}) {
  return <header className="panel-title">
    {description ? <div className="panel-heading"><h2>{icon}{children}</h2><p className="muted">{description}</p></div>
      : <h2>{icon}{children}</h2>}
    {end && <div className="panel-end">{end}</div>}
  </header>;
}

export function Empty({ title, detail, icon }: { title: string; detail?: string; icon?: ReactNode }) {
  return <div className="empty">{icon}<strong>{title}</strong>{detail && <span>{detail}</span>}</div>;
}

export type Option = { label: string; value: string };
export function Select({ label, options, value, onChange, disabled = false }: {
  label: string; options: readonly Option[]; value: string;
  onChange: (value: string) => void; disabled?: boolean;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const [placement, setPlacement] = useState<CSSProperties>();
  const selected = options.findIndex(item => item.value === value);
  useLayoutEffect(() => {
    if (!open) return;
    const anchor = trigger.current;
    if (!anchor) return;
    const place = () => {
      const { left, top, bottom, width } = anchor.getBoundingClientRect();
      const { clientWidth, clientHeight } = document.documentElement;
      const menuWidth = Math.min(Math.max(120, width), clientWidth - 12);
      const below = clientHeight - bottom - 12;
      const above = top - 12;
      const opensAbove = below < Math.min(216, options.length * 36 + 10) && above > below;
      setPlacement({ left: Math.max(6, Math.min(left, clientWidth - menuWidth - 6)), width: menuWidth,
        maxHeight: Math.min(216, opensAbove ? above : below),
        ...(opensAbove ? { bottom: clientHeight - top + 5 } : { top: bottom + 5 }) });
    };
    const scrolled = (event: Event) => {
      if (event.target instanceof Node && event.target.contains(anchor)) setOpen(false);
    };
    place();
    window.addEventListener("resize", place);
    document.addEventListener("scroll", scrolled, true);
    return () => {
      window.removeEventListener("resize", place);
      document.removeEventListener("scroll", scrolled, true);
    };
  }, [open, options.length]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !document.getElementById(id)?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open, id]);
  useEffect(() => {
    if (!open) return;
    const selected = document.getElementById(`${id}-${index}`);
    const list = selected?.parentElement;
    if (!selected || !list) return;
    if (selected.offsetTop < list.scrollTop) list.scrollTop = selected.offsetTop;
    const bottom = selected.offsetTop + selected.offsetHeight;
    if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }, [open, index, id]);
  const choose = (at: number) => {
    if (options[at]) onChange(options[at].value);
    setOpen(false);
    trigger.current?.focus();
  };
  return <div className="select" ref={menu} data-open={open || undefined}>
    <button ref={trigger} type="button" className="select-trigger" role="combobox"
      aria-label={label} aria-expanded={open} aria-controls={id}
      aria-activedescendant={open ? `${id}-${index}` : undefined} disabled={disabled || !options.length}
      onBlur={() => setOpen(false)}
      onClick={() => { setIndex(Math.max(0, selected)); setOpen(!open); }}
      onKeyDown={event => {
        if (event.key === "Escape" && open) { event.stopPropagation(); setOpen(false); return; }
        if (event.key === "Tab") { setOpen(false); return; }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          if (!open) { setIndex(Math.max(0, selected)); setOpen(true); return; }
          const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
            : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
          setIndex(next);
        }
        if (open && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          choose(index);
        }
      }}>
      <span>{options[selected]?.label ?? "请选择"}</span><ChevronDown size={16} />
    </button>
    {open && placement && createPortal(<div className="select-menu" id={id} role="listbox" aria-label={label} style={placement}>
      {options.map((option, at) => <div key={option.value} id={`${id}-${at}`} role="option"
        aria-selected={option.value === value} className={at === index ? "highlight" : ""}
        onPointerMove={() => setIndex(at)} onPointerDown={event => event.preventDefault()}
        onClick={() => choose(at)}>{option.label}{option.value === value && <Check size={15} />}</div>)}
    </div>, document.body)}
  </div>;
}

export function Switch({ checked, onChange, children, disabled = false }: {
  checked: boolean; onChange: (value: boolean) => void; children: ReactNode; disabled?: boolean;
}) {
  return <label className="switch-label">
    <input type="checkbox" role="switch" checked={checked} onChange={event => onChange(event.target.checked)}
      disabled={disabled} />
    <span className="switch-track" aria-hidden="true" /><span>{children}</span>
  </label>;
}

export function NoticeToast({ message, error, leaving, close }: {
  message: string; error: boolean; leaving: boolean; close: () => void;
}) {
  return <div className="toast-layer">
    <div className={`toast ${error ? "toast-error" : ""} ${leaving ? "leaving" : ""}`} role={error ? "alert" : "status"}>
      <span className="toast-icon" aria-hidden="true">{error ? <CircleAlert size={19} /> : <Check size={19} />}</span>
      <span className="toast-message">{message}</span>
      <IconButton variant="ghost" label="关闭提示" icon={<X size={16} />} onClick={close} />
    </div>
  </div>;
}

export function Confirm({ title, detail, confirm, cancel, destructive = false }: {
  title: string; detail: string; confirm: () => void; cancel: () => void; destructive?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const [leaving, setLeaving] = useState(false);
  const label = useId();
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => {
      window.clearTimeout(timer.current);
      node?.close();
    };
  }, []);
  const finish = (action: () => void) => {
    if (timer.current !== undefined) return;
    setLeaving(true);
    timer.current = window.setTimeout(action, reducesMotion() ? 0 : 165);
  };
  return <dialog ref={dialog} className={`confirm-dialog ${leaving ? "leaving" : ""}`} aria-labelledby={label}
    onCancel={event => { event.preventDefault(); finish(cancel); }}>
    <div className="confirm-heading">
      <span className={`confirm-icon ${destructive ? "destructive" : ""}`} aria-hidden="true">
        {destructive ? <Trash2 size={20} /> : <CircleHelp size={21} />}
      </span>
      <h2 id={label}>{title}</h2>
      <IconButton label="关闭确认" icon={<X size={16} />} onClick={() => finish(cancel)} /></div>
    <p>{detail}</p>
    <div className="confirm-actions"><Button autoFocus onClick={() => finish(cancel)}>取消</Button>
      <Button variant={destructive ? "danger" : "primary"} onClick={() => finish(confirm)}>{destructive ? "移除" : "确认"}</Button></div>
  </dialog>;
}

export function useDraftGuard(page: string, dirty: boolean) {
  useLayoutEffect(() => {
    void window.shiguang.draft(page, dirty).catch(() => {});
    return () => { void window.shiguang.draft(page, false).catch(() => {}); };
  }, [page, dirty]);
}
