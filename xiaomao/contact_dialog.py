"""Search the entire local directory without navigating WeChat or enabling it."""
import tkinter as tk
from tkinter import ttk
from .contacts import search_directory


class ContactDialog:
    def __init__(self,panel):
        self.panel=panel;self.rows=[];self.visible=[];self.loading=False
        self.window=tk.Toplevel(panel.root);self.window.title('选择回复对象 · 个人 / 群聊')
        self.window.geometry('880x580');self.window.minsize(720,460)
        self.query=tk.StringVar();self.kind=tk.StringVar(value='全部');self.note=tk.StringVar(value='正在读取本机联系人…')
        top=ttk.Frame(self.window,padding=16);top.pack(fill='x')
        ttk.Label(top,text='搜索昵称、备注、微信号').pack(side='left')
        entry=ttk.Entry(top,textvariable=self.query);entry.pack(side='left',fill='x',expand=True,padx=12)
        scope=ttk.Combobox(top,textvariable=self.kind,values=['全部','个人','群聊','服务 / 系统'],state='readonly',width=12)
        scope.pack(side='left')
        frame=ttk.Frame(self.window,padding=(16,0));frame.pack(fill='both',expand=True)
        self.list=ttk.Treeview(frame,columns=('kind','name','nickname','state'),show='headings',selectmode='browse')
        for key,label,width in [('kind','类型',70),('name','会话 / 备注',230),('nickname','昵称',200),('state','状态',260)]:
            self.list.heading(key,text=label);self.list.column(key,width=width,minwidth=60)
        self.list.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.list.yview);scroll.pack(side='right',fill='y')
        self.list.configure(yscrollcommand=scroll.set)
        bottom=ttk.Frame(self.window,padding=16);bottom.pack(fill='x')
        ttk.Label(bottom,textvariable=self.note,wraplength=780).pack(fill='x',pady=(0,10))
        ttk.Button(bottom,text='刷新联系人',command=self.refresh).pack(side='left')
        ttk.Button(bottom,text='使用微信当前会话',command=self.current).pack(side='left',padx=10)
        self.choose=ttk.Button(bottom,text='选择此会话',command=self.select,state='disabled');self.choose.pack(side='right')
        self.query.trace_add('write',lambda *_:self.render());self.kind.trace_add('write',lambda *_:self.render())
        self.list.bind('<<TreeviewSelect>>',self.changed)
        entry.focus_set();self.refresh()

    def alive(self):return bool(self.window.winfo_exists())

    def refresh(self):
        if self.loading:return
        self.loading=True;self.choose.configure(state='disabled');self.note.set('正在读取本机联系人；不会打开聊天或发送消息…')
        from .selection import load_directory
        def done(rows):
            if not self.alive():return
            self.loading=False;self.rows=rows;self.render()
        def failed(error):
            if not self.alive():return
            self.loading=False;self.note.set(error)
        self.panel.work(lambda:load_directory(self.panel.directory),done,failed,mutates=False)

    def render(self):
        if not self.alive():return
        kind={'全部':'all','个人':'private','群聊':'group','服务 / 系统':'service'}[self.kind.get()]
        self.visible=search_directory(self.rows,self.query.get(),kind)
        self.list.delete(*self.list.get_children())
        for index,row in enumerate(self.visible):
            self.list.insert('', 'end',iid=str(index),values=({'private':'个人','group':'群聊','service':'服务'}[row.kind],row.name,row.nickname,
                '可选择' if row.selectable else row.reason))
        self.choose.configure(state='disabled')
        self.note.set(f'显示 {len(self.visible)} / {len(self.rows)} 个本机联系人。选择后保留当前暂停或运行状态，可再单独开启。')

    def changed(self,*_):
        selection=self.list.selection()
        row=self.visible[int(selection[0])] if selection else None
        self.choose.configure(state='normal' if row and row.selectable and not self.loading else 'disabled')
        if row and not row.selectable:self.note.set(row.reason)

    def select(self):
        selection=self.list.selection()
        if not selection:return
        row=self.visible[int(selection[0])]
        if self.loading or not row.selectable:return
        self.window.destroy();self.panel.select_contact(row)

    def current(self):
        self.window.destroy();self.panel.use_current_group()
