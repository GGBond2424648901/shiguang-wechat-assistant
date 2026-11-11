import { CheckCircle2, CircleAlert, CircleHelp, Globe, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, request, type PageKey, type Status } from "./api";
import { Button, Empty, PanelTitle } from "./components";
import type { DiagnosticReport } from "./tasks-api";
import "./operations.css";

const stateLabels = { ok: "通过", warn: "需留意", error: "需处理", unknown: "尚未确认" };
export function DiagnosticsPage({ active, status, tabs, navigate, notify }: {
  active: boolean; status?: Status; tabs: ReactNode; navigate: (page: PageKey) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [result, setResult] = useState<{ scope: string; report: DiagnosticReport }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const serial = useRef(0);
  const mounted = useRef(true);
  const activeRef = useRef(active); activeRef.current = active;
  const scope = `${status?.accountScope ?? status?.accountLabel ?? ""}:${status?.revision ?? ""}`;
  const latestScope = useRef(scope); latestScope.current = scope;
  const run = useCallback(async (network: boolean) => {
    if (network && status?.readOnly !== false) return;
    const token = ++serial.current;
    setBusy(true); setError("");
    try {
      const report = await request<DiagnosticReport>("diagnostics.run", { network });
      if (mounted.current && activeRef.current && serial.current === token && latestScope.current === scope) setResult({ scope, report });
    } catch (e) {
      if (mounted.current && activeRef.current && serial.current === token && latestScope.current === scope) setError(errorMessage(e));
    } finally { if (mounted.current && serial.current === token) setBusy(false); }
  }, [scope, status?.readOnly]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; ++serial.current; }; }, []);
  useEffect(() => { if (active) void run(false); else { ++serial.current; setBusy(false); } }, [active, run]);
  const report = result?.scope === scope ? result.report : undefined;
  const exportResult = async () => {
    if (!report) return;
    try {
      const text = ['拾光运行诊断', new Date(report.at * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }),
        ...report.items.map(item => `${item.label}：${stateLabels[item.state]}\n${item.detail}`)].join("\n\n");
      await navigator.clipboard.writeText(text); notify("诊断结果已复制，请核对后再分享");
    } catch { notify("暂时无法复制，请直接查看诊断结果", true); }
  };
  return <section className="glass panel diagnostics-page">
    <PanelTitle icon={<ShieldCheck size={24} />} end={tabs}>运行诊断</PanelTitle>
    <div className="diagnostics-toolbar"><div className="flex">
      <Button icon={<RefreshCw size={17} />} busy={busy} onClick={() => void run(false)}>检查本地运行条件</Button>
      <Button variant="primary" icon={<Globe size={17} />} disabled={status?.readOnly !== false || busy} onClick={() => void run(true)}>检查模型与联网</Button>
    </div><Button variant="ghost" disabled={!report || busy} onClick={() => void exportResult()}>复制诊断结果</Button></div>
    <p className="diagnostics-note">检查结果用于定位问题。联网检查会实际访问已配置服务，诊断过程保留当前运行模式。</p>
    {error && <div className="inline-error" role="status">{error}</div>}
    <div className="diagnostics-results" aria-busy={busy}>
      {report ? report.items.map(item => <article key={item.code} className={`diagnostics-item ${item.state}`}>
        {item.state === "ok" ? <CheckCircle2 size={24} /> : item.state === "unknown" ? <CircleHelp size={24} /> : <CircleAlert size={24} />}
        <div><h3>{item.label}<span className="tag">{stateLabels[item.state]}</span></h3><p>{item.detail}</p></div>
        {item.action && <Button disabled={busy} onClick={() => navigate(item.action!)}>前往{item.action === "sessions" ? "会话" : "设置"}</Button>}
      </article>) : <Empty title={busy ? "正在检查运行条件" : "点击检查开始诊断"} />}
    </div>
    {report && <p className="diagnostics-note">检查时间：{new Date(report.at * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（北京时间）</p>}
  </section>;
}
