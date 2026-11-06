"""Identity-scoped reply preferences; editing a row never switches WeChat."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk

from .client import ControlClient, ControlUnavailable
from .config import Settings
from .reply_preferences import REPLY_MODES, STICKER_FREQUENCIES, validate_rule_changes
from .workspace_ui import PAPER, MUTED, LINE, FONT, button, label


def persist_preferences(root,preferences,group=None,scope=None):
    validate_rule_changes(preferences,allow_inherit=group is not None)
    client=ControlClient(root)
    try:client.status()
    except ControlUnavailable:
        from .windows import worker_scope
        if worker_scope(root) is not None:
            raise ControlUnavailable('后台正在运行但暂未连接，未修改设置，请稍后重试')
        cfg=Settings.load(root)
        if group is not None:
            target=cfg.conversation_targets.get(group,{})
            if scope!=target.get('account','')+'/'+target.get('id',''):
                raise ValueError('会话身份已改变，请重新选择')
            cfg=cfg.with_conversation_rules(group,preferences)
        else:cfg=replace(cfg,**preferences)
        cfg.save()
        return {'offline':True}
    return (client.configure_reply_rules(group,scope,preferences) if group is not None
            else client.configure_reply_defaults(preferences))


class ReplyRulesEditor(tk.Frame):
    def __init__(self,parent,panel,*,defaults=False,on_saved=None):
        super().__init__(parent,bg=PAPER)
        self.panel,self.defaults,self.on_saved=panel,defaults,on_saved
        self.group=self.scope=self.baseline=None
        self.busy=False
        self.mode=tk.StringVar(self,value='natural')
        self.frequency=tk.StringVar(self,value='自然')
        self.inherit_mode=tk.BooleanVar(self,value=True)
        self.inherit_stickers=tk.BooleanVar(self,value=True)
        self.note=tk.StringVar(self)
        self.controls=[]
        self.title=label(self,'默认表情频率' if defaults else '当前会话的回复规则',15,bold=True)
        self.title.pack(anchor='w')
        self.recipient=label(self,'所有继承默认值的会话' if defaults else '选择左侧会话进行设置',10,color=MUTED)
        self.recipient.pack(fill='x',pady=(4,12))
        if not defaults:
            check=ttk.Checkbutton(self,text='回复方式跟随默认',variable=self.inherit_mode,command=self._states)
            check.pack(anchor='w',pady=(0,7));self.controls.append(check)
            self.radios=[]
            descriptions={'natural':'接话、参与话题；不必每条都回', 'mentions':'只回 @小玲 或明确点名', 'all':'每位成员的新消息都进入回复流程'}
            for key in ('natural','mentions','all'):
                row=tk.Frame(self,bg=PAPER,highlightthickness=1,highlightbackground=LINE,padx=9,pady=7)
                row.pack(fill='x',pady=(0,5))
                radio=ttk.Radiobutton(row,text=REPLY_MODES[key],value=key,variable=self.mode)
                radio.pack(anchor='w');self.radios.append(radio)
                desc=label(row,descriptions[key],9,color=MUTED);desc.pack(fill='x',padx=(23,0))
                row.bind('<Configure>',lambda e,w=desc:w.configure(wraplength=max(120,e.width-48)))
            tk.Frame(self,bg=LINE,height=1).pack(fill='x',pady=10)
            label(self,'表情表达',11,bold=True).pack(anchor='w')
            check=ttk.Checkbutton(self,text='表情频率跟随默认',variable=self.inherit_stickers,command=self._states)
            check.pack(anchor='w',pady=(5,6));self.controls.append(check)
        self.picker=ttk.Combobox(self,textvariable=self.frequency,values=list(STICKER_FREQUENCIES.values()),
                               state='readonly',font=(FONT,11),width=12)
        self.picker.pack(fill='x',pady=(0,6))
        self.help=label(self,'',9,color=MUTED);self.help.pack(fill='x',pady=(0,8))
        self.frequency.trace_add('write',lambda *_:self._help())
        self.actions=tk.Frame(self,bg=PAPER);self.actions.pack(fill='x',pady=(6,0))
        self.apply=button(self.actions,'保存默认频率' if defaults else '保存当前会话',self.save,primary=True,icon_name='save')
        self.apply.pack(fill='x')
        if not defaults:
            self.discard_button=button(self.actions,'撤销未保存修改',self.discard,compact=True)
            self.discard_button.pack(anchor='w',pady=(6,0))
        self.feedback=label(self,textvariable=self.note,size=9,color=MUTED)
        self.feedback.pack(fill='x',pady=(7,0))
        self.bind('<Configure>',self._resize)
        self._help()
        if defaults:self.select(None,force=True)

    def _resize(self,event):
        for w in (self.recipient,self.help,self.feedback):w.configure(wraplength=max(120,event.width-8))

    def _help(self):
        self.help.configure(text={
            '仅按需':'有人明确索图才发表情，其他时候用文字',
            '偶尔':'少量点缀，两次表情至少隔 120 秒和 3 条文字',
            '自然':'按语境选择，至少隔 60 秒和 2 条文字',
            '爱用表情':'更常用图表达情绪，自动配图至少隔 20 秒；也可只回一张图',
        }.get(self.frequency.get(),'请选择表达偏好')+'。仍受总发送额度限制，明确索图另按请求处理。')

    def _snapshot(self):
        return (self.inherit_mode.get(),self.mode.get(),self.inherit_stickers.get(),self.frequency.get())

    def dirty(self):return self.baseline is not None and self._snapshot()!=self.baseline

    def select(self,name,*,force=False):
        if self.busy and not force:return False
        if not force and self.dirty():
            if name!=self.group:self.note.set('当前会话有未保存修改，请先保存或撤销，再切换')
            return name==self.group
        cfg=Settings.load(self.panel.directory)
        target=cfg.conversation_targets.get(name,{})
        self.group=name
        self.scope=target.get('account','')+'/'+target.get('id','') if target else None
        overrides=cfg.conversation_rules.get(self.scope,{}) if not self.defaults else {}
        self.inherit_mode.set('reply_mode' not in overrides)
        self.inherit_stickers.set('sticker_frequency' not in overrides)
        self.mode.set(overrides.get('reply_mode',cfg.reply_mode))
        self.frequency.set(STICKER_FREQUENCIES[overrides.get('sticker_frequency',cfg.sticker_frequency)])
        if not self.defaults:self.recipient.configure(text=name or '选择左侧会话进行设置')
        self.baseline=self._snapshot();self.note.set('')
        if not self.defaults and not self.scope:self.note.set('先保存会话并绑定准确身份，再设置独立规则')
        self._states()
        return True

    def _states(self):
        enabled=not self.busy and (self.defaults or bool(self.scope))
        for w in self.controls:w.configure(state='normal' if enabled else 'disabled')
        for w in getattr(self,'radios',[]):w.configure(state='normal' if enabled and not self.inherit_mode.get() else 'disabled')
        self.picker.configure(state='readonly' if enabled and (self.defaults or not self.inherit_stickers.get()) else 'disabled')
        self.apply.configure(state='normal' if enabled else 'disabled')
        if hasattr(self,'discard_button'):self.discard_button.configure(state='disabled' if self.busy else 'normal')

    def discard(self):
        if not self.busy:self.select(self.group,force=True);self.note.set('已恢复已保存的设置')

    def save(self):
        if self.busy or (not self.defaults and not self.scope):return
        preferences={'sticker_frequency':next(k for k,v in STICKER_FREQUENCIES.items() if v==self.frequency.get())}
        if not self.defaults:
            preferences['reply_mode']=None if self.inherit_mode.get() else self.mode.get()
            if self.inherit_stickers.get():preferences['sticker_frequency']=None
        group,scope=self.group,self.scope
        self.busy=True;self._states();self.note.set('正在保存…')
        def done(result):
            if not self.winfo_exists():return
            self.busy=False
            self.panel.settings=Settings.load(self.panel.directory)
            self.select(group,force=True)
            pending=any(r.get('rules_pending') for r in result.get('groups',[]) if self.defaults or r['name']==group)
            if self.on_saved:self.on_saved()
            self.note.set('已保存，下次启动生效' if result.get('offline') else
                          '已保存，待当前发送结束后应用' if pending else '已保存并应用，后续回复使用新规则')
        def failed(error):
            if self.winfo_exists():self.busy=False;self._states();self.note.set(str(error))
        self.panel.work(lambda:persist_preferences(self.panel.directory,preferences,group,scope),done,failed,
                        label='保存默认表情频率' if self.defaults else '保存会话规则')
