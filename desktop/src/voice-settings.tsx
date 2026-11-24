import { Download, Mic, RefreshCw, Volume2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, request } from "./api";
import { Button, Empty, PanelTitle, Select } from "./components";
import "./voice-settings.css";

type Device = { id: string; name: string };
type Route = { outputId: string; inputId: string };
type VoiceJob = {
  id: string; kind: "install" | "probe"; state: "running" | "done" | "failed" | "cancelled";
  message: string; cancelable: boolean; downloaded: number; total: number; route: Route | null;
};
type DeviceState = {
  supported: boolean; output: Device[]; input: Device[]; suggested: Route | null; job: VoiceJob | null;
  configured: Route | null; configuredAvailable: boolean;
  wechat: { state: keyof typeof wechatStatus; version: string };
  song: boolean; testTarget: string;
};
const wechatStatus = {
  update: "请更新至 4.1.9 或更高版本并登录微信。",
  check_recording: "版本符合录音测试条件。",
  open_chat: "请打开已登录的微信主窗口。",
  unverified: "暂时无法核对正在运行的微信版本。",
};
const choose = (prior: string, fallback: string | undefined, endpoints: Device[]) =>
  endpoints.some(device => device.id === prior) ? prior : fallback ?? "";
const sameRoute = (left: Route | null | undefined, right: Route) =>
  left?.inputId === right.inputId && left?.outputId === right.outputId;

export function VoiceSettings({ active, readOnly, tabs }: { active: boolean; readOnly: boolean; tabs: ReactNode }) {
  const [devices, setDevices] = useState<DeviceState>();
  const [route, setRoute] = useState<Route>({ outputId: "", inputId: "" });
  const [job, setJob] = useState<VoiceJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [testStatus, setTestStatus] = useState("");
  const pendingTest = useRef("");
  const running = job?.state === "running" ? job.id : "";
  const load = useCallback(async () => {
    setError("");
    setBusy(true);
    try {
      const state = await request<DeviceState>("voice.devices");
      setDevices(state);
      setJob(state.job);
      const preferred = state.configuredAvailable ? state.configured : state.configured ? null : state.suggested;
      setRoute(previous => ({
        outputId: choose(previous.outputId, preferred?.outputId, state.output),
        inputId: choose(previous.inputId, preferred?.inputId, state.input),
      }));
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => { if (active) void load(); }, [active, load]);
  useEffect(() => {
    if (!active || !running) return;
    let current = true;
    let timer: number;
    const poll = async () => {
      try {
        const update = await request<VoiceJob | null>("voice.status");
        if (!current) return;
        setJob(update);
        if (update?.state === "running") timer = window.setTimeout(poll, 500);
        else if (update?.kind === "install") void load();
      } catch (cause) {
        if (!current) return;
        setError(errorMessage(cause));
        setJob(null);
      }
    };
    timer = window.setTimeout(poll, 300);
    return () => { current = false; window.clearTimeout(timer); };
  }, [active, running, load]);

  async function run(method: string, params: Record<string, unknown> = {}) {
    setError("");
    setBusy(true);
    try { setJob(await request<VoiceJob>(method, params)); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function saveRoute() {
    if (!job) return;
    setError("");
    setBusy(true);
    try {
      const saved = await request<{ route: Route }>("voice.saveRoute", { probeId: job.id, ...route });
      setDevices(previous => previous && { ...previous, configured: saved.route, configuredAvailable: true });
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function pickSong() {
    setError("");
    setBusy(true);
    try {
      const result = await window.shiguang.pickVoiceSong();
      if (result) setDevices(previous => previous && { ...previous, song: result.song });
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function sendTestVoice() {
    setBusy(true);
    setError("");
    setTestStatus("");
    pendingTest.current ||= crypto.randomUUID().replaceAll("-", "");
    try {
      const result = await request<{ queued: boolean; duplicate?: boolean }>("voice.sendTest", { requestId: pendingTest.current });
      if (result.queued || result.duplicate) {
        setTestStatus("测试已提交，请在工作台查看发送回执。");
        pendingTest.current = "";
      }
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  const disabled = busy || Boolean(running);
  const verified = sameRoute(job?.route, route);
  const saved = devices?.configuredAvailable && sameRoute(devices.configured, route);
  return <section className="glass panel voice-setup">
    <PanelTitle icon={<Mic size={20} />} end={tabs}
      description="测试拾光的音频能否到达准备给微信使用的录音输入。">语音设备</PanelTitle>
    <div className="form-stack voice-setup-form">
      {devices?.supported ? <>
        <div className="voice-wechat-status muted" role="status">
          {devices.wechat.version ? `微信 ${devices.wechat.version} · ` : ""}{wechatStatus[devices.wechat.state]}
        </div>
        {devices.testTarget && <div className="voice-driver-box">
          <p className="muted">固定语音测试会话 · {devices.testTarget}</p>
          <Button disabled={readOnly || disabled || !devices.configuredAvailable || devices.wechat.state !== "check_recording"}
            onClick={() => void sendTestVoice()}>发送测试语音</Button>
          {testStatus && <p className="muted" role="status">{testStatus}</p>}
        </div>}
        <div className="voice-device-fields">
          <div className="field"><span>拾光播放设备</span><Select label="拾光播放设备" value={route.outputId}
            options={devices.output.map(({ id, name }) => ({ value: id, label: name }))}
            disabled={disabled} onChange={outputId => setRoute(previous => ({ ...previous, outputId }))} /></div>
          <div className="field"><span>录音输入设备</span><Select label="录音输入设备" value={route.inputId}
            options={devices.input.map(({ id, name }) => ({ value: id, label: name }))}
            disabled={disabled} onChange={inputId => setRoute(previous => ({ ...previous, inputId }))} /></div>
        </div>
        <div className="flex voice-setup-actions">
          <Button variant="primary" icon={<Volume2 size={16} />} disabled={readOnly || disabled || !route.outputId || !route.inputId}
            onClick={() => void run("voice.probe", route)}>测试音频回路</Button>
          <Button disabled={readOnly || disabled || !verified || saved}
            onClick={() => void saveRoute()}>保存语音设备</Button>
          <Button icon={<RefreshCw size={16} />} disabled={disabled} onClick={() => void load()}>刷新设备</Button>
          {job?.cancelable && <Button disabled={busy} onClick={() => void run("voice.cancel", { id: job.id })}>停止当前操作</Button>}
        </div>
        {job && <div className={job.state === "failed" ? "inline-error" : "muted"} role="status">
          {job.route && !verified ? "已更换设备，请重新测试音频回路。" : job.message}
        </div>}
        {saved && <p className="muted voice-route-note" role="status">已保存这组设备。</p>}
        {saved && <div className="voice-driver-box">
          <p className="muted">让微信的麦克风使用这组设备中的录音输入。拾光会在每次提交前核对微信实际使用的设备。</p>
          <Button disabled={disabled} onClick={() => void window.shiguang.openAudioSettings().catch(cause => setError(errorMessage(cause)))}>
            打开 Windows 音量混合器</Button>
        </div>}
        {devices.configured && !devices.configuredAvailable && <p className="inline-error voice-route-note" role="status">
          已保存的设备不可用。重新连接设备，或测试并保存一组新的设备。
        </p>}
        {job?.kind === "install" && job.state === "running" && job.cancelable &&
          <progress className="voice-download-progress" aria-label="驱动下载进度" max={job.total} value={job.downloaded} />}
        {!devices.suggested && <div className="voice-driver-box">
          <p className="muted">缺少可以回传声音的设备时，可选用 VB-Audio Software 的 VB-CABLE。Windows 会打开原版安装程序。</p>
          <div className="flex voice-setup-actions">
            <Button icon={<Download size={16} />} disabled={readOnly || disabled}
              onClick={() => void run("voice.installCable")}>下载 VB-CABLE 并打开安装程序</Button>
            <a href="https://vb-audio.com/Services/licensing.htm" target="_blank" rel="noreferrer">Donationware 许可与作者</a>
            <a href="https://shop.vb-audio.com/en/win-apps/11-vb-cable.html" target="_blank" rel="noreferrer">支持 VB-Audio</a>
          </div>
        </div>}
        <div className="voice-driver-box">
          <p className="muted">{devices.song ? "唱歌请求会播放已选的歌声片段。" : "唱歌请求会用当前音色哼唱原创短旋律，也可选择一段最长 52 秒的 WAV 歌声。"}</p>
          <Button disabled={readOnly || disabled} onClick={() => void pickSong()}>{devices.song ? "更换歌声片段" : "选择歌声片段"}</Button>
        </div>
      </> : <Empty title={devices ? "语音设备仅支持 Windows" : "正在读取音频设备"} />}
      {error && <div className="inline-error" role="alert">{error}
        <Button disabled={disabled} onClick={() => void load()}>重新读取</Button>
      </div>}
    </div>
  </section>;
}
