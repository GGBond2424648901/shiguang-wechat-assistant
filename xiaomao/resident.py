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
    def __init__(self,settings,store,*,desktop_factory=None,vision=None,model_factory=ChatModel,clock=time.time,settle_seconds=5,
                 account_detector=None,account_profiles=None,account_binder=None):
        self.settings,self.store,self.clock=settings,store,clock
        self.stop_event=threading.Event()
        self.lock=threading.RLock()
        self.cycle_lock=threading.Lock()
        self.reload_request=None
        self.rules_pending=set()
        self.index=self.turns=0
        self.navigation=NavigationActivity()
        self.tray=None
        self.tray_error=''
        self.window_turns={}
        self._next_discovery=0
        self._discovering=False
        self.use_database=desktop_factory is None and settings.receive_mode=='database'
        from .account_detection import AccountDetector
        from .account_profiles import AccountProfiles
        from .account_switching import auto_bind_account
        self.account_detector=account_detector or (AccountDetector(self.stop_event) if self.use_database else None)
        self.account_profiles=account_profiles or AccountProfiles(settings.runtime)
        self.account_binder=account_binder or auto_bind_account
        self.account_status='正在自动检测已登录微信' if self.account_detector else ''
        self._next_account_binding=0
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
        pid=binding.pid if binding else (settings.account_process_id if account==settings.calibration_account else 0)
        if not pid:return None
        key=(account,pid)
        hub=self.hubs.get(key)
        if hub is None:
            hub=self.hubs[key]=DatabaseHub(settings,self.stop_event,process_id=pid)
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
                state['reply_mode']=engine.settings.reply_mode
                state['sticker_frequency']=engine.settings.sticker_frequency
                state['rules_pending']=engine.settings.memory_scope in self.rules_pending
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
        reporting=current if current['mode']==mode else next(s for s in states if s['mode']==mode)
        last_result=max((s['last_result'] for s in states if s.get('last_result')),key=lambda r:r['at'],default=None)
        return {**states[0],'mode':mode,'reason':reporting['reason'],'reason_group':reporting['group'],
                'active_group':current['group'],
                'last_result':last_result,
                'saved_groups':list(settings.saved_groups),'monitor_all_groups':settings.monitor_all_groups,
                'group_names':list(settings.allowed_groups),'pending_count':sum(s['pending_count'] for s in states),
                'window_reload_pending':reload_pending,'idle_wake':settings.idle_wake,'can_stop_if_idle':True,
                'can_configure_reply_rules':True,
                'can_control_selected':True,
                'account_display_name':settings.account_display_name,
                'account_scope':settings.calibration_account,
                'account_status':self.account_status,
                'tray':{'running':bool(self.tray and self.tray.available),'error':self.tray_error},
                'last_observed':max((s['last_observed'] for s in states if s['last_observed'] is not None),default=None),
                'groups':[{'name':s['group'],'mode':s['mode'],'reason':s['reason'],'calibrated':s['calibrated'],
                           'window_kind':s['window_kind'],'window_state':s['window_state'],'last_result':s.get('last_result'),
                           'receive_mode':s['receive_mode'],'receive_retrying':s['receive_retrying'],
                            'model_health':s.get('model_health',{}),'model_usage':s.get('model_usage',{}),
                           'last_observed':s['last_observed'],
                           'reply_mode':s['reply_mode'],'sticker_frequency':s['sticker_frequency'],
                           'rules_pending':s['rules_pending'],
                           'pending_count':s['pending_count'],'companion':s.get('companion')} for s in states]}

    def configure_reply_rules(self,group,scope,preferences):
        return self._configure_rules(preferences,group,scope)

    def configure_reply_defaults(self,preferences):
        return self._configure_rules(preferences)

    def _configure_rules(self,preferences,group=None,scope=None):
        from .reply_preferences import validate_rule_changes
        preferences=validate_rule_changes(preferences,allow_inherit=group is not None)
        with self.lock:
            saved=Settings.load(self.settings.root)
            if group is not None:
                for cfg in (saved,self.settings):
                    target=cfg.conversation_targets.get(group,{})
                    if group not in cfg.saved_groups or scope!=target.get('account','')+'/'+target.get('id',''):
                        raise ValueError('该会话身份已改变，请重新选择')
                fresh=saved.with_conversation_rules(group,preferences)
            else:fresh=replace(saved,**preferences)
            # Persist first. Failed writes must not claim success or affect the worker.
            fresh.save(expected=saved)
            changes={k:getattr(fresh,k) for k in ('reply_mode','sticker_frequency','conversation_rules')}
            self.settings=replace(self.settings,**changes)
            if self.reload_request:self.reload_request=replace(self.reload_request,**changes)
            self.rules_pending.update(e.settings.memory_scope for e in self.engines)
        if self.cycle_lock.acquire(blocking=False):
            try:self._apply_reply_rules()
            finally:self.cycle_lock.release()
        return self.status()

    def _apply_reply_rules(self):
        with self.lock:
            for e in self.engines:
                scope=e.settings.memory_scope
                if scope not in self.rules_pending:continue
                with e.lock:
                    if any(t.continuation_of or t.state in ('staged','sending','awaiting_receipt','unknown') for t in e.tasks):continue
                    desired=self.settings.for_group(e.settings.group)
                    cfg=replace(e.settings,reply_mode=desired.reply_mode,sticker_frequency=desired.sticker_frequency,
                                conversation_rules=self.settings.conversation_rules)
                    policy=TriggerPolicy(cfg.assistant_name,cfg.at_display_name,cfg.wake_aliases,cfg.reply_mode)
                    # Tightening to mentions must not send an unstarted ordinary reply.
                    # Operator sends and already-started multi-bubble turns retain their identity.
                    if cfg.reply_mode=='mentions' and e.settings.reply_mode!='mentions':
                        for task in list(e.tasks):
                            if (task.state in ('candidate','generated') and task.messages and not task.continuation_of
                                    and not any(policy.addressed(m) for m in task.messages)):
                                e._retire(task,'skipped','reply_rule_changed')
                    e.settings,e.policy=cfg,policy
                    if hasattr(e.model,'settings'):e.model.settings=cfg
                    if e.companion:e.companion.settings=cfg
                    self.rules_pending.discard(scope)
            self.rules_pending.intersection_update(e.settings.memory_scope for e in self.engines)

    def pending(self):
        with self.lock:
            # Each engine already bounds its own queue. A busy first group
            # must not hide a later group's work from its dashboard filter.
            return [{**task,'group':engine.settings.group} for engine in self.engines for task in engine.pending()]

    def _control(self,operation,*args,**kwargs):
        for engine in self.engines:getattr(engine,operation)(*args,**kwargs)
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
            self._save_pause_states([(e,False) for e in self.engines])
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
            self._control('pause',reason)
            if reason=='用户已暂停':self._save_pause_states([(e,True) for e in self.engines])
            return self.status()

    def restore_auto(self):
        """Resume an auto worker after restart without undoing room pauses."""
        with self.lock:
            self.requested_mode='auto'
            for engine in self.engines:
                if engine.settings.is_conversation_paused():continue
                if engine.settings.binding and engine.settings.binding.bottom_confirmed and engine.settings.model_ready:
                    engine.enable()
            return self.status()

    def _save_pause_states(self,changes):
        path=self.settings.runtime/'config.json'
        exists=path.exists()
        saved=Settings.load(self.settings.root) if exists else self.settings
        fresh=saved
        for engine,paused in changes:
            name,scope=engine.settings.group,engine.settings.memory_scope
            if name not in saved.saved_groups or saved.conversation_scope(name)!=scope:
                raise ValueError('该会话身份或监听范围已改变，请重新选择')
            fresh=fresh.with_conversation_pause(name,paused)
        fresh.save(expected=saved if exists else None)
        keys=fresh.paused_conversations
        self.settings=replace(self.settings,paused_conversations=keys)
        if self.reload_request:self.reload_request=replace(self.reload_request,paused_conversations=keys)
        for engine in self.engines:
            engine.settings=replace(engine.settings,paused_conversations=keys)
            if hasattr(engine.model,'settings'):engine.model.settings=engine.settings
            if engine.companion:engine.companion.settings=engine.settings

    def reload_windows(self):
        saved=Settings.load(self.settings.root)
        with self.lock:
            # Only window routes and their explicitly selected recipients
            # belong to this operation. Live model/pacing settings are not
            # reconfigured halfway through a generated turn.
            self.reload_request=replace(self.settings,group=saved.group,group_names=saved.group_names,
                monitor_all_groups=saved.monitor_all_groups,conversation_targets=saved.conversation_targets,
                calibration=saved.calibration,calibration_account=saved.calibration_account,idle_wake=saved.idle_wake,
                paused_conversations=saved.paused_conversations,
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
                if (engine.settings.memory_scope in self.rules_pending or
                        any(t.state in ('staged','sending','awaiting_receipt','unknown') for t in engine.tasks)):
                    cfg=replace(cfg,reply_mode=engine.settings.reply_mode,sticker_frequency=engine.settings.sticker_frequency)
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
                if engine.settings.at_display_name!=cfg.at_display_name or engine.settings.reply_mode!=cfg.reply_mode:
                    engine.policy=TriggerPolicy(cfg.assistant_name,cfg.at_display_name,cfg.wake_aliases,cfg.reply_mode)
                engine.settings=cfg
                if hasattr(engine.model,'settings'):engine.model.settings=cfg
                if engine.companion:engine.companion.settings=cfg
                if cfg.is_conversation_paused() and engine.mode!='paused':engine.pause('本会话已暂停')
                if engine.mode!='paused':
                    if not cfg.binding or not cfg.binding.bottom_confirmed:
                        if engine.mode=='auto':engine.observe()
                        engine.reason='等待校准此会话窗口；其他就绪会话继续监听'
                    elif engine.mode=='observe' and self.requested_mode=='auto' and cfg.model_ready and not unknown:
                        engine.enable()
            for removed in existing.values():removed.stop(preserve_companion=True)
            self.engines,self.settings=[route[0] for route in routes],fresh
            self._set_companion_budgets()
            self.index=self.turns=0
            self.reload_request=None

    def _check_account_change(self):
        """A resident-owned transaction, independent of the panel and reply mode.

        True fences the current observer cycle. It never means a send succeeded.
        Detector results are authenticated, stable and scoped to the config that
        started the probe; this transaction also uses disk CAS before applying.
        """
        from .account_switching import configured_account,prepare_account_settings,route_snapshot,desired_account_profile,PROFILE_FIELDS
        if not self.account_detector:return False
        try:observation=self.account_detector.poll(self.settings)
        except Exception:
            self.account_status='账号自动检测暂时不可用，等待重试'
            return False
        if observation is None:return False
        with self.lock:
            if self.stop_event.is_set() or self.reload_request:return False
            previous=self.settings
            try:account=configured_account(previous)
            except ValueError:
                self.account_status='已保存对象包含不同微信账号，已暂停发送；请在会话页核对监听范围'
                self.requested_mode='paused'
                for engine in self.engines:
                    engine.cancelled.set()
                    with engine.lock:engine.mode,engine.reason='paused',self.account_status
                return True
            changed=account!=observation.account
            pids={b.pid for n in previous.saved_groups if (b:=previous.binding_for(n))}
            handles={(w.get('pid'),w.get('hwnd')) for w in observation.windows}
            missing_handle=bool(handles) and any((b.pid,b.hwnd) not in handles
                for n in previous.allowed_groups if (b:=previous.binding_for(n)))
            process_changed=(previous.account_process_id not in (0,observation.pid)
                             or bool(pids-{observation.pid}) or missing_handle)
            transition=changed or process_changed
            if transition:
                unsafe=any(t.state in ('staged','sending','awaiting_receipt','unknown')
                           for e in self.engines for t in e.tasks)
                if unsafe:
                    self.account_status='发现微信换号，存在草稿或发送待确认，已暂停；不会重发'
                    self.requested_mode='paused'
                    for engine in self.engines:
                        engine.cancelled.set()
                        with engine.lock:engine.mode,engine.reason='paused',self.account_status
                    return True
                # Cancel old model work before any route/profile can change.
                # Do not persist this fence as an operator's per-room pause.
                for engine in self.engines:engine.pause('微信账号正在自动切换',preserve_companion=True)
            missing=any(not previous.binding_for(n) for n in previous.allowed_groups)
            from .vision import detached_title_matches
            prefer_detached=False
            for name in previous.allowed_groups:
                bound=previous.binding_for(name)
                if not bound or bound.window_kind!='main':continue
                matches=[w for w in observation.windows if w.get('kind')=='detached'
                         and w.get('pid')==observation.pid and detached_title_matches(w.get('title',''),bound)]
                if len(matches)==1:prefer_detached=True;break
            metadata=(previous.account_display_name!=observation.display_name
                      or previous.account_process_id!=observation.pid)
            retry_binding=(missing or prefer_detached) and time.monotonic()>=self._next_account_binding
            try:pending_profile=self.account_profiles.load('pending:'+account) if account else None
            except (ValueError,OSError):
                self.account_status='账号加密配置暂不可读，自动切换等待重试；当前规则已保留'
                return transition
            desired=(pending_profile.get('source') if isinstance(pending_profile,dict)
                     and pending_profile.get('applied')==route_snapshot(previous) else None)
            if not isinstance(desired,dict) or set(desired)!=set(PROFILE_FIELDS):desired=None
            new_ids={(r.account,r.id,r.kind) for r in observation.rows if r.selectable}
            current_ids={tuple(t[k] for k in ('account','id','kind')) for t in previous.conversation_targets.values()}
            rematch=bool(desired and any(tuple(t[k] for k in ('account','id','kind')) in new_ids-current_ids
                                       for t in desired.get('conversation_targets',{}).values()))
            if not (transition or metadata or retry_binding or rematch):
                self.account_status=('已自动识别账号与联系人，等待可见聊天窗口自动核验；不会抢前台'
                                     if missing else '已自动识别当前微信账号')
                return False
            self._next_account_binding=time.monotonic()+20
        try:
            if Settings.load(previous.root)!=previous:
                with self.lock:
                    if self.settings is previous and self.reload_request is None:
                        self.reload_request=Settings.load(previous.root)
                self.account_status='设置刚有更新，等待下一次自动检测'
                return transition
            if transition:
                if account:self.account_profiles.save(account,desired or route_snapshot(previous))
                profile=(desired or route_snapshot(previous)) if not changed else self.account_profiles.load(observation.account)
                desired=profile or desired_account_profile(previous,observation.account,observation.display_name)
                fresh=prepare_account_settings(previous,observation,desired)
            elif rematch:
                fresh=prepare_account_settings(previous,observation,desired)
            else:
                fresh=replace(previous,account_display_name=observation.display_name,
                              account_process_id=observation.pid,calibration_account=observation.account)
            fresh=self.account_binder(fresh,observation,self.stop_event) if transition or retry_binding or rematch else fresh
            if self.stop_event.is_set():return True
            with self.lock:
                if self.settings is not previous or self.reload_request:return transition
                if desired and (transition or rematch):
                    self.account_profiles.save('pending:'+observation.account,
                        {'source':desired,'applied':route_snapshot(fresh)})
                if fresh!=previous:fresh.save(expected=previous)
                self.reload_request=fresh
                self._apply_window_reload()
                if self.reload_request is None:
                    for engine in self.engines:
                        if engine.reason=='微信账号正在自动切换' and not engine.settings.is_conversation_paused():
                            bound=engine.settings.binding
                            if (self.requested_mode=='auto' and bound and bound.bottom_confirmed
                                    and engine.settings.model_ready):engine.enable()
                            elif self.requested_mode in ('auto','observe'):engine.observe(preserve_companion=True)
                self.account_status=('已自动识别当前微信账号' if all(fresh.binding_for(n) for n in fresh.allowed_groups)
                    else '已自动识别账号与联系人，等待可见聊天窗口自动核验；不会抢前台')
            return self.reload_request is not None
        except Exception as error:
            with self.lock:
                try:
                    saved=Settings.load(previous.root)
                    if saved!=previous and self.settings is previous and self.reload_request is None:
                        self.reload_request=saved
                except (ValueError,OSError):pass
            self.account_status='账号自动切换等待重试；旧账号消息不会发送' if transition else '账号已识别，窗口自动核验等待重试'
            logging.getLogger(__name__).info('Account route update deferred: %s',type(error).__name__)
            return transition

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

    def stop(self,*,preserve_companion=False):
        with self.lock:
            result=self._control('stop',preserve_companion=True) if preserve_companion else self._control('stop')
            self.stop_event.set()
            if self.account_detector:self.account_detector.close()
            return result

    def stop_if_idle(self):
        # Guard against a new arrival between the panel's status read and stop.
        if not self.cycle_lock.acquire(timeout=0.5):
            raise ValueError('正在处理消息，未修改设置，请稍后保存')
        try:
            with self.lock:
                if any(e.tasks for e in self.engines):
                    raise ValueError('仍有待发或待确认回复，已保留队列，请稍后保存')
                return self.stop(preserve_companion=True)
        finally:self.cycle_lock.release()

    def reply_latest(self, *, group=None, scope=None):
        # 本机恢复入口只指向群列表第一项，绝不把所有群历史一起排队。
        with self.lock:
            primary = self.engines[0]
            if (group is not None or scope is not None) and self._selected_engine(group,scope) is not primary:
                raise ValueError('后台正在应用新主会话，请稍后再试')
            self.index=self.turns=0
            primary.reply_latest()
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

    def queue_outbound(self,group,scope,text,sticker_ids,request_id):
        """Explicit local send plan, through the ordinary guarded delivery queue.

        No synthetic inbound message, external file path or alternate recipient
        is accepted. A request remains idempotent after completion or restart.
        """
        import re
        from .domain import ReplyTask
        from .stickers import StickerUnavailable
        if (not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,96}',request_id)
                or not isinstance(text,str) or len(text)>400 or text and not text.isprintable()
                or not isinstance(sticker_ids,list) or len(sticker_ids)>3
                or any(not isinstance(s,str) or not 1<=len(s)<=100 for s in sticker_ids)
                or len(set(sticker_ids))!=len(sticker_ids) or not (text.strip() or sticker_ids)):
            raise ValueError('请填写有效的文字、素材编号和唯一请求编号')
        with self.lock:
            engine=self._selected_engine(group,scope)
            with engine.lock:
                if (engine.mode!='auto' or not engine.settings.binding
                        or not engine.settings.binding.bottom_confirmed or not engine.message_source):
                    raise ValueError('该监听对象尚不能自动发送')
                prepared=[]
                if sticker_ids:
                    if not engine.settings.stickers_enabled:raise ValueError('本会话未开启表情发送')
                    engine.stickers.refresh()
                    try:
                        for identity in sticker_ids:
                            item=next((s for s in engine.stickers.items if s.id==identity and s.priority!='停用'),None)
                            if item is None:raise StickerUnavailable('素材不在可用素材库中')
                            prepared.append(engine.stickers.prepare(item))
                    except (StickerUnavailable,OSError) as error:
                        raise ValueError('表情素材未通过校验，未加入队列') from error
                    if len({s.sha256 for s in prepared})!=len(prepared):raise ValueError('表情内容重复')
                now=self.clock()
                task_id=self.store.create_unique(self.store.fingerprint(f'local-plan:{scope}:{request_id}'),now)
                if task_id is None:return {'queued':False,'duplicate':True}
                task=ReplyTask(task_id,now,now,'',reply=text,state='generated',
                    sticker=prepared[0] if prepared and not text else None,
                    remaining_stickers=prepared if text else prepared[1:],
                    sticker_requested=bool(sticker_ids),operator_plan=True)
                if task.sticker:task.reply=task.sticker.description
                engine.tasks.append(task)
                self.store.update(task_id,'generated')
                return {'queued':True,'id':task_id}

    def enable_selected(self,group,scope):
        with self.lock:
            selected=self._selected_engine(group,scope)
            if not selected.settings.binding or not selected.settings.binding.bottom_confirmed:
                raise ValueError('请先校准当前会话窗口')
            if not selected.settings.model_ready:raise ValueError('请先配置模型及有效的加密凭据')
            changes=[(e,True) for e in self.engines if e is not selected and e.mode=='paused']+[(selected,False)]
            self._save_pause_states(changes)
            selected.enable()
            self.requested_mode='auto'
            return self.status()

    def pause_selected(self,group,scope):
        with self.lock:
            selected=self._selected_engine(group,scope)
            selected.pause('本会话已暂停')
            try:self._save_pause_states([(selected,True)])
            except (OSError,ValueError):
                raise ValueError('本会话暂停已生效，但重启偏好未能保存，请稍后重试') from None
            return self.status()

    def companion_configure(self,group,scope,preferences):
        from .companion import CompanionPreferences
        with self.lock:
            e=self._selected_engine(group,scope)
            if not e.companion or group not in e.settings.conversation_targets:raise ValueError('请先绑定准确的回复对象')
            if not isinstance(preferences,dict):raise ValueError('陪伴策略格式无效')
            if 'routines' not in preferences and hasattr(e.companion.planner.preferences,'routines'):
                preferences={**preferences,'routines':e.companion.planner.preferences.routines}
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
            if self._check_account_change():return
            self._apply_reply_rules()
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
            if self.account_detector:self.account_detector.close()
            for engine in self.engines:
                if engine.companion:engine.companion.close()
            for hub in self.hubs.values():hub.close()
