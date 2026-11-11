import {
  ArrowUpRight, Atom, BookOpen, Bot, CalendarDays, Car, Check, ChevronRight, CircleAlert, Clock3, Coffee, Coins,
  Cpu, Dumbbell, FileText, Film, Gamepad2, Globe2, GraduationCap, Heart, Info, Link2,
  LoaderCircle, MailOpen, MessageCircle, Monitor, Music2, Newspaper, Plane, Play, Plus, RefreshCw,
  Save, Search, Send, Settings2, Tag, Trash2, Trophy, Tv, UserRound, UsersRound, Utensils, X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { errorMessage, type Session, type Status } from "./api";
import { Button, Empty, IconButton, PanelTitle, Select, Switch, useDraftGuard } from "./components";
import {
  commandNews, deleteNews, editableSubscription, readNews, readNewsState, safeNewsUrl, saveNews, subscriptionSignature,
  feedbackNews, clearNewsFeedback,
  type NewsArticle, type NewsCategory, type NewsDelivery, type NewsDraft, type NewsEdition, type NewsRead, type NewsState, type NewsSubscription, type NewsTimeRange,
  type NewsFeed, type NewsFeedback, type NewsTopicSettings,
} from "./news-api";
import "./news.css";

type Notify = (text: string, error?: boolean) => void;
type Confirmation = { title: string; detail: string; confirmLabel: string; cancelLabel: string; destructive?: boolean; run: () => void };
const weekdays = ["一", "二", "三", "四", "五", "六", "日"];
const categoryIcons: Record<string, typeof Gamepad2> = {
  game: Gamepad2, anime: Tv, music: Music2, esports: Trophy, food: Utensils, world: Globe2,
  technology: Cpu, ai: Bot, digital: Monitor, film: Film, finance: Coins, sports: Dumbbell,
  travel: Plane, life: Coffee, education: GraduationCap, science: Atom, auto: Car, health: Heart,
};
const initialCategories: NewsCategory[] = [
  ["game", "游戏"], ["anime", "动漫"], ["music", "音乐"], ["esports", "电竞"], ["food", "美食"], ["world", "国际"],
  ["technology", "科技"], ["ai", "人工智能"], ["digital", "数码"], ["film", "影视"], ["finance", "财经"], ["sports", "体育"],
  ["travel", "旅游"], ["life", "生活"], ["education", "教育"], ["science", "科学"], ["auto", "汽车"], ["health", "健康"],
].map(([id, label]) => ({ id, label }));
const emptyDraft: NewsDraft = {
  name: "我的每日早报", enabled: false, selectionMode: "multiple", categories: ["technology"], categoryTopics: {}, keywords: "",
  excludeKeywords: "", sourceDomains: "", time: "09:00", times: ["09:00"], timeRanges: [], weekdays: [0, 1, 2, 3, 4, 5, 6], itemCount: 5,
  lookbackHours: 24, style: "brief", includeLinks: true, recipients: [],
};
const collectingStates = new Set(["collecting", "generating", "researching"]);
const sendingStates = new Set(["sending", "publishing", "queued", "waiting", "staged"]);
const cancellableStates = new Set([...collectingStates, ...sendingStates, "ready"]);
const stateLabels: Record<string, string> = {
  collecting: "正在联网采集", generating: "正在生成简报", researching: "正在联网检索", ready: "已生成",
  queued: "等待发送", waiting: "等待发送条件", staged: "待发送", sending: "正在推送", publishing: "正在核对回执", completed: "推送完成",
  sent: "推送完成", verified: "已确认", partial: "部分完成", failed: "失败", unknown: "回执待核对",
  cancelled: "已取消", canceled: "已取消", missed: "已错过", skipped: "已跳过", planned: "已排程",
  pending: "等待发送", attempted: "等待回执", retry: "等待重试", blocked: "等待发送条件", empty: "暂无可用资讯",
};
const labelFor = (state: string) => stateLabels[state] || state || "等待处理";
const dateTime = (value: number) => value > 0 ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(value * 1000) : "时间待确认";
const dayText = (value: number) => value > 0 ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "long", day: "numeric",
}).format(value * 1000) : "简报预览";

const validTime = (value: string) => /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
const validTopic = (value: string) => value.trim().length > 0 && Array.from(value.trim()).length <= 60;

function topicValidationError(draft: NewsDraft) {
  const groups = Object.entries(draft.categoryTopics ?? {});
  if (groups.reduce((count, [, topics]) => count + topics.length, 0) > 24) return "细分主题总计最多设置 24 个（含未选方向保留的主题）";
  for (const [category, topics] of groups) {
    const label = initialCategories.find(value => value.id === category)?.label || category;
    if (topics.length > 8) return `${label}最多设置 8 个细分主题`;
    const invalid = topics.findIndex(topic => !validTopic(topic));
    if (invalid >= 0) return `${label}主题 ${invalid + 1} 应为 1 至 60 字，请填写或删除${draft.categories.includes(category) ? "" : "（请重新选择该方向）"}`;
  }
  return "";
}

function scheduleValidationError(draft: NewsDraft) {
  const times = draft.times ?? [draft.time];
  const ranges = draft.timeRanges ?? [];
  if (!times.length && !ranges.length) return "请至少保留一个固定时间或时间段";
  if (times.length > 48) return "固定时间最多可设置 48 个";
  const invalidTime = times.findIndex(time => !validTime(time));
  if (invalidTime >= 0) return `请填写固定时间 ${invalidTime + 1}（HH:MM）`;
  if (ranges.length > 8) return "时间段最多可设置 8 个";
  for (const [index, range] of ranges.entries()) {
    if (!validTime(range.start) || !validTime(range.end)) return `请填写时间段 ${index + 1} 的开始和结束时间`;
    if (range.start === range.end) return `时间段 ${index + 1} 的开始和结束时间不能相同`;
    if (!Number.isInteger(range.intervalMinutes) || range.intervalMinutes < 1 || range.intervalMinutes > 1440) {
      return `时间段 ${index + 1} 的间隔应为 1 至 1440 分钟`;
    }
  }
  return "";
}

function scheduleSummary(draft: NewsDraft) {
  const times = [...new Set((draft.times ?? [draft.time]).filter(validTime))].sort();
  const rangeCount = draft.timeRanges?.length ?? 0;
  const dayLabel = draft.weekdays.length === 7 ? "每天" : draft.weekdays.length
    ? [...draft.weekdays].sort((a, b) => a - b).map(day => `周${weekdays[day]}`).join("、") : "尚未选择星期";
  const fixed = times.length > 3 ? `${times.slice(0, 2).join("、")} 等 ${times.length} 个固定时间`
    : times.length ? `${times.join("、")} · ${times.length} 个固定时间` : "";
  return `${dayLabel} · ${[fixed, rangeCount ? `${rangeCount} 个时间段` : ""].filter(Boolean).join(" · ") || "请设置推送时间"}`;
}

function validationError(draft: NewsDraft) {
  if (!draft.name.trim()) return "请填写订阅名称";
  if (!draft.categories.length && !draft.keywords.trim()) return "请至少选择一个资讯方向，或填写公开关键词";
  const topicError = topicValidationError(draft);
  if (topicError) return topicError;
  for (const topics of Object.values(draft.topicSettings ?? {})) for (const [topic, settings] of Object.entries(topics)) {
    for (const [key, label] of [['aliases', '别名'], ['focusTerms', '关注词'], ['exclusions', '排除词']] as const) {
      if (settings[key].length > 6 || settings[key].some(term => !validTopic(term))) return `${topic}的${label}最多 6 项，每项 1 至 60 字`;
    }
  }
  for (const feed of draft.feeds ?? []) {
    if (!safeNewsUrl(feed.url)) return '请填写公共 RSS/Atom 订阅源地址';
    if (!Number.isInteger(feed.priority) || feed.priority < 0 || feed.priority > 10) return '订阅源优先级应为 0 至 10';
  }
  if (draft.sourceMode === 'feed' && !(draft.feeds ?? []).some(feed => feed.enabled)) return '仅订阅源模式至少需要启用一个 RSS/Atom 来源';
  const scheduleError = scheduleValidationError(draft);
  if (scheduleError) return scheduleError;
  if (!draft.weekdays.length) return "请至少选择一个推送星期";
  if (!Number.isInteger(draft.itemCount) || draft.itemCount < 1 || draft.itemCount > 12) return "资讯条数应为 1 至 12 条";
  if (draft.enabled && !draft.recipients.length) return "启用定时推送前，请选择已确认身份的目标会话";
  return "";
}

function sessionBlock(session?: Session) {
  if (!session?.bound) return "身份待确认";
  if (!session.calibrated) return "未校准";
  if (session.mode === "paused") return "已暂停";
  if (session.mode === "observe") return "只观察";
  if (session.mode !== "auto") return "未运行";
  return "";
}

export function NewsPage({ active, status, notify, manageSessions }: {
  active: boolean; status?: Status; notify: Notify; manageSessions: () => void;
}) {
  const [data, setData] = useState<NewsRead>();
  const [base, setBase] = useState<NewsSubscription>();
  const [draft, setDraft] = useState<NewsDraft>(() => editableSubscription(emptyDraft, false));
  const [baseline, setBaseline] = useState(() => subscriptionSignature(emptyDraft));
  const [scopedLive, setLive] = useState<{ id: string; data: NewsState }>();
  const [readError, setReadError] = useState("");
  const [stateError, setStateError] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<"preview" | "history">("preview");
  const [advanced, setAdvanced] = useState(false);
  const [feedbackScope, setFeedbackScope] = useState<NewsFeedback['scope']>('article');
  const [showTargets, setShowTargets] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [stale, setStale] = useState(false);
  const [reload, setReload] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [reading, setReading] = useState(true);
  const selection = useRef("");
  const dirtyRef = useRef(false);
  const requestId = useRef(0);
  const stateVersion = useRef(0);
  const loadedAccount = useRef<string | undefined>(undefined);
  const dataRef = useRef<NewsRead | undefined>(undefined);
  const lastStateError = useRef("");
  const forceRead = useRef(false);
  const pendingTaskEdit = useRef('');
  const scroll = useRef<HTMLDivElement>(null);
  const confirmId = useId();
  const advancedId = useId();
  const targetsId = useId();
  const topicsId = useId();
  const scheduleId = useId();
  const dirty = subscriptionSignature(draft) !== baseline;
  const accountKey = status?.accountScope || (status?.sessions.map(session => session.id).sort().join("|") ?? "");
  const accountChanged = loadedAccount.current !== undefined && loadedAccount.current !== accountKey;
  const activeAccount = useRef(accountKey);
  activeAccount.current = accountKey;
  dirtyRef.current = dirty;
  selection.current = draft.id || "";
  dataRef.current = data;
  const live = !accountChanged && !stale && scopedLive && scopedLive.id === draft.id ? scopedLive.data : undefined;
  const edition = live?.edition;
  const readOnly = status?.readOnly ?? true;
  const accountReady = data?.accountReady ?? false;
  const mutable = !!data && accountReady && !readOnly && !busy && !stale && !accountChanged;
  const categories = data?.categories.length ? data.categories : initialCategories;
  const categoryNames = new Map(categories.map(row => [row.id, row.label]));
  const sessions = status?.sessions ?? [];
  const targets = draft.recipients.map(id => sessions.find(session => session.id === id));
  const availableTargets = sessions.filter(session => session.bound);
  const contentMatches = !!edition && !dirty && !stale && subscriptionSignature(edition.snapshot) === subscriptionSignature(draft);
  const collecting = !!edition && collectingStates.has(edition.state);
  const sending = !!edition && sendingStates.has(edition.state);
  const formError = validationError(draft);
  const times = draft.times ?? [draft.time];
  const timeRanges = draft.timeRanges ?? [];
  const scheduleError = scheduleValidationError(draft);
  const categoryTopics = draft.categoryTopics ?? {};
  const topicCount = Object.values(categoryTopics).reduce((count, topics) => count + topics.length, 0);
  const topicError = topicValidationError(draft);
  const preferences = live?.feedback ?? [];
  useDraftGuard("news", dirty);

  function accept(value: NewsSubscription | NewsDraft, saved = true) {
    stateVersion.current++;
    const clean = editableSubscription(value, saved);
    setBase(saved ? value as NewsSubscription : undefined);
    setDraft(clean);
    setBaseline(subscriptionSignature(clean));
    selection.current = clean.id || "";
    dirtyRef.current = false;
    setStale(false);
    setConfirmation(undefined);
    setShowTargets(false);
    setStateError("");
  }

  useEffect(() => {
    if (!active) return;
    const at = ++requestId.current;
    setReading(true);
    readNews().then(next => {
      if (at !== requestId.current) return;
      const accountChanged = loadedAccount.current !== undefined && loadedAccount.current !== accountKey;
      const previousData = dataRef.current;
      setData(next);
      setReadError("");
      if (dirtyRef.current && !forceRead.current) {
        const current = next.subscriptions.find(row => row.id === selection.current);
        if (accountChanged || (selection.current && current?.revision !== base?.revision)) setStale(true);
        return;
      }
      loadedAccount.current = accountKey;
      const current = next.subscriptions.find(row => row.id === selection.current);
      if (accountChanged) setLive(undefined);
      if (current) accept(current);
      else if (!previousData || forceRead.current || selection.current) {
        if (next.subscriptions[0]) accept(next.subscriptions[0]);
        else accept(next.defaultSubscription, false);
        setLive(undefined);
      }
      forceRead.current = false;
    }).catch(error => {
      if (at !== requestId.current) return;
      const message = errorMessage(error);
      setReadError(message);
      notify(message, true);
    }).finally(() => { if (at === requestId.current) setReading(false); });
    return () => { requestId.current++; };
    // Configuration is re-read on entry/account change, never by live polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, accountKey, reload]);

  useEffect(() => {
    if (!active || !draft.id || stale || !accountReady || accountChanged) return;
    const id = draft.id;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (!document.hidden) {
        try {
          const version = stateVersion.current;
          const next = await readNewsState(id);
          if (alive && version === stateVersion.current) {
            setLive(before => before?.id === id && JSON.stringify(before.data) === JSON.stringify(next) ? before : { id, data: next });
            setStateError("");
            lastStateError.current = "";
          }
        } catch (error) {
          if (alive) {
            const message = errorMessage(error);
            setStateError(message);
            if (lastStateError.current !== message) { lastStateError.current = message; notify(message, true); }
          }
        }
      }
      if (alive) timer = setTimeout(tick, 1500);
    };
    setStateError("");
    void tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [active, draft.id, stale, accountReady, accountChanged, accountKey, refresh, notify]);

  function field<K extends keyof NewsDraft>(key: K, value: NewsDraft[K]) {
    setDraft(before => ({ ...before, [key]: value }));
  }

  function addCategoryTopic(category: string) {
    setDraft(before => {
      const groups = before.categoryTopics ?? {};
      const current = groups[category] ?? [];
      if (current.length >= 8 || Object.values(groups).reduce((count, topics) => count + topics.length, 0) >= 24) return before;
      return { ...before, categoryTopics: { ...groups, [category]: [...current, ""] } };
    });
  }

  function updateCategoryTopic(category: string, index: number, value: string) {
    setDraft(before => ({ ...before, categoryTopics: { ...before.categoryTopics,
      [category]: (before.categoryTopics?.[category] ?? []).map((topic, at) => at === index ? value : topic) } }));
  }

  function removeCategoryTopic(category: string, index: number) {
    setDraft(before => ({ ...before, categoryTopics: { ...before.categoryTopics,
      [category]: (before.categoryTopics?.[category] ?? []).filter((_, at) => at !== index) } }));
  }

  function updateTime(index: number, value: string) {
    setDraft(before => ({ ...before, times: (before.times ?? [before.time]).map((time, at) => at === index ? value : time) }));
  }

  function addTime() {
    setDraft(before => {
      const current = before.times ?? [before.time];
      if (current.length >= 48) return before;
      // Start each new row empty so the chosen time is always explicit.
      return { ...before, times: [...current, ""] };
    });
  }

  function removeTime(index: number) {
    setDraft(before => {
      const current = before.times ?? [before.time];
      if (current.length + (before.timeRanges?.length ?? 0) <= 1) return before;
      return { ...before, times: current.filter((_, at) => at !== index) };
    });
  }

  function updateRange(index: number, changes: Partial<NewsTimeRange>) {
    setDraft(before => ({ ...before, timeRanges: (before.timeRanges ?? []).map((range, at) => at === index ? { ...range, ...changes } : range) }));
  }

  function addRange() {
    setDraft(before => (before.timeRanges?.length ?? 0) >= 8 ? before
      : { ...before, timeRanges: [...(before.timeRanges ?? []), { start: "09:00", end: "18:00", intervalMinutes: 60 }] });
  }

  function removeRange(index: number) {
    setDraft(before => {
      const current = before.timeRanges ?? [];
      if ((before.times ?? [before.time]).length + current.length <= 1) return before;
      return { ...before, timeRanges: current.filter((_, at) => at !== index) };
    });
  }

  function chooseCategory(id: string) {
    setDraft(before => ({ ...before, categories: before.selectionMode === "single" ? [id]
      : before.categories.includes(id) ? before.categories.filter(value => value !== id) : [...before.categories, id] }));
  }

  function guard(action: () => void) {
    if (dirty) setConfirmation({ title: "有未保存的订阅设置", detail: "继续会放弃当前修改。也可以保留草稿，先保存后再操作。",
      confirmLabel: "放弃并继续", cancelLabel: "保留草稿", run: action });
    else action();
  }

  function changeSubscription(id: string) {
    if (draft.id === id) return;
    guard(() => {
      const next = data?.subscriptions.find(row => row.id === id);
      if (!next) return;
      accept(next);
      setLive(undefined);
      setTab("preview");
      if (scroll.current) scroll.current.scrollTop = 0;
    });
  }

  useEffect(() => {
    const edit = (event: Event) => {
      const detail = (event as CustomEvent<{ page: string; subscription: string }>).detail;
      if (!active || detail?.page !== 'news' || !detail.subscription) return;
      if (!data || reading) { pendingTaskEdit.current = detail.subscription; return; }
      if (!data?.subscriptions.some(row => row.id === detail.subscription)) {
        notify('订阅尚未读取或已删除，请读取最新设置后选择', true);
        return;
      }
      changeSubscription(detail.subscription);
    };
    window.addEventListener('shiguang:task-edit', edit);
    return () => window.removeEventListener('shiguang:task-edit', edit);
    // The listener tracks the current draft so external navigation honors its guard.
  }, [active, data, dirty, draft.id]);

  useEffect(() => {
    if (!active || reading || !data || !pendingTaskEdit.current) return;
    const id = pendingTaskEdit.current;
    pendingTaskEdit.current = '';
    if (data.subscriptions.some(row => row.id === id)) changeSubscription(id);
    else notify('此订阅已删除或不属于当前账号', true);
  }, [active, reading, data, draft.id]);

  function topicCondition(category: string, topic: string, key: keyof NewsTopicSettings, value: string) {
    const terms = value ? value.split(/[,，\n]/).map(term => term.trim()) : [];
    setDraft(before => ({ ...before, topicSettings: { ...before.topicSettings,
      [category]: { ...before.topicSettings?.[category], [topic]: {
        aliases: [], focusTerms: [], exclusions: [], ...before.topicSettings?.[category]?.[topic], [key]: terms,
      } } } }));
  }

  function updateFeed(id: string, changes: Partial<NewsFeed>) {
    setDraft(before => ({ ...before, feeds: (before.feeds ?? []).map(feed => feed.id === id ? { ...feed, ...changes } : feed) }));
  }

  const markArticle = (article: Pick<NewsArticle, 'url'>, value: NewsFeedback['value'], scope = feedbackScope) => perform('feedback', async () => {
    const id = draft.id;
    if (!id || !base) return;
    stateVersion.current++;
    await feedbackNews(id, article, value, scope);
    assertAccount();
    if (selection.current !== id) return;
    setRefresh(before => before + 1);
    notify(scope === 'article' ? '文章反馈已保存；下次采集生效，可在偏好管理撤销' : '已保存此订阅的轻量排序偏好；不会隐藏整个来源或主题');
  });

  const clearPreference = (id: string) => perform('feedback', async () => {
    const subscription = draft.id;
    if (!subscription) return;
    stateVersion.current++;
    await clearNewsFeedback(subscription, id);
    assertAccount();
    if (selection.current !== subscription) return;
    setRefresh(before => before + 1);
    notify('反馈已撤销，下次采集恢复原有规则');
  });

  function newSubscription() {
    guard(() => {
      accept(data?.defaultSubscription ?? emptyDraft, false);
      setLive(undefined);
      setTab("preview");
      if (scroll.current) scroll.current.scrollTop = 0;
    });
  }

  function reloadConfiguration() {
    guard(() => { forceRead.current = true; setReload(value => value + 1); });
  }

  async function perform(key: string, action: () => Promise<void>) {
    if (busy) return;
    setBusy(key);
    try { await action(); }
    catch (error) {
      const message = errorMessage(error);
      if (/冲突|版本|已更新|账号|身份.*变化/.test(message)) setStale(true);
      notify(message, true);
    } finally { setBusy(""); }
  }

  async function persist() {
    const invalid = validationError(draft);
    if (invalid) throw new Error(invalid);
    const next = await saveNews(draft, base?.revision ?? null);
    assertAccount();
    accept(next);
    setData(before => before && ({ ...before, subscriptions: before.subscriptions.some(row => row.id === next.id)
      ? before.subscriptions.map(row => row.id === next.id ? next : row) : [...before.subscriptions, next] }));
    return next;
  }

  function assertAccount() {
    if (activeAccount.current !== accountKey) throw new Error("微信账号已变化，请在原账号核对操作结果后读取最新设置");
  }

  const save = () => perform("save", async () => { await persist(); notify("订阅已保存"); });
  const generate = () => perform("preview", async () => {
    const saved = dirty || !base ? await persist() : base;
    assertAccount();
    stateVersion.current++;
    await commandNews(saved.id, "preview");
    const next = await readNewsState(saved.id);
    assertAccount();
    setTab("preview");
    setLive({ id: saved.id, data: next });
    setRefresh(value => value + 1);
    notify("已开始联网采集，预览完成后可手动推送");
  });
  const push = () => base && edition && perform("push", async () => {
    stateVersion.current++;
    await commandNews(base.id, "push", edition.id);
    const next = await readNewsState(base.id);
    assertAccount();
    setLive({ id: base.id, data: next });
    setRefresh(value => value + 1);
    notify(`已提交推送，${draft.recipients.length} 个目标的发送回执见右侧`);
  });
  const cancelEdition = () => base && edition && perform("cancel", async () => {
    stateVersion.current++;
    await commandNews(base.id, "cancel", edition.id);
    const next = await readNewsState(base.id);
    assertAccount();
    setLive({ id: base.id, data: next });
    setRefresh(value => value + 1);
    notify("已取消尚未发送的内容");
  });
  const remove = () => base && setConfirmation({ title: `删除“${base.name}”？`,
    detail: "删除后停止此订阅的定时推送，并取消尚未开始的任务。已经发送的微信消息不会撤回。",
    confirmLabel: "确认删除", cancelLabel: "保留订阅", destructive: true, run: () => void perform("delete", async () => {
      await deleteNews(base.id, base.revision);
      assertAccount();
      const remaining = data?.subscriptions.filter(row => row.id !== base.id) ?? [];
      setData(before => before && ({ ...before, subscriptions: remaining }));
      if (remaining[0]) accept(remaining[0]);
      else accept(data?.defaultSubscription ?? emptyDraft, false);
      setLive(undefined);
      notify("订阅已删除");
    }) });

  const globalReason = readOnly ? "当前为只读模式，可查看设置和记录" : accountChanged ? "微信账号已切换，请读取最新设置后核对"
    : stale ? "账号或订阅已更新，请读取最新设置后核对"
    : !data ? reading ? "正在读取订阅设置" : "尚未读取到订阅设置"
    : !accountReady ? "微信账号尚未核对，请先在会话页确认身份"
    : !status?.connected ? "后台未连接，推送暂不可用"
    : status.mode === "paused" ? "全局已暂停，资讯不会自动发送"
    : status.mode === "observe" ? "全局为只观察模式，资讯不会发送"
    : status.mode !== "auto" ? "助手尚未运行，资讯发送正在等待"
    : live && !live.connected ? live.reason || "资讯后台尚未连接，请稍后刷新"
    : "";
  const pushReason = globalReason || (stateError ? "暂时无法核对预览状态，请刷新后推送"
    : dirty ? "设置已修改，请重新生成预览后推送"
    : !draft.recipients.length ? "请先选择推送目标"
    : !edition || !edition.articles.length ? "请先生成有可靠来源的简报"
    : !contentMatches ? "当前预览与订阅设置不一致，请重新生成"
    : edition.state !== "ready" ? collecting ? "正在采集，完成后可推送" : labelFor(edition.state)
    : targets.every(target => !!sessionBlock(target)) ? "所选目标尚未具备发送条件，请核对校准与会话状态"
    : "");
  const blockedTargets = targets.filter(target => !!sessionBlock(target)).length;
  const schedule = draft.enabled ? scheduleSummary(draft) : "定时推送未启用";
  const selectorOptions = [
    ...(!draft.id ? [{ value: "", label: `${draft.name.trim() || "新订阅"}（未保存）` }] : []),
    ...(data?.subscriptions.map(row => ({ value: row.id, label: row.name })) ?? []),
  ];

  return <div className="news-page" data-tab={tab}>
    <div className="news-scroll" ref={scroll}>
      <section className="glass news-toolbar" aria-label="订阅管理">
        <div className="news-subscription-selector"><MailOpen size={25} aria-hidden="true" />
          <Select label="资讯订阅" value={draft.id ?? ""} options={selectorOptions} onChange={changeSubscription} disabled={!!busy || !data} /></div>
        <span className="news-toolbar-divider" aria-hidden="true" />
        <Button className="news-new" icon={<Plus size={20} />} disabled={!mutable} onClick={newSubscription}>新建订阅</Button>
        <IconButton label="删除订阅" icon={<Trash2 size={21} />} disabled={!mutable || !base} onClick={remove} />
        <IconButton className="news-reload" label="读取最新订阅" icon={<RefreshCw size={18} />} disabled={!!busy || reading} onClick={reloadConfiguration} />
        <div className="news-toolbar-enabled"><Switch checked={draft.enabled} onChange={value => field("enabled", value)} disabled={!mutable}>定时推送</Switch></div>
      </section>

      {confirmation && <div className={`news-confirm ${confirmation.destructive ? "destructive" : ""}`} role="alertdialog"
        aria-labelledby={confirmId} aria-describedby={`${confirmId}-detail`}>
        <CircleAlert size={22} aria-hidden="true" /><div><strong id={confirmId}>{confirmation.title}</strong>
          <p id={`${confirmId}-detail`}>{confirmation.detail}</p></div>
        <div className="flex"><Button autoFocus onClick={() => setConfirmation(undefined)}>{confirmation.cancelLabel}</Button>
          <Button variant={confirmation.destructive ? "danger" : "primary"} onClick={() => { const run = confirmation.run; setConfirmation(undefined); run(); }}>
            {confirmation.confirmLabel}</Button></div>
      </div>}

      {(globalReason || readError) && <div className={`news-notice ${readError || stale ? "news-notice-error" : ""}`} role={readError ? "alert" : "status"}>
        {reading && !data ? <LoaderCircle size={17} className="spinner" /> : <Info size={17} />}
        <span>{readError || globalReason}</span>
        {(!accountReady && data) ? <Button variant="ghost" onClick={manageSessions}>管理会话 <ArrowUpRight size={14} /></Button>
          : (readError || stale) ? <Button variant="ghost" disabled={!!busy || reading} onClick={reloadConfiguration}>读取最新设置</Button> : null}
      </div>}

      <div className="news-columns">
        <section className="glass panel news-settings" aria-label="订阅设置">
          <PanelTitle icon={<Settings2 />}>订阅设置</PanelTitle>
          <label className="field news-name"><span>订阅名称</span><input aria-label="订阅名称" value={draft.name} maxLength={40}
            disabled={!mutable} onChange={event => field("name", event.target.value)} /></label>

          <section className="news-topics" aria-label="资讯方向">
            <div className="news-section-heading"><h3><Search size={21} />资讯方向</h3>
              <div className="segments news-selection-mode" role="group" aria-label="方向选择模式">
                <Button variant={draft.selectionMode === "single" ? "primary" : "ghost"} aria-pressed={draft.selectionMode === "single"}
                  disabled={!mutable} onClick={() => setDraft(before => ({ ...before, selectionMode: "single", categories: before.categories.slice(0, 1) }))}>单选</Button>
                <Button variant={draft.selectionMode === "multiple" ? "primary" : "ghost"} aria-pressed={draft.selectionMode === "multiple"}
                  disabled={!mutable} onClick={() => field("selectionMode", "multiple")}>多选</Button>
              </div>
            </div>
            <div className="news-categories" role="group" aria-label="选择资讯方向">{categories.map(category => {
              const selected = draft.categories.includes(category.id);
              const Icon = categoryIcons[category.id] ?? Newspaper;
              return <Button key={category.id} className={`news-category ${selected ? "selected" : ""}`}
                variant={selected ? "primary" : undefined} aria-label={category.label} aria-pressed={selected}
                disabled={!mutable} onClick={() => chooseCategory(category.id)} icon={<Icon size={18} />}>
                <span>{category.label === "人工智能" ? "AI" : category.label}</span>{selected && <Check className="news-category-check" size={13} />}
              </Button>;
            })}</div>
            <p className="news-topic-count muted">已选 {draft.categories.length} 个方向{draft.selectionMode === "single" ? " · 点击其他方向替换" : " · 可再次点击取消"}</p>
            {draft.categories.length > 0 && <section className="news-category-topic-settings" aria-label="按方向细化关注内容">
              <div className="news-topic-settings-heading"><h4>细化关注内容</h4>
                <span role="status" aria-label="细分主题数量">{topicCount} / 24 个主题</span></div>
              <div className="news-category-topic-groups">{draft.categories.map(category => {
                const label = categoryNames.get(category) || category;
                const topics = categoryTopics[category] ?? [];
                const Icon = categoryIcons[category] ?? Tag;
                const placeholder = category === "game" ? "如：三角洲" : category === "sports" ? "如：张雪摩托" : "填写公开作品、事件或人物";
                return <div className="news-category-topics" key={category} role="group" aria-label={`${label}细分内容`}>
                  <div className="news-category-topic-heading"><span><Icon size={14} />{label}<small>{topics.length} / 8</small></span>
                    <Button variant="ghost" className="news-add-rule" icon={<Plus size={14} />} disabled={!mutable || topics.length >= 8 || topicCount >= 24}
                      onClick={() => addCategoryTopic(category)}>添加{label}主题</Button></div>
                  {topics.length > 0 && <div className="news-category-topic-values">{topics.map((topic, index) =>
                    <div className="news-category-topic-row" key={index}><label className="field"><input aria-label={`${label}细分主题 ${index + 1}`}
                      value={topic} placeholder={placeholder} maxLength={120} disabled={!mutable} aria-invalid={!validTopic(topic)}
                      aria-describedby={`${topicsId}-hint${topicError ? ` ${topicsId}-error` : ""}`}
                      onChange={event => updateCategoryTopic(category, index, event.target.value)} /></label>
                      <IconButton label={`删除${label}主题 ${index + 1}`} icon={<X size={14} />} disabled={!mutable}
                        onClick={() => removeCategoryTopic(category, index)} /></div>)}</div>}
                </div>;
              })}</div>
              <p id={`${topicsId}-hint`} className="news-category-topic-hint muted">每个主题 1 至 60 字；未填写时按整个方向检索。主题只用于对应方向，总数包含未选方向保留的主题。</p>
              {topicError && <p className="news-schedule-error" id={`${topicsId}-error`} role="alert"><CircleAlert size={14} />{topicError}</p>}
            </section>}
          </section>

          <label className="field news-keywords"><span><Tag size={20} />关注关键词</span><input aria-label="关注关键词" value={draft.keywords}
            placeholder="对所有方向生效，可选" maxLength={180} disabled={!mutable}
            onChange={event => field("keywords", event.target.value)} /></label>

          <section className="news-schedule" aria-label="推送计划">
            <div className="news-section-heading"><h3><CalendarDays size={22} />推送计划</h3>
              <div className="news-schedule-options"><span className="muted news-timezone">北京时间</span>
              <div className="field news-count"><Select label="资讯条数" value={String(draft.itemCount)} disabled={!mutable}
                options={Array.from({ length: 12 }, (_, index) => ({ value: String(index + 1), label: `${index + 1} 条` }))}
                onChange={value => field("itemCount", Number(value))} /></div>
              </div>
            </div>
            <div className="news-rule-heading"><span>固定时间 <small>{times.length} / 48</small></span>
              <Button className="news-add-rule" variant="ghost" icon={<Plus size={15} />} disabled={!mutable || times.length >= 48}
                onClick={addTime}>添加固定时间</Button></div>
            {times.length > 0 && <div className="news-fixed-times" role="group" aria-label="固定推送时间">{times.map((time, index) =>
              <div className="news-fixed-time-row" key={index}>
                <label className="field"><span>时间 {index + 1}</span><input type="time" aria-label={`固定时间 ${index + 1}`}
                  value={time} disabled={!mutable} aria-invalid={!validTime(time)} aria-describedby={scheduleError ? `${scheduleId}-error` : undefined}
                  onChange={event => updateTime(index, event.target.value)} /></label>
                <IconButton label={`删除固定时间 ${index + 1}`} icon={<X size={15} />} disabled={!mutable || times.length + timeRanges.length <= 1}
                  onClick={() => removeTime(index)} />
              </div>)}</div>}
            <div className="news-rule-heading news-ranges-heading"><span>时间段 <small>{timeRanges.length} / 8</small></span>
              <Button className="news-add-rule" variant="ghost" icon={<Plus size={15} />} disabled={!mutable || timeRanges.length >= 8}
                onClick={addRange}>添加时间段</Button></div>
            {timeRanges.length > 0 && <div className="news-time-ranges" role="group" aria-label="按间隔推送的时间段">{timeRanges.map((range, index) =>
              <div className="news-time-range-row" key={index} role="group" aria-label={`时间段 ${index + 1}`}>
                <label className="field"><span>开始</span><input type="time" aria-label={`时间段 ${index + 1} 开始时间`} value={range.start}
                  disabled={!mutable} aria-invalid={!validTime(range.start) || range.start === range.end}
                  aria-describedby={`${scheduleId}-hint`} onChange={event => updateRange(index, { start: event.target.value })} /></label>
                <label className="field"><span>结束{validTime(range.end) && validTime(range.start) && range.end < range.start ? " · 次日" : ""}</span>
                  <input type="time" aria-label={`时间段 ${index + 1} 结束时间`} value={range.end} disabled={!mutable}
                    aria-invalid={!validTime(range.end) || range.start === range.end} aria-describedby={`${scheduleId}-hint`}
                    onChange={event => updateRange(index, { end: event.target.value })} /></label>
                <label className="field news-range-interval"><span>每隔（分钟）</span><input type="number" min={1} max={1440} step={1}
                  aria-label={`时间段 ${index + 1} 间隔分钟`} value={Number.isNaN(range.intervalMinutes) ? "" : range.intervalMinutes}
                  disabled={!mutable} aria-invalid={!Number.isInteger(range.intervalMinutes) || range.intervalMinutes < 1 || range.intervalMinutes > 1440}
                  onChange={event => updateRange(index, { intervalMinutes: event.target.valueAsNumber })} /></label>
                <IconButton label={`删除时间段 ${index + 1}`} icon={<X size={15} />} disabled={!mutable || times.length + timeRanges.length <= 1}
                  onClick={() => removeRange(index)} />
              </div>)}</div>}
            <p className="news-schedule-hint muted" id={`${scheduleId}-hint`}>固定时间与时间段可组合，同一分钟只推送一期。跨午夜时，星期按开始日计算。</p>
            <p className="news-schedule-hint muted">定时推送恢复时只补最近一期；新一期开始后，停止上一期未发送的内容。</p>
            <div className="news-weekdays" role="group" aria-label="推送星期">{weekdays.map((label, day) =>
              <Button key={day} className={draft.weekdays.includes(day) ? "selected" : ""} aria-label={`星期${label}`}
                aria-pressed={draft.weekdays.includes(day)} disabled={!mutable} onClick={() => field("weekdays",
                  draft.weekdays.includes(day) ? draft.weekdays.filter(value => value !== day) : [...draft.weekdays, day].sort((a, b) => a - b))}>{label}</Button>)}</div>
            <p className="news-schedule-summary" role="status" aria-label="推送计划摘要">{scheduleSummary(draft)}</p>
            {scheduleError && <p className="news-schedule-error" id={`${scheduleId}-error`} role="alert"><CircleAlert size={14} />{scheduleError}</p>}
            <div className="news-target-heading"><span><Send size={18} />发送到</span>
              <Button variant="ghost" className="news-manage" onClick={manageSessions}>管理会话 <ArrowUpRight size={13} /></Button></div>
            <div className="news-targets" role="group" aria-label="已选推送目标">
              {draft.recipients.map((id, index) => {
                const session = targets[index];
                const snapshot = base?.targets?.find(target => target.session === id);
                const Icon = session?.kind === "group" ? UsersRound : UserRound;
                const reason = sessionBlock(session);
                return <Button key={id} className={`news-target-chip ${reason ? "unavailable" : ""}`} aria-label={`移除目标 ${session?.name || snapshot?.name || "已移除的会话"}`}
                  disabled={!mutable} onClick={() => field("recipients", draft.recipients.filter(value => value !== id))}>
                  <Icon size={16} /><span>{session?.name || snapshot?.name || "已移除的会话"}{reason ? ` · ${reason}` : ""}</span><X size={14} />
                </Button>;
              })}
              <Button className="news-add-target" icon={<Plus size={18} />} aria-expanded={showTargets} aria-controls={targetsId}
                disabled={!mutable} onClick={() => setShowTargets(value => !value)}>添加目标</Button>
            </div>
            <p className="news-target-caption muted">仅向已确认身份的会话推送{blockedTargets ? ` · ${blockedTargets} 个目标正在等待发送条件` : ""}</p>
            {showTargets && <div className="news-target-picker" id={targetsId}>
              <div role="group" aria-label="选择推送目标">{availableTargets.length ? availableTargets.map(session => {
                const selected = draft.recipients.includes(session.id);
                const reason = sessionBlock(session);
                const Icon = session.kind === "group" ? UsersRound : UserRound;
                return <Button key={session.id} aria-label={session.name} aria-pressed={selected} className={selected ? "selected" : ""}
                  disabled={!mutable || (!selected && draft.recipients.length >= 8)} onClick={() => field("recipients", selected
                    ? draft.recipients.filter(value => value !== session.id) : [...draft.recipients, session.id])}>
                  <Icon size={16} /><span>{session.name}</span>{reason && <small>{reason}</small>}{selected && <Check size={15} />}</Button>;
              }) : <p className="muted">暂无已确认身份的会话，请先添加并核对监听对象。</p>}</div>
              <div className="news-target-picker-actions"><span className="muted">已选 {draft.recipients.length} / 8 个目标</span>
                <Button variant="ghost" onClick={() => setShowTargets(false)}>完成</Button></div>
            </div>}
          </section>

          <div className="news-style-line"><div className="news-style-options" role="radiogroup" aria-label="简报风格">
            <FileText size={20} aria-hidden="true" />
            {([["brief", "简洁简报"], ["persona", "当前人格"]] as const).map(([value, label]) => <label key={value}>
              <input type="radio" name={`${advancedId}-style`} aria-label={label} checked={draft.style === value} disabled={!mutable}
                onChange={() => field("style", value)} /><span aria-hidden="true" />{label}</label>)}
          </div><div className="news-links"><Link2 size={17} /><Switch checked={draft.includeLinks} disabled={!mutable}
            onChange={value => field("includeLinks", value)}>附上来源链接</Switch></div></div>

          <div className="news-advanced"><Button className="news-advanced-trigger" variant="ghost" icon={<ChevronRight size={18} />}
            aria-expanded={advanced} aria-controls={advancedId} onClick={() => setAdvanced(value => !value)}>高级筛选</Button>
            {advanced && <div id={advancedId} className="news-advanced-fields">
              <label className="field"><span>排除关键词</span><input value={draft.excludeKeywords} maxLength={120} disabled={!mutable}
                placeholder="用逗号分隔" onChange={event => field("excludeKeywords", event.target.value)} /></label>
              <label className="field"><span>来源域名</span><input value={draft.sourceDomains} maxLength={600} disabled={!mutable}
                placeholder="最多 5 个公开域名，用逗号分隔" onChange={event => field("sourceDomains", event.target.value)} /></label>
              <div className="field"><span>资讯回看范围</span><Select label="资讯回看范围" value={String(draft.lookbackHours)} disabled={!mutable}
                options={[24, 48, 72].map(hours => ({ value: String(hours), label: `最近 ${hours} 小时` }))}
                onChange={value => field("lookbackHours", Number(value))} /></div>
              <details className="news-advanced-block"><summary>每个主题的检索条件</summary>
                <p className="muted">别名可替代主题名匹配；关注词至少命中一项，排除词命中即跳过。每项最多 60 字，每栏最多 6 项，用逗号分隔。只影响对应主题，未启用的方向和主题仍保留设置。</p>
                {draft.categories.flatMap(category => (categoryTopics[category] ?? []).filter(topic => topic.trim()).map(topic => {
                  const settings = draft.topicSettings?.[category]?.[topic];
                  return <fieldset key={`${category}-${topic}`} className="news-topic-conditions" disabled={!mutable}>
                    <legend>{categoryNames.get(category)} · {topic}</legend>
                    {([['aliases', '主题别名'], ['focusTerms', '主题关注词'], ['exclusions', '主题排除词']] as const).map(([key, label]) =>
                      <label className="field" key={key}><span>{label}</span><textarea rows={2} aria-label={`${topic}${label}`} maxLength={420}
                        value={(settings?.[key] ?? []).join(', ')} placeholder="用逗号分隔，可留空"
                        onChange={event => topicCondition(category, topic, key, event.target.value)} /></label>)}
                  </fieldset>;
                }))}
                {!draft.categories.some(category => (categoryTopics[category] ?? []).length) && <p className="muted">先在“细化关注内容”添加主题。</p>}
              </details>
              <details className="news-advanced-block"><summary>来源与公共 RSS / Atom</summary>
                <div className="field"><span>采集来源</span><Select label="采集来源模式" value={draft.sourceMode ?? 'search'} disabled={!mutable}
                  options={[{ value: 'search', label: '仅公开检索' }, { value: 'feed', label: '仅 RSS / Atom 订阅源' }, { value: 'both', label: '公开检索 + 订阅源' }]}
                  onChange={value => field('sourceMode', value as NewsDraft['sourceMode'])} /></div>
                <p className="muted">仅抓取公共 XML 订阅源；不使用登录状态。来源绑定本订阅，可限制到方向或细分主题。高优先级先排序；关闭的来源保留配置。</p>
                {(draft.feeds ?? []).map((feed, index) => <fieldset className="news-feed" key={feed.id} disabled={!mutable}>
                  <legend>订阅源 {index + 1}</legend>
                  <label className="field"><span>名称</span><input aria-label={`订阅源 ${index + 1} 名称`} maxLength={60} value={feed.label}
                    onChange={event => updateFeed(feed.id, { label: event.target.value })} /></label>
                  <label className="field"><span>公共地址</span><input type="url" aria-label={`订阅源 ${index + 1} 地址`} maxLength={800} value={feed.url}
                    placeholder="https://公开网站/rss.xml" onChange={event => updateFeed(feed.id, { url: event.target.value })} /></label>
                  <div className="news-feed-options">
                    <div className="field"><span>绑定方向</span><Select label={`订阅源 ${index + 1} 绑定方向`} value={feed.category} disabled={!mutable}
                      options={[{ value: '', label: '本订阅所有方向' }, ...categories.map(row => ({ value: row.id, label: row.label }))]}
                      onChange={value => updateFeed(feed.id, { category: value, topic: '' })} /></div>
                    <div className="field"><span>绑定主题</span><Select label={`订阅源 ${index + 1} 绑定主题`} value={feed.topic} disabled={!mutable || !feed.category}
                      options={[{ value: '', label: '该方向所有主题' }, ...[...new Set([...(categoryTopics[feed.category] ?? []), ...(feed.topic ? [feed.topic] : [])])].filter(Boolean)
                        .map(topic => ({ value: topic, label: topic }))]}
                      onChange={value => updateFeed(feed.id, { topic: value })} /></div>
                    <label className="field"><span>优先级 0–10</span><input type="number" min={0} max={10} aria-label={`订阅源 ${index + 1} 优先级`}
                      value={Number.isNaN(feed.priority) ? '' : feed.priority} onChange={event => updateFeed(feed.id, { priority: event.target.valueAsNumber })} /></label>
                  </div>
                  <div className="news-feed-actions"><Switch checked={feed.enabled} disabled={!mutable} onChange={value => updateFeed(feed.id, { enabled: value })}>启用此来源</Switch>
                    <Button variant="ghost" icon={<Trash2 size={14} />} disabled={!mutable} onClick={() => field('feeds', (draft.feeds ?? []).filter(row => row.id !== feed.id))}>移除来源 {index + 1}</Button></div>
                </fieldset>)}
                <Button icon={<Plus size={15} />} disabled={!mutable || (draft.feeds?.length ?? 0) >= 12} onClick={() => field('feeds', [...(draft.feeds ?? []),
                  { id: crypto.randomUUID().replaceAll('-', ''), label: '', url: '', enabled: true, priority: 0, category: '', topic: '' }])}>添加公共订阅源</Button>
              </details>
              <details className="news-advanced-block"><summary>事件合并与偏好管理</summary>
                <Switch checked={draft.groupSameEvent ?? false} disabled={!mutable} onChange={value => field('groupSameEvent', value)}>保守合并同事件来源</Switch>
                <p className="muted">仅在标题、正文摘录及数字高度一致时关联来源；版本、比分、日期、实质更新与不同观点保留。不确定时保持独立。已核实回执会按目标记录关联来源，避免换链接重复推送。</p>
                <h4>已保存偏好 · {preferences.length} 项</h4>
                <p className="muted">只作用于当前账号的这个订阅。文章“不相关 / 重复”会隐藏该链接；喜欢会优先排序。明确选择来源或主题时只轻微调整排序，不会批量隐藏。可修改或撤销，不改写人格或历史发送内容。</p>
                {preferences.map(preference => <div className="news-preference" key={preference.id}>
                  <p><strong>{preference.scope === 'article' ? '文章' : preference.scope === 'source' ? '来源' : '主题'}</strong> · {preference.scope === 'article' ? preference.title : preference.scope === 'source' ? preference.source : preference.topic}</p>
                  <div className="news-preference-actions"><Select label={`修改反馈 ${preference.title}`} value={preference.value} disabled={!mutable || !base}
                    options={[{ value: 'like', label: '喜欢 / 优先' }, { value: 'irrelevant', label: preference.scope === 'article' ? '不相关 / 隐藏' : '减少推荐' },
                      ...(preference.scope === 'article' ? [{ value: 'duplicate', label: '重复 / 隐藏' }] : [])]}
                    onChange={value => void markArticle({ url: preference.url }, value as NewsFeedback['value'], preference.scope)} />
                    <Button variant="ghost" disabled={!mutable || !base} onClick={() => void clearPreference(preference.id)}>撤销反馈</Button></div>
                </div>)}
                {!preferences.length && <p className="muted">暂无反馈。生成简报后，可在文章下方保存偏好。</p>}
              </details>
              <p className="muted">检索只使用公开方向、关键词和日期；文章时间未核实时会明确标注。</p>
            </div>}
          </div>
        </section>

        <section className="glass panel news-preview" aria-label={tab === "preview" ? "今日简报预览" : "推送记录"}>
          <div className="news-preview-tabs" role="group" aria-label="简报与记录">
            <Button className={tab === "preview" ? "active" : ""} variant="ghost" aria-pressed={tab === "preview"}
              icon={<FileText size={24} />} onClick={() => setTab("preview")}>今日简报预览</Button>
            <span className="news-online-label"><Globe2 size={14} />联网采集</span>
            <Button className={tab === "history" ? "active" : ""} variant="ghost" aria-pressed={tab === "history"}
              icon={<BookOpen size={18} />} onClick={() => setTab("history")}>推送记录</Button>
          </div>
          {stateError && <div className="news-state-error" role="alert"><CircleAlert size={17} /><span>{stateError}</span>
            <Button variant="ghost" onClick={() => setRefresh(value => value + 1)}>重试</Button></div>}
          {tab === "preview" ? <>
            {edition ? <>
              <div className="news-edition-meta"><span>{dayText(edition.createdAt)} · {edition.snapshot.categories.length} 个方向 · {edition.articles.length} 条资讯</span>
                {collecting && <LoaderCircle size={17} className="spinner" />}</div>
              {edition.articles.length ? <ol className="news-articles">{edition.articles.map((article, index) => {
                const url = safeNewsUrl(article.url);
                const Icon = categoryIcons[article.category] ?? Newspaper;
                return <li className="news-article" key={article.id || `${article.url}-${index}`}>
                  <span className="news-article-index" aria-hidden="true">{index + 1}</span>
                  <div className="news-article-main"><span className="news-article-category"><Icon size={15} /><span>{categoryNames.get(article.category) || "资讯"}{article.topic ? ` · ${article.topic}` : ""}</span></span>
                    <h3>{article.title}</h3><p>{article.summary}</p>
                    <div className="news-article-source">{url ? <a href={url} target="_blank" rel="noopener noreferrer"><ArrowUpRight size={16} />来源：{article.source || new URL(url).hostname}</a>
                      : <span>来源链接不可用</span>}
                      <time>{article.publishedAt > 0 ? article.timeLabel || dateTime(article.publishedAt) : "发布时间未核实"}</time></div>
                    <span className="news-source-kind">{article.collectedVia?.includes('search') && article.collectedVia?.includes('feed')
                      ? '公开检索 + RSS / Atom' : article.sourceType === 'feed' ? 'RSS / Atom 订阅源' : '公开检索'}</span>
                    {article.sources && article.sources.length > 1 && <details className="news-associated-sources"><summary>关联来源 · {article.sources.length} 个</summary>
                      <p className="muted">{article.merge?.reason}</p>{article.sources.map(source => {
                        const link = safeNewsUrl(source.url);
                        return link ? <a key={source.url} href={link} target="_blank" rel="noopener noreferrer">{source.source || new URL(link).hostname} · {source.title}</a> : null;
                      })}</details>}
                    {article.merge?.confidence === 'unmerged' && <small className="muted news-merge-note">{article.merge.reason}</small>}
                    <div className="news-article-feedback" role="group" aria-label={`${article.title}反馈`}>
                      <Select label={`${article.title}反馈范围`} value={feedbackScope} disabled={!mutable || !base}
                        options={[{ value: 'article', label: '仅这篇文章' }, { value: 'source', label: '这个来源 · 轻量排序' },
                          ...(article.topic ? [{ value: 'topic', label: '这个主题 · 轻量排序' }] : [])]}
                        onChange={value => setFeedbackScope(value as NewsFeedback['scope'])} />
                      {([['like', '喜欢'], ['irrelevant', feedbackScope === 'article' ? '不相关' : '减少推荐'], ['duplicate', '重复']] as const)
                        .filter(([value]) => value !== 'duplicate' || feedbackScope === 'article').map(([value, label]) =>
                          <Button key={value} variant="ghost" disabled={!mutable || !base} aria-pressed={preferences.some(preference => preference.scope === feedbackScope && preference.value === value
                            && (feedbackScope === 'article' ? preference.url === article.url : feedbackScope === 'source' ? preference.source === article.source : preference.topic === article.topic && preference.category === article.category))}
                            onClick={() => void markArticle(article, value)}>{label}</Button>)}
                    </div>
                  </div>
                </li>;
              })}</ol> : collecting ? <Empty icon={<LoaderCircle className="spinner" size={31} />} title="正在收集公开资讯"
                detail="正在核对来源与时间，完成后会显示在这里。" />
                : <Empty icon={<Newspaper size={34} />} title={edition.state === "failed" ? "这次没有取得可用简报" : edition.state === "cancelled" ? "本次采集已取消" : "暂无可靠来源的资讯"}
                  detail={edition.reason || "可以调整方向或关键词后重新生成。"} />}
              {edition.warnings.length > 0 && <div className="news-warnings" role="status">{edition.warnings.map((warning, index) =>
                <p key={index}><Info size={15} /><span>{warning}</span></p>)}</div>}
              {!!edition.deliveries.length && <DeliveryReceipts deliveries={edition.deliveries} />}
              {edition.reason && edition.articles.length > 0 && <p className="news-result-reason"><Info size={15} />{edition.reason}</p>}
              {edition.articles.length > 0 && <details className="news-message-preview"><summary><MessageCircle size={16} />查看实际推送内容 <ChevronRight size={15} /></summary>
                <div>{edition.parts.length ? edition.parts.map((part, index) => <p key={index}>{part}</p>) : <p className="muted">推送文本尚未生成</p>}</div>
              </details>}
              <div className="news-result-footer"><span className={`news-edition-state state-${edition.state}`}>{collecting ? <LoaderCircle size={15} className="spinner" /> : <Check size={15} />}{labelFor(edition.state)}</span>
                <span>{!contentMatches && !collecting ? "设置有变化，请重新生成" : "预览不会自动发送"}</span>
                {cancellableStates.has(edition.state) && <Button variant="ghost" disabled={!mutable} busy={busy === "cancel"} onClick={() => void cancelEdition()}>取消本次</Button>}</div>
            </> : <div className="news-empty-preview"><Empty icon={<Newspaper size={37} />} title={draft.id && !live && !stateError ? "正在读取简报状态" : "还没有生成简报"}
              detail="选好关注方向，点击底部“生成预览”。内容会先在这里展示。" />
              <p><Info size={15} />预览只采集公开资讯，不会向微信发送消息</p></div>}
          </> : <div className="news-history">
            {live?.history.length ? live.history.map(row => <details className="news-history-item" key={row.id}>
              <summary><span className={`news-history-state state-${row.state}`}>{labelFor(row.state)}</span><span>{dateTime(row.scheduledAt || row.createdAt)}</span>
                <small>{row.trigger === "schedule" || row.trigger === "scheduled" ? "定时推送" : row.trigger === "preview" ? "手动预览 / 推送" : "手动推送"} · {row.articles.length} 条</small><ChevronRight size={16} /></summary>
              {row.reason && <p className="news-history-reason">{row.reason}</p>}
              {row.deliveries.length ? <DeliveryReceipts deliveries={row.deliveries} /> : <p className="news-history-reason muted">本次尚无实际发送回执</p>}
              {!!row.warnings.length && row.warnings.map((warning, index) => <p className="news-history-reason muted" key={index}>{warning}</p>)}
            </details>) : <Empty icon={<BookOpen size={34} />} title="还没有推送记录" detail="生成预览和推送后，可在这里查看任务状态与每个目标的回执。" />}
          </div>}
        </section>
      </div>
    </div>

    <footer className="news-footer glass">
      <div className="news-footer-status"><Clock3 size={23} /><div><span>推送计划 <strong>{schedule}</strong></span>
        <small>{dirty ? "有未保存的修改" : base ? "订阅已保存" : "新订阅未保存"} · 北京时间{draft.recipients.length
          ? ` · ${draft.recipients.length} 个目标 / ${contentMatches && edition?.articles.length ? edition.articles.length : `最多 ${draft.itemCount}`} 条资讯`
          : " · 尚未选择目标"}</small></div></div>
      <div className="news-footer-actions">
        {dirty && <Button variant="ghost" disabled={!!busy} onClick={() => accept(base ?? data?.defaultSubscription ?? emptyDraft, !!base)}>撤销修改</Button>}
        <Button icon={<Save size={19} />} busy={busy === "save"} disabled={!mutable || (!dirty && !!base)} onClick={() => void save()}>保存订阅</Button>
        <Button icon={<Play size={19} />} busy={busy === "preview" || collecting} disabled={!mutable || !!formError || sending || collecting}
          title={formError || "保存当前设置并生成预览，不会发送"} onClick={() => void generate()}>生成预览</Button>
        <Button variant="primary" icon={<Send size={20} />} busy={busy === "push"} disabled={!mutable || !!pushReason || sending}
          title={pushReason || `向 ${draft.recipients.length} 个目标推送 ${edition?.articles.length ?? 0} 条资讯`} onClick={() => void push()}>立即推送</Button>
      </div>
      {(pushReason || formError) && <p className="news-action-hint"><Info size={14} />{formError || pushReason}</p>}
    </footer>
  </div>;
}

function DeliveryReceipts({ deliveries }: { deliveries: NewsDelivery[] }) {
  return <div className="news-deliveries" aria-label="各目标推送回执"><h3><Send size={16} />目标回执</h3>
    {deliveries.map(delivery => <div className="news-delivery" key={`${delivery.session}-${delivery.scope}`}>
      <div className="news-delivery-heading"><span><UsersRound size={15} />{delivery.name || "目标会话"}</span>
        <span className={`news-receipt-state state-${delivery.state}`}>{labelFor(delivery.state)}</span></div>
      {delivery.reason && <p>{delivery.reason}</p>}
      {!!delivery.parts.length && <ol>{delivery.parts.map((part, index) => <li key={part.requestId || index}>
        <span>第 {index + 1} 段</span><strong className={`state-${part.state}`}>{labelFor(part.state)}</strong>{part.reason && <span>{part.reason}</span>}
      </li>)}</ol>}
    </div>)}
  </div>;
}
