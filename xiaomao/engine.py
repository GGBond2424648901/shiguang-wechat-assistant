"""串行观察与回复状态机，暂停标志独立于网络请求。"""
from collections import deque, OrderedDict
from dataclasses import replace
from concurrent.futures import Future
import threading
import time
import uuid
import re
import logging
import random
from .config import Settings
from .dafeiyu import formatted_reply, split_reply
from .domain import Frame, Message, ReplyTask, normalized
from .policy import TriggerPolicy, clean_reply
from .tracker import SequenceTracker, AmbiguousFrame
from .store import StateStore
from .model import ModelError
from .windows import DesktopUnavailable, DesktopTemporarilyHidden, DesktopInputNotStarted, StickerClipboardBlocked


_STICKER_CLIPBOARD_WAIT={
    'rich':'表情待发：剪贴板含需保留的富文本或图元；剪贴板内容变化后自动继续',
    'busy':'表情待发：剪贴板暂被占用，正在后台等待',
}


def readback_key(text: str) -> str:
    text=''.join(normalized(text).split())
    return re.sub(r'(?<=[\u3400-\u9fff])—+(?=[\u3400-\u9fff])','',text)


def bubble_matches(task, text, *, visual=False):
    # Whitespace belongs to the generated text in a split or formatted
    # paste. Only the Windows line-ending representation may differ.
    if (task.remaining_text or task.continuation_of or formatted_reply(task.reply)
            or task.reply.strip()!=task.reply or re.search(r'[\t\n]|\s{2,}',task.reply)):
        return text.replace('\r\n','\n').replace('\r','\n')==task.reply
    key=readback_key if visual else normalized
    return key(text)==key(task.reply)


_SEND_WAITS = {
    'foreground':'等待微信自然回到前台，继续后台接收',
    'desktop_busy':'等待电脑空闲，键盘或鼠标仍在使用',
    'paced':'等待本条回复的短暂停顿',
    'interval':'等待与上一次发送的间隔',
    'minute_limit':'等待分钟发送额度恢复',
    'hourly_limit':'等待小时发送额度恢复',
    'draft':'等待确认微信输入框为空，保留现有内容',
    'window':'等待目标微信窗口可安全输入',
    'window_turn':'等待共用微信窗口完成上一会话的发送',
    'clipboard':'等待剪贴板可用，尚未输入',
    'sticker_draft':'表情草稿保留中，等待空闲后核对；继续接收新消息',
    'sticker_space':'等待表情编辑区完整展开，尚未粘贴；继续接收新消息',
}


class Engine:
    def __init__(self, settings: Settings, store: StateStore, desktop, vision, model, *, clock=time.time, settle_seconds=5,message_source=None,recovered_unknown=None):
        self.settings, self.store = settings, store
        self.desktop, self.vision, self.model = desktop, vision, model
        self.message_source=message_source
        from .stickers import StickerLibrary
        self.stickers=StickerLibrary(settings.root)
        self.received_ids=set()
        self.last_arrival_id=''
        self.source_failures=0
        self.source_rebaseline=False
        self.receive_cutoff=clock()
        self.clock, self.settle_seconds = clock, settle_seconds
        self.lock = threading.RLock()
        self.work_lock = threading.Lock()
        self.window_turn=None
        self.cancelled = threading.Event()
        self.stop_event = threading.Event()
        if hasattr(desktop, "cancelled"):
            desktop.cancelled = self.cancelled
        self.mode, self.reason = "observe", "等待校准或观察目标群"
        self.tracker = SequenceTracker()
        self.policy = TriggerPolicy(settings.assistant_name, settings.at_display_name, settings.wake_aliases,settings.reply_mode)
        self.context = deque(maxlen=settings.context_limit or 1)
        self.memory_pending=[]
        self.media_refs=OrderedDict()
        self.next_compaction_at=0
        self._compaction_future=None
        self._compaction_thread=None
        self._compaction_plan=None
        self._compaction_epoch=None
        self._compaction_failures=0
        self.legacy_context=[]
        self.memory=None
        if settings.context_limit:
            from .memory import ConversationMemory,MemoryPersistenceError
            try:self.memory=ConversationMemory(settings.runtime,settings.memory_scope,clock,settings.context_limit)
            except MemoryPersistenceError:logging.getLogger(__name__).warning('群记忆暂不可用，继续保留当前会话')
            recent=self.memory.messages if self.memory and (self.memory.messages or self.memory.summary) else (
                store.load_context(settings.group,clock()) if settings.memory_scope==settings.group else [])
            self.context.extend(recent[-settings.context_limit:])
            if self.memory and not self.memory.messages and not self.memory.summary:
                self.legacy_context=list(recent)
        self.tasks: list[ReplyTask] = []
        self.epoch = uuid.uuid4().hex
        self.ready_at = clock() + settle_seconds
        self.last_observed = None
        self.last_result = None
        self.last_input_generation = None
        self.last_frame_keys = None
        self.hidden_since = None
        self.hidden_last_clock = None
        self.hidden_reset_required = False
        self.sequence = 0
        self.latest_requested=False
        self.latest_candidate=None
        self.bottom_recovery_attempts=0
        self.bottom_retry_at=0
        self.baseline_position_checked=False
        if (store.recover() if recovered_unknown is None else recovered_unknown):
            self.mode, self.reason = "paused", "存在发送结果未知的记录，请核实微信后重新观察"
            self.cancelled.set()
        elif settings.is_conversation_paused():
            self.mode,self.reason='paused','本会话已暂停'
            self.cancelled.set()
        elif settings.auto_on_launch and settings.model_ready and settings.binding and settings.binding.bottom_confirmed:
            self.mode,self.reason='auto','工具已启动，正在建立后台监听基线'
        self.store.prune(clock())
        self.companion=None
        if self.memory and hasattr(model,'proactive'):
            from .companion_runtime import CompanionRuntime
            try:
                self.companion=CompanionRuntime(settings,store,model,clock)
                self.companion.proactive_generate=self._generate_proactive
                for message in self.memory.messages:self.companion.observe(message,baseline=True)
            except Exception:
                if self.companion:self.companion.close()
                self.companion=None
                logging.getLogger(__name__).warning('陪伴功能暂不可用，普通回复继续')

    def _companion_call(self,method,*args,**kwargs):
        if not self.companion:return
        try:return getattr(self.companion,method)(*args,**kwargs)
        except Exception:
            self.companion.error='陪伴状态暂未保存，主动功能等待恢复；普通回复继续'
            self.companion.planner.preferences=replace(self.companion.planner.preferences,enabled=False)
            logging.getLogger(__name__).warning('可选陪伴状态未更新')

    def _cancel_tasks(self):
        for task in self.tasks:
            if task.state in ("sending", "awaiting_receipt", "unknown"):
                task.state = "unknown"
                self.store.update(task.id, "unknown", "uncertain_send")
            else:
                task.state = "skipped"
                self.store.update(task.id, "skipped", "paused")
            if task.proactive:
                outcome='partial' if task.state=='skipped' and task.continuation_of else task.state
                self._companion_call('finish',task.proactive,outcome)
            task.messages.clear()
            task.context.clear()
            task.reply = ""
            task.raw_bubble=None
            task.remaining_stickers.clear()
            task.remaining_text=();task.media_first=False
            task.remaining_text=()
        self.tasks.clear()

    def pause(self, reason="用户已暂停",*,preserve_companion=False) -> dict:
        self.cancelled.set()
        with self.lock:
            self.mode, self.reason = "paused", reason
            self.epoch = uuid.uuid4().hex
            self.hidden_since=None
            self.hidden_last_clock=None
            self.hidden_reset_required=False
            self._cancel_tasks()
            self._companion_call('suspend',preserve_timers=True) if preserve_companion else self._companion_call('suspend')
            self.latest_requested=False
            self.latest_candidate=None
            self.bottom_recovery_attempts=0
            self.bottom_retry_at=0
        return self.status()

    def observe(self,*,preserve_companion=False) -> dict:
        self.pause("正在切换到观察模式",preserve_companion=preserve_companion)
        with self.lock:
            self.mode, self.reason = "observe", "正在后台观察目标群，首帧仅建立基线"
            self.tracker = SequenceTracker()
            self.source_rebaseline=True
            self.receive_cutoff=self.clock()
            if self.message_source:
                self.last_observed=None
                self.source_failures=0
            self.baseline_position_checked=False
            self.last_input_generation = None
            self.last_frame_keys = None
            self.ready_at = self.clock() + self.settle_seconds
            self.epoch = uuid.uuid4().hex
            self.cancelled.clear()
        return self.status()

    def enable(self) -> dict:
        if not self.settings.binding or not self.settings.binding.bottom_confirmed:
            raise ValueError("请先校准窗口并确认消息列表位于底部")
        if not self.settings.model_ready:
            raise ValueError("请先配置模型及有效的加密凭据")
        self.observe(preserve_companion=True)
        with self.lock:
            self.mode, self.reason = "auto", "正在开启后台监听，微信在前台且空闲时发送"
        return self.status()

    def stop(self,*,preserve_companion=False) -> dict:
        if self.mode=='stopped':return self.status()
        self.pause("助手已停止",preserve_companion=preserve_companion)
        with self.lock:
            self.mode = "stopped"
        self.stop_event.set()
        if self.companion:self.companion.close()
        return self.status()

    def reply_latest(self) -> dict:
        with self.lock:
            if self.mode!='auto' or self.cancelled.is_set():
                raise ValueError('请先开启自动回复，再处理当前最新点名')
            self.latest_requested=True
            self.latest_candidate=None
            self.latest_requested_at=self.clock()
            self.reason='正在连续核对当前最后一条点名'
        return self.status()

    def _recover_latest(self,frame):
        if not self.latest_requested:return
        age=self.clock()-self.latest_requested_at
        if age<0 or age>self.settings.expiry_seconds:
            self.latest_requested=False
            self.reason='最新点名恢复请求已过期'
            return
        signature=tuple(m.key for m in frame.messages)
        if self.latest_candidate!=signature:
            self.latest_candidate=signature
            return
        self.latest_requested=False
        last=frame.messages[-1] if frame.messages else None
        if last is None or not self.policy.matches(last):
            self.reason='最后一条不是可回复的完整入站点名'
            return
        if any(last.key==m.key for task in self.tasks for m in task.messages):return
        if not self.store.claim_latest(last.key,self.clock(),group=self.settings.memory_scope,legacy_group=self.settings.group):
            self.reason='这条最新点名已处理过，不重复认领'
            return
        self._record([last])

    def status(self) -> dict:
        with self.lock:
            return {"mode": self.mode, "group": self.settings.group, "assistant": self.settings.assistant_name,
                    "reason": self.reason, "model_configured": self.settings.model_ready,
                    "model": self.settings.model_name, "at_enabled": bool(self.settings.at_display_name), "at_note": self.settings.at_note,
                    "model_health":self.model.health_snapshot() if hasattr(self.model,'health_snapshot') else {},
                    "model_usage":self.model.usage_snapshot() if hasattr(self.model,'usage_snapshot') else {},
                    "calibrated": self.settings.binding is not None, "pending_count": len(self.tasks),
                    "counts": self.store.counts(), "last_observed": self.last_observed,
                    "last_result":dict(self.last_result) if self.last_result else None,
                    "auto_on_launch":self.settings.auto_on_launch,"capture_mode":"background",
                    "reply_mode":self.settings.reply_mode,
                    "split_replies":self.settings.split_replies,
                    "split_replies_effective":self.settings.split_replies and self.message_source is not None,
                    "stickers_enabled":self.settings.stickers_enabled,"sticker_count":len(self.stickers.items),"sticker_preference":getattr(self.stickers,'preference_label',''),
                    "context_count":len(self.context),
                    "memory_count":len(self.memory.messages) if self.memory else len(self.context),
                    "memory_summary_chars":len(self.memory.summary) if self.memory else 0,
                    "natural_pacing":self.settings.natural_pacing,
                    "send_interval_range":[self.settings.min_interval,self.settings.min_interval+(self.settings.pacing_jitter if self.settings.natural_pacing else 0)],
                    "minute_limit":self.settings.minute_limit if self.settings.natural_pacing else 0,"hourly_limit":self.settings.hourly_limit,
                    "receive_mode":"database" if self.message_source else "screen",
                    "receive_retrying":bool(self.message_source and self.source_failures),
                    "companion":self.companion.status() if self.companion else {'enabled':False,'reason':'陪伴模块暂不可用'},
                    "data_destination": self.settings.model_base_url.split("/", 3)[:3][-1] if self.settings.model_base_url else ""}

    def pending(self) -> list[dict]:
        with self.lock:
            now = self.clock()
            rows=[]
            for task in self.tasks:
                if not task.created<=now<=self._deadline(task):continue
                progress={'staged':'正在核对输入内容','sending':'已开始发送','awaiting_receipt':'等待微信回执',
                          'generated':'已准备，等待发送条件','unknown':'发送结果待核对'}.get(task.state)
                if task.sticker_clipboard_wait:progress=_STICKER_CLIPBOARD_WAIT[task.sticker_clipboard_wait]
                elif task.draft_waiting:progress=task.wait_detail or _SEND_WAITS['sticker_draft']
                elif task.state=='generated' and task.wait_reason:progress=task.wait_detail or _SEND_WAITS[task.wait_reason]
                if not progress:
                    if task.media_attempts and task.retry_at>now:progress='等待微信图片加载'
                    elif task.model_attempts:progress='正在准备回复'
                    else:progress='已入队，等待处理'
                rows.append({'id':task.id,'sender':task.sender,'sender_name':self.memory.member_names().get(task.sender,'') if self.memory else '',
                             'messages':[m.text for m in task.messages],
                             'reply':task.reply,'state':task.state,'progress':progress})
            return rows[:20]

    def _frame(self,observation=False) -> Frame:
        binding = self.settings.binding
        if not binding:
            raise ValueError("尚未校准微信窗口")
        image=self.desktop.capture()
        binding=getattr(self.desktop,'calibration',None) or binding
        if self.message_source and hasattr(self.message_source,'assert_identity'):
            # Database identity proves uniqueness even if a long sidebar label
            # is clipped. The full chat header below must still match exactly.
            self.message_source.assert_identity()
        else:
            self.desktop.validate_target(image)
        reader=getattr(self.vision,'read_controls',self.vision.read) if self.message_source else self.vision.read
        read_bound = getattr(self.desktop, 'read_bound_frame', None)
        if read_bound:
            frame = read_bound(image, reader, getattr(self.vision, 'ocr', None))
            binding = self.desktop.calibration
        else:
            frame = reader(image, binding)
        if frame.safe and frame.at_bottom and frame.group==self.settings.group and not frame.draft and not frame.draft_empty:
            confirm=getattr(self.desktop,'confirm_empty_editor',None)
            painted=confirm(image,self.vision) if confirm else None
            if painted is not None:
                native=getattr(self.vision,'read_controls',self.vision.read)(painted,binding)
                if native.safe and native.group==frame.group and native.draft_empty:
                    # Keep all history and navigation evidence from the
                    # visible desktop. Only the editor has two witnesses.
                    frame=replace(frame,draft_empty=True)
        if observation and not frame.safe and frame.reason=='群标题与已校准目标不一致':
            if binding.window_kind=='detached':
                raise DesktopTemporarilyHidden('独立窗口标题与已绑定会话不符，等待重新核对')
            selected=self.desktop.select_target(self.settings.idle_seconds)
            if selected and self.window_turn and self.message_source:
                self.window_turn(self,claim=True)
            raise DesktopTemporarilyHidden('已自动选择目标群，等待稳定画面' if selected else '微信已连接，等待空闲后定位所选群；当前聊天标题尚未匹配')
        if observation and frame.safe and frame.group==self.settings.group and not frame.draft_empty and self.message_source:
            raise DesktopTemporarilyHidden('微信输入框有草稿，等待处理后自动继续；仍在接收消息')
        if observation and frame.safe and frame.group==self.settings.group and not frame.draft_empty:
            raise ValueError('输入框已有草稿或内容不明确，请手动处理后继续')
        if observation and not self.message_source and frame.safe and frame.group==self.settings.group and not self.baseline_position_checked:
            # 微信失焦时隐藏滚动滑块，背景截图不能证明首帧已在最新位置。
            # 仅首次基线空闲后普通抬窗核验；后续仍持续后台接收。
            if not self.desktop.prepare_baseline(self.settings.idle_seconds):
                raise DesktopTemporarilyHidden('正在核验最新位置并建立基线')
            self.baseline_position_checked=True
            return self._frame(observation=True)
        if observation and frame.safe and frame.group==self.settings.group and not frame.at_bottom:
            if self.bottom_recovery_attempts>=3:
                # A transient scroll/layout failure must not latch forever.
                # Keep collecting new messages, then retry navigation under
                # the same identity, empty-draft and human-input guards.
                now=self.clock()
                if not self.bottom_retry_at or self.bottom_retry_at-now>30:
                    self.bottom_retry_at=now+30
                if now<self.bottom_retry_at:
                    raise DesktopTemporarilyHidden('最新消息位置尚未确认，稍后自动重新定位；仍在后台接收')
                self.bottom_recovery_attempts=0
                self.bottom_retry_at=0
            if self.desktop.go_to_latest(self.settings.idle_seconds):
                with self.lock:
                    self.bottom_recovery_attempts+=1
                    if not self.message_source:
                        self._cancel_tasks();self.context.clear();self.tracker=SequenceTracker()
                        self.last_input_generation=self.last_frame_keys=self.last_observed=None
                        self.ready_at=self.clock()+self.settle_seconds
                raise DesktopTemporarilyHidden('已定位最新消息，正在建立新基线，不补发历史')
            raise DesktopTemporarilyHidden('等待空闲后定位微信群最新消息')
        if not frame.safe or frame.group != self.settings.group or not frame.at_bottom:
            raise ValueError(frame.reason or "目标群或消息底部无法确认")
        self.bottom_recovery_attempts=0
        self.bottom_retry_at=0
        return frame

    def _active(self, task: ReplyTask) -> bool:
        with self.lock:
            if self.mode!='auto' or self.cancelled.is_set() or task not in self.tasks:return False
            if task.proactive and task.state not in ('sending','awaiting_receipt') and (not self.companion or
                    not self.companion.valid(task.proactive,continuing=bool(task.continuation_of))):
                if task.draft_waiting:self._retire(task,'skipped','draft_expired')
                elif task.state=='staged':self.pause('主动话题已失效，已保留输入草稿且不会发送')
                else:self._retire(task,'skipped','proactive_expired')
                return False
            if task.created<=self.clock()<=self._deadline(task):return True
            if task.state in ('staged','sending'):
                if task.draft_waiting:self._retire(task,'skipped','draft_expired')
                else:self.pause('输入或发送过程超过时效，已保留草稿且不会重发')
            return False

    def _retire(self, task: ReplyTask, state: str, reason=""):
        with self.lock:
            if reason in ('expired','insufficient_input_time'):
                if task.sticker_clipboard_wait:reason='sticker_clipboard_'+task.sticker_clipboard_wait
                elif task.wait_reason:reason='expired_'+task.wait_reason
            task.state = state
            self.store.update(task.id, state, reason)
            if state=='verified' and hasattr(self.desktop,'finish_input'):
                self.desktop.finish_input()
            labels={'model_format_failed':'未发送：模型回复格式连续异常',
                    'model_unavailable':'未发送：模型服务连续失败',
                    'participation_declined':'自然参与：这条消息选择不插话',
                    'media_followup':'这位成员接着发了文字，图片已作为新消息的上下文',
                    'media_unavailable':'表情未发送：当前素材或预览不可用',
                    'sticker_clipboard_rich':'表情到期未发：保留了原剪贴板的富文本或图元',
                    'sticker_clipboard_busy':'表情到期未发：剪贴板暂不可用',
                    'expired':'未发送：等待超过时限',
                    'insufficient_input_time':'未发送：等待超过安全输入时限',
                    'uncertain_send':'发送结果未知，未重发'}
            labels.update(draft_expired='表情草稿未能在时限内核对，已保留且未发送',
                          proactive_expired='主动消息已失效，未发送',
                          draft_removed='表情草稿已被清空，未重新粘贴或发送')
            self.last_result={'state':state,'reason':reason,'at':self.clock(),'group':self.settings.group,
                              'summary':'上一条回复已确认发送' if state=='verified' else
                              ('到期未发送：'+_SEND_WAITS[task.wait_reason] if reason=='expired_'+task.wait_reason
                               else labels.get(reason,'未发送：本条任务已取消'))}
            if task.continuation_of and state=='skipped':
                self.last_result['summary']='前面的消息已确认；'+self.last_result['summary']
            if self.companion:
                if task.proactive:
                    if state=='verified' and (task.remaining_text or task.remaining_stickers):
                        self._companion_call('progress',task.proactive)
                    else:
                        outcome='partial' if state=='skipped' and task.continuation_of else state
                        detail=self.last_result['summary'] if state=='skipped' else ''
                        self._companion_call('finish',task.proactive,outcome,detail=detail)
                elif state=='verified' and not task.continuation_of:self._companion_call('replied',task.id,self.clock())
            if task in self.tasks:
                self.tasks.remove(task)
            task.messages.clear()
            task.context.clear()
            task.reply = ""
            task.remaining_stickers.clear()
            task.remaining_text=()

    def _deadline(self,task):
        base=task.created+self.settings.expiry_seconds
        if task.proactive:
            # A routine owns its due window. Other proactive drafts can expire
            # sooner than a regular reply if generation used part of their time.
            return task.proactive.expires if task.proactive.kind=='routine' else min(base,task.proactive.expires)
        if task.idle_deadline and (task.state in ('staged','sending','awaiting_receipt') or
                (task.messages and self.last_arrival_id==task.messages[-1].message_id)):
            return task.idle_deadline
        return base

    def _waiting(self,task,reason):
        task.wait_reason=reason
        task.wait_detail=''
        self.reason=_SEND_WAITS[reason]
        # An accepted text may wait one extra freshness interval for the user
        # to finish typing. Never extend a draft, a partial delivery or an
        # already expired request. Newer traffic immediately ends this grace.
        base=task.created+self.settings.expiry_seconds
        if (reason in ('desktop_busy','foreground') and self.message_source and task.state=='generated'
                and task.reply and not (task.sticker or task.remaining_stickers or task.continuation_of or task.proactive)
                and task.messages and all(m.kind=='text' for m in task.messages)
                and task.messages[-1].message_id and task.messages[-1].message_id==self.last_arrival_id
                and task.created<=self.clock()<base):
            task.idle_deadline=base+self.settings.expiry_seconds

    def _ready_for_input(self,task):
        with self.lock:
            if not self._active(task):return False
            # Do not start a clipboard/edit/readback transaction at the edge
            # of the arrival deadline. This reserve adds no wait and grants no
            # extension to an edit already started; those still fail closed.
            if self._deadline(task)-self.clock()<5:
                self._retire(task,'skipped','insufficient_input_time')
                self.reason='本条等待过久，已在输入前跳过；继续监听'
                return False
            return True

    def _participation_pending(self,task):
        return (self.settings.reply_mode=='natural' and task.state=='candidate' and not task.reply
                and not any(self.policy.addressed(m) for m in task.messages))

    def _wait_for_sticker_clipboard(self,task,reason):
        with self.lock:
            if self._active(task):
                task.sticker_clipboard_wait=reason
                task.retry_at=self.clock()+5
                self.reason=_STICKER_CLIPBOARD_WAIT[reason]

    def _next_task(self):
        with self.lock:
            if not self.tasks:return None
            # An in-flight editor transaction or receipt always owns the queue.
            protected=next((t for t in self.tasks if t.state in ('staged','sending','awaiting_receipt','unknown')),None)
            if protected:return protected
            now=self.clock()
            waiting=[t for t in self.tasks if t.sticker_clipboard_wait]
            if any(t.retry_at<=now for t in waiting):
                # One read-only format probe covers this group's waiting GIFs.
                reason=self.desktop.sticker_clipboard_status()
                for task in waiting:
                    task.sticker_clipboard_wait=reason
                    task.retry_at=now+5 if reason else 0
            candidates=[t for t in self.tasks if not t.sticker_clipboard_wait]
            if not candidates:return min(waiting,key=lambda t:t.retry_at)
            if self.settings.reply_mode!='natural':return candidates[0]
            addressed=next((t for t in candidates if any(self.policy.addressed(m) for m in t.messages)),None)
            if addressed:return addressed
            from .stickers import named_catalog_order
            return next((t for t in candidates if named_catalog_order(t.messages)),candidates[0])

    def _can_merge(self, previous_id, message, speaker, now):
        if not speaker or not self.tasks:return False
        last=self.tasks[-1]
        if (last.sender!=speaker or last.state!='candidate' or last.model_attempts or last.reply
                or not 0<=now-last.last_added<=self.settings.merge_seconds):return False
        if self.settings.reply_mode=='mentions':return True
        if self.settings.reply_mode!='natural' or not re.fullmatch(r'p_[a-f0-9]{16}',message.speaker_id):return False
        previous=last.messages[-1]
        return (bool(previous_id) and previous.message_id==previous_id
                and (not previous.created_at or not message.created_at
                     or 0<=message.created_at-previous.created_at<=self.settings.merge_seconds))

    def _record(self, messages: list[Message]):
        now = self.clock()
        with self.lock:
            for message in messages:
                if message.message_id:
                    if message.message_id in self.received_ids:continue
                    self.received_ids.add(message.message_id)
                previous_id,self.last_arrival_id=self.last_arrival_id,message.message_id
                self._remember(message)
                if message.message_id and message.created_at and (message.created_at<int(self.receive_cutoff)
                        or now-message.created_at>self.settings.expiry_seconds):continue
                if self.companion:
                    self._companion_call('observe',message)
                    if message.direction=='in':
                        for pending in list(self.tasks):
                            if pending.proactive and pending.state in ('candidate','generated'):
                                self._retire(pending,'skipped','new_input')
                if not self.policy.matches(message):
                    continue
                speaker=message.speaker_id or message.sender
                if self._can_merge(previous_id,message,speaker,now):
                    task = self.tasks[-1]
                    task.messages.append(message)
                    task.last_added = now
                    continue
                self.sequence += 1
                identity=message.message_id or f"{self.epoch}:{self.sequence}:{message.key}"
                scope = self.settings.memory_scope
                legacy = self.store.fingerprint(f'{self.settings.group}:{identity}') if scope != self.settings.group else None
                task_id = self.store.create_unique(self.store.fingerprint(f'{scope}:{identity}'),now,legacy_digest=legacy)
                if task_id is None:continue
                if (self.settings.reply_mode=='natural' and message.kind=='text' and previous_id
                        and re.fullmatch(r'p_[a-f0-9]{16}',message.speaker_id)):
                    for older in list(self.tasks):
                        if (older.sender==speaker and older.state in ('candidate','generated')
                                and not older.proactive and not older.continuation_of and older.messages
                                and older.messages[-1].message_id==previous_id
                                and all(m.kind in ('image','emoji') for m in older.messages)):
                            # Preserve the picture for a question about it, but don't
                            # send a separate reaction after the member has moved on.
                            self._retire(older,'skipped','media_followup')
                            break
                if len(self.tasks)>=20 and self.settings.reply_mode=='natural' and self.policy.addressed(message):
                    replaceable=next((t for t in self.tasks if self._participation_pending(t) and not t.model_attempts),None)
                    if replaceable:self._retire(replaceable,'skipped','addressed_priority')
                if len(self.tasks) >= 20:
                    self.store.update(task_id, "skipped", "queue_full")
                    continue
                created=min(now,message.created_at) if message.message_id and message.created_at else now
                self.tasks.append(ReplyTask(task_id, created, now, speaker, [message],context=self._context_snapshot()))

    def _context_snapshot(self):
        if not self.memory:return list(self.context)
        return list(self.legacy_context)+self.memory.context()+list(self.memory_pending)

    def _compaction_tick(self):
        with self.lock:
            if self._compaction_future is not None:
                if not self._compaction_future.done():return
                future,plan,epoch=self._compaction_future,self._compaction_plan,self._compaction_epoch
                self._compaction_future=self._compaction_plan=self._compaction_epoch=None
                if self.mode!='auto' or self.cancelled.is_set() or epoch!=self.epoch:
                    self.next_compaction_at=self.clock()+30
                    return
                queued=[(task,self._reply_context(task)) for task in self.tasks if not task.reply]
                if self.memory.commit_compaction(plan,future.result()):
                    self._compaction_failures=0
                    # Pin later verified receipts before their raw rows leave
                    # memory; every task still keeps its own arrival snapshot.
                    for task,context in queued:task.context=context
                    # Read the current memory, never the pre-network tail: new
                    # arrivals and enriched receipts may have been appended.
                    current=[m for m in self.memory.context() if m.kind!='summary']+list(self.memory_pending)
                    self.context.clear();self.context.extend(current[-self.settings.context_limit:])
                    self.next_compaction_at=self.clock()+30
                else:
                    self._compaction_failures+=1
                    self.next_compaction_at=self.clock()+min(600,30*2**min(self._compaction_failures,5))
                return
            if (self.mode!='auto' or self.cancelled.is_set() or not self.memory or self.memory_pending
                    or not hasattr(self.model,'compact') or self.clock()<self.next_compaction_at):return
            plan=self.memory.prepare_compaction()
            if plan is None:
                self.next_compaction_at=self.clock()+30
                return
            future=Future()
            self._compaction_future,self._compaction_plan,self._compaction_epoch=future,plan,self.epoch
            def calculate(model,material,result):
                # Background work never writes memory, touches SQLite/desktop,
                # or acquires the Engine lock. Stop need not join the network.
                try:summary=model.compact(material.previous,list(material.older))
                except Exception:summary=None
                result.set_result(summary)
            self._compaction_thread=threading.Thread(target=calculate,args=(self.model,plan,future),
                name='xiaomao-memory',daemon=True)
            self._compaction_thread.start()

    def _reply_context(self,task):
        target=list(task.messages)
        if not self.settings.context_limit:return target
        target_ids={m.message_id for m in target if m.message_id}
        context=[];seen=set()
        for message in task.context:
            if message.message_id:
                if message.message_id in target_ids or message.message_id in seen:continue
                seen.add(message.message_id)
            context.append(message)
        # Screen/legacy rows lack stable IDs. Only remove the matching trailing
        # target prefix; a mention task may have gained messages after its snapshot.
        anonymous=[m for m in target if not m.message_id]
        def signature(message):
            return (message.text,message.direction,message.kind,message.speaker_id,message.created_at)
        for count in range(min(len(context),len(anonymous)),0,-1):
            if [signature(m) for m in context[-count:]]==[signature(m) for m in anonymous[:count]]:
                context=context[:-count]
                break
        if self.message_source:
            # Receipts can arrive in the same DB second as the triggering input,
            # or leave the small display window during a burst. Their stable
            # source relationship, not a timestamp/window, determines relevance.
            latest=self._context_snapshot()
            outgoing={m.message_id:m for m in latest if m.direction=='out' and m.message_id}
            context=[outgoing.get(m.message_id,m) if m.direction=='out' else m for m in context]
            sources={m.message_id for m in context if m.direction=='in' and m.message_id}
            for message in outgoing.values():
                if message.message_id not in seen and message.reply_to_id in sources:
                    context.append(message)
                    seen.add(message.message_id)
        latest=target[-1] if target else None
        if (self.message_source and latest and latest.kind=='text' and latest.speaker_id
                and re.search(r'(?:没(?:有)?|不|漏)(?:给我)?(?:回|答)|(?:前|上)一[条句].{0,5}回了吗',latest.text)):
            for i in range(len(context)-1,-1,-1):
                prior=context[i]
                if prior.direction!='in' or prior.speaker_id!=latest.speaker_id:continue
                if prior.kind=='text' and prior.message_id:
                    state=self.store.delivery_status(self.settings.memory_scope,prior.message_id)
                    if state:context[i]=replace(prior,delivery_status=state)
                break
        return context+target

    def _remember(self,message):
        if not self.settings.context_limit or message.kind not in ('text','image','emoji') or message.quoted or message.quality<.92:return
        if message.message_id and message.media_raw:
            self.media_refs[message.message_id]=message
            self.media_refs.move_to_end(message.message_id)
            while len(self.media_refs)>96:self.media_refs.popitem(last=False)
        from .member_names import trusted_display_name
        name=trusted_display_name(message)
        item=replace(message,sender=name,sender_source=message.sender_source if name else '',media_raw='',media_key='')
        if not message.message_id and self.context and self.context[-1].key==item.key:return
        if self.memory:
            try:
                # A temporary disk error must not erase messages when saving
                # succeeds again. Flush the retained RAM backlog in order.
                while self.memory_pending:
                    self.memory.remember(self.memory_pending[0]);self.memory_pending.pop(0)
                self.memory.remember(item)
                self.context.clear();self.context.extend(self.memory.messages[-self.settings.context_limit:])
                return
            except Exception:
                logging.getLogger(__name__).warning('会话加密保存失败，仅保留当前内存上下文')
                if not any(old.message_id==item.message_id and old.image_data==item.image_data for old in self.memory_pending) or not item.message_id:
                    self.memory_pending.append(item)
        self.context.append(item)

    def _seed_context(self,messages):
        if self.companion:
            for message in messages:self._companion_call('observe',message,baseline=True)
        if self.memory:
            # Old versions stored no IDs. Prefer authenticated baseline rows
            # for the overlap, preserving only older unavailable legacy text.
            incoming={(m.kind,m.direction,normalized(m.text)) for m in messages}
            legacy,self.legacy_context=self.legacy_context,[]
            for old in legacy:
                if (old.kind,old.direction,normalized(old.text)) not in incoming:self._remember(old)
            # Same stable IDs survive restart; never wipe the compacted summary.
            for message in messages:self._remember(message)
            return
        if self.settings.context_limit and not self.context and self.settings.memory_scope==self.settings.group:
            self.context.extend(self.store.load_context(self.settings.group,self.clock())[-self.settings.context_limit:])
        visible=[m for m in messages if m.kind=='text' and not m.quoted and m.quality>=.92]
        cut=0
        if self.context:
            last=self.context[-1]
            cut=next((i+1 for i in range(len(visible)-1,-1,-1) if visible[i].text==last.text and visible[i].direction==last.direction),0)
        for m in visible[cut:]:self._remember(m)

    def companion_preview(self):
        with self.lock:
            if not self.companion:raise ValueError('陪伴模块暂不可用')
            self.companion.preview(self._context_snapshot(),self.epoch)
        return self.status()

    def companion_once(self):
        with self.lock:
            if self.mode!='auto' or self.cancelled.is_set():raise ValueError('请先开启自动回复')
            if self.tasks or self.store.counts().get('unknown',0):raise ValueError('仍有待处理或结果未知的发送任务')
            if not self.settings.conversation_targets.get(self.settings.group):raise ValueError('请先绑定准确的回复对象')
            if not self.companion:raise ValueError('陪伴模块暂不可用')
            self.companion.request_once(self.epoch)
        return self.status()

    def _proactive_sticker_ready(self):
        return (self.settings.stickers_enabled and self.message_source is not None
            and hasattr(self.desktop,'type_sticker') and hasattr(self.desktop,'verify_sticker_input')
            and self.store.sticker_ready(self.settings.memory_scope,self.clock(),self.settings.sticker_frequency))

    def _generate_proactive(self,context,candidate,profile):
        if not hasattr(self.model,'proactive_plan'):
            return self.model.proactive(context,candidate,profile)
        choices=[]
        if self._proactive_sticker_ready():
            selection_context=context
            if candidate.kind=='routine' and candidate.label:
                # The owner's scheduled intent ranks reactions; it is not an
                # invented inbound message or an addition to chat memory.
                selection_context=[*context,Message(candidate.label,kind='context')]
            choices=self.stickers.candidates(selection_context,excluded=self.store.recent_stickers(self.settings.memory_scope,self.clock()))
        return self.model.proactive_plan(context,candidate,profile,choices=choices)

    def _companion_tick(self, *, source_wait=''):
        if not self.companion:return
        try:
            with self.lock:
                blocked_reason=''
                if self.mode!='auto' or self.cancelled.is_set():
                    blocked_reason='仅观察，未开启自动回复' if self.mode=='observe' else '本会话已暂停'
                elif not self.settings.conversation_targets.get(self.settings.group):
                    blocked_reason='尚未绑定准确的回复对象'
                waiting_reason=''
                if not blocked_reason:
                    waiting_reason=source_wait or ('等待当前发送任务完成' if self.tasks else '')
                result=self.companion.tick(self._context_snapshot(),epoch=self.epoch,
                    eligible=not (blocked_reason or waiting_reason),memory=self.memory,
                    waiting_reason=waiting_reason,blocked_reason=blocked_reason)
                if result:
                    candidate,reply=result
                    from .stickers import ReplyPlan,StickerRequest,StickerUnavailable
                    prepared=[]
                    if isinstance(reply,ReplyPlan):
                        plan=reply
                        reply=clean_reply(plan.text) if plan.text else ''
                        if plan.sticker and self._proactive_sticker_ready():
                            self.stickers.refresh()
                            try:prepared=self._prepare_sticker_plan(plan,self.stickers.items,StickerRequest())
                            except (StickerUnavailable,OSError):pass  # Retain a usable text opening.
                        if not reply and not prepared:
                            self.companion.finish(candidate,'skipped',detail='未生成可发送的文字或表情')
                            return
                    # Scheduling/generation time is distinct from the send queue.
                    # The candidate still retains its original expiry/revision guard.
                    queued_at=self.clock()
                    identity=self.store.create(self.store.fingerprint('proactive:'+candidate.id),queued_at)
                    self.tasks.append(ReplyTask(identity,queued_at,queued_at,'',reply=reply,
                        context=self._context_snapshot(),proactive=candidate,
                        sticker=prepared[0] if prepared and not reply else None,
                        remaining_stickers=prepared if reply else prepared[1:]))
                    if prepared and not reply:self.tasks[-1].reply=prepared[0].description
        except Exception:
            logging.getLogger(__name__).warning('可选陪伴任务未完成，普通回复继续')

    def _note_hidden_clock(self,now):
        if self.hidden_since is not None:
            if now<self.hidden_last_clock or now-self.hidden_since>=self.settings.expiry_seconds:
                self.hidden_reset_required=True
            self.hidden_last_clock=now

    def _expire_receipt(self):
        with self.lock:
            now=self.clock()
            for task in self.tasks:
                if task.state in ('sending','awaiting_receipt') and not 0<=now-task.sent_at<=30:
                    self._retire(task,'unknown','uncertain_send')
                    self.pause('发送结果未知，已暂停且不会重发；请检查微信')
                    return True
            return False

    def observe_once(self):
        if not self.work_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                if self.mode not in ("observe", "auto"):
                    self._companion_tick()
                    return
                now=self.clock()
                if self.message_source and self._expire_receipt():return
                # 隐藏期间一旦观察到倒退或超时就保留作废标记，不能因之后校时而复活。
                self._note_hidden_clock(now)
                if now < self.ready_at:
                    return
                epoch, tracker = self.epoch, self.tracker
            if self.message_source:
                self._observe_database(epoch)
                return
            try:
                try:
                    frame = self._frame(observation=True)
                except DesktopTemporarilyHidden as error:
                    with self.lock:
                        if epoch==self.epoch and not self.cancelled.is_set():
                            if self.hidden_since is None:
                                self.hidden_since=self.last_observed if self.last_observed is not None else self.clock()
                                self.hidden_last_clock=self.hidden_since
                            self._note_hidden_clock(self.clock())
                            self.reason='等待恢复：'+str(error)+'；工具正在自动重试'
                    return
                if not frame.draft_empty:
                    self.pause("输入框已有草稿或内容不明确，请手动处理后继续")
                    return
                generation=self.desktop.input_generation()
                keys=tuple(message.key for message in frame.messages)
                with self.lock:
                    if epoch != self.epoch or self.cancelled.is_set():
                        return
                    if self.hidden_since is not None:
                        elapsed=self.clock()-self.hidden_since
                        if self.hidden_reset_required or elapsed<0 or elapsed>=self.settings.expiry_seconds:
                            self._cancel_tasks()
                            self.context.clear()
                            tracker=self.tracker=SequenceTracker()
                        self.hidden_since=None
                        self.hidden_last_clock=None
                        self.hidden_reset_required=False
                        # 切回窗口本身会产生人工输入。短暂离开仍由两个不同锚点、
                        # 连续帧和位置检查对齐；超过时效则只建立新基线。
                        self.last_input_generation=None
                        self.last_frame_keys=None
                    if self.desktop.is_foreground() and self.last_input_generation is not None and generation != self.last_input_generation and keys != self.last_frame_keys:
                        raise AmbiguousFrame("人工操作时消息视图发生变化，请重新观察建立基线")
                initial=tracker.baseline is None
                new = tracker.update(frame.messages)
                with self.lock:
                    if epoch != self.epoch or self.cancelled.is_set():
                        return
                    self.last_observed = self.clock()
                    self.last_input_generation,self.last_frame_keys=generation,keys
                    self.reason = "观察中" if self.mode == "observe" else "自动回复监听中"
                    if initial:self._seed_context(frame.messages)
                    self._record(new)
                    self._recover_latest(frame)
                self._companion_tick()
                now = self.clock()
                for task in list(self.tasks):
                    if not task.created<=now<=self._deadline(task):
                        self._retire(task, "skipped", "expired")
                if self.mode != "auto" or self.cancelled.is_set() or not self.tasks:
                    return
                task = self._next_task()
                if task is None:return
                if now<task.retry_at:
                    self.reason=_STICKER_CLIPBOARD_WAIT.get(task.sticker_clipboard_wait,'模型服务暂不可用，正在等待自动重试')
                    return
                if self.settings.reply_mode=='mentions' and now-task.last_added < self.settings.merge_seconds:
                    return
                allowed, reason = self.store.allow_send(now, self.settings.min_interval, self.settings.hourly_limit,minute_limit=self.settings.minute_limit if self.settings.natural_pacing else 0)
                if not allowed and not self._participation_pending(task):
                    with self.lock:
                        self.reason = "已达发送频率限制，任务过期后会跳过"
                    return
                if self.desktop.idle_for() < self.settings.idle_seconds and not self._participation_pending(task):
                    with self.lock:
                        self.reason = "等待人工操作结束"
                    return
                if self._active(task):
                    self._reply_and_send(task)
            except (AmbiguousFrame, ValueError, DesktopUnavailable) as error:
                if epoch == self.epoch:
                    self.pause(str(error))
            except Exception:
                if epoch == self.epoch:
                    self.pause("观察异常，已暂停；请检查窗口、校准和模型状态")
        finally:
            self.work_lock.release()

    def _observe_database(self,epoch):
        if self._expire_receipt():return
        try:
            batch=self.message_source.poll()
        except Exception as error:
            with self.lock:
                if epoch==self.epoch and not self.cancelled.is_set():
                    for task in self.tasks:
                        if task.state=='generated':task.idle_deadline=0
                    if self._expire_receipt():return
                    self.source_failures+=1
                    from .database import DatabaseUnavailable
                    self.reason=str(error)+'；自动重试中' if isinstance(error,DatabaseUnavailable) else '数据库暂时不可读，正在后台自动重连'
                    logging.getLogger(__name__).warning('数据库接收重试: %s',type(error).__name__)
                    self.ready_at=self.clock()+min(15,self.source_failures)
                    self._companion_tick(source_wait='等待消息监听恢复')
            return
        with self.lock:
            if epoch!=self.epoch or self.cancelled.is_set():return
            if self._expire_receipt():return
            now=self.clock()
            if (batch.initial or self.source_rebaseline or self.last_observed is None or
                    not 0<=now-self.last_observed<=self.settings.expiry_seconds):
                for task in self.tasks:
                    if task.state=='generated':task.idle_deadline=0
            self.source_failures=0;self.last_observed=now
            self.reason='数据库后台监听中' if self.mode=='auto' else '数据库观察中'
            baseline=batch.initial or self.source_rebaseline
            self.source_rebaseline=False
            if self.memory and hasattr(self.message_source,'member_profiles'):
                try:self.memory.refresh_member_profiles(self.message_source.member_profiles(self.memory.known_member_ids()))
                except Exception:logging.getLogger(__name__).warning('成员名册暂未更新，聊天继续')
            if baseline:
                self._seed_context(batch.history)
            else:
                if self.memory:
                    try:self.memory.refresh_members(batch.history)
                    except Exception:logging.getLogger(__name__).warning('成员昵称暂未更新，聊天继续')
                self._record(batch.new)
        now=self.clock()
        for task in list(self.tasks):
            if task.state=='awaiting_receipt':continue
            if task.state=='staged' and task.draft_waiting:
                if not task.created<=now<=self._deadline(task):self._retire(task,'skipped','draft_expired')
                continue
            if task.state in ('staged','sending','unknown'):
                self.pause('上次输入或发送尚未完成，已保留草稿且不会重发')
                return
            if not task.created<=now<=self._deadline(task):self._retire(task,'skipped','expired')
        self._companion_tick()
        if self.mode!='auto' or self.cancelled.is_set():return
        self._compaction_tick()
        if not self.tasks:
            return
        task=self._next_task()
        if task is None:return
        if task.state=='awaiting_receipt':
            if self._match_receipt(task,batch.new):return
            self.reason='已点击发送，正在等待微信回执；不会重复发送'
            return
        if now<task.retry_at:
            self.reason=task.wait_detail or _STICKER_CLIPBOARD_WAIT.get(task.sticker_clipboard_wait,'正在等待本条回复自动重试；仍在后台接收消息')
            return
        allowed,limit_reason=self.store.allow_send(now,self.settings.min_interval,self.settings.hourly_limit,minute_limit=self.settings.minute_limit if self.settings.natural_pacing else 0)
        if not allowed and not self._participation_pending(task):
            self._waiting(task,limit_reason)
            return
        # Generating text needs no foreground window or idle desktop.
        try:self._reply_and_send(task)
        except DesktopTemporarilyHidden as error:
            with self.lock:
                if epoch!=self.epoch or self.cancelled.is_set():return
                if self._defer_sticker_draft(task):return
                if task.state in ('staged','sending','awaiting_receipt','unknown'):
                    self.pause('输入或发送被弹窗、失焦或遮挡打断，已保留草稿；请核对微信')
                else:
                    task.retry_at=max(task.retry_at,self.clock()+3)
                    task.wait_reason='draft' if '草稿' in str(error) else 'window'
                    task.wait_detail=str(error)
                    task.idle_deadline=0
                    self.reason=str(error)
        except (ValueError,DesktopUnavailable) as error:
            if isinstance(error,DesktopUnavailable):
                logging.getLogger(__name__).warning('Send preparation interrupted: %s',str(error))
            with self.lock:
                if epoch!=self.epoch or self.cancelled.is_set():return
                if isinstance(error,DesktopUnavailable) and self._defer_sticker_draft(task):return
                if task.state in ('staged','sending','awaiting_receipt','unknown'):
                    self.pause('输入或发送状态发生变化，已保留草稿，请核对微信')
                else:
                    task.retry_at=max(task.retry_at,self.clock()+3)
                    task.wait_reason='window';task.idle_deadline=0
                    task.wait_detail=str(error)
                    self.reason=task.wait_detail+'；正在自动重试，数据库继续接收'
        except Exception:
            with self.lock:
                if epoch!=self.epoch or self.cancelled.is_set():return
                if task.state in ('staged','sending','awaiting_receipt','unknown'):self.pause('发送状态需核对，已保留草稿')
                else:self.reason='发送准备暂未完成，正在自动重试'

    def _defer_sticker_draft(self,task):
        # Only the same in-memory, never-clicked GIF transaction may resume.
        # Recovery never pastes again; a stricter complete-editor proof is required.
        if (task.state!='staged' or not task.sticker or task.sent_at or
                not callable(getattr(self.desktop,'verify_owned_sticker_input',None))):return False
        task.draft_waiting=True
        task.retry_at=self.clock()+3
        task.wait_reason='sticker_draft'
        task.wait_detail=_SEND_WAITS['sticker_draft']
        self.reason=task.wait_detail
        return True

    def _resume_sticker_draft(self,task):
        if not self._active(task):return
        if self.desktop.idle_for()<self.settings.idle_seconds:
            self._defer_sticker_draft(task);return
        self.message_source.assert_identity()
        if not self.desktop.is_foreground():
            wake=getattr(self.desktop,'try_idle_wake',None)
            if not (self.settings.idle_wake and wake and wake(self.settings.idle_seconds)):
                self._defer_sticker_draft(task);return
        generation=self.desktop.prepare_send(self.settings.idle_seconds)
        before=self._frame()  # validate this exact bound chat without selecting or clearing it
        if before.draft_empty:
            self._retire(task,'skipped','draft_removed');return
        if self.desktop.input_generation()!=generation:
            self._defer_sticker_draft(task);return
        ensure=getattr(self.desktop,'ensure_sticker_preview_space',None)
        if ensure:
            generation=ensure(task.sticker,self.settings.idle_seconds)
            if generation is None:
                self._defer_sticker_draft(task);return
            before=self._frame()
            if before.draft_empty:
                self._retire(task,'skipped','draft_removed');return
            if not self._active(task):return
        self.desktop.click_input(generation)
        if not self.desktop.verify_owned_sticker_input(task.sticker):
            self._defer_sticker_draft(task)
            task.wait_detail='表情草稿与原内容不一致或未完整显示，保留内容；继续监听'
            self.reason=task.wait_detail
            return
        if not self._active(task):return
        self._send_verified(task,task.reply,before)

    def _prepare_sticker_plan(self,plan,choices,request):
        from .stickers import StickerUnavailable
        ids=(plan.sticker,*plan.extra_stickers)
        if len(ids)>(request.count if request.explicit else 1) or len(set(ids))!=len(ids):
            raise StickerUnavailable('数量无效')
        if request.explain_after and len(ids)!=request.count:
            raise StickerUnavailable('素材数量不足，不能提前解释完整发送结果')
        prepared=[]
        for identity in ids:
            item=next((s for s in choices if s.id==identity),None)
            if item is None:raise StickerUnavailable('素材不在候选中')
            prepared.append(self.stickers.prepare(item))
        if len({s.sha256 for s in prepared})!=len(prepared):raise StickerUnavailable('素材内容重复')
        return prepared

    def _sticker_unavailable(self,task,reason):
        if task.continuation_of or task.proactive or task.operator_plan:
            self._retire(task,'skipped','media_unavailable')
            self.reason=('剩余表情暂不可用，已保留之前的发送回执' if task.continuation_of else
                         '指定表情暂不可用，未改写为其他消息' if task.operator_plan else '本次主动表情暂不可用，保持安静')
        else:
            task.sticker=None;task.reply='';task.sticker_failed=True;task.state='candidate'
            task.raw_bubble=None
            task.remaining_stickers.clear()
            self.reason=reason

    def _reply_and_send(self, task: ReplyTask):
        if task.state=='staged' and task.draft_waiting:
            return self._resume_sticker_draft(task)
        if task.state not in ('candidate','generated') or not self._active(task):return
        if not task.pacing_planned:
            task.reply_not_before=task.created+(random.uniform(2,5) if self.settings.natural_pacing else 0)
            task.send_interval=self.settings.min_interval+(random.uniform(0,self.settings.pacing_jitter) if self.settings.natural_pacing else 0)
            task.pacing_planned=True
        from .stickers import named_catalog_order,turn_sticker_request
        local_order=named_catalog_order(task.messages)
        if not task.reply and not local_order and not self._prepare_media(task):return
        with self.lock:
            context=self._reply_context(task)
        if task.reply:
            reply=task.raw_bubble if task.raw_bubble is not None else task.reply
        else:
            try:
                task.model_attempts+=1
                request=local_order or turn_sticker_request(task.messages,history=context[:-len(task.messages)])
                if (len(context)>96 or sum(len(m.text) for m in context)>16000
                        or sum(len(m.image_data) for m in context)>12*1024*1024):
                    context=self._compact_context(context,targets=task.messages)
                    task.context=list(context)
                optional=self.settings.reply_mode=='natural' and not request.explicit and not any(self.policy.addressed(m) for m in task.messages)
                task.sticker_requested=request.explicit
                task.sticker_source_id=request.source_id if request.explicit else ''
                choices=[]
                sticker_mode=(self.settings.stickers_enabled and self.message_source
                        and hasattr(self.desktop,'type_sticker') and hasattr(self.desktop,'verify_sticker_input')
                        and hasattr(self.model,'reply_plan'))
                ready=request.explicit or self.store.sticker_ready(self.settings.memory_scope,self.clock(),self.settings.sticker_frequency)
                if sticker_mode and not task.sticker_failed and ready:
                    choices=self.stickers.candidates(context,request=request,excluded=self.store.recent_stickers(self.settings.memory_scope,self.clock(),all_history=request.avoid_repeats))
                if sticker_mode and local_order:
                    prepared=self.stickers.prepare_order(choices,request)
                    if prepared:
                        task.sticker,*task.remaining_stickers=prepared
                        reply=task.sticker.description
                    else:
                        reply=f'这会儿没找到能发的{request.character}表情'
                elif sticker_mode:
                    enriched=self.companion.enrich(context) if self.companion else context
                    plan=self.model.reply_plan(enriched,choices=choices,request=request,optional=optional,profile=self.companion.profile if self.companion else '',
                                               sticker_state='available' if choices else ('cooldown' if not ready else 'unavailable'))
                    if plan is None:reply=None
                    elif plan.sticker:
                        from .stickers import StickerUnavailable
                        reply=clean_reply(plan.text) if plan.text and (not request.explicit or request.explain_after) else ''
                        try:
                            prepared=self._prepare_sticker_plan(plan,choices,request)
                            if reply and request.explicit and request.explain_after:
                                task.media_first=True
                                task.remaining_text=(reply,)
                                task.sticker=prepared[0];task.remaining_stickers=prepared[1:]
                                reply=task.sticker.description
                            elif reply:
                                task.remaining_stickers=prepared
                            else:
                                task.sticker=prepared[0];task.remaining_stickers=prepared[1:]
                                reply=task.sticker.description
                        except StickerUnavailable:
                            task.sticker_failed=True
                            if request.explain_after:reply=''
                            if not reply:
                                task.retry_at=self.clock()+1
                                self.reason='这张表情暂不可用，正在改用文字回复'
                                return
                    else:reply=clean_reply(plan.text)
                elif optional:
                    if not callable(getattr(self.model,'participate',None)):
                        raise ModelError('当前模型接口不支持自然参与，请更新后重试')
                    enriched=self.companion.enrich(context) if self.companion else context
                    result=self.model.participate(enriched,profile=self.companion.profile if self.companion else '')
                    reply=clean_reply(result) if result is not None else None
                elif self.companion and hasattr(self.model,'reply_profile'):
                    reply=clean_reply(self.model.reply_profile(self.companion.enrich(context),self.companion.profile))
                else:reply = clean_reply(self.model.reply(context))
            except ModelError as error:
                if getattr(error,'context_overflow',False) and task.model_attempts<3:
                    compacted=self._compact_context(context,force=True,targets=task.messages)
                    if compacted!=context:
                        task.context=compacted;task.retry_at=self.clock()+1
                        self.reason='上下文已压缩，正在继续回复'
                        return
                if not self._active(task):
                    with self.lock:
                        if self.mode=='auto' and task in self.tasks:self._retire(task,'skipped','expired')
                    return
                if error.code=='delivery_claim':
                    if task.model_attempts<2:
                        task.retry_at=self.clock()+1
                        self.reason='正在依据发送记录重新回答这位成员'
                        return
                    reply='刚才那条确实没发出去' if any(m.delivery_status=='not_sent' for m in context) else '我这边没核实上一条发出'
                elif error.retryable:
                    format_error=getattr(error,'code','')=='invalid_response'
                    if task.model_attempts<3:
                        delay=1 if format_error else (5 if task.model_attempts==1 else 15)
                        task.retry_at=self.clock()+max(delay,error.retry_after)
                        self.reason='回复格式异常，正在重新生成' if format_error else '模型服务暂不可用，稍后自动重试'
                    else:
                        self._retire(task,'skipped','model_format_failed' if format_error else 'model_unavailable')
                        if not format_error and self.last_result:
                            self.last_result['summary']='未发送：'+str(error)
                            self.last_result['model_error']=error.code
                        self.reason=self.last_result['summary']+'；继续监听新消息'
                    return
                else:
                    self.pause(str(error))
                    return
        with self.lock:
            if not self._active(task):return
            if reply is None:
                self._retire(task,'skipped','participation_declined')
                self.reason='这条消息已理解，暂不插话；继续监听'
                return
            if task.state=='candidate' and not task.sticker:
                # OCR receipts have no stable message IDs. Keep a single paste
                # in screen mode; database deliveries can attest each bubble.
                enabled=(self.settings.split_replies and self.message_source is not None
                         and not (task.proactive and task.proactive.kind in ('checkin','signoff')))
                split=getattr(self.model,'split_wechat_bubbles',None)
                parts=split(reply,task.messages,enabled=enabled) if split else split_reply(reply,enabled=enabled)
                reply, task.remaining_text=parts[0],tuple(parts[1:])
            if task.remaining_text and not task.sticker:
                others=sum(1 for other in self.tasks if other is not task and other.messages)
                # The queue may grow while a generated reply waits for idle
                # time or its rate slot. Combine only the still-unsent text.
                if others>=3 or not self.settings.split_replies or task.idle_deadline:
                    reply+=''.join(task.remaining_text)
                    task.remaining_text=()
            finalize=getattr(self.model,'finalize_wechat_bubble',None)
            if finalize and not task.sticker:
                history=self._context_snapshot()
                while True:
                    task.raw_bubble=reply
                    reply=finalize(reply,history,task.messages)
                    if reply.strip() or not task.remaining_text:break
                    reply,*remaining=task.remaining_text
                    task.remaining_text=tuple(remaining)
                if not reply.strip() and task.remaining_stickers:
                    task.sticker=task.remaining_stickers.pop(0)
                    task.raw_bubble=None
                    reply=task.sticker.description
                if not reply.strip():
                    self._retire(task,'skipped','participation_declined')
                    self.reason='这条只剩重复装饰，继续听群友聊天'
                    return
            task.reply, task.state = reply, "generated"
            self.store.update(task.id, "generated")
        if task.sticker and hasattr(self.stickers,'collection_enabled') and not self.stickers.collection_enabled(task.sticker.item.character):
            self._sticker_unavailable(task,'该表情分类已停用，改用文字回复')
            return
        if task.sticker and not task.sticker_requested and not self.store.sticker_ready(self.settings.memory_scope,self.clock(),self.settings.sticker_frequency):
            self._sticker_unavailable(task,'刚刚已发表情，这条改用文字接话')
            return
        # Deadlines are sampled once, never redrawn by polling or model retry.
        # Wait before activating WeChat, leaving the user's input untouched.
        if self.clock()<task.reply_not_before:
            self._waiting(task,'paced')
            return
        allowed,limit_reason=self.store.allow_send(self.clock(),max(self.settings.min_interval,task.send_interval),self.settings.hourly_limit,minute_limit=self.settings.minute_limit if self.settings.natural_pacing else 0)
        if not allowed:
            self._waiting(task,limit_reason)
            return
        if self.window_turn and not self.window_turn(self):
            self._waiting(task,'window_turn')
            return
        foreground=getattr(self.desktop,'is_foreground',None)
        if foreground is not None and not foreground():
            wake=getattr(self.desktop,'try_idle_wake',None)
            if not (self.settings.idle_wake and wake and self.message_source and
                    hasattr(self.message_source,'assert_identity')):
                self._waiting(task,'foreground')
                return
            if self.desktop.idle_for()<self.settings.idle_seconds:
                self._waiting(task,'desktop_busy')
                return
            # No restore or focus request before account, freshness and quota checks.
            if not self._ready_for_input(task):return
            self.message_source.assert_identity()
            if not wake(self.settings.idle_seconds) or not foreground():
                self._waiting(task,'foreground')
                self.reason=getattr(self.desktop,'wake_reason','') or '空闲唤起未获前台，保留待发回复；不模拟按键强抢焦点'
                task.wait_detail=self.reason
                return
        if self.desktop.idle_for()<self.settings.idle_seconds:
            self._waiting(task,'desktop_busy')
            return
        if task.sticker:
            probe=getattr(self.desktop,'sticker_clipboard_status',None)
            if probe:
                if task.sticker_clipboard_wait and self.clock()<task.retry_at:return
                blocked=probe()
                if blocked:
                    self._wait_for_sticker_clipboard(task,blocked)
                    return
                task.sticker_clipboard_wait=''
            from .stickers import StickerUnavailable
            try:task.sticker.verified_bytes()
            except (OSError,StickerUnavailable):
                self._sticker_unavailable(task,'表情缓存已变化，改用文字回复')
                return
        if not self._ready_for_input(task):return
        try:prepared_generation=self.desktop.prepare_send(self.settings.idle_seconds)
        except DesktopTemporarilyHidden as error:
            with self.lock:
                if self._active(task):
                    self._waiting(task,'window')
                    task.wait_detail=str(error)
                    task.retry_at=max(task.retry_at,self.clock()+3)
                    self.reason=str(error)
            return
        park=getattr(self.desktop,'park_editor_pointer',None)
        if park:park(self.settings.idle_seconds)
        before = self._frame(observation=bool(self.message_source))
        if not before.draft_empty:
            self.pause("输入框已有草稿，未执行输入")
            return
        if task.sticker:
            ensure=getattr(self.desktop,'ensure_sticker_preview_space',None)
            if ensure:
                prepared_generation=ensure(task.sticker,self.settings.idle_seconds)
                if prepared_generation is None:
                    self._waiting(task,'sticker_space');task.retry_at=self.clock()+5
                    return
                before=self._frame(observation=bool(self.message_source))
                if not before.draft_empty:
                    self._waiting(task,'draft');return
            fits=getattr(self.desktop,'sticker_preview_fits',None)
            if fits and not fits(task.sticker):
                self._sticker_unavailable(task,'输入区太窄，无法核对表情预览，本轮改用文字')
                return
        # 激活后截图/OCR也耗时，不能让新的人工输入被 click_input 建立的 cookie 吞掉。
        if self.desktop.input_generation()!=prepared_generation:
            with self.lock:self._waiting(task,'desktop_busy')
            return
        if not self._ready_for_input(task):
            return
        if self.message_source and hasattr(self.message_source,'assert_identity'):
            self.message_source.assert_identity()
        if task.idle_deadline and self.clock()>=task.created+self.settings.expiry_seconds:
            batch=self.message_source.poll()
            with self.lock:
                if not self._active(task):return
                if batch.initial:
                    self._retire(task,'skipped','expired')
                    self._seed_context(batch.history)
                    return
                self._record(batch.new)
                if (not batch.history or batch.history[-1].message_id!=task.messages[-1].message_id
                        or not self._active(task)):
                    self._retire(task,'skipped','expired')
                    return
        task.wait_reason=''
        task.wait_detail=''
        self.desktop.click_input(prepared_generation)
        focused = self._frame()
        if self.message_source and hasattr(self.desktop,'confirm_empty_editor'):
            # A moving GIF can make one pair of captures inconclusive.
            # While this edit is still untouched, sample again without a second click.
            for _ in range(3):
                if focused.draft_empty or focused.draft:break
                if self.cancelled.wait(.15) or not self._ready_for_input(task):return
                self.desktop.assert_input_focus()
                focused=self._frame()
        if not focused.draft_empty:
            if self.message_source and not focused.draft:
                task.retry_at=self.clock()+4
                task.wait_reason='draft'
                self.reason='输入区尚未核实为空，保留内容，稍后重新检查'
                return
            self.pause("输入前状态发生变化，已停止")
            return
        if not self._ready_for_input(task):return
        self.desktop.assert_input_focus()
        task.state='staged'
        self.store.update(task.id,'staged')
        try:
            if task.sticker:self.desktop.type_sticker(task.sticker)
            else:self.desktop.type_text(reply)
        except DesktopInputNotStarted as error:
            with self.lock:
                # Only the adapter's explicit no-edit outcome can be retried.
                # Do not revive a task cancelled by a concurrent user pause.
                if self.mode=='auto' and not self.cancelled.is_set() and task in self.tasks:
                    task.state='generated'
                    if self._active(task):
                        self.store.update(task.id,'generated')
                        if isinstance(error,StickerClipboardBlocked):
                            self._wait_for_sticker_clipboard(task,error.reason)
                        else:
                            task.retry_at=self.clock()+2
                            task.wait_reason='clipboard'
                            self.reason=str(error)
            return
        if not self._active(task):
            return
        self.desktop.assert_input_focus()
        task.state = "staged"
        self.store.update(task.id, "staged")
        matched=False
        exact_readback=False
        if task.sticker:
            exact_readback=True
            verify_preview=self.desktop.verify_sticker_input
            probe_preview=getattr(self.desktop,'probe_sticker_input',verify_preview)
            reveal=getattr(self.desktop,'reveal_sticker_preview',None)
            for attempt in range(6):
                if not self._active(task):return
                check=verify_preview if attempt==5 else probe_preview
                if check(task.sticker):matched=True;break
                if attempt==1 and reveal and self._active(task):reveal()
                if attempt<5 and self.cancelled.wait(.24):return
        elif hasattr(self.desktop,'read_input_text'):
            from .clipboard import ClipboardUnavailable
            try:
                actual=self.desktop.read_input_text(reply)
                exact_readback=True
                matched=bubble_matches(task,actual)
            except ClipboardUnavailable:
                pass  # preserve unsupported clipboard formats and use visual readback
        for _ in range(0 if exact_readback else 3):
            if not self._active(task):return
            self.desktop.assert_input_focus()
            staged=self._frame()
            matches=(staged.exact_draft_line==reply if getattr(self.desktop,'typed_without_clipboard',False)
                     else bubble_matches(task,staged.draft,visual=True))
            if not staged.draft_empty and matches:
                matched=True;break
            if self.cancelled.wait(.18):return
        if not matched:
            if exact_readback and not task.sticker:
                from difflib import SequenceMatcher
                import unicodedata
                changes=[(kind, i2-i1, j2-j1,
                          all(unicodedata.category(c).startswith(('P','Z')) for c in reply[i1:i2]+actual[j1:j2]))
                         for kind,i1,i2,j1,j2 in SequenceMatcher(None,reply,actual).get_opcodes() if kind!='equal']
                logging.getLogger(__name__).warning('Input readback mismatch: expected_length=%d actual_length=%d edits=%s',len(reply),len(actual),changes)
            if self.message_source and self._defer_sticker_draft(task):return
            self.pause("输入表情未能完整核对，已保留草稿" if task.sticker else "输入内容未能完整核对，请检查微信草稿")
            return
        self._send_verified(task,reply,before)

    def _send_verified(self,task,reply,before):
        if not self._active(task):
            return
        allowed, _ = self.store.allow_send(self.clock(), max(self.settings.min_interval,task.send_interval), self.settings.hourly_limit,minute_limit=self.settings.minute_limit if self.settings.natural_pacing else 0)
        if not allowed:
            if task.draft_waiting:
                self._defer_sticker_draft(task);return
            self.pause("输入后触及发送限制，已保留草稿")
            return
        self.desktop.assert_input_focus()
        if self.message_source:
            baseline=None
            for _ in range(8):
                if self.cancelled.is_set():return
                try:baseline=self.message_source.poll();break
                except Exception:
                    if self.cancelled.wait(.15):return
            if baseline is None:
                self.pause('发送前数据库仍在更新，已保留本次草稿')
                return
            with self.lock:self._record(baseline.new)
            if hasattr(self.message_source,'assert_identity'):self.message_source.assert_identity()
            self.desktop.assert_input_focus()
        verify_send = getattr(self.desktop, 'verify_send_control', None)
        if verify_send:
            verify_send(getattr(self.vision, 'ocr', None))
        if not self._active(task):return
        if task.proactive and not task.continuation_of:self.companion.begin_send(task.proactive)
        # 在发送事件之前先持久化；即便此刻崩溃，也不会在重启后重发。
        task.sent_at=self.clock()
        baseline_ids=[m.message_id for m in baseline.history+baseline.new] if self.message_source else []
        self.store.mark_sending(task.id, task.sent_at,[message.key for message in task.messages],group=self.settings.memory_scope,reply=reply,baseline_ids=baseline_ids,
                                sticker_md5=task.sticker.md5 if task.sticker else '',sticker_id=task.sticker.item.id if task.sticker else '')
        task.state = "sending"
        try:
            if not self._active(task):
                raise ValueError("paused")
            self.desktop.click_send()
            if self.message_source:
                self._confirm_database_send(task,reply)
                release=getattr(self.desktop,'release_send_focus',None)
                if release:release()
                return
            original = sum(m.direction == "out" and bubble_matches(task,m.text,visual=True) for m in before.messages)
            stable = None
            for _ in range(4):
                if self.cancelled.wait(.35):
                    break
                after = self._frame()
                own = [m for m in after.messages if m.direction == "out" and m.kind == "text" and bubble_matches(task,m.text,visual=True)]
                keys = tuple(m.key for m in after.messages)
                if after.draft_empty and len(own) > original and after.messages and after.messages[-1] == own[-1]:
                    if stable == keys:
                        self.last_input_generation=self.desktop.input_generation()
                        self.last_frame_keys=keys
                        self._remember(Message(reply,'','out'))
                        self._retire(task, "verified")
                        return
                    stable = keys
                else:
                    stable = None
        except Exception:
            pass
        self._retire(task, "unknown", "uncertain_send")
        self.pause("发送结果未知，已暂停且不会重发；请检查微信")

    def _compact_context(self,context,force=False,targets=()):
        # This is a bounded request view, not persistent memory compaction.
        # Never make a second network request before replying to queued input.
        keep=48 if force else 96
        text_limit=8000 if force else 16000
        image_limit=(6 if force else 12)*1024*1024
        raw=[m for m in context if m.kind!='summary']
        if not raw:return context
        previous='\n'.join(m.text for m in context if m.kind=='summary')
        summaries=[Message(previous,kind='summary')] if previous else []
        target_ids={m.message_id for m in targets if m.message_id}
        selected={i for i,m in enumerate(raw) if m.message_id and m.message_id in target_ids}
        selected.add(len(raw)-1)
        # Preserve anonymous merged target messages as well.
        if targets:selected.update(range(max(0,len(raw)-len(targets)),len(raw)))
        indices={m.message_id:i for i,m in enumerate(raw) if m.message_id}
        text_size=len(previous)+sum(len(raw[i].text) for i in selected)
        image_size=sum(len(raw[i].image_data) for i in selected)
        def include(index):
            nonlocal text_size,image_size
            pending=set()
            while index not in selected and index not in pending:
                pending.add(index)
                source=indices.get(raw[index].reply_to_id)
                if source is None:break
                index=source
            chars=sum(len(raw[i].text) for i in pending)
            images=sum(len(raw[i].image_data) for i in pending)
            if (len(selected)+len(pending)+len(summaries)>keep
                    or text_size+chars>text_limit or image_size+images>image_limit):return
            selected.update(pending);text_size+=chars;image_size+=images
        for index in tuple(selected):
            source=indices.get(raw[index].reply_to_id)
            if source is not None:include(source)
        speaker=raw[-1].speaker_id
        if speaker and speaker not in ('self','unknown'):
            related=[i for i,m in enumerate(raw) if m.speaker_id==speaker
                     or (m.direction=='out' and m.reply_to_speaker==speaker)]
            for index in reversed(related[-24:]):include(index)
        for index in range(len(raw)-1,-1,-1):include(index)
        return summaries+[raw[i] for i in sorted(selected)]

    def _prepare_media(self,task):
        if not self.message_source or not hasattr(self.message_source,'resolve_media'):return True
        # Current media gets priority over optional old frames; don't re-scan
        # five historical missing files before answering a new text question.
        current=[m for m in task.messages if m.kind in ('image','emoji') and not m.image_data]
        old=[m for m in task.context if m.kind in ('image','emoji') and not m.image_data][-2:]
        recent=current+old
        resolved={}
        for message in recent:
            if message.message_id in resolved:continue
            reference=self.media_refs.get(message.message_id,message)
            try:data=self.message_source.resolve_media(reference)
            except Exception:data=None
            if data:
                updated=replace(message,image_data=data,media_raw='',media_key='')
                resolved[message.message_id]=updated;self._remember(updated)
        task.context=[resolved.get(m.message_id,m) for m in task.context]
        task.messages=[resolved.get(m.message_id,m) for m in task.messages]
        missing=any(m.kind in ('image','emoji') and not m.image_data for m in task.messages)
        if missing and task.media_attempts<3:
            task.media_attempts+=1;task.retry_at=self.clock()+3
            self.reason='图片暂未加载完成，正在重试；继续接收后续消息'
            return False
        return True

    def _confirm_database_send(self,task,reply):
        for _ in range(10):
            if self.cancelled.wait(.25):break
            if self._expire_receipt():return
            try:batch=self.message_source.poll()
            except Exception:
                # A WAL commit can overlap the first read after clicking Send.
                # Retry only the observation; never click Send again.
                continue
            with self.lock:
                if task not in self.tasks or self.cancelled.is_set():return
                if self._expire_receipt():return
                self._record(batch.new)
                self.last_observed=self.clock()
            if self._match_receipt(task,batch.new):return
        with self.lock:
            if self.cancelled.is_set() or task not in self.tasks or self._expire_receipt():return
            task.state='awaiting_receipt'
            self.reason='已点击发送，正在等待微信回执；不会重复发送'

    def _match_receipt(self,task,messages):
        with self.lock:
            if (self.cancelled.is_set() or self.mode!='auto' or task not in self.tasks
                    or task.state not in ('sending','awaiting_receipt')):return False
            if self._expire_receipt():return True
            epoch=self.epoch
            for message in messages:
                if not task.sticker and not bubble_matches(task,message.text):continue
                if not self.store.confirm_receipt(task.id,self.settings.memory_scope,message):continue
                if task.sticker:message=replace(message,text=task.sticker.description,media_raw='',media_key='')
                source=list(task.messages)
                if source:
                    target=next((m for m in source if task.sticker_source_id and m.message_id==task.sticker_source_id),source[-1])
                    message=replace(message,reply_to_id=target.message_id,reply_to_speaker=target.speaker_id)
                self._remember(message)
                text=task.remaining_text
                stickers=list(task.remaining_stickers)
                position=self.tasks.index(task)
                self._retire(task,'verified')
                if epoch!=self.epoch or self.cancelled.is_set() or self.mode!='auto':
                    if task.proactive and (text or stickers):self._companion_call('finish',task.proactive,'partial')
                    return True
                self.reason='回复已由微信数据库确认，继续后台监听'
                if not (text or stickers):return True
                if not task.created<=self.clock()<self._deadline(task):
                    if task.proactive:self._companion_call('finish',task.proactive,'partial')
                    return True
                # Only one successor exists at a time. It keeps the original
                # source deadline, queue position and member, but earns its own
                # send attempt and new text/hash receipt.
                if stickers and task.media_first:
                    sticker=stickers.pop(0)
                    reply,rest_text=sticker.description,text
                elif text:
                    reply,rest_text=text[0],text[1:]
                    sticker=None
                else:
                    sticker=stickers.pop(0)
                    reply,rest_text=sticker.description,()
                identity=self.store.create_unique(self.store.fingerprint(f'bubble:{self.settings.memory_scope}:{task.id}'),task.created)
                if identity:
                    self.tasks.insert(position,ReplyTask(identity,task.created,task.last_added,task.sender,
                        messages=source,reply=reply,state='generated',proactive=task.proactive,
                        continuation_of=task.continuation_of or task.id,remaining_text=rest_text,
                        sticker=sticker,sticker_requested=task.sticker_requested,sticker_source_id=task.sticker_source_id,
                        remaining_stickers=stickers,operator_plan=task.operator_plan,media_first=task.media_first))
                elif task.proactive:self._companion_call('finish',task.proactive,'partial')
                return True
            return False

    def run(self):
        while not self.stop_event.is_set():
            self.observe_once()
            self.stop_event.wait(self.settings.poll_seconds)
