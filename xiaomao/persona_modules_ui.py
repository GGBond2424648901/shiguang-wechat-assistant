"""One persona, independent editable modules, explicit atomic apply feedback."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import re
import uuid
import tkinter as tk
from tkinter import ttk, filedialog

from .persona_modules import PersonaModule, load_modules, save_modules, export_modules
from .persona import validate_persona
from .persona_ui import BG, PAPER, LINE, INK, PEACH, MUTED, FONT, _action, _card


class PersonaModulesPage(tk.Frame):
    def __init__(self,parent,panel):
        super().__init__(parent,bg=BG)
        self.panel=panel;self.state=load_modules(panel.directory)
        if self.state is None:raise ValueError('尚未配置人格模块')
        self.modules=list(self.state.modules);self.selected=None
        self.previewing=False;self.saving=False;self.loading=False;self.shown=''
        self.editor_positions={};self.modified_timer=None
        self.grid_columnconfigure(1,weight=1);self.grid_rowconfigure(2,weight=1)
        header=_card(self);header.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,10))
        header.grid_columnconfigure(0,weight=1)
        tk.Label(header,text=self.state.title,bg=PAPER,fg=INK,font=(FONT,16,'bold'),anchor='w'
                 ).grid(row=0,column=0,sticky='ew',padx=15,pady=(10,3))
        tk.Label(header,text='一套人格，按模块协作 · 原文完整保留',bg=PAPER,fg=MUTED,font=(FONT,10),anchor='w'
                 ).grid(row=1,column=0,sticky='ew',padx=15,pady=(0,10))
        self.state_text=tk.StringVar(self,value='已应用')
        self.badge=tk.Label(header,textvariable=self.state_text,bg='#E7F1E9',fg='#28613D',font=(FONT,10,'bold'),padx=12,pady=7)
        self.badge.grid(row=0,column=1,rowspan=2,padx=15)

        bar=tk.Frame(self,bg=BG);bar.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,10))
        bar.grid_columnconfigure(4,weight=1)
        self.import_button=_action(bar,'导入模块',self.import_files,icon_name='add');self.import_button.grid(row=0,column=0,padx=(0,8))
        self.preview_button=_action(bar,'完整预览',self.toggle_preview,icon_name='eye');self.preview_button.grid(row=0,column=1,padx=(0,8))
        self.export_button=_action(bar,'导出整套',self.export_file);self.export_button.grid(row=0,column=2)
        self.discard_button=_action(bar,'撤销修改',self.discard_changes);self.discard_button.grid(row=0,column=3,padx=(8,0))
        self.save_button=_action(bar,'保存并应用',self.save,True,'save');self.save_button.grid(row=0,column=5)

        rail=_card(self);rail.grid(row=2,column=0,sticky='nsew',padx=(0,12))
        rail.grid_rowconfigure(2,weight=1);rail.grid_columnconfigure(0,weight=1)
        self.count_text=tk.StringVar(self)
        tk.Label(rail,textvariable=self.count_text,bg=PAPER,fg=INK,font=(FONT,11,'bold'),anchor='w'
                 ).grid(row=0,column=0,columnspan=2,sticky='ew',padx=12,pady=12)
        self.search=tk.StringVar(self)
        search=ttk.Entry(rail,textvariable=self.search,width=21);search.grid(row=1,column=0,columnspan=2,sticky='ew',padx=10,pady=(0,8))
        self.search.trace_add('write',lambda *_:self._render_nav())
        ttk.Style(self).configure('Persona.Modules.Treeview',rowheight=38,font=(FONT,10),borderwidth=0)
        self.nav=ttk.Treeview(rail,show='tree',selectmode='browse',style='Persona.Modules.Treeview',height=10)
        self.nav.column('#0',width=208,minwidth=170,stretch=False)
        self.nav.grid(row=2,column=0,sticky='nsew',padx=(6,0),pady=(0,6))
        scroll=ttk.Scrollbar(rail,command=self.nav.yview);scroll.grid(row=2,column=1,sticky='ns',pady=(0,6))
        self.nav.configure(yscrollcommand=scroll.set)
        self.nav.bind('<<TreeviewSelect>>',self._pick)

        content=_card(self);content.grid(row=2,column=1,sticky='nsew')
        content.grid_columnconfigure(0,weight=1);content.grid_rowconfigure(3,weight=1)
        meta=tk.Frame(content,bg=PAPER);meta.grid(row=0,column=0,sticky='ew',padx=14,pady=(12,6));meta.grid_columnconfigure(0,weight=1)
        self.name=tk.StringVar(self);self.enabled=tk.BooleanVar(self)
        self.name_entry=ttk.Entry(meta,textvariable=self.name,font=(FONT,14,'bold'),width=10)
        self.name_entry.grid(row=0,column=0,sticky='ew',padx=(0,8))
        self.enabled_button=ttk.Checkbutton(meta,text='参与回复',variable=self.enabled,command=self._state_changed)
        self.enabled_button.grid(row=0,column=1)
        self.origin=tk.StringVar(self)
        tk.Label(content,textvariable=self.origin,bg=PAPER,fg=MUTED,font=(FONT,9),anchor='w',wraplength=440,justify='left'
                 ).grid(row=1,column=0,sticky='ew',padx=15,pady=(0,8))
        chapter_bar=tk.Frame(content,bg=PAPER);chapter_bar.grid(row=2,column=0,sticky='ew',padx=14,pady=(0,8));chapter_bar.grid_columnconfigure(1,weight=1)
        tk.Label(chapter_bar,text='章节',bg=PAPER,fg=MUTED,font=(FONT,9)).grid(row=0,column=0,padx=(0,8))
        self.chapters=ttk.Combobox(chapter_bar,state='readonly',width=12)
        self.chapters.grid(row=0,column=1,sticky='ew');self.chapters.bind('<<ComboboxSelected>>',self._jump)
        area=tk.Frame(content,bg=PAPER,highlightthickness=1,highlightbackground=LINE)
        area.grid(row=3,column=0,sticky='nsew',padx=14,pady=(0,8))
        scrollbar=ttk.Scrollbar(area);scrollbar.pack(side='right',fill='y')
        self.editor=tk.Text(area,wrap='word',undo=True,relief='flat',borderwidth=0,font=(FONT,11),
                            bg=PAPER,fg=INK,insertbackground=INK,selectbackground='#FBE1D2',padx=12,pady=12,
                            spacing2=4,width=8,height=5,yscrollcommand=scrollbar.set)
        self.editor.pack(side='left',fill='both',expand=True);scrollbar.configure(command=self.editor.yview)
        self.detail=tk.StringVar(self)
        tk.Label(content,textvariable=self.detail,bg=PAPER,fg=MUTED,font=(FONT,9),anchor='w'
                 ).grid(row=4,column=0,sticky='ew',padx=15,pady=(0,10))
        tk.Label(self,text='保存后下一次生成使用新版本 · 记忆随交流积累，聊天不会自行改写人格规则',
                 bg='#FBE6D8',fg=INK,font=(FONT,9),anchor='w',padx=12,pady=9,wraplength=720,justify='left'
                 ).grid(row=3,column=0,columnspan=2,sticky='ew',pady=(10,0))
        self.editor.bind('<<Modified>>',self._edited)
        self.editor.bind('<Control-s>',lambda e:(self.save(),'break')[-1])
        self.name.trace_add('write',lambda *_:self._state_changed())
        self._render_nav();self.select_module(self.modules[0].id)

    def _stash(self):
        if self.loading or self.previewing or self.selected is None:return
        i=next(i for i,m in enumerate(self.modules) if m.id==self.selected)
        text=self.editor.get('1.0','end-1c')
        self.modules[i]=replace(self.modules[i],text=text,title=self.name.get(),enabled=self.enabled.get())
        self.editor_positions[self.selected]=self.editor.yview()[0]

    def dirty(self):
        self._stash()
        return tuple(self.modules)!=self.state.modules

    def can_leave(self):return not self.saving and not self.dirty()

    def _state_changed(self):
        if self.loading:return
        changed=self.dirty()
        self.state_text.set('有未保存的修改' if changed else '已应用')
        self.badge.configure(bg='#FBE1D2' if changed else '#E7F1E9',fg=INK if changed else '#28613D')

    def _edited(self,_event=None):
        if self.editor.edit_modified():
            self.editor.edit_modified(False)
            if self.modified_timer:self.after_cancel(self.modified_timer)
            self.modified_timer=self.after(100,self._finish_edit)

    def _finish_edit(self):
        self.modified_timer=None
        self._state_changed()

    def _render_nav(self):
        self.loading=True
        self.nav.delete(*self.nav.get_children())
        query=self.search.get().strip().casefold()
        for m in self.modules:
            if query and query not in m.title.casefold():continue
            self.nav.insert('','end',iid=m.id,text=('● ' if m.enabled else '○ ')+m.title)
        self.count_text.set(f'人格模块 {len(self.modules)} · 启用 {sum(m.enabled for m in self.modules)}')
        if self.selected and self.nav.exists(self.selected):self.nav.selection_set(self.selected)
        self.loading=False

    def _pick(self,_event=None):
        selected=self.nav.selection()
        if not self.loading and selected and selected[0]!=self.selected:self.select_module(selected[0])

    def select_module(self,identity):
        if self.saving:return
        self._stash();self.previewing=False;self.selected=identity
        m=next(m for m in self.modules if m.id==identity)
        self.loading=True;self.name.set(m.title);self.enabled.set(m.enabled)
        self.origin.set(f'{m.origin} · 单独编辑这个模块，保存后与其他启用模块共同生效')
        self.name_entry.configure(state='normal');self.enabled_button.configure(state='normal')
        self.editor.configure(state='normal');self.editor.delete('1.0','end');self.editor.insert('1.0',m.text)
        self.editor.edit_reset();self.editor.edit_modified(False);self.shown=m.text
        self.editor.yview_moveto(self.editor_positions.get(identity,0))
        self.heading_lines=[(i,re.sub(r'^#+\s*','',line)) for i,line in enumerate(m.text.splitlines(),1) if re.match(r'^#{1,6}\s+',line)]
        self.chapters.configure(values=[t for _,t in self.heading_lines],state='readonly' if self.heading_lines else 'disabled')
        if self.heading_lines:self.chapters.current(0)
        else:self.chapters.set('全文')
        self.detail.set(f'{len(m.text):,} 字 · {len(self.heading_lines)} 个章节 · {"启用" if m.enabled else "停用，原文仍保留"}')
        self.preview_button.configure(text='完整预览');self.loading=False
        self._render_nav();self._state_changed()

    def _jump(self,_event=None):
        index=self.chapters.current()
        if 0<=index<len(self.heading_lines):self.editor.yview(f'{self.heading_lines[index][0]}.0')

    def toggle_preview(self):
        if self.saving:return
        if self.previewing:self.select_module(self.selected);return
        self._stash();self.previewing=True;self.loading=True
        text=export_modules(replace(self.state,modules=tuple(self.modules)))
        self.editor.configure(state='normal');self.editor.delete('1.0','end');self.editor.insert('1.0',text);self.editor.configure(state='disabled')
        self.name.set('整套人格 · 实际编译顺序');self.name_entry.configure(state='disabled');self.enabled_button.configure(state='disabled');self.chapters.configure(state='disabled')
        self.origin.set('当前草稿预览 · 只包含启用模块；保存后才生效')
        self.detail.set(f'{len(text):,} 字 · 模块全文加载，不做静默截断')
        self.preview_button.configure(text='返回编辑');self.loading=False

    def save(self):
        if self.saving:return
        self._stash();candidate=tuple(self.modules);self.saving=True
        self.save_button.configure(state='disabled')
        self._freeze(True)
        def done(state):
            self.state=state;self.modules=list(state.modules);self.saving=False
            self._freeze(False)
            self.save_button.configure(state='normal');self._state_changed();self._render_nav()
            self.panel.notice.set(f'{sum(m.enabled for m in state.modules)} 个人格模块已应用，下一次生成使用新版本')
        def failed(error):
            self.saving=False;self._freeze(False);self.save_button.configure(state='normal');self.panel.notice.set(str(error))
        try:self.panel.work(lambda:save_modules(self.panel.directory,candidate,title=self.state.title,expected_revision=self.state.revision),
                            done,failed,label='应用人格模块',controls=(self.save_button,))
        except (ValueError,OSError) as error:failed(error)

    def import_files(self):
        if self.saving:return
        paths=filedialog.askopenfilenames(parent=self,title='导入人格模块，保留完整原文',filetypes=[('人格模块与文本','*.json *.md *.markdown *.txt')])
        if not paths:return
        self._stash();created=[]
        try:
            for path in paths:
                p=Path(path)
                if p.stat().st_size>3*1024*1024:raise ValueError('单个导入文件需小于 3 MB')
                text=p.read_bytes().decode('utf-8-sig')
                if p.suffix.lower()=='.json':
                    from .persona_modules import _validate
                    data=json.loads(text)
                    if not isinstance(data,dict) or set(data)!={'version','title','modules'} or data['version']!=1:
                        raise ValueError('不是受支持的人格模块备份')
                    items=[PersonaModule(**m) for m in data['modules']]
                    _validate(data['title'],items)
                    created.extend(replace(m,id='custom_'+uuid.uuid4().hex) for m in items)
                else:
                    validate_persona(text)
                    if '<!-- persona-module:' in text:
                        raise ValueError('整套 Markdown 用于阅读；请导入 JSON 备份，以保留各模块的启用状态')
                    created.append(PersonaModule('custom_'+uuid.uuid4().hex,p.stem,text,origin='用户导入'))
            from .persona_modules import _validate
            _validate(self.state.title,self.modules+created)
        except (OSError,ValueError,UnicodeDecodeError,TypeError,KeyError) as error:
            self.panel.notice.set(str(error));return
        self.modules.extend(created);self._render_nav();self.select_module(created[-1].id)
        self.panel.notice.set(f'已载入 {len(created)} 个模块，点击“保存并应用”后参与回复')

    def export_file(self):
        self._stash()
        path=filedialog.asksaveasfilename(parent=self,title='导出完整人格（含停用模块）',defaultextension='.json',filetypes=[('可重新导入的完整备份','*.json'),('Markdown 阅读版','*.md')])
        if not path:return
        try:
            from .privacy import atomic_private_write
            text=(json.dumps({'version':1,'title':self.state.title,'modules':[asdict(m) for m in self.modules]},ensure_ascii=False,indent=2)
                  if Path(path).suffix.lower()=='.json' else export_modules(replace(self.state,modules=tuple(self.modules)),include_disabled=True))
            atomic_private_write(Path(path),text.encode('utf-8'))
            self.panel.notice.set('整套人格已导出，包含全部原文与启用状态；未修改生效版本')
        except (OSError,ValueError) as error:self.panel.notice.set(str(error))

    def on_show(self):
        if self.saving:return
        if self.dirty():return
        try:
            current=load_modules(self.panel.directory)
            if current and current.revision!=self.state.revision:
                self.state=current;self.modules=list(current.modules)
                identity=self.selected if any(m.id==self.selected for m in self.modules) else self.modules[0].id
                # Old editor contents must not be stashed into the new revision.
                self.selected=None;self.select_module(identity)
        except (OSError,ValueError) as error:self.panel.notice.set(str(error))

    def _freeze(self,frozen):
        state='disabled' if frozen else 'normal'
        for control in (self.import_button,self.preview_button,self.export_button,self.discard_button):control.configure(state=state)
        self.editor.configure(state='disabled' if frozen or self.previewing else 'normal')
        self.name_entry.configure(state='disabled' if frozen or self.previewing else 'normal')
        self.enabled_button.configure(state='disabled' if frozen or self.previewing else 'normal')

    def discard_changes(self):
        if self.saving:return
        try:
            current=load_modules(self.panel.directory)
            if current is None:raise ValueError('人格模块文件缺失，保留当前草稿')
            identity=self.selected if any(m.id==self.selected for m in current.modules) else current.modules[0].id
            self.state=current;self.modules=list(current.modules);self.selected=None
            self.select_module(identity);self.panel.notice.set('未保存的修改已撤销，已加载当前生效版本')
        except (OSError,ValueError) as error:self.panel.notice.set(str(error))

    def destroy(self):
        if self.modified_timer:
            self.after_cancel(self.modified_timer);self.modified_timer=None
        super().destroy()
