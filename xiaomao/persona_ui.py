"""Native, section-based editor for the shared Markdown persona."""
from dataclasses import replace
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .persona import DEFAULT, PersonaSection, join_persona, load_persona, save_persona, split_persona, validate_persona
from .workspace_ui import button, card


BG, PAPER, LINE, INK = '#F5F2EB','#FFFFFF','#DBE0E1','#213647'
PEACH, SELECTED, MUTED = '#F6B18C','#FBE1D2','#566875'
FONT = 'Microsoft YaHei UI'
GROUPS = {
    'identity':('身份与性格','\ue77b'), 'style':('语言与节奏','\ue8bd'),
    'relationship':('关系与称呼','\ue734'), 'capability':('能力与边界','\ue83d'),
    'memory':('记忆与上下文','\ue8f1'), 'custom':('自定义','\ue713'),
}
SHORT_TITLES = {'语言习惯（全面拟人层）':'表达习惯', '口吻参考 · 虚构示例的表达意图':'口吻参考',
                '聊天节奏':'回复节奏','分寸与情绪':'情绪分寸','身份、群聊与隐私':'身份与称呼'}


def _card(parent):
    return card(parent,pad=2)


def _action(parent,text,callback,primary=False,icon_name=None):
    return button(parent,text,callback,primary=primary,icon_name=icon_name,compact=True)


class PersonaPage(tk.Frame):
    def __init__(self,parent,panel):
        super().__init__(parent,bg=BG)
        self.panel=panel
        self.category='style'
        self.index=None
        self.previewing=False
        self.saving=False
        self.loaded=False
        self.source=''
        self.invalid_revision=None
        self.before_default=None
        self.sections=[]
        self.shown=''
        self.active_by_category={}
        self.grid_columnconfigure(1,weight=1)
        self.grid_rowconfigure(1,weight=1)
        banner=_card(self);banner.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,16))
        avatar=Image.open(Path(__file__).with_name('ui_assets')/'persona-avatar.png')
        self.avatar=ImageTk.PhotoImage(avatar.resize((62,60),Image.Resampling.LANCZOS),master=self)
        tk.Label(banner,image=self.avatar,bg=PAPER).pack(side='left',padx=(16,14),pady=10)
        self.persona_name=tk.StringVar(self)
        tk.Label(banner,textvariable=self.persona_name,bg=PAPER,fg=INK,font=(FONT,17,'bold')).pack(side='left')
        self.draft_status=tk.StringVar(self)
        self.draft_badge=tk.Label(banner,textvariable=self.draft_status,font=(FONT,9,'bold'),padx=10,pady=4)
        self.draft_badge.pack(side='right',padx=16)

        rail=_card(self);rail.grid(row=1,column=0,sticky='nsew',padx=(0,12))
        self.category_rows={}
        for category,(label,icon) in GROUPS.items():
            row=tk.Frame(rail,bg=PAPER,takefocus=True,cursor='hand2',highlightthickness=1,
                         highlightbackground=PAPER,highlightcolor=PEACH)
            row.pack(fill='x',padx=5,pady=(5,1))
            symbol=tk.Label(row,text=icon,bg=PAPER,fg=INK,font=('Segoe MDL2 Assets',15))
            symbol.pack(side='left',padx=(9,8),pady=11)
            name=tk.Label(row,text=label,bg=PAPER,fg=INK,font=(FONT,11,'bold'),anchor='w')
            name.pack(side='left',padx=(0,12),pady=10)
            for widget in (row,symbol,name):
                widget.bind('<Button-1>',lambda e,c=category:self.select_category(c))
                widget.bind('<Return>',lambda e,c=category:self.select_category(c))
                widget.bind('<space>',lambda e,c=category:self.select_category(c))
            self.category_rows[category]=(row,symbol,name)
            tk.Frame(rail,bg=LINE,height=1).pack(fill='x',padx=10)

        content=_card(self);content.grid(row=1,column=1,sticky='nsew')
        content.grid_columnconfigure(0,weight=1);content.grid_rowconfigure(3,weight=1)
        self.heading=tk.StringVar(self)
        tk.Label(content,textvariable=self.heading,bg=PAPER,fg=INK,font=(FONT,17,'bold'),anchor='w'
                 ).grid(row=0,column=0,sticky='ew',padx=17,pady=(12,8))
        tk.Frame(content,bg=LINE,height=1).grid(row=1,column=0,sticky='ew',padx=15)
        self.tabs=tk.Frame(content,bg=PAPER);self.tabs.grid(row=2,column=0,sticky='ew',padx=15,pady=(8,9))
        area=tk.Frame(content,bg=PAPER,highlightbackground=LINE,highlightthickness=1)
        area.grid(row=3,column=0,sticky='nsew',padx=15,pady=(4,12))
        scrollbar=ttk.Scrollbar(area);scrollbar.pack(side='right',fill='y')
        self.editor=tk.Text(area,wrap='word',undo=True,relief='flat',borderwidth=0,font=(FONT,12),
                            bg=PAPER,fg=INK,insertbackground=INK,selectbackground=SELECTED,
                            padx=12,pady=10,spacing2=3,width=8,height=4,yscrollcommand=scrollbar.set)
        self.editor.pack(side='left',fill='both',expand=True);scrollbar.configure(command=self.editor.yview)
        tk.Frame(content,bg=LINE,height=1).grid(row=4,column=0,sticky='ew',padx=15)
        actions=tk.Frame(content,bg=PAPER);actions.grid(row=5,column=0,sticky='ew',padx=15,pady=12)
        self.save_button=_action(actions,'保存',self.save,True,'save');self.save_button.pack(side='left')
        self.restore_button=_action(actions,'恢复默认稿',self.restore_default)
        self.restore_button.pack(side='left',padx=8)
        self.preview_button=_action(actions,'完整预览',self.toggle_preview,icon_name='eye');self.preview_button.pack(side='left')
        self.editor.bind('<<Modified>>',self._edited)
        self.editor.bind('<Control-s>',self._save_shortcut)
        self.editor.bind('<Escape>',self._leave_preview)
        self.on_show()

    def _save_shortcut(self,_event):
        self.save()
        return 'break'

    def _leave_preview(self,_event):
        if self.previewing:
            self.toggle_preview()
            return 'break'

    def _edited(self,_event):
        if self.editor.edit_modified():
            self.editor.edit_modified(False)
            self._draft_state()

    def _draft_state(self):
        changed=join_persona(self.sections)!=self.source
        if not self.previewing:
            changed=changed or self.editor.get('1.0','end-1c')!=self.shown
        state='保存中…' if self.saving else '原稿读取失败' if not self.loaded else '有未保存修改' if changed else '已保存'
        self.draft_status.set(state)
        self.draft_badge.configure(bg=SELECTED if changed else '#E2F1E8',fg=INK if changed else '#31663E')
        self.save_button.configure(state='normal' if self.loaded and changed and not self.saving else 'disabled')

    def _load(self,raw):
        self.sections=split_persona(raw)
        self.previewing=False
        self.index=None
        self.active_by_category.clear()
        self.persona_name.set('当前人格')
        for section in self.sections:
            if re.match(r'^\s*#\s+',section.heading):
                title=section.heading.lstrip('# ').strip()
                self.persona_name.set('小玲 · 大肥鱼' if title.startswith('小玲 · 大肥鱼 v') else title[:40])
                break
        self._select_first()
        self._paint()

    def _select_first(self):
        indexes=[i for i,s in enumerate(self.sections) if s.category==self.category]
        preferred=self.active_by_category.get(self.category)
        self.index=preferred if preferred in indexes else next(iter(indexes),None)

    def _stash(self):
        if self.previewing or not self.loaded:return
        body=self.editor.get('1.0','end-1c')
        if body==self.shown:return
        if self.index is None:
            if not body.strip():return
            heading='【'+GROUPS[self.category][0]+'】'
            self.index=len(self.sections)
            self.sections.append(PersonaSection(self.category,heading,body))
        else:
            section=self.sections[self.index]
            newline=section.newline or '\n'
            self.sections[self.index]=replace(section,body=body.replace('\n',newline))
        self.active_by_category[self.category]=self.index
        self.shown=body

    def select_category(self,category):
        self._stash()
        self.previewing=False
        self.category=category
        self._select_first()
        self._paint()

    def select_section(self,index):
        self._stash()
        self.index=index
        self.previewing=False
        self.active_by_category[self.category]=index
        self._paint()

    def _tab_title(self,index):
        raw=self.sections[index].heading.strip('#【】 \t')
        if raw.startswith('小玲 · 大肥鱼 v'):return '角色简介'
        return SHORT_TITLES.get(raw,raw or '原文')

    def _paint(self):
        self.heading.set('完整人格预览' if self.previewing else GROUPS[self.category][0])
        self.preview_button.configure(text='返回编辑' if self.previewing else '完整预览')
        for category,widgets in self.category_rows.items():
            color=SELECTED if category==self.category else PAPER
            for widget in widgets:widget.configure(bg=color)
            widgets[0].configure(highlightbackground=color)
        for widget in self.tabs.winfo_children():widget.destroy()
        if not self.previewing:
            indexes=[i for i,s in enumerate(self.sections) if s.category==self.category]
            limit=5 if all(len(self._tab_title(i))<=6 for i in indexes) else 3
            visible=indexes[:limit]
            if self.index is not None and self.index not in visible:
                visible=visible[:limit-1]+[self.index]
            for index in visible:
                label=self._tab_title(index)
                button(self.tabs,label if len(label)<=10 else label[:9]+'…',
                       lambda i=index:self.select_section(i),selected=self.index==index,
                       compact=True).pack(side='left',padx=(0,4))
            if len(visible)<len(indexes):
                more=tk.Menubutton(self.tabs,text='更多 ▾',bg=PAPER,fg=INK,font=(FONT,10),relief='flat')
                menu=tk.Menu(more,tearoff=False)
                for index in indexes:menu.add_command(label=self._tab_title(index),command=lambda i=index:self.select_section(i))
                more.configure(menu=menu);more.pack(side='left',padx=3)
            if not visible:
                tk.Label(self.tabs,text='新分段',bg=SELECTED,fg=INK,padx=10,pady=4,font=(FONT,10)).pack(side='left')
        self.editor.configure(state='normal')
        self.editor.delete('1.0','end')
        raw=join_persona(self.sections) if self.previewing else self.sections[self.index].body if self.index is not None else ''
        self.shown=raw.replace('\r\n','\n').replace('\r','\n')
        self.editor.insert('1.0',self.shown)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        if self.previewing:self.editor.configure(state='disabled')
        self._draft_state()

    def on_show(self):
        self._stash()
        if self.loaded and join_persona(self.sections)!=self.source:return
        try:raw=load_persona(self.panel.directory)
        except (ValueError,OSError) as error:
            if not self.loaded:
                self.source=None
                self.invalid_revision=self._file_revision()
                self._load(DEFAULT)
                self.editor.configure(state='disabled')
            self.panel.notice.set(str(error));return
        if not self.loaded or raw!=self.source:
            self.loaded=True;self.source=raw;self._load(raw)

    def refresh_scope(self):
        # The persona belongs to the assistant, not the selected conversation.
        pass

    def toggle_preview(self):
        self._stash()
        self.previewing=not self.previewing
        self._paint()

    def restore_default(self):
        if not self.loaded and self.invalid_revision is None:
            self.panel.notice.set('当前无法读取人格文件，请检查配置目录后重试。')
            return
        self._stash()
        if self.before_default is not None:
            old,self.before_default=self.before_default,None
            self._load(old)
            self.restore_button.configure(text='恢复默认稿')
            self.panel.notice.set('已返回恢复前的草稿。')
            return
        self.before_default=join_persona(self.sections) if self.loaded else None
        self.loaded=True;self._load(DEFAULT)
        if self.before_default is not None:self.restore_button.configure(text='撤销恢复')
        self.panel.notice.set('默认稿已载入编辑区，保存后应用。')

    def save(self):
        if self.saving or not self.loaded:return
        self._stash()
        raw=join_persona(self.sections)
        try:validate_persona(raw)
        except ValueError as error:
            self.panel.notice.set(str(error));return
        previous=self.source
        if raw==previous:return
        self.saving=True;self._draft_state()
        def write():
            current=(self._file_revision()==self.invalid_revision if previous is None else
                     load_persona(self.panel.directory)==previous)
            if not current:
                raise ValueError('人格原稿已被其他编辑更新，请重新打开并核对草稿。')
            save_persona(self.panel.directory,raw)
        def saved(_):
            self.saving=False;self.source=raw;self.invalid_revision=None;self._draft_state()
            self.before_default=None;self.restore_button.configure(text='恢复默认稿')
            self.panel.notice.set('人格已保存，下一次回复使用这份设定。')
        def failed(error):
            self.saving=False;self._draft_state();self.panel.notice.set(error)
        self.panel.work(write,saved,failed,label='保存人格')

    def _file_revision(self):
        try:info=(self.panel.directory/'config'/'persona.md').stat()
        except OSError:return None
        return info.st_size,info.st_mtime_ns
