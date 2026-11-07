"""原生控制面板；网络和 OCR 不阻塞 Tk 主线程。"""
from dataclasses import asdict,replace
from pathlib import Path
import queue
import threading
import tkinter as tk
from .client import ControlClient, ControlUnavailable
from .config import Settings
from .credentials import CredentialStore
from .model import ChatModel
from .settings_service import apply_saved_settings
from .windows import DesktopUnavailable

def default_regions():
    return {"header": [.287,.021,.70,.073], "messages": [.279,.128,.995,.763],
            "input": [.288,.817,.98,.932], "send": [.90,.944,.984,.985]}


class Panel:
    def __init__(self, root: tk.Tk, directory: Path):
        self.root, self.directory = root, directory
        # 本面板按像素排版，固定字号换算，避免系统缩放改变后控件文字挤压。
        root.tk.call('tk','scaling',96/72)
        self.client = ControlClient(directory)
        self.settings = Settings.load(directory)
        self.results = queue.Queue()
        self.closing = False
        self.exiting=False
        self.mutation_lock=threading.RLock()
        self.pending_mutations=0
        self.poll_busy = False
        self.connection_error = None
        self.testing_model = False
        self.switch_busy=False
        self.switch_cancel=threading.Event()
        from .workspace_ui import logo,BG
        self.app_icon=logo(root,48)
        root.iconphoto(True,self.app_icon)
        from .brand import APP_NAME
        root.title(APP_NAME)
        width=min(1200,root.winfo_screenwidth()-42);height=min(800,root.winfo_screenheight()-85)
        root.geometry(f"{width}x{height}")
        root.minsize(min(930,width),min(620,height))
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.bind('<Unmap>',self._minimize_to_tray,add='+')
        self.mode=tk.StringVar(value="只观察")
        self.reason=tk.StringVar(value="等待校准或观察目标群")
        self.notice=tk.StringVar(value="")
        self.details=tk.StringVar()
        self.target_group=tk.StringVar(value=self.settings.group)
        self.reply_scope=tk.StringVar(value='正在读取本机状态…')
        self.display_name=tk.StringVar(value=self.settings.self_display_name)
        self.at_unique=tk.BooleanVar(value=self.settings.self_name_unique_confirmed)
        self.auto_on_launch=tk.BooleanVar(value=self.settings.auto_on_launch)
        self.split_replies=tk.BooleanVar(value=self.settings.split_replies)
        self.idle_wake=tk.BooleanVar(value=self.settings.idle_wake)
        self.owner_tone=tk.BooleanVar(value=self.settings.owner_confirmed)
        self.model_name=tk.StringVar(value=self.settings.model_name)
        self.secret=tk.StringVar(value="")
        self.remember_account_form()
        self.secret_state=tk.StringVar(value="已使用 Windows DPAPI 加密保存；留空保留" if CredentialStore(self.settings.runtime).available else "尚未设置 DeepSeek 加密凭据")
        from .theme import GlassView,titlebar
        self.view=GlassView(self)
        self.view.pack(fill="both",expand=True)
        from .feedback_ui import FeedbackBar
        self.feedback=FeedbackBar(self.view)
        self.feedback.watch(self.notice)
        titlebar(root,self.view)
        root.after(100,self.drain)
        root.after(120,self.poll)
        self.connect_worker()

    def work(self, action, success=None, failure=None,*,mutates=True,allow_exit=False,label=None,controls=()):
        # Only user actions have labels. Polling never creates a toast or steals focus.
        if not hasattr(self,'active_actions'):self.active_actions=set()
        if label and label in self.active_actions:return
        previous_controls=[]
        if label:
            self.active_actions.add(label)
            self.notice.set('正在'+label+'…')
            for control in controls:
                if control.winfo_exists():
                    previous_controls.append((control,control.cget('text'),control.cget('state')))
                    control.configure(text='处理中…',state='disabled')
        def finish(ok,value):
            for control,text,state in previous_controls:
                if control.winfo_exists():control.configure(text=text,state=state)
            try:
                callback=success if ok else failure or self.show_error
                before=self.notice.get() if label else None
                if callback:callback(value)
                if label:
                    if not ok:self.show_error(value)
                    elif self.notice.get()==before:self.notice.set(label+'已完成')
            except Exception as error:
                self.show_error(str(error) if isinstance(error,ValueError) else '界面未能显示操作结果，请刷新状态后核对')
            finally:
                if label:self.active_actions.discard(label)
        if mutates:self.pending_mutations+=1
        def run():
            try:
                if mutates:
                    with self.mutation_lock:
                        if self.exiting and not allow_exit:raise ValueError('助手正在退出，本次操作已取消')
                        result=action()
                else:result=action()
                self.results.put((lambda value:finish(True,value),result))
            except Exception as error:
                safe=str(error) if isinstance(error,(ControlUnavailable,ValueError,DesktopUnavailable)) else "操作未完成，请检查设置或运行环境"
                self.results.put((lambda value:finish(False,value),safe))
            finally:
                if mutates:self.pending_mutations-=1
        threading.Thread(target=run,daemon=True).start()

    def drain(self):
        if self.closing:
            return
        while True:
            try:
                callback,value=self.results.get_nowait()
            except queue.Empty:
                break
            if callback:
                try:callback(value)
                except Exception as error:
                    self.show_error(str(error) if isinstance(error,ValueError) else '界面未能显示操作结果，请刷新状态后核对')
        self.root.after(100,self.drain)

    def show_error(self,error):
        self.notice.set(error)
        if hasattr(self,'feedback'):self.feedback.show(error,'error')

    def show_status(self,data):
        if self.connection_error and self.notice.get()==self.connection_error:
            self.notice.set("")
        self.connection_error=None
        modes={"observe":"只观察","auto":"自动回复已开启","paused":"已暂停","stopped":"已停止",
               "starting":"正在启动后台","disconnected":"未连接本地助手"}
        self.mode.set(modes.get(data.get("mode"),"未连接"))
        self.reason.set(data.get("reason",""))
        groups=data.get('group_names',self.settings.allowed_groups)
        active=data.get('active_group',data.get('group',self.settings.group))
        scope=f'同时监听{len(groups)}个会话，当前：{active}' if len(groups)>1 else f'正在回复：{active}'
        if not data.get('running',True) or data.get('mode') in ('stopped','paused','observe'):
            scope=f'已选：{active} · {modes.get(data.get("mode"),"未运行")}'
        reply_mode=data.get('reply_mode',self.settings.reply_mode)
        reply_modes={'natural':'自然参与 · 模型判断普通消息，@点名必回',
                     'all':'逐条回复 · 文字、图片和表情',
                     'mentions':'仅点名 · @自己或称呼触发'}
        self.reply_scope.set(scope+' · '+reply_modes.get(reply_mode,'回复模式未知'))
        self.view.status=data
        self.view.update_text()
        aliases=dict.fromkeys((*self.settings.wake_aliases,self.settings.assistant_name))
        at='点名：'+' / '.join('@'+name for name in aliases)
        if data.get('receive_mode')=='database':at+=' · 群聊原生 @ 按微信账号识别'
        elif data.get('at_enabled'):at+=' · 额外群昵称已配置'
        calibrated="窗口已校准" if data.get("calibrated") else "需要校准窗口"
        counts=data.get("counts",{})
        self.details.set(f"{calibrated}  ·  {at}  ·  已核对 {counts.get('verified',0)}  ·  结果未知 {counts.get('unknown',0)}  ·  待处理 {data.get('pending_count',0)}")

    _account_fields={'display_name':'self_display_name','at_unique':'self_name_unique_confirmed',
                     'auto_on_launch':'auto_on_launch','split_replies':'split_replies','idle_wake':'idle_wake',
                     'owner_tone':'owner_confirmed','model_name':'model_name'}

    def remember_account_form(self):
        from .account_switching import configured_account
        try:self._form_account=configured_account(self.settings)
        except ValueError:self._form_account=''
        self._form_baseline={name:getattr(self,name).get() for name in Panel._account_fields if hasattr(self,name)}

    def sync_account_settings(self,data):
        """Follow a resident account transition without discarding local edits."""
        from .account_switching import configured_account
        account=data.get('account_scope')
        if not account:return
        try:current_account=configured_account(self.settings)
        except ValueError:
            self.notice.set('已保存对象包含不同微信账号，请在会话页核对监听范围')
            return
        form_account=getattr(self,'_form_account',current_account)
        if not account or (account==current_account and account==form_account
                and data.get('account_display_name','')==self.settings.account_display_name):return
        fields=Panel._account_fields
        baseline=getattr(self,'_form_baseline',{})
        dirty=any(hasattr(self,name) and getattr(self,name).get()!=baseline.get(name,getattr(self.settings,field))
                  for name,field in fields.items())
        dirty=dirty or bool(self.secret.get()) or bool(getattr(self,'pending_mutations',0))
        for page in self.view.pages.values():
            checker=getattr(page,'dirty',None)
            if checker and checker():dirty=True
            if any(entry.get().strip() for entry in getattr(page,'key_entries',())):dirty=True
        if dirty:
            self.notice.set('后台已自动识别新微信；本页未保存的修改已保留，请撤销旧账号修改后继续编辑')
            return
        fresh=Settings.load(self.directory)
        if configured_account(fresh)!=account:return
        self.settings=fresh
        for name,field in fields.items():
            if hasattr(self,name):getattr(self,name).set(getattr(fresh,field))
        self.target_group.set(fresh.group)
        Panel.remember_account_form(self)
        self.view.render()

    def connect_worker(self):
        if self.closing or getattr(self,'exiting',False) or self.view.status.get('mode')=='starting':
            return
        self.worker_start=object()
        self.show_status({'running':False,'mode':'starting','reason':'正在启动本机后台，连接后自动读取队列'})
        if self.view.page=='assistant':
            self.set_preview('正在连接本机后台…')

        def connected(data):
            self.show_status(data)
            if self.view.page=='assistant':
                self.view.refresh_pending(background=True)

        self.work(self.client.start,connected,self._connection_failed)

    def _connection_failed(self,error):
        if self.view.status.get('mode')=='disconnected':
            return
        self.show_status({'running':False,'mode':'disconnected','reason':error})
        self.connection_error=error
        if self.view.page=='assistant':
            self.set_preview('后台未连接。点「重连后台」后读取待回复队列。')
        self.notice.set(error)

    def poll(self):
        if self.closing:
            return
        if (not self.poll_busy and not getattr(self,'exiting',False)
                and not getattr(self,'pending_mutations',0) and self.view.status.get('mode')!='starting'):
            self.poll_busy=True
            start=getattr(self,'worker_start',None)
            def done(data):
                self.poll_busy=False
                if start is not getattr(self,'worker_start',None):return
                previous=self.view.status
                Panel.sync_account_settings(self,data)
                self.show_status(data)
                if self.view.page in ('assistant','pending') and (self.view._queue_scope!=self.view._current_scope()
                        or data.get('pending_count') or any(previous.get(k)!=data.get(k) for k in ('group','mode','reason','pending_count','counts'))):
                    self.view.refresh_pending(background=True)
            def failed(error):
                self.poll_busy=False
                if start is getattr(self,'worker_start',None) and not getattr(self,'pending_mutations',0):
                    self._connection_failed(error)
            self.work(self.client.status,done,failed,mutates=False)
        self.root.after(1500,self.poll)

    def save_settings(self):
        display,model,key=self.display_name.get().strip(),self.model_name.get(),self.secret.get()
        unique=self.at_unique.get()
        auto_on_launch=self.auto_on_launch.get()
        split_replies=self.split_replies.get()
        idle_wake=self.idle_wake.get() if hasattr(self,'idle_wake') else self.settings.idle_wake
        private_key,owner_tone=self.settings.private_tone_key,self.owner_tone.get()
        from .account_switching import configured_account
        account=getattr(self,'_form_account',configured_account(self.settings))
        self.secret.set("")
        def save():
            original=Settings.load(self.directory)
            if configured_account(original)!=account:raise ValueError('微信账号已自动切换，未把旧页面设置应用到新账号；请先撤销旧修改')
            cfg=replace(original)
            if cfg.private_tone_key!=private_key:raise ValueError('回复对象已变化，请在当前会话重新保存设置')
            cfg.self_display_name=display
            cfg.self_name_unique_confirmed=unique
            cfg.auto_on_launch=auto_on_launch
            cfg.split_replies=split_replies
            cfg.idle_wake=idle_wake
            cfg.set_owner_tone(owner_tone)
            if unique:
                cfg.at_note=""
            cfg.model_base_url="https://api.deepseek.com"
            cfg.model_name=model
            cfg.__post_init__()
            return apply_saved_settings(self.client,original,cfg,key)
        def saved(result):
            self.settings=Settings.load(self.directory)
            self.remember_account_form()
            self.secret_state.set("已使用 Windows DPAPI 加密保存；留空保留" if CredentialStore(self.settings.runtime).available else "尚未设置 DeepSeek 加密凭据")
            self.show_status(result)
            self.notice.set('设置已保存并应用到后台助手')
        controls=tuple(c for page in self.view.pages.values() for c in [getattr(page,'save_button',None)] if c is not None)
        self.work(save,saved,label='保存设置',controls=controls)

    def edit_groups(self):
        self.view.select('sessions')

    def choose_contact(self):
        self.view.select('contacts')

    def select_contact(self,contact):
        self.target_group.set(contact.name)
        self._switch(False,contact=contact)

    def control(self,operation):
        self.switch_cancel.set()
        def act():
            if operation in ('enable','observe'):self.client.start()
            try:return getattr(self.client,operation)()
            except ControlUnavailable:
                if operation=='pause':
                    from .selection import stop_confirmed
                    stop_confirmed(self.directory,self.client)
                    return {'running':False,'mode':'stopped','group':self.settings.group,'reason':'自动回复已停止'}
                raise
        name={'enable':'开启回复','pause':'暂停回复','observe':'切换观察模式'}.get(operation,operation)
        def done(data):
            self.show_status(data)
            groups=data.get('groups',[])
            if operation=='enable':
                enabled=sum(row.get('mode')=='auto' for row in groups) if groups else int(data.get('mode')=='auto')
                total=len(groups) or 1
                self.notice.set(f'自动回复已开启 · {enabled}/{total} 个会话就绪'+('，请查看未就绪会话的原因' if enabled<total else ''))
            else:self.notice.set('自动回复已暂停，后台停止发送' if operation=='pause' else '已切换为只观察，不会发送消息')
        controls=tuple(self.view.actions[k] for k in ('enable','pause') if k in self.view.actions)
        self.work(act,done,label=name,controls=controls)

    def control_conversation(self,group,operation):
        if operation not in ('enable','pause'):raise ValueError('会话控制操作无效')
        cfg=self.settings
        if group not in cfg.allowed_groups:
            self.show_error('请先选择并绑定准确的回复对象');return
        if operation=='enable':
            bound=cfg.binding_for(group)
            row=next((r for r in self.view.status.get('groups',[]) if r.get('name')==group),{})
            if (group not in cfg.conversation_targets or not bound or not bound.bottom_confirmed
                    or row.get('window_state') in ('待校准','需重新绑定')):
                self.calibrate(group,auto_match=True)
                return
        if group not in cfg.conversation_targets:
            self.show_error('请先选择并绑定准确的回复对象');return
        scope=cfg.conversation_scope(group)
        def action():
            if operation=='enable':
                status=self.client.start(initial_mode='paused')
                if status.get('window_reload_pending') and group not in status.get('group_names',[]):
                    raise ValueError('该会话已保存，等待后台应用校准后即可开启')
            return getattr(self.client,operation+'_selected')(group,scope)
        def done(data):
            self.settings=Settings.load(self.directory)
            self.view.render();self.show_status(data)
            self.notice.set(f'已{"暂停" if operation=="pause" else "开启"}“{group}”的自动回复，其他会话保持各自状态')
        controls=tuple(self.view.actions[k] for k in ('enable','pause','enable_all','pause_all'))+tuple(self.view.watch_controls.values())
        self.work(action,done,label='暂停此会话' if operation=='pause' else '开启此会话',controls=controls)

    def _switch(self,read_current,contact=None):
        if self.switch_busy:return
        self.switch_busy=True;self.switch_cancel=threading.Event()
        cancelled=self.switch_cancel
        selected=self.target_group.get()
        self.notice.set('正在读取微信当前会话并切换…' if read_current else f'正在切换到“{selected}”…')
        self.view.set_switch_busy(True)
        def action():
            from .selection import read_current_group,switch_group,load_directory
            current=read_current_group(self.directory,entries=load_directory(self.directory)) if read_current else None
            return switch_group(self.directory,current.name if current else selected,self.client,
                                binding=current.calibration if current else None,cancelled=cancelled,
                                contact=current.contact if current else contact)
        def reload_selection():
            self.switch_busy=False;self.settings=Settings.load(self.directory)
            self.owner_tone.set(self.settings.owner_confirmed)
            self.target_group.set(self.settings.group);self.at_unique.set(self.settings.self_name_unique_confirmed)
            self.view.set_switch_busy(False);self.view.render()
            if self.view.page in ('assistant','pending'):self.view.refresh_pending()
        def done(data):
            reload_selection();self.show_status(data)
            self.notice.set(f'已切换到“{self.settings.group}”，仅回复这个会话的新消息。' if data.get('mode')=='auto' else data.get('reason','会话已切换'))
        def failed(error):
            reload_selection();self.notice.set(error)
        self.work(action,done,failed)

    def use_current_group(self):self._switch(True)
    def switch_selected_group(self):self._switch(False)

    def apply_group_settings(self,names,mode,rate,done,failed=None,monitor_all=True,stickers_enabled=None,*,contacts=None,keep_pacing=False,rule_edit=None):
        if getattr(self,'switch_busy',False):return
        def save():
            if not names:raise ValueError('请至少填写一个要回复的会话名')
            original=cfg=Settings.load(self.directory)
            old_group=cfg.group
            if contacts is not None:
                from .selection import bind_saved_conversations,load_directory
                cfg=bind_saved_conversations(cfg,names,load_directory(self.directory),contacts)
            else:cfg=cfg.with_saved_conversations(names)
            if rule_edit:
                name,scope,preferences=rule_edit
                target=cfg.conversation_targets.get(name,{})
                if scope!=target.get('account','')+'/'+target.get('id',''):
                    raise ValueError('会话身份已改变，请重新选择')
                cfg=cfg.with_conversation_rules(name,preferences)
            cfg.reply_mode=mode
            cfg.monitor_all_groups=monitor_all
            if stickers_enabled is not None:cfg.stickers_enabled=stickers_enabled
            if not keep_pacing:
                cfg.min_interval,cfg.hourly_limit,cfg.minute_limit,cfg.pacing_jitter=rate
                cfg.natural_pacing=True
                cfg.context_limit=24
                if cfg.min_interval<=5:cfg.poll_seconds,cfg.idle_seconds=1,2
            # Saving listening rules never requires a visible window. The runtime
            # validates each recipient's window again before sending any input.
            if cfg.group!=old_group:cfg.self_name_unique_confirmed=False
            cfg.__post_init__()
            return apply_saved_settings(self.client,original,cfg)
        def saved(result):
            self.settings=Settings.load(self.directory)
            self.target_group.set(self.settings.group)
            self.owner_tone.set(self.settings.owner_confirmed)
            self.at_unique.set(self.settings.self_name_unique_confirmed)
            self.view.render();self.show_status(result);done()
        self.work(save,saved,failed,label='应用监听设置')

    def apply_pacing_settings(self,rate,done,failed):
        rate=tuple(rate)
        def save():
            from .config import validate_send_pacing
            validate_send_pacing(*rate)
            original=Settings.load(self.directory)
            minimum,hour,minute,jitter=rate
            updated=replace(original,min_interval=minimum,hourly_limit=hour,
                            minute_limit=minute,pacing_jitter=jitter,natural_pacing=True)
            return apply_saved_settings(self.client,original,updated)
        def saved(result):
            self.settings=Settings.load(self.directory)
            self.view.render();self.show_status(result);done()
        self.work(save,saved,failed,label='保存发送节奏')

    def test_model(self):
        from .domain import Message
        if self.testing_model:
            return
        self.testing_model=True
        self.notice.set("正在连接 DeepSeek，请稍候…")
        def test():
            model=ChatModel(Settings.load(self.directory))
            available=model.models()
            if model.settings.model_name not in available:
                raise ValueError("当前模型不在此密钥可用的模型列表中")
            result=model.reply([Message("小猫，只回复连接正常","本机连接测试")])
            return "DeepSeek 已连接并返回文字；未发送到微信"
        def finished(message):
            self.testing_model=False
            self.notice.set(message)
        self.work(test,finished,finished,mutates=False,label='测试模型连接')

    def show_pending(self,data):
        from .ui_labels import member_label
        tasks=data.get("tasks",[])
        from .member_names import known_member,safe_name
        people={};lines=[]
        for t in tasks:
            group=t.get('group',self.settings.group)
            if known_member(t['sender']) and safe_name(t.get('sender_name')):
                people.setdefault(group,{})[t['sender']]=safe_name(t['sender_name'])
        for t in tasks:
            group=t.get('group',self.settings.group)
            who=member_label(t['sender'],people.setdefault(group,{}),'对方')
            body=f"[{group}] {who}：{' / '.join(t['messages'])}"
            if t.get('progress'):body+='\n'+t['progress']
            if t['reply']:body+='\n回复：'+t['reply']
            lines.append(body)
        self.set_preview("\n\n".join(lines) if lines else "当前没有待回复消息")

    def set_preview(self,text):
        if getattr(self,'_preview_text',None)==text:return
        self._preview_text=text
        self.preview.configure(state="normal")
        self.preview.delete("1.0","end")
        self.preview.insert("end",text)
        self.preview.configure(state="disabled")

    def edit_persona(self):
        self.view.select('persona')

    def edit_companion(self):
        if self.switch_busy:return
        self.view.select('companion')

    def calibrate(self,target=None,*,auto_match=False):
        from .calibration_ui import CalibrationFlow
        flow=getattr(self,'calibration_flow',None)
        if flow:
            flow.present()
            return
        if self.switch_busy:return
        self.calibration_flow=CalibrationFlow(self,target or self.settings.group,auto_match=auto_match)

    def save_calibration(self,calibration,cancelled,close,failed):
        def save():
            from .selection import bind_reviewed_window,verify_reviewed_window,stop_confirmed
            cfg=Settings.load(self.directory)
            checked,contact=verify_reviewed_window(cfg,calibration,cancelled)
            updated=bind_reviewed_window(cfg,checked,contact,stage_for_enable=True)
            if cancelled.is_set():raise ValueError('校准已取消')
            if Settings.load(self.directory)!=cfg:raise ValueError('校准期间设置已变化，请重新核对')
            updated.save(expected=cfg)
            try:previous=self.client.start(initial_mode='observe')
            except ControlUnavailable as error:
                raise ValueError('校准已保存，后台未能连接。'+str(error)) from None
            try:result=self.client.reload_windows()
            except ControlUnavailable as error:
                if '没有这个控制操作' not in str(error):
                    raise ValueError('校准已保存，后台暂未确认应用。'+str(error)) from None
                if previous.get('mode')!='paused' or self.client.pending().get('tasks'):
                    raise ValueError('校准已保存。旧版后台仍在运行，待本轮结束后停止并打开新版即可应用') from None
                stop_confirmed(self.directory,self.client)
                result=self.client.start(initial_mode='paused')
            return checked.target_group,result
        def saved(value):
            target,result=value
            self.settings=Settings.load(self.directory);self.target_group.set(self.settings.group)
            self.owner_tone.set(self.settings.owner_confirmed)
            self.at_unique.set(self.settings.self_name_unique_confirmed)
            self.view.render();self.show_status(result)
            close()
            self.view.focus_conversation(target)
            if result.get('window_reload_pending'):
                next_step='，正在等待后台应用'
            elif self.settings.is_conversation_paused(target):
                next_step='，已在工作台选中，点「开启此会话」开始监听'
            else:
                next_step='并已应用'
            self.notice.set(f'“{target}”窗口校准已保存'+next_step)
        self.work(save,saved,failed,label='保存窗口校准')

    def close(self):
        if self.closing or getattr(self,'exiting',False) or getattr(self,'_close_prompting',False):return
        from .feedback_ui import ask_close_action
        self._close_prompting=True
        try:
            status=getattr(self.view,'status',{})
            choice=ask_close_action(self.root,background_available=status.get('tray',{}).get('running',True))
        finally:self._close_prompting=False
        if self.closing or getattr(self,'exiting',False):return
        if choice=='exit':self.stop_and_exit()
        elif choice=='background':self.hide_panel()

    def hide_panel(self):
        if self.closing or getattr(self,'exiting',False):return
        status=getattr(self.view,'status',{})
        if 'tray' in status and not status['tray'].get('running'):
            self.show_error('系统托盘入口暂不可用，保留面板；可以稍后重试，或使用“完全退出”')
            return
        if self.switch_busy or getattr(self,'pending_mutations',0):
            if self.switch_busy:self.switch_cancel.set()
            self.reason.set('正在完成设置操作，随后关闭面板并保留后台…')
            self.root.after(100,self.hide_panel)
            return
        for key,page in self.view.pages.items():
            if hasattr(page,'can_leave') and not page.can_leave():
                self.view.select(key)
                self.show_error('有尚未保存的修改，请先保存或撤销，再关闭面板')
                return
        self.closing=True
        self.root.destroy()

    def _minimize_to_tray(self,event):
        if event.widget is not self.root or self.closing:return
        if self.root.state()=='iconic' and getattr(self.view,'status',{}).get('tray',{}).get('running'):
            def hide():
                if not self.closing and self.root.state()=='iconic':self.root.withdraw()
            self.root.after_idle(hide)

    def _exit_has_unsaved_changes(self):
        """Inspect existing form snapshots without saving or exposing their text."""
        settings=getattr(self,'settings',None)
        baseline=getattr(self,'_form_baseline',{})
        if settings is not None:
            for name,field in Panel._account_fields.items():
                if hasattr(self,name) and getattr(self,name).get()!=baseline.get(name,getattr(settings,field)):
                    return True
        if hasattr(self,'secret') and self.secret.get().strip():return True
        for key,page in self.view.pages.items():
            if hasattr(page,'can_leave') and not page.can_leave():return True
            if hasattr(page,'dirty') and page.dirty():return True
            if any(entry.get().strip() for entry in getattr(page,'key_entries',())):return True
            if key=='persona':
                if getattr(page,'document_drafts',{}):return True
                if getattr(page,'loaded',False):
                    from .persona import join_persona
                    if join_persona(page.sections)!=page.source:return True
                    if not page.previewing and page.editor.get('1.0','end-1c')!=page.shown:return True
                dialog=getattr(page,'import_dialog',None)
                if dialog is not None and dialog.winfo_exists() and dialog.editor.get('1.0','end-1c').strip():return True
            pacing=getattr(page,'pacing_editor',None)
            if pacing is not None and settings is not None:
                from .sessions_ui import parse_rate
                try:
                    value=parse_rate([variable.get() for variable in pacing.rate_values])
                    saved=Settings.load(self.directory)
                    persisted=(saved.min_interval,saved.hourly_limit,saved.minute_limit,saved.pacing_jitter)
                    original=(settings.min_interval,settings.hourly_limit,settings.minute_limit,settings.pacing_jitter)
                    if value not in (persisted,original):return True
                except ValueError:return True
            if key=='companion' and getattr(page,'ready',False):
                try:
                    if page.values()!=page.loaded_preferences:return True
                    if page.routines_editor.values()!=page.loaded_preferences.routines:return True
                except ValueError:return True
        return False

    def stop_and_exit(self):
        if getattr(self,'exiting',False):return True
        if self.closing or getattr(self,'_exit_prompting',False):return False
        if self._exit_has_unsaved_changes():
            from tkinter import messagebox
            self._exit_prompting=True
            try:
                if not messagebox.askokcancel('完全退出','有未保存修改。要放弃这些修改，结束全部会话的监听、定时任务和后台进程吗？',parent=self.root):
                    return False
            finally:self._exit_prompting=False
        self.exiting=True
        if hasattr(self.view,'set_exiting'):self.view.set_exiting(True)
        self.switch_cancel.set()
        self.reason.set("正在结束后台进程，完成后将关闭界面…")
        def stopped(_):
            self.closing=True
            self.root.destroy()
        def stop():
            from .selection import stop_confirmed
            stop_confirmed(self.directory,self.client)
        def failed(error):
            self.exiting=False
            if hasattr(self.view,'set_exiting'):self.view.set_exiting(False)
            self.show_error(error)
        self.work(stop,stopped,failed,allow_exit=True)
        return True


def launch(directory: Path):
    from .panel_entry import PanelLease,signal_panel,watch_panel_signal
    lease=PanelLease(directory)
    try:
        if not lease.owner:
            for _ in range(20):
                if signal_panel(directory):return
                threading.Event().wait(.1)
            raise ValueError('助手面板正在打开，请稍后从托盘重试')
        lease.open_signal()
        root=tk.Tk()
        panel=Panel(root,directory)
        root.after(200,lambda:watch_panel_signal(root,lease,on_exit=panel.stop_and_exit,on_show=panel.connect_worker))
        root.mainloop()
    finally:lease.close()
