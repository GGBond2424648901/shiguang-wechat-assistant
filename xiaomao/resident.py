"""独立常驻的多群协调器；每群状态隔离，共享一个串行桌面。"""
import threading
import time
import logging
import traceback
from dataclasses import replace
from pathlib import Path
from .config import Settings
from .database import DatabaseHub, LazyDatabaseFeed
from .engine import Engine
from .model import ChatModel
from .policy import TriggerPolicy
from .vision import Vision
from .windows import WindowsDesktop,NavigationActivity


class Resident:
    def __init__(self,settings,store,*,desktop_factory=None,vision=None,model_factory=ChatModel,clock=time.time,settle_seconds=5):
        self.settings,self.store,self.clock=settings,store,clock
        self.stop_event=threading.Event()
        self.lock=threading.RLock()
        self.cycle_lock=threading.Lock()
        self.reload_request=None
        self.index=self.turns=0
        self.navigation=NavigationActivity()
        self.window_turns={}
        self._next_discovery=0
        self._discovering=False
        self.use_database=desktop_factory is None and settings.receive_mode=='database'
        self.hubs={}
        self.vision=vision or Vision()
        self.desktop_factory=desktop_factory or (lambda binding:WindowsDesktop(binding,navigation=self.navigation))
        self.model_factory,self.settle_seconds=model_factory,settle_seconds
        unknown=store.recover()
        self.requested_mode='paused' if unknown else 'auto' if settings.auto_on_launch else 'observe'
        self.engines=[self._new_engine(settings,settings.for_group(name),unknown) for name in settings.allowed_groups]
        self._set_companion_budgets()

    def _set_companion_budgets(self):
        accounts={}
        for e in self.engines:
            if e.companion and e.companion.planner.preferences.enabled:
                account=e.settings.memory_scope.split('/')[0]
                accounts[account]=accounts.get(account,0)+e.companion.planner.preferences.daily_max
        for e in self.engines:
            if e.companion:e.companion.account_daily_max=max(3,accounts.get(e.settings.memory_scope.split('/')[0],0))

    def _source_for(self, settings, cfg):
        if not self.use_database:return None
        target=cfg.conversation_targets.get(cfg.group,{})
        account=target.get('account','')
        binding=cfg.binding
        if not binding and account:
            # An uncalibrated recipient on this account may still receive.
            # The other window supplies a PID for database discovery only.
            for name in settings.allowed_groups:
                if settings.conversation_targets.get(name,{}).get('account')==account:
                    binding=settings.binding_for(name)
                    if binding:break
        if not binding:return None
        key=(account,binding.pid)
        hub=self.hubs.get(key)
        if hub is None:
            hub=self.hubs[key]=DatabaseHub(settings,self.stop_event,process_id=binding.pid)
        return LazyDatabaseFeed(hub,cfg.group)

    def _new_engine(self, settings, cfg, unknown):
        engine=Engine(cfg,self.store,self.desktop_factory(cfg.binding),self.vision,self.model_factory(cfg),
                      clock=self.clock,settle_seconds=self.settle_seconds,
                      message_source=self._source_for(settings,cfg),recovered_unknown=unknown)
        engine.window_turn=self._window_turn
        return engine

    def _window_turn(self,engine,claim=False):
        # A sidebar click needs another observation to verify the new header.
        # Keep accepting traffic for every room; reserve only this native HWND.
        binding=engine.settings.binding
        if not binding or binding.window_kind!='main' or not engine.message_source:return True
        key=(binding.pid,binding.hwnd)
        now=time.monotonic()
        owner=self.window_turns.get(key)
        if owner:
            previous,task_id,until=owner
            if (now<until and previous in self.engines and previous.mode=='auto'
                    and any(t.id==task_id and t.state=='generated' for t in previous.tasks)):
                return previous is engine
            self.window_turns.pop(key,None)
        if claim:
            task=engine._next_task()
            if task:self.window_turns[key]=(engine,task.id,now+max(12,2*self.settings.poll_seconds+5))
        return True

    def status(self):
        with self.lock:
            states=[engine.status() for engine in self.engines]
            windows={}
            for engine,state in zip(self.engines,states):
                b=engine.settings.binding
                state['window_kind']=b.window_kind if b else None
                key=(b.pid,b.hwnd,b.target_group if b.window_kind=='detached' else '') if b else None
                if key not in windows:
                    probe=getattr(engine.desktop,'route_state',None)
                    try:windows[key]=probe() if probe else ('已绑定' if b else '待校准')
                    except Exception:windows[key]='窗口状态暂不可读'
                state['window_state']=windows[key]
            current=states[self.index]
            settings=self.settings
            reload_pending=self.reload_request is not None
        modes={s['mode'] for s in states}
        mode=next(iter(modes)) if len(modes)==1 else 'auto' if 'auto' in modes else 'observe' if 'observe' in modes else 'paused'
        last_result=max((s['last_result'] for s in states if s.get('last_result')),key=lambda r:r['at'],default=None)
        return {**states[0],'mode':mode,'reason':current['reason'],'active_group':current['group'],
                'last_result':last_result,
                'saved_groups':list(settings.saved_groups),'monitor_all_groups':settings.monitor_all_groups,
                'group_names':list(settings.allowed_groups),'pending_count':sum(s['pending_count'] for s in states),
                'window_reload_pending':reload_pending,'idle_wake':settings.idle_wake,'can_stop_if_idle':True,
                'last_observed':max((s['last_observed'] for s in states if s['last_observed'] is not None),default=None),
                'groups':[{'name':s['group'],'mode':s['mode'],'reason':s['reason'],'calibrated':s['calibrated'],
                           'window_kind':s['window_kind'],'window_state':s['window_state'],'last_result':s.get('last_result'),
                           'pending_count':s['pending_count'],'companion':s.get('companion')} for s in states]}

    def pending(self):
        with self.lock:
            # Each engine already bounds its own queue. A busy first group
            # must not hide a later group's work from its dashboard filter.
            return [{**task,'group':engine.settings.group} for engine in self.engines for task in engine.pending()]

    def _control(self,operation,*args):
        for engine in self.engines:getattr(engine,operation)(*args)
        return self.status()

    def observe(self):
        with self.lock:
            self.requested_mode='observe'
            return self._control('observe')

    def enable(self):
        with self.lock:
            ready=[e for e in self.engines if e.settings.binding and e.settings.binding.bottom_confirmed]
            if not ready:raise ValueError('请先校准窗口并确认消息列表位于底部')
            if not self.settings.model_ready:raise ValueError('请先配置模型及有效的加密凭据')
            self.requested_mode='auto'
            for engine in self.engines:
                if engine in ready:engine.enable()
                else:
                    engine.observe()
                    engine.reason='等待校准此会话窗口；其他就绪会话继续自动回复'
            return self.status()

    def pause(self,reason='用户已暂停'):
        with self.lock:
            self.requested_mode='paused'
            return self._control('pause',reason)

    def reload_windows(self):
        saved=Settings.load(self.settings.root)
        with self.lock:
            # Only window routes and their explicitly selected recipients
            # belong to this operation. Live model/pacing settings are not
            # reconfigured halfway through a generated turn.
            self.reload_request=replace(self.settings,group=saved.group,group_names=saved.group_names,
                monitor_all_groups=saved.monitor_all_groups,conversation_targets=saved.conversation_targets,
                calibration=saved.calibration,calibration_account=saved.calibration_account,idle_wake=saved.idle_wake,
                window_bindings=saved.window_bindings,self_name_unique_confirmed=(
                    self.settings.self_name_unique_confirmed and self.settings.memory_scope==saved.memory_scope))
        if self.cycle_lock.acquire(blocking=False):
            try:self._apply_window_reload()
            finally:self.cycle_lock.release()
        return self.status()

    def _apply_window_reload(self):
        with self.lock:
            fresh=self.reload_request
            if fresh is None or self.stop_event.is_set():return
            configs=[fresh.for_group(name) for name in fresh.allowed_groups]
            configs_by_scope={cfg.memory_scope:cfg for cfg in configs}
            existing={e.settings.memory_scope:e for e in self.engines}
            for scope,engine in existing.items():
                cfg=configs_by_scope.get(scope)
                if cfg and cfg.binding==engine.settings.binding and cfg.group==engine.settings.group:continue
                if any(task.state in ('staged','sending','awaiting_receipt','unknown') for task in engine.tasks):
                    return
            # Prepare every replacement before swapping any live route. A
            # resource failure leaves the previous windows operational.
            unknown=self.store.counts().get('unknown',0)
            added=[]
            routes=[]
            try:
                for cfg in configs:
                    engine=existing.pop(cfg.memory_scope,None)
                    if engine is None:
                        engine=self._new_engine(fresh,cfg,unknown)
                        engine.source_rebaseline=True
                        added.append(engine)
                        routes.append((engine,cfg,engine.desktop,engine.message_source))
                    else:
                        changed=cfg.binding!=engine.settings.binding or cfg.group!=engine.settings.group
                        desktop=self.desktop_factory(cfg.binding) if changed else engine.desktop
                        source=self._source_for(fresh,cfg)
                        if source and engine.message_source and source.hub is engine.message_source.hub and source.group==engine.message_source.group:
                            source=engine.message_source
                        routes.append((engine,cfg,desktop,source))
            except Exception as error:
                for engine in added:
                    if engine.companion:engine.companion.close()
                logging.getLogger(__name__).warning('Window update waiting: %s',type(error).__name__)
                return
            for engine,cfg,desktop,source in routes:
                if source:source.hub.settings=fresh
                if engine in added:
                    if self.requested_mode=='paused':engine.pause()
                    elif self.requested_mode=='observe' and engine.mode=='auto':engine.observe()
                elif engine.desktop is not desktop:
                    if hasattr(desktop,'cancelled'):desktop.cancelled=engine.cancelled
                    engine.desktop=desktop
                    if source is None:
                        from .tracker import SequenceTracker
                        engine.tracker=SequenceTracker()
                        engine.baseline_position_checked=False
                        engine.last_input_generation=engine.last_frame_keys=None
                        engine.hidden_since=engine.hidden_last_clock=None
                        engine.hidden_reset_required=False
                engine.message_source=source
                if engine.settings.at_display_name!=cfg.at_display_name:
                    engine.policy=TriggerPolicy(cfg.assistant_name,cfg.at_display_name,cfg.wake_aliases,cfg.reply_mode)
                engine.settings=cfg
                if hasattr(engine.model,'settings'):engine.model.settings=cfg
                if engine.companion:engine.companion.settings=cfg
                if engine.mode!='paused':
                    if not cfg.binding or not cfg.binding.bottom_confirmed:
                        if engine.mode=='auto':engine.observe()
                        engine.reason='等待校准此会话窗口；其他就绪会话继续监听'
                    elif engine.mode=='observe' and self.requested_mode=='auto' and cfg.model_ready and not unknown:
                        engine.enable()
            for removed in existing.values():removed.stop()
            self.engines,self.settings=[route[0] for route in routes],fresh
            self._set_companion_budgets()
            self.index=self.turns=0
            self.reload_request=None

    def _check_reopened_windows(self):
        now=time.monotonic()
        with self.lock:
            if (not self.use_database or self.requested_mode!='auto' or self._discovering
                    or self.reload_request or now<self._next_discovery):return
            self._next_discovery=now+20
            previous=self.settings
            missing=[e.settings.group for e in self.engines if e.settings.binding
                     and e.settings.binding.window_kind=='detached' and hasattr(e.desktop,'route_state')
                     and e.desktop.route_state()=='需重新绑定']
            if not missing:return
            self._discovering=True
        def discover():
            from .selection import rediscover_detached
            try:
                updated=rediscover_detached(previous,missing,self.stop_event)
                with self.lock:
                    if (updated!=previous and self.settings is previous and self.reload_request is None
                            and not self.stop_event.is_set()):
                        self.reload_request=updated
            except Exception as error:
                logging.getLogger(__name__).info('Detached discovery deferred: %s',type(error).__name__)
            finally:
                with self.lock:self._discovering=False
        threading.Thread(target=discover,name='wechat-window-discovery',daemon=True).start()

    def stop(self):
        with self.lock:
            result=self._control('stop')
            self.stop_event.set()
            return result

    def stop_if_idle(self):
        # Guard against a new arrival between the panel's status read and stop.
        if not self.cycle_lock.acquire(timeout=0.5):
            raise ValueError('正在处理消息，未修改设置，请稍后保存')
        try:
            with self.lock:
                if any(e.tasks for e in self.engines):
                    raise ValueError('仍有待发或待确认回复，已保留队列，请稍后保存')
                return self.stop()
        finally:self.cycle_lock.release()

    def reply_latest(self):
        # 本机恢复入口只指向群列表第一项，绝不把所有群历史一起排队。
        with self.lock:
            self.index=self.turns=0
            self.engines[0].reply_latest()
            return self.status()

    def queue_text(self, group, text, request_id):
        import re
        from .domain import ReplyTask
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,96}',request_id) or not text.strip() or len(text)>400 or not text.isprintable():
            raise ValueError('请输入单行聊天文字及唯一请求编号')
        with self.lock:
            engine=next((e for e in self.engines if e.settings.group==group),None)
            if engine is None:raise ValueError('该会话不在监听范围')
            with engine.lock:
                if engine.mode!='auto' or not engine.settings.binding or not engine.message_source:
                    raise ValueError('该监听对象尚不能自动发送')
                scope=engine.settings.memory_scope
                now=self.clock()
                task_id=self.store.create_unique(self.store.fingerprint(f'local-text:{scope}:{request_id}'),now)
                if task_id is None:return {'queued':False,'duplicate':True}
                engine.tasks.append(ReplyTask(task_id,now,now,'',reply=text,state='generated'))
                self.store.update(task_id,'generated')
                return {'queued':True,'id':task_id}

    def _selected_engine(self,group=None,scope=None):
        if group is None and scope is None:return self.engines[0]
        for e in self.engines:
            if e.settings.group==group and e.settings.memory_scope==scope:return e
        raise ValueError('该会话身份或监听范围已改变，请重新选择')

    def enable_selected(self,group,scope):
        with self.lock:
            self._selected_engine(group,scope).enable()
            self.requested_mode='auto'
            return self.status()

    def companion_configure(self,group,scope,preferences):
        from .companion import CompanionPreferences
        with self.lock:
            e=self._selected_engine(group,scope)
            if not e.companion or group not in e.settings.conversation_targets:raise ValueError('请先绑定准确的回复对象')
            if not isinstance(preferences,dict):raise ValueError('陪伴策略格式无效')
            try:prefs=CompanionPreferences(**preferences)
            except TypeError:raise ValueError('陪伴策略字段无效') from None
            with e.lock:e.companion.configure(prefs)
            self._set_companion_budgets()
            return self.status()

    def companion_preview(self,group=None,scope=None):
        with self.lock:
            self._selected_engine(group,scope).companion_preview()
            return self.status()

    def companion_once(self,group=None,scope=None):
        with self.lock:
            self._selected_engine(group,scope).companion_once()
            return self.status()

    def _observe_engine(self,engine):
        try:engine.observe_once()
        except Exception as error:
            # A live HTTP endpoint must not hide a dead observer thread.
            # Log only code locations/types, never chat/model/credential data.
            locations=' > '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}'
                                 for f in traceback.extract_tb(error.__traceback__)[-6:])
            logging.getLogger(__name__).error('Observer fault at %.3f: %s [%s]',
                                               self.clock(),type(error).__name__,locations)
            try:engine.pause('监听遇到内部错误，已暂停发送并记录原因；助手进程仍在运行')
            except Exception as cleanup_error:
                # A disk failure can also break pause's journal writes. The
                # cancellation fence must still stop input; keep tasks for
                # inspection rather than replaying an uncertain transaction.
                engine.cancelled.set()
                with engine.lock:
                    engine.mode='paused'
                    engine.reason='本机状态保存异常，已暂停发送；助手进程仍在运行'
                logging.getLogger(__name__).error('Observer pause cleanup failed: %s',type(cleanup_error).__name__)

    def observe_once(self):
        if not self.cycle_lock.acquire(blocking=False):return
        try:
            if self.stop_event.is_set():return
            self._apply_window_reload()
            self._check_reopened_windows()
            start=self.index
            ordered=self.engines[start:]+self.engines[:start]
            shared=None
            for engine in ordered:
                if self.stop_event.is_set():return
                binding=engine.settings.binding
                if not engine.message_source:
                    if not binding:continue
                    if binding.window_kind=='main':
                        if shared is not None:continue
                        shared=engine
                self._observe_engine(engine)
            with self.lock:
                self.turns+=1
                # Only shared OCR windows require several consecutive frames
                # on one selected sidebar chat. Database and detached targets
                # each receive a turn on every polling cycle.
                if shared and shared.mode in ('observe','auto') and self.turns<(4 if shared.tasks else 3):
                    self.index=self.engines.index(shared)
                else:
                    last=self.engines.index(shared) if shared else start
                    self.index=(last+1)%len(self.engines)
                    self.turns=0
            # SQLite snapshot connections are owned by the observer thread.
            used={e.message_source.hub for e in self.engines if isinstance(e.message_source,LazyDatabaseFeed)}
            for key,hub in list(self.hubs.items()):
                if hub not in used:
                    hub.close()
                    del self.hubs[key]
        finally:self.cycle_lock.release()

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.observe_once()
                self.stop_event.wait(self.settings.poll_seconds)
        finally:
            for engine in self.engines:
                if engine.companion:engine.companion.close()
            for hub in self.hubs.values():hub.close()
