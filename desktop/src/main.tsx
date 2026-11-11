import { createRoot } from "react-dom/client";
import {
  ArrowDownToLine, CircleHelp, Grid2X2, Heart, MessageCircle, Minus, Power, RefreshCw,
  Settings2, Smile, Square, UserRound, X, Newspaper, CalendarClock,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { defaultAppearance, errorMessage, request, runLabels, stepZoom, type Appearance, type Info, type PageKey, type Status, type WindowAction } from "./api";
import { Button, Empty, IconButton, NoticeToast, StatusPill, useDraftGuard } from "./components";
import { PersonaPage } from "./persona";
import { SessionsPage } from "./sessions";
import { Workbench } from "./workbench";
import { StickersPage } from "./stickers";
import { SettingsPage } from "./settings";
import { CalibrationWindow } from "./calibration";
import { BackupWindow, CompanionPage } from "./companion";
import { NewsPage } from "./news";
import { TasksPage } from "./tasks";
import type { TaskDestination } from "./tasks-api";
import { enterRoute, reducesMotion } from "./motion";
import logo from "../../xiaomao/ui_assets/app.png";
import "./styles.css";
import "./fixed-actions.css";

const navigation = [
  { key: "workbench", title: "工作台", icon: Grid2X2 },
  { key: "sessions", title: "会话", icon: MessageCircle },
  { key: "persona", title: "人格", icon: UserRound },
  { key: "companion", title: "记忆与陪伴", icon: Heart },
  { key: "news", title: "每日资讯", icon: Newspaper },
  { key: "tasks", title: "任务中心", icon: CalendarClock },
  { key: "stickers", title: "表情包", icon: Smile },
  { key: "settings", title: "运行设置", icon: Settings2 },
] as const;

type Notify = (message: string, error?: boolean) => void;

function App() {
  const [page, setPage] = useState<PageKey>("workbench");
  const [directory, setDirectory] = useState(false);
  const [editTarget, setEditTarget] = useState<TaskDestination>();
  const [visited, setVisited] = useState<ReadonlySet<PageKey>>(() => new Set(["workbench"]));
  const [status, setStatus] = useState<Status | undefined>();
  const [updated, setUpdated] = useState("");
  const [maximized, setMaximized] = useState(false);
  const [appearance, setAppearance] = useState(defaultAppearance);
  const [savedAppearance, setSavedAppearance] = useState(defaultAppearance);
  const updateAppearance = (value: Appearance) => {
    setAppearance(value);
    if (value.revision !== savedAppearance.revision) setSavedAppearance(value);
  };
  useDraftGuard("appearance", status?.readOnly === false && (appearance.transparency !== savedAppearance.transparency
    || appearance.reduceMotion !== savedAppearance.reduceMotion || appearance.zoomPercent !== savedAppearance.zoomPercent));
  const noticeId = useRef(0);
  const [notice, setNotice] = useState<{ id: number; message: string; error: boolean; leaving: boolean }>();
  const notify: Notify = useCallback((message, error = false) =>
    setNotice({ id: ++noticeId.current, message, error, leaving: false }), []);
  const closeNotice = () => setNotice(current => current && !current.leaving ? { ...current, leaving: true } : current);
  const scrollHost = useRef<HTMLDivElement>(null);
  const currentPage = useRef<PageKey>("workbench");
  const requestedPage = useRef<PageKey>("workbench");
  const scrollPositions = useRef(new Map<PageKey, number>());
  const navigate = useCallback((next: PageKey) => {
    const current = currentPage.current;
    if (next === requestedPage.current) return;
    requestedPage.current = next;
    if (scrollHost.current) scrollPositions.current.set(current, scrollHost.current.scrollTop);
    setPage(next);
    setVisited(items => items.has(next) ? items : new Set([...items, next]));
  }, []);
  useEffect(() => {
    if (!editTarget || page !== editTarget.page) return;
    let second = 0;
    const first = requestAnimationFrame(() => { second = requestAnimationFrame(() => {
      window.dispatchEvent(new CustomEvent("shiguang:task-edit", { detail: editTarget }));
      setEditTarget(undefined);
    }); });
    return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); };
  }, [editTarget, page]);

  useLayoutEffect(() => {
    const host = scrollHost.current;
    if (!host || currentPage.current === page) return;
    const direction = navigation.findIndex(item => item.key === page) > navigation.findIndex(item => item.key === currentPage.current) ? 1 : -1;
    currentPage.current = page;
    host.scrollTop = scrollPositions.current.get(page) ?? 0;
    const route = host.querySelector<HTMLElement>(".route:not([hidden])");
    if (route) return enterRoute(route, direction, appearance.reduceMotion);
  }, [page, appearance.reduceMotion]);

  useEffect(() => {
    const update = (value: Status) => {
      setStatus(value);
      setUpdated(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    };
    const off = window.shiguang.watch(update);
    const windowOff = window.shiguang.windowState(data => setMaximized(data.maximized));
    window.shiguang.appInfo().then(info => {
      setMaximized(info.maximized);
      document.documentElement.dataset.wallpaper = info.hasWallpaper ? "local" : "default";
      if (info.hasWallpaper) document.documentElement.style.setProperty("--wallpaper", 'url("sg-media://wallpaper/selected")');
    }).catch(() => {});
    request<Status>("view.status").then(update).catch(error => notify(errorMessage(error), true));
    request<Appearance>("appearance.read").then(value => { setAppearance(value); setSavedAppearance(value); }).catch(error => notify(errorMessage(error), true));
    return () => { off(); windowOff(); };
  }, [notify]);

  useEffect(() => {
    const opacity = 1 - appearance.transparency / 100;
    document.documentElement.style.setProperty("--glass-alpha", String(opacity));
    document.documentElement.style.setProperty("--glass-top", String(Math.min(1, opacity * 1.13)));
    document.documentElement.style.setProperty("--glass-edge", String(.15 + opacity ** 1.3 * .15));
    document.documentElement.style.setProperty("--wallpaper-scrim", String(.58 + opacity * .22));
    document.documentElement.dataset.reduceMotion = String(appearance.reduceMotion);
  }, [appearance.transparency, appearance.reduceMotion]);

  useLayoutEffect(() => {
    document.documentElement.style.setProperty("--ui-zoom", String(appearance.zoomPercent / 100));
    void window.shiguang.zoom(appearance.zoomPercent).catch(error => notify(errorMessage(error), true));
  }, [appearance.zoomPercent, notify]);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.ctrlKey && !event.altKey && !event.metaKey && ["-", "+", "=", "0"].includes(event.key)) {
        event.preventDefault();
        setAppearance(value => {
          const zoomPercent = event.key === "0" ? defaultAppearance.zoomPercent : stepZoom(value.zoomPercent, event.key === "-" ? -1 : 1);
          return zoomPercent === value.zoomPercent ? value : { ...value, zoomPercent };
        });
        return;
      }
      const at = Number(event.key) - 1;
      if (event.altKey && at >= 0 && at < navigation.length) {
        event.preventDefault();
        navigate(navigation[at].key);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [navigate]);
  useEffect(() => {
    if (!notice || (notice.error && !notice.leaving)) return;
    const timer = window.setTimeout(() => setNotice(current => current !== notice
      ? current : notice.leaving ? undefined : { ...notice, leaving: true }),
    notice.leaving ? reducesMotion() ? 0 : 180 : 3500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const current = navigation.find(item => item.key === page)!;
  const calibrate = (session?: string) => window.shiguang.calibrate(session);
  function content(key: PageKey): ReactNode {
    if (key === "workbench") return <Workbench active={key === page} status={status} notify={notify} navigate={navigate}
      calibrate={calibrate} />;
    if (key === "stickers") return <StickersPage active={key === page} readOnly={status?.readOnly ?? true} notify={notify} />;
    if (key === "sessions") return <SessionsPage active={key === page} status={status} notify={notify}
      directory={directory} setDirectory={setDirectory} calibrate={calibrate} />;
    if (key === "persona") return <PersonaPage active={key === page} readOnly={status?.readOnly ?? true} accountRevision={status?.revision ?? ""} notify={notify} />;
    if (key === "companion") return <CompanionPage active={key === page} status={status} notify={notify} />;
    if (key === "news") return <NewsPage active={key === page} status={status} notify={notify} manageSessions={() => navigate("sessions")} />;
    if (key === "tasks") return <TasksPage active={key === page} status={status} notify={notify} navigate={navigate}
      edit={target => { setEditTarget(target); navigate(target.page); }} />;
    if (key === "settings") return <SettingsPage active={key === page} status={status} appearance={appearance}
      setAppearance={updateAppearance} notify={notify} navigate={navigate} />;
    return <div className="glass phase-note"><Empty icon={<CircleHelp size={28} />} title="正在迁移此页面"
      detail="这份开发预览用于验证工作台和表情包页。旧版保留完整入口。" /></div>;
  }
  const action = (value: WindowAction) => window.shiguang.windowAction(value).catch(error => notify(errorMessage(error), true));
  const refresh = async () => {
    try {
      setStatus(await request<Status>("view.status"));
      setUpdated(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch (error) { notify(errorMessage(error), true); }
  };
  return <div className={`shell ${maximized ? "maximized" : ""}`}>
    <aside className="rail">
      <div className="brand drag-region"><img src={logo} alt="" /><div><b>拾光</b><small>微信聊天助手</small></div></div>
      <nav aria-label="主要功能">{navigation.map(({ key, title, icon: Icon }) =>
        <button key={key} type="button" className={`nav-item ${page === key ? "selected" : ""}`}
          aria-current={page === key ? "page" : undefined} aria-label={title} title={title} onClick={() => navigate(key)}>
          <Icon size={29} strokeWidth={1.75} /><span>{title}</span>
        </button>)}</nav>
      <div className="rail-footer"><span>{status?.readOnly ? "只读预览" : "拾光桌面版"}</span><small>Alt + 1–8 切换页面</small></div>
    </aside>
    <main className="main-pane" data-page={page} data-subpage={page === "sessions" && directory ? "directory" : undefined}>
      <header className="chrome drag-region">
        <div className="heading"><h1>{page === "sessions" && directory ? "选择会话" : current.title}</h1></div>
        <div className="chrome-actions no-drag">
          <StatusPill mode={status?.mode ?? "starting"} />
          <div className="window-buttons">
            <IconButton label="最小化" icon={<Minus size={17} />} onClick={() => void action("minimize")} />
            <IconButton label={maximized ? "还原" : "最大化"} icon={<Square size={13} />} onClick={() => void action("maximize")} />
            <IconButton label="关闭助手" className="window-close" icon={<X size={16} />} onClick={() => void action("close")} />
          </div>
        </div>
      </header>
      <div ref={scrollHost} className="routes">{navigation.filter(item => visited.has(item.key)).map(item =>
        <section className="route" key={item.key} hidden={item.key !== page} aria-label={item.title}>
          {content(item.key)}
        </section>)}</div>
      <footer className="footer"><span><i className={status?.connected ? "live" : ""} />
        {status?.mode === "auto" ? "助手运行中" : status ? runLabels[status.mode] : "连接中"}
        <span aria-hidden="true">·</span>监听 {status?.sessions.filter(session => session.mode === "auto").length ?? 0} / {status?.sessions.length ?? 0}
        {status?.readOnly && " · 只读预览"}</span><span className="footer-refresh">
        {updated && `最后刷新  ${updated}`}{page === "workbench" && <IconButton label="刷新运行状态" icon={<RefreshCw size={20} />}
          onClick={() => void refresh()} />}</span></footer>
    </main>
    {notice && <NoticeToast key={notice.id} message={notice.message} error={notice.error}
      leaving={notice.leaving} close={closeNotice} />}
  </div>;
}

function CloseWindow() {
  const [info, setInfo] = useState<Info>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<WindowAction>();
  const [leaving, setLeaving] = useState(false);
  const [discard, setDiscard] = useState(false);
  useEffect(() => {
    window.shiguang.appInfo().then(value => {
      setInfo(value);
      document.documentElement.dataset.reduceMotion = String(value.reduceMotion);
    }).catch(e => setError(errorMessage(e)));
  }, []);
  const close = useCallback((action: WindowAction) => {
    if (busy) return;
    setBusy(action);
    setError("");
    if (action !== "exit" && action !== "discardExit") setLeaving(true);
    window.setTimeout(() => {
      window.shiguang.windowAction(action).catch(e => {
        setBusy(undefined);
        setLeaving(false);
        setError(errorMessage(e));
      });
    }, action === "exit" || action === "discardExit" || info?.reduceMotion || matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 95);
  }, [busy, info?.reduceMotion]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") close("cancel"); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [close]);
  return <div className={`close-window ${leaving ? "leaving" : ""}`}>
    <header className="close-chrome drag-region"><Power size={19} /><strong>关闭助手</strong>
      <IconButton className="no-drag" label="取消关闭" icon={<X size={16} />} disabled={!!busy} onClick={() => close("cancel")} />
    </header>
    <div className="close-body">
      <p>{discard ? "退出会放弃当前草稿" : info?.dirty ? "请先保存修改，或选择完全退出" : "请选择关闭方式"}</p>
      <Button variant="primary" className="close-option" icon={<ArrowDownToLine size={18} />}
        disabled={!info || (!info.tray && !info.readOnly) || !!busy || !!info.dirty || discard}
        onClick={() => close(info?.readOnly ? "dismissPreview" : "background")}>
        <span><strong>{info?.readOnly ? "关闭预览" : "收起到托盘"}</strong><small>后台继续运行</small></span>
      </Button>
      <Button className="close-option" icon={<Power size={17} />} busy={busy === "exit" || busy === "discardExit"}
        disabled={!info || info.readOnly || !!busy} onClick={() => info?.dirty && !discard ? setDiscard(true) : close(discard ? "discardExit" : "exit")}>
        <span><strong>{discard ? "放弃修改并退出" : "完全退出"}</strong><small>结束监听、回复和定时任务</small></span>
      </Button>
      {error && <span className="inline-error" role="alert">{error}</span>}
      <Button className="close-cancel" onClick={() => close("cancel")} disabled={!!busy}>取消</Button>
    </div>
  </div>;
}

if (new URLSearchParams(location.search).get("motion") === "reduced") document.documentElement.dataset.reduceMotion = "true";

createRoot(document.getElementById("root")!).render(location.hash === "#close" ? <CloseWindow />
  : location.hash === "#calibration" ? <CalibrationWindow />
    : location.hash === "#backup" ? <BackupWindow /> : <App />);
