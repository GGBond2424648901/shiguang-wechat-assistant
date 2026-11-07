"""Runtime preferences and masked credential entry for the native workspace."""
import tkinter as tk
from tkinter import ttk, messagebox

from .credentials import CredentialError, CredentialStore
from .persona_ui import BG, FONT, INK, LINE, MUTED, PAPER, PEACH, SELECTED, _action, _card


class SettingsPage(tk.Frame):
    def __init__(self,parent,panel):
        super().__init__(parent,bg=BG)
        self.panel=panel
        self.scope=None
        self.key_count=0
        self.key_busy=False
        self.key_rows=()
        self.key_delete_buttons={}
        self.pacing_editor=None
        self.key_mode=tk.StringVar(self,value='append')
        self.key_entries=[]
        self.key_status=tk.StringVar(self)
        self.entry_status=tk.StringVar(self)
        self.scope_text=tk.StringVar(self)
        self.calibration_text=tk.StringVar(self)
        self.rate_text=tk.StringVar(self)
        self.receive_text=tk.StringVar(self)
        self.route_note=tk.StringVar(self)
        self.account_text=tk.StringVar(self)
        self._wide=None
        self._build()
        self.on_show()

    def _refresh_count(self):
        try:
            self.key_rows=CredentialStore(self.panel.settings.runtime).list_keys()
            self.key_count=len(self.key_rows)
        except CredentialError as error:
            self.key_rows=();self.key_count=0;self.key_status.set(str(error))
        else:
            self.key_status.set(f'已加密保存 {self.key_count} 个密钥' if self.key_count else '尚未保存密钥')
        self.panel.secret_state.set(self.key_status.get())
        self._render_keys()

    def _render_keys(self):
        for widget in self.key_list.winfo_children():widget.destroy()
        self.key_delete_buttons={}
        for index,key in enumerate(self.key_rows):
            row=tk.Frame(self.key_list,bg=PAPER,height=38)
            row.pack(fill='x');row.pack_propagate(False)
            tk.Label(row,text=f'密钥 {index+1}',bg=PAPER,fg=INK,font=(FONT,10),anchor='w'
                     ).pack(side='left',padx=(0,12))
            action=_action(row,'删除',lambda key=key:self.delete_key(key))
            action.configure(takefocus=True,state='normal' if self.key_count>1 and not self.key_busy else 'disabled')
            action.pack(side='right',pady=3)
            action.bind('<FocusIn>',lambda event,i=index:self.key_canvas.yview_moveto(i/max(1,self.key_count)))
            tk.Label(row,text=f'••••  {key.suffix}',bg=PAPER,fg=MUTED,font=(FONT,10),anchor='w'
                     ).pack(side='left',fill='x',expand=True)
            self.key_delete_buttons[key.identifier]=action
            for child in (row,*row.winfo_children()):child.bind('<MouseWheel>',self._key_wheel)
            self._tag_controls(row)
        if not self.key_rows:
            tk.Label(self.key_list,text='保存后将在这里显示安全编号与末四位',bg=PAPER,fg=MUTED,
                     font=(FONT,9),anchor='w').pack(fill='x',pady=5)
        self.key_canvas.configure(height=min(3,max(1,self.key_count))*38)
        if self.key_count>3:self.key_scroll.pack(side='right',fill='y')
        else:self.key_scroll.pack_forget()

    def _key_wheel(self,event):
        if self.key_count>3 and event.delta:
            self.key_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
            return 'break'

    def _key_busy(self,busy):
        self.key_busy=busy
        for button in self.key_action_buttons:button.configure(state='disabled' if busy else 'normal')
        for button in self.key_delete_buttons.values():
            button.configure(state='disabled' if busy or self.key_count<=1 else 'normal')
        self._entry_state()

    def delete_key(self,key):
        if self.key_busy:return
        if self.key_count<=1:
            self.panel.notice.set('请至少保留一个密钥；可先追加新密钥，再删除旧密钥')
            return
        if not messagebox.askokcancel('删除已保存的密钥？',
                f'将删除末四位为 {key.suffix} 的密钥。其余密钥继续保留。',
                parent=self.winfo_toplevel(),icon='warning'):
            self.panel.notice.set('已取消删除，已保存的密钥保持不变')
            return
        runtime=self.panel.settings.runtime
        self._key_busy(True)
        def remove():
            try:CredentialStore(runtime).remove_key(key.identifier)
            except CredentialError as error:raise ValueError(str(error)) from None
        def done(_):
            self._key_busy(False);self._refresh_count()
            self.panel.notice.set(f'密钥已删除，仍保留 {self.key_count} 个密钥。')
        def failed(error):
            self._key_busy(False);self._refresh_count();self.panel.notice.set(error)
        self.panel.work(remove,done,failed,label='删除加密密钥')

    def on_show(self):
        self._refresh_count()
        self.refresh_scope()

    def refresh_scope(self):
        cfg=self.panel.settings
        live=getattr(getattr(self.panel,'view',None),'status',{})
        account=live.get('account_display_name') or cfg.account_display_name or '等待自动检测'
        state=live.get('account_status') or '常驻后台自动检测换号，按账号保存监听规则与记忆'
        self.account_text.set('当前微信：'+account+'\n'+state)
        if self.scope is not None and self.scope!=(cfg.memory_scope,cfg.private_tone_key):
            self.panel.owner_tone.set(cfg.owner_confirmed)
            self.panel.at_unique.set(cfg.self_name_unique_confirmed)
        self.scope=(cfg.memory_scope,cfg.private_tone_key)
        self.scope_text.set(cfg.group)
        self.route_picker.configure(values=cfg.allowed_groups)
        if self.route_picker.get() not in cfg.allowed_groups:self.route_picker.set(cfg.group)
        self.refresh_route()
        self.rate_text.set(f'{cfg.minute_limit} 条 / 分钟  ·  {cfg.hourly_limit} 条 / 小时')
        self.owner_check.configure(state='normal' if cfg.private_tone_key else 'disabled')
        screen=cfg.receive_mode=='screen'
        self.receive_text.set('屏幕读取 · 等待微信可见' if screen else '数据库监听 · 微信最小化时也可接收')
        self.split_check.configure(state='disabled' if screen else 'normal',
                                  text='屏幕读取仅发单条回复' if screen else '分气泡发送')

    def refresh_route(self,*_):
        name=self.route_picker.get()
        binding=self.panel.settings.binding_for(name)
        calibrated=bool(binding and binding.bottom_confirmed)
        self.calibration_text.set('●  已校准' if calibrated else '○  待校准')
        self.calibration_badge.configure(bg='#E2F3E1' if calibrated else '#F8EBD9',fg='#31663E' if calibrated else '#865E37')
        kind='独立聊天窗口' if binding and binding.window_kind=='detached' else '共享微信主窗口' if binding else '尚未绑定发送窗口'
        live=getattr(getattr(self.panel,'view',None),'status',{})
        row=next((r for r in live.get('groups',[]) if r['name']==name),{})
        self.route_note.set(' · '.join(s for s in (kind,row.get('window_state','')) if s))

    def _add_entry(self):
        if len(self.key_entries)>=CredentialStore.MAX_KEYS:return
        var=tk.StringVar(self)
        entry=tk.Entry(self.key_inputs,textvariable=var,show='●',bg=PAPER,fg=INK,
                       insertbackground=INK,relief='flat',borderwidth=0,highlightbackground=LINE,
                       highlightcolor=PEACH,highlightthickness=1,font=(FONT,11),width=20)
        entry.key_var=var # keep Tk's variable alive as long as this input
        entry.pack(fill='x',ipady=5,pady=(0,4))
        self.key_entries.append(entry)
        var.trace_add('write',self._entry_state)
        self.input_canvas.configure(height=min(len(self.key_entries),3)*34)
        if len(self.key_entries)>3:
            self.input_scroll.pack(side='right',fill='y')
            self.after_idle(lambda:self.input_canvas.yview_moveto(1))
        if len(self.key_entries)>1:entry.focus_set()
        if len(self.key_entries)>=CredentialStore.MAX_KEYS:self.add_button.configure(state='disabled')
        self._entry_state()

    def _entry_state(self,*_):
        filled=sum(bool(entry.get().strip()) for entry in self.key_entries)
        self.entry_status.set(f'{filled} 条密钥输入待保存' if filled else '输入的新密钥仅在保存后生效')
        if hasattr(self,'clear_input'):
            self.clear_input.configure(state='normal' if filled and not self.key_busy else 'disabled')

    def _clear_inputs(self):
        if self.key_busy:return
        for entry in self.key_entries:entry.delete(0,'end')
        self.key_entries[0].focus_set()

    def save_keys(self,after=None):
        if self.key_busy:return
        snapshot=[entry.get() for entry in self.key_entries]
        values=[line for value in snapshot for line in value.splitlines() if line.strip()]
        if not values:
            if after:after()
            else:
                self.key_mode.set('append')
                self.panel.notice.set('请输入要保存的新密钥。')
            return
        mode=self.key_mode.get()
        if mode=='replace' and self.key_count and not messagebox.askokcancel(
                '替换已保存的密钥？','原密钥将被本次输入替换。取消会保留当前密钥与输入内容。',
                parent=self.winfo_toplevel(),icon='warning'):
            self.panel.notice.set('已取消替换，原密钥保持不变')
            return
        runtime=self.panel.settings.runtime
        self._key_busy(True)
        def save():
            try:
                store=CredentialStore(runtime)
                if mode=='replace':store.set_keys(values)
                else:store.add_keys(values)
                return store.count
            except CredentialError as error:
                raise ValueError(str(error)) from None
        def done(count):
            self._key_busy(False)
            self.key_mode.set('append')
            for entry,value in zip(self.key_entries,snapshot):
                if entry.get()==value:entry.delete(0,'end')
            self._refresh_count()
            self._entry_state()
            self.panel.secret_state.set(self.key_status.get())
            self.panel.notice.set(self.key_status.get()+'，后续模型调用将使用当前列表。')
            if after:after()
        def failed(error):
            self._key_busy(False);self.panel.notice.set(error)
        self.panel.work(save,done,failed,label='保存加密密钥')

    def save(self):
        self.save_keys(after=self.panel.save_settings)

    def _test(self):
        if self.panel.model_name.get()!=self.panel.settings.model_name or any(entry.get().strip() for entry in self.key_entries):
            self.panel.notice.set('请先保存当前模型和密钥，再测试连接。')
            return
        self.panel.test_model()

    def _commit_mode(self,mode):
        self.key_mode.set(mode)
        self.save_keys()

    def open_pacing(self):
        if self.pacing_editor is not None:
            self.pacing_editor.entries[0].focus_set()
            return
        from .config import Settings
        from .pacing_ui import PacingEditor
        try:cfg=Settings.load(self.panel.directory)
        except ValueError as error:
            self.panel.notice.set(str(error));return
        def close():
            self.pacing_editor.destroy();self.pacing_editor=None
            self.refresh_scope()
        self.pacing_editor=PacingEditor(self.pacing_host,self.panel,cfg,close)
        self.pacing_editor.pack(fill='x',pady=(4,8))
        self._tag_controls(self.pacing_editor)

    def _build(self):
        self.grid_rowconfigure(0,weight=1)
        self.grid_columnconfigure(0,weight=1)
        self.bar=ttk.Scrollbar(self)
        # Reserve the scrollbar lane even when the bar is hidden. Appearing
        # and disappearing must not change the card breakpoint under the cursor.
        self.columnconfigure(1,minsize=self.bar.winfo_reqwidth())
        self.canvas=tk.Canvas(self,bg=BG,bd=0,highlightthickness=0,width=1,height=1,
                              yscrollcommand=self._scroll_changed)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        self.bar.configure(command=self.canvas.yview)
        self.body=tk.Frame(self.canvas,bg=BG)
        self.body_window=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        model=self.model_card=_card(self.body)
        wechat=self.wechat_card=_card(self.body)
        for card,title,icon in [(model,'模型与密钥','\ue81e'),(wechat,'微信与启动','\ue8bd')]:
            top=tk.Frame(card,bg=PAPER)
            top.pack(fill='x',padx=16,pady=(14,12))
            tk.Label(top,text=icon,font=('Segoe MDL2 Assets',20),fg=INK,bg=PAPER).pack(side='left',padx=(0,12))
            tk.Label(top,text=title,font=(FONT,16,'bold'),fg=INK,bg=PAPER).pack(side='left')
            tk.Frame(card,bg=LINE,height=1).pack(fill='x',padx=16)
        left=tk.Frame(model,bg=PAPER);left.pack(fill='x',padx=16,pady=(18,8))
        left.grid_columnconfigure(1,weight=1)
        def label(parent,value,row):
            tk.Label(parent,text=value,bg=PAPER,fg=INK,font=(FONT,11,'bold'),anchor='w'
                     ).grid(row=row,column=0,sticky='w',padx=(0,12))
        label(left,'模型',0)
        style=ttk.Style(self)
        style.configure('WorkspaceModel.TCombobox',padding=5,fieldbackground=PAPER,background=PAPER,
                        foreground=INK,arrowcolor=INK,bordercolor=LINE)
        style.map('WorkspaceModel.TCombobox',fieldbackground=[('readonly',PAPER)],
                  foreground=[('readonly',INK)],selectbackground=[('readonly',PAPER)],selectforeground=[('readonly',INK)])
        models=tuple(dict.fromkeys((self.panel.settings.model_name,'deepseek-flash','deepseek-v4-pro')))
        self.model_box=ttk.Combobox(left,textvariable=self.panel.model_name,values=models,
                                    state='readonly',font=(FONT,11),style='WorkspaceModel.TCombobox',width=15)
        self.model_box.grid(row=0,column=1,sticky='ew',pady=(0,6))
        label(left,'密钥',1)
        key_heading=tk.Frame(left,bg=PAPER)
        key_heading.grid(row=1,column=1,sticky='ew',pady=(10,8))
        tk.Label(key_heading,textvariable=self.key_status,fg=INK,bg=PAPER,font=(FONT,10),anchor='w'
                 ).pack(side='left',fill='x',expand=True)
        self.add_button=_action(key_heading,'输入栏',self._add_entry,icon_name='add')
        self.add_button.pack(side='right')
        saved=tk.Frame(model,bg=PAPER);saved.pack(fill='x',padx=16)
        self.key_scroll=ttk.Scrollbar(saved)
        self.key_canvas=tk.Canvas(saved,height=38,width=1,bg=PAPER,highlightthickness=0,
                                  yscrollcommand=self.key_scroll.set,yscrollincrement=38)
        self.key_canvas.pack(side='left',fill='x',expand=True)
        self.key_scroll.configure(command=self.key_canvas.yview)
        self.key_list=tk.Frame(self.key_canvas,bg=PAPER)
        key_window=self.key_canvas.create_window(0,0,anchor='nw',window=self.key_list)
        self.key_canvas.bind('<Configure>',lambda e:self.key_canvas.itemconfigure(key_window,width=e.width))
        self.key_list.bind('<Configure>',lambda e:self.key_canvas.configure(scrollregion=self.key_canvas.bbox('all')))
        self.key_canvas.bind('<MouseWheel>',self._key_wheel)
        tk.Label(model,text='只显示末四位 · 至少保留一个密钥，可先追加再删除。',fg=MUTED,bg=PAPER,
                 anchor='w',justify='left',wraplength=300,font=(FONT,9)).pack(fill='x',padx=16,pady=(5,9))
        scrollbox=tk.Frame(model,bg=PAPER);scrollbox.pack(fill='x',padx=16)
        self.input_scroll=ttk.Scrollbar(scrollbox)
        self.input_canvas=tk.Canvas(scrollbox,height=34,width=1,bg=PAPER,highlightthickness=0,
                                    yscrollcommand=self.input_scroll.set)
        self.input_canvas.pack(side='left',fill='x',expand=True);self.input_scroll.configure(command=self.input_canvas.yview)
        self.key_inputs=tk.Frame(self.input_canvas,bg=PAPER)
        window=self.input_canvas.create_window(0,0,anchor='nw',window=self.key_inputs)
        self.input_canvas.bind('<Configure>',lambda e:self.input_canvas.itemconfigure(window,width=e.width))
        self.key_inputs.bind('<Configure>',lambda e:self.input_canvas.configure(scrollregion=self.input_canvas.bbox('all')))
        self._add_entry()
        key_actions=tk.Frame(model,bg=PAPER);key_actions.pack(fill='x',padx=16,pady=(12,5))
        self.key_action_buttons=[]
        for col,(name,command) in enumerate((('追加密钥',lambda:self._commit_mode('append')),
                                              ('替换密钥',lambda:self._commit_mode('replace')),
                                              ('测试连接',self._test))):
            button=_action(key_actions,name,command);button.configure(padx=4)
            button.grid(row=0,column=col,sticky='ew',padx=(0,5 if col<2 else 0))
            key_actions.columnconfigure(col,weight=1,uniform='keys')
            self.key_action_buttons.append(button)
            if col==0:self.key_save=button
        tk.Label(model,text='追加会保留已保存的密钥。替换会用本次输入覆盖整组密钥。',fg=MUTED,bg=PAPER,
                 anchor='w',justify='left',wraplength=300,font=(FONT,9)).pack(fill='x',padx=16,pady=(3,10))
        draft=tk.Frame(model,bg='#F2F5F4');draft.pack(fill='x',padx=16,pady=(0,12))
        tk.Label(draft,textvariable=self.entry_status,fg=MUTED,bg='#F2F5F4',anchor='w',
                 justify='left',font=(FONT,9),wraplength=180).pack(side='left',fill='x',expand=True,padx=9,pady=8)
        self.clear_input=_action(draft,'清空输入',self._clear_inputs)
        self.clear_input.pack(side='right',padx=6,pady=4)
        self._entry_state()

        right=tk.Frame(wechat,bg=PAPER);right.pack(fill='x',padx=16,pady=(16,5))
        right.grid_columnconfigure(1,weight=1)
        label(right,'微信窗口',0)
        self.calibration_badge=tk.Label(right,textvariable=self.calibration_text,font=(FONT,10),padx=6,pady=2)
        self.calibration_badge.grid(row=0,column=1,sticky='w')
        _action(right,'管理监听',lambda:self.panel.view.select('sessions')).grid(row=0,column=2,sticky='e')
        label(right,'监听对象',1)
        self.route_picker=ttk.Combobox(right,state='readonly',width=12)
        self.route_picker.grid(row=1,column=1,columnspan=2,sticky='ew',pady=(8,3))
        self.route_picker.bind('<<ComboboxSelected>>',self.refresh_route)
        tk.Label(right,textvariable=self.route_note,fg=MUTED,bg=PAPER,anchor='w',font=(FONT,9)
                 ).grid(row=2,column=0,columnspan=2,sticky='w')
        self.route_calibrate=_action(right,'校准该窗口',lambda:self.panel.calibrate(self.route_picker.get()))
        self.route_calibrate.grid(row=2,column=2,sticky='e')
        tk.Label(wechat,textvariable=self.account_text,fg=MUTED,bg=PAPER,anchor='w',justify='left',
                 font=(FONT,9),wraplength=330).pack(fill='x',padx=16,pady=(7,2))
        tk.Frame(wechat,bg=LINE,height=1).pack(fill='x',padx=16,pady=(12,5))
        def check(parent,text,var):
            return tk.Checkbutton(parent,text=text,variable=var,fg=INK,bg=PAPER,activebackground=PAPER,
                                  activeforeground=INK,anchor='w',selectcolor=PAPER,
                                  borderwidth=0,highlightthickness=0,font=(FONT,11),padx=0,pady=3)
        check(wechat,'启动时自动回复',self.panel.auto_on_launch).pack(fill='x',padx=16,pady=(4,0))
        self.split_check=check(wechat,'分气泡发送',self.panel.split_replies)
        self.split_check.pack(fill='x',padx=16)
        if hasattr(self.panel,'idle_wake'):
            check(wechat,'允许空闲时唤起微信发送（会切换前台）',self.panel.idle_wake).pack(fill='x',padx=16)
        tk.Frame(wechat,bg=LINE,height=1).pack(fill='x',padx=16,pady=(12,8))
        fields=tk.Frame(wechat,bg=PAPER);fields.pack(fill='x',padx=16,pady=(3,12))
        fields.grid_columnconfigure(1,weight=1)
        label(fields,'当前会话',0)
        tk.Label(fields,textvariable=self.scope_text,bg=PAPER,fg=INK,anchor='w',font=(FONT,10)
                 ).grid(row=0,column=1,sticky='ew',pady=(3,8))
        label(fields,'群内昵称',1)
        tk.Entry(fields,textvariable=self.panel.display_name,bg=PAPER,fg=INK,insertbackground=INK,
                 relief='flat',highlightthickness=1,highlightbackground=LINE,highlightcolor=PEACH,
                 font=(FONT,11),width=12).grid(row=1,column=1,sticky='ew',ipady=5,pady=(0,6))
        label(fields,'额外提及',2)
        check(fields,'已确认群内昵称唯一',self.panel.at_unique).grid(row=2,column=1,sticky='w',pady=2)
        label(fields,'私聊称呼',3)
        self.owner_check=check(fields,'可称呼当前私聊为“主人”',self.panel.owner_tone)
        self.owner_check.grid(row=3,column=1,sticky='w',pady=2)
        pacing=tk.Frame(wechat,bg='#F2F5F4')
        pacing.pack(fill='x',padx=16,pady=(0,9))
        tk.Label(pacing,textvariable=self.rate_text,bg='#F2F5F4',fg=INK,
                 font=(FONT,9)).pack(side='left',expand=True,padx=5)
        _action(pacing,'调整发送节奏',self.open_pacing,icon_name='clock').pack(side='right',padx=5,pady=4)
        self.pacing_host=tk.Frame(wechat,bg=PAPER)
        self.pacing_host.pack(fill='x',padx=16)
        flow=self.flow=_card(self.body)
        inner=tk.Frame(flow,bg=PAPER);inner.pack(fill='x',padx=14,pady=9)
        _action(inner,'查看队列',lambda:self.panel.view.select('pending'),icon_name='list').pack(side='right',padx=3)
        tk.Label(inner,textvariable=self.receive_text,bg=PAPER,fg=INK,font=(FONT,11,'bold'),anchor='w').pack(fill='x')
        self.transport_hint=tk.Label(inner,text='默认不抢焦点，微信在前台且空闲时发送。需要空闲时自动还原微信，可勾选上方唤起选项。独立聊天窗口逐个校准，多个对象也可以共用主窗口。',
                                     bg=PAPER,fg=MUTED,font=(FONT,9),anchor='w',justify='left')
        self.transport_hint.pack(fill='x',pady=(3,0))
        footer=tk.Frame(self,bg=BG);footer.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(10,0))
        self.save_button=_action(footer,'保存设置',self.save,True,'save')
        self.save_button.pack(side='right')
        _action(footer,'停止并退出',self.panel.stop_and_exit).pack(side='right',padx=10)
        self.canvas.bind('<Configure>',self._arrange)
        self._wheel_tag=f'SettingsScroll{self.winfo_id()}'
        self.bind_class(self._wheel_tag,'<MouseWheel>',self._wheel)
        self._tag_controls(self.body)
        self.bind('<Destroy>',self._release,add='+')

    def _arrange(self,event):
        width=max(1,event.width)
        self.canvas.itemconfigure(self.body_window,width=width)
        wide=width >= (720 if self._wide is None else 704 if self._wide else 736)
        self.transport_hint.configure(wraplength=max(150,width-180))
        if wide==self._wide:return
        self._wide=wide
        self.body.columnconfigure(0,weight=1,uniform='cards' if wide else '')
        self.body.columnconfigure(1,weight=1 if wide else 0,uniform='cards' if wide else '')
        self.model_card.grid(row=0,column=0,sticky='new',padx=(0,6 if wide else 0))
        self.wechat_card.grid(row=0 if wide else 1,column=1 if wide else 0,sticky='new',
                              padx=(6 if wide else 0,0),pady=(0 if wide else 9,0))
        self.flow.grid(row=1 if wide else 2,column=0,columnspan=2 if wide else 1,sticky='ew',pady=(10,3))

    def _scroll_changed(self,first,last):
        self.bar.set(first,last)
        needed=float(last)-float(first)<.999
        if needed and not self.bar.winfo_manager():self.bar.grid(row=0,column=1,sticky='ns')
        elif not needed and self.bar.winfo_manager():self.bar.grid_remove()

    def _tag_controls(self,widget):
        widget.bindtags((*widget.bindtags(),self._wheel_tag))
        for child in widget.winfo_children():self._tag_controls(child)

    def _wheel(self,event):
        if self.bar.winfo_manager() and event.delta:
            self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
            return 'break'

    def _release(self,event):
        if event.widget is self:self.unbind_class(self._wheel_tag,'<MouseWheel>')
