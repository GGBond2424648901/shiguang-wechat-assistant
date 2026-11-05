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
        root.title("小猫 · 微信聊天助手")
        width=min(1200,root.winfo_screenwidth()-42);height=min(800,root.winfo_screenheight()-85)
        root.geometry(f"{width}x{height}")
        root.minsize(min(930,width),min(620,height))
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW",self.close)
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
        self.owner_tone=tk.BooleanVar(value=self.settings.owner_confirmed)
        self.model_name=tk.StringVar(value=self.settings.model_name)
        self.secret=tk.StringVar(value="")
        self.secret_state=tk.StringVar(value="已使用 Windows DPAPI 加密保存；留空保留" if CredentialStore(self.settings.runtime).available else "尚未设置 DeepSeek 加密凭据")
        from .theme import GlassView,titlebar
        self.view=GlassView(self)
        self.view.pack(fill="both",expand=True)
        root.after(30,lambda:titlebar(root))
        root.after(100,self.drain)
        root.after(120,self.poll)
        self.work(self.client.start,self.show_status)

    def work(self, action, success=None, failure=None,*,mutates=True,allow_exit=False):
        if mutates:self.pending_mutations+=1
        def run():
            try:
                if mutates:
                    with self.mutation_lock:
                        if self.exiting and not allow_exit:raise ValueError('助手正在退出，本次操作已取消')
                        result=action()
                else:result=action()
                self.results.put((success,result))
            except Exception as error:
                safe=str(error) if isinstance(error,(ControlUnavailable,ValueError,DesktopUnavailable)) else "操作未完成，请检查设置或运行环境"
                self.results.put((failure or self.show_error,safe))
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
                callback(value)
        self.root.after(100,self.drain)

    def show_error(self,error):
        self.notice.set(error)

    def show_status(self,data):
        if self.connection_error and self.notice.get()==self.connection_error:
            self.notice.set("")
        self.connection_error=None
        modes={"observe":"只观察","auto":"自动回复已开启","paused":"已暂停","stopped":"已停止"}
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
        if data.get('at_enabled'):at+=' · 额外群昵称已配置'
        calibrated="窗口已校准" if data.get("calibrated") else "需要校准窗口"
        counts=data.get("counts",{})
        self.details.set(f"{calibrated}  ·  {at}  ·  已核对 {counts.get('verified',0)}  ·  结果未知 {counts.get('unknown',0)}  ·  待处理 {data.get('pending_count',0)}")

    def poll(self):
        if self.closing:
            return
        if not self.poll_busy:
            self.poll_busy=True
            def done(data):
                self.poll_busy=False
                previous=self.view.status
                self.show_status(data)
                if self.view.page in ('assistant','pending') and (data.get('pending_count') or any(previous.get(k)!=data.get(k) for k in ('group','mode','reason','pending_count','counts'))):
                    self.view.refresh_pending(background=True)
            def failed(error):
                self.poll_busy=False
                self.mode.set("未连接本地助手")
                self.connection_error=error
                self.view.status={'running':False,'mode':'disconnected','reason':error}
                self.view.update_text()
                if self.view.page in ('assistant','pending'):
                    self.view._queue_request=None
                    self.set_preview('暂时无法读取队列：'+error)
                self.notice.set(error)
            self.work(self.client.status,done,failed,mutates=False)
        self.root.after(1500,self.poll)

    def save_settings(self):
        display,model,key=self.display_name.get().strip(),self.model_name.get(),self.secret.get()
        unique=self.at_unique.get()
        auto_on_launch=self.auto_on_launch.get()
        split_replies=self.split_replies.get()
        private_key,owner_tone=self.settings.private_tone_key,self.owner_tone.get()
        self.secret.set("")
        def save():
            from .selection import stop_confirmed
            try:previous_mode=self.client.status().get('mode')
            except ControlUnavailable:previous_mode=None
            cfg=Settings.load(self.directory)
            if cfg.private_tone_key!=private_key:raise ValueError('回复对象已变化，请在当前会话重新保存设置')
            cfg.self_display_name=display
            cfg.self_name_unique_confirmed=unique
            cfg.auto_on_launch=auto_on_launch
            cfg.split_replies=split_replies
            cfg.set_owner_tone(owner_tone)
            if unique:
                cfg.at_note=""
            cfg.model_base_url="https://api.deepseek.com"
            cfg.model_name=model
            cfg.__post_init__()
            stop_confirmed(self.directory,self.client)
            if key.strip():
                CredentialStore(cfg.runtime).set_key(key)
            cfg.save()
            return self.client.start(initial_mode=previous_mode) if previous_mode in ('paused','observe','auto') else self.client.start()
        def saved(result):
            self.settings=Settings.load(self.directory)
            self.secret_state.set("已使用 Windows DPAPI 加密保存；留空保留" if CredentialStore(self.settings.runtime).available else "尚未设置 DeepSeek 加密凭据")
            self.show_status(result)
        self.work(save,saved)

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
        self.work(act,self.show_status)

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

    def apply_group_settings(self,names,mode,rate,done,failed=None,monitor_all=True,stickers_enabled=None,*,contacts=None,keep_pacing=False):
        if getattr(self,'switch_busy',False):return
        def save():
            if not names:raise ValueError('请至少填写一个要回复的会话名')
            try:previous_mode=self.client.status().get('mode')
            except ControlUnavailable:previous_mode=None
            cfg=Settings.load(self.directory)
            old_group=cfg.group
            if contacts is not None:
                from .selection import bind_saved_conversations,load_directory
                cfg=bind_saved_conversations(cfg,names,load_directory(self.directory),contacts)
            else:cfg=cfg.with_saved_conversations(names)
            cfg.reply_mode=mode
            cfg.monitor_all_groups=monitor_all
            if stickers_enabled is not None:cfg.stickers_enabled=stickers_enabled
            if not keep_pacing:
                cfg.min_interval,cfg.hourly_limit,cfg.minute_limit,cfg.pacing_jitter=rate
                cfg.natural_pacing=True
                cfg.context_limit=24
                if cfg.min_interval<=5:cfg.poll_seconds,cfg.idle_seconds=1,2
            from .selection import refresh_binding
            cfg.calibration=asdict(refresh_binding(cfg))
            if cfg.group!=old_group:cfg.self_name_unique_confirmed=False
            cfg.__post_init__()
            from .selection import stop_confirmed
            stop_confirmed(self.directory,self.client)
            cfg.save()
            return self.client.start(initial_mode=previous_mode) if previous_mode in ('paused','observe','auto') else self.client.start()
        def saved(result):
            self.settings=Settings.load(self.directory)
            self.target_group.set(self.settings.group)
            self.owner_tone.set(self.settings.owner_confirmed)
            self.at_unique.set(self.settings.self_name_unique_confirmed)
            self.view.render();self.show_status(result);done()
        self.work(save,saved,failed)

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
        self.work(test,finished,finished,mutates=False)

    def show_pending(self,data):
        from .ui_labels import member_label
        tasks=data.get("tasks",[])
        people={};lines=[]
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

    def calibrate(self):
        if self.switch_busy:return
        target=self.target_group.get()
        self.notice.set(f'正在读取“{target}”的微信窗口并校准…')
        def action():
            self.client.pause()
            return self.capture_calibration(target)
        self.work(action,self.open_calibration)

    def capture_calibration(self,target=None):
        from .selection import capture_calibration
        return capture_calibration(self.directory,target or self.settings.group)

    def open_calibration(self,value):
        from .calibration_ui import CalibrationDialog
        image,calibration=value
        CalibrationDialog(self,image,calibration)

    def save_calibration(self,calibration):
        def save():
            from .selection import switch_group,load_directory,directory_for_settings
            cfg=Settings.load(self.directory)
            target=calibration.target_group
            contact=None
            if cfg.receive_mode=='database':
                entries=load_directory(self.directory) if cfg.binding else directory_for_settings(replace(cfg,calibration=asdict(calibration)))
                rows=[r for r in entries if r.name==target and r.selectable]
                if len(rows)!=1:raise ValueError('校准对象未能与联系人列表唯一对应')
                contact=rows[0]
            return switch_group(self.directory,target,self.client,binding=calibration,contact=contact)
        def saved(result):
            self.settings=Settings.load(self.directory);self.target_group.set(self.settings.group)
            self.owner_tone.set(self.settings.owner_confirmed)
            self.at_unique.set(self.settings.self_name_unique_confirmed)
            self.view.render();self.show_status(result);self.notice.set('校准已保存，后台已重新启动')
        self.work(save,saved)

    def close(self):
        if self.closing or getattr(self,'exiting',False):return
        if self.switch_busy or getattr(self,'pending_mutations',0):
            if self.switch_busy:self.switch_cancel.set()
            self.reason.set('正在完成设置操作，随后关闭面板并保留后台…')
            self.root.after(100,self.close)
            return
        self.closing=True
        self.root.destroy()

    def stop_and_exit(self):
        if self.closing:
            return
        self.exiting=True
        self.switch_cancel.set()
        self.reason.set("正在停止助手…")
        def stopped(_):
            self.closing=True
            self.root.destroy()
        def stop():
            from .selection import stop_confirmed
            stop_confirmed(self.directory,self.client)
        def failed(error):
            self.exiting=False;self.show_error(error)
        self.work(stop,stopped,failed,allow_exit=True)


def launch(directory: Path):
    root=tk.Tk()
    Panel(root,directory)
    root.mainloop()
