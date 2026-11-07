"""Native, section-based editor for the shared Markdown persona."""
from dataclasses import replace
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog

from PIL import Image, ImageTk

from .persona import DEFAULT, PersonaSection, join_persona, load_persona, save_persona, split_persona, validate_persona
from .persona_documents import (list_persona_documents, get_persona_document, import_persona_file,
                                import_persona_text, update_persona_document)
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
        self.document_id=''
        self.documents=()
        self.document_drafts={}
        self.current_document=None
        self.active_document=None
        self.import_dialog=None
        self.compiler_evidence={}
        self.compiler_generation=0
        self.grid_columnconfigure(1,weight=1)
        self.grid_rowconfigure(2,weight=1)
        banner=_card(self);banner.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,16))
        avatar=Image.open(Path(__file__).with_name('ui_assets')/'persona-avatar.png')
        self.avatar=ImageTk.PhotoImage(avatar.resize((62,60),Image.Resampling.LANCZOS),master=self)
        tk.Label(banner,image=self.avatar,bg=PAPER).pack(side='left',padx=(16,14),pady=10)
        self.persona_name=tk.StringVar(self)
        tk.Label(banner,textvariable=self.persona_name,bg=PAPER,fg=INK,font=(FONT,17,'bold')).pack(side='left')
        self.draft_status=tk.StringVar(self)
        self.draft_badge=tk.Label(banner,textvariable=self.draft_status,font=(FONT,9,'bold'),padx=10,pady=4)
        self.draft_badge.pack(side='right',padx=16)

        document_bar=tk.Frame(self,bg=BG);document_bar.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,9))
        document_bar.grid_columnconfigure(1,weight=1)
        self.activation_status=tk.StringVar(self)
        tk.Label(document_bar,textvariable=self.activation_status,bg=BG,fg='#31663E',font=(FONT,10,'bold'),
                 anchor='w',wraplength=500,justify='left').grid(row=0,column=0,columnspan=4,sticky='ew',pady=(0,7))
        self.disable_button=_action(document_bar,'仅用主稿',self.disable_document)
        self.disable_button.grid(row=0,column=4,sticky='e',pady=(0,7))

        tk.Label(document_bar,text='查看或编辑',bg=BG,fg=INK,font=(FONT,10)).grid(row=1,column=0,padx=(0,8))
        self.document_picker=ttk.Combobox(document_bar,state='readonly',font=(FONT,10),width=22)
        self.document_picker.grid(row=1,column=1,sticky='ew',padx=(0,8))
        self.document_picker.bind('<<ComboboxSelected>>',self._document_selected)
        self.save_button=_action(document_bar,'保存修改',self.save,True,'save')
        self.save_button.grid(row=1,column=2,padx=(0,8))
        self.import_button=_action(document_bar,'导入文件',self.import_files)
        self.import_button.grid(row=1,column=3,padx=(0,6))
        self.paste_button=_action(document_bar,'粘贴新稿',self.open_paste_dialog)
        self.paste_button.grid(row=1,column=4)
        self.document_hint=tk.StringVar(self)
        tk.Label(document_bar,textvariable=self.document_hint,bg=BG,fg=MUTED,font=(FONT,9),
                 anchor='w',wraplength=560,justify='left').grid(row=2,column=0,columnspan=5,sticky='ew',pady=(5,0))

        rail=_card(self);rail.grid(row=2,column=0,sticky='nsew',padx=(0,12))
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

        content=_card(self);content.grid(row=2,column=1,sticky='nsew')
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
        self.restore_button=_action(actions,'恢复默认稿',self.restore_default)
        self.restore_button.grid(row=0,column=0,padx=(0,8))
        self.rename_button=_action(actions,'重命名',self.rename_document)
        self.rename_button.grid(row=0,column=0,padx=(0,8))
        self.preview_button=_action(actions,'完整预览',self.toggle_preview,icon_name='eye')
        self.preview_button.grid(row=0,column=1)
        self.corpus_status=tk.StringVar(self)
        tk.Label(actions,textvariable=self.corpus_status,bg=PAPER,fg=MUTED,font=(FONT,9),
                 anchor='w',wraplength=280,justify='left').grid(row=1,column=0,columnspan=2,sticky='w',pady=(4,0))
        self.editor.bind('<<Modified>>',self._edited)
        self.editor.bind('<Control-s>',self._save_shortcut)
        self.editor.bind('<Escape>',self._leave_preview)
        self.on_show()

    def _save_shortcut(self,_event):
        self.save()
        return 'break'

    def _refresh_documents(self,identity=None):
        identity=self.document_id if identity is None else identity
        documents=list_persona_documents(self.panel.directory)
        ids=['',*(item.id for item in documents)]
        if identity not in ids:raise ValueError('人格文档已不存在，请重新选择')
        self.document_id=identity
        self.documents=documents
        self.current_document=next((item for item in documents if item.id==identity),None)
        labels=['主稿（始终生效）']
        labels.extend(f'补充稿 · {"已启用" if item.enabled else "未启用"} · {item.title}' for item in documents)
        self.document_picker.configure(values=labels)
        self.document_picker.current(ids.index(identity))
        active=next((item for item in documents if item.enabled),None)
        self.active_document=active
        self.activation_status.set(f'回复使用 · 主稿 +「{active.title}」' if active else '回复使用 · 仅主稿')
        if self.current_document:
            title=self.current_document.title
            self.persona_name.set(title if len(title)<=30 else title[:29]+'…')
            self.restore_button.grid_remove()
            self.rename_button.grid()
        else:
            self.restore_button.grid()
            self.rename_button.grid_remove()
        if active:self.disable_button.grid()
        else:self.disable_button.grid_remove()
        self._draft_state()
        self._refresh_compiler_state()

    def _refresh_compiler_state(self):
        from .model import ChatModel
        from .config import Settings
        self.compiler_generation+=1
        generation=self.compiler_generation
        self.corpus_status.set('正在检查人格')
        def show(evidence):
            if generation!=self.compiler_generation or not self.winfo_exists():return
            self.compiler_evidence=evidence
            state=('官方 Harness 已启用' if evidence['backend']=='deepseek-harness' else
                   '兼容模式：官方 Harness 编译器未就绪')
            self.corpus_status.set(state)
        def failed(_error):
            if generation==self.compiler_generation and self.winfo_exists():
                self.corpus_status.set('人格编译失败，请检查原文')
        self.panel.work(lambda:ChatModel(Settings(self.panel.directory)).prompt_snapshot(),show,failed,mutates=False)

    def _document_selected(self,_event=None):
        index=self.document_picker.current()
        if index>=0:self.select_document((['']+[item.id for item in self.documents])[index])

    def select_document(self,identity):
        self._stash()
        if self.loaded:
            raw=join_persona(self.sections)
            if raw!=self.source:self.document_drafts[self.document_id]=(raw,self.source)
            else:self.document_drafts.pop(self.document_id,None)
        try:
            raw=load_persona(self.panel.directory) if not identity else None
            self._refresh_documents(identity)
            if self.current_document:raw=self.current_document.text
            draft=self.document_drafts.get(identity)
            self.source=draft[1] if draft else raw
            self.loaded=True;self.invalid_revision=None;self.before_default=None
            self.restore_button.configure(text='恢复默认稿')
            self._load(draft[0] if draft else raw)
        except (OSError,ValueError) as error:
            ids=['',*(item.id for item in self.documents)]
            if self.document_id in ids:self.document_picker.current(ids.index(self.document_id))
            self.panel.notice.set(str(error))

    def import_files(self):
        paths=filedialog.askopenfilenames(parent=self,title='导入人格 Markdown（可多选）',
            filetypes=[('Markdown 文档','*.md *.markdown'),('纯文本','*.txt')])
        if not paths:return
        category=self.category
        def imported(documents):
            self.select_document(documents[-1].id)
            self.panel.notice.set(f'已导入 {len(documents)} 份补充稿，可在列表里选稿启用')
        self.panel.work(lambda:[import_persona_file(self.panel.directory,path,category=category,enabled=False) for path in paths],
                        imported,lambda error:self.panel.notice.set(str(error)),label='导入人格文档')

    def open_paste_dialog(self):
        if self.import_dialog is not None and self.import_dialog.winfo_exists():
            self.import_dialog.lift();return
        self.import_dialog=PersonaImportDialog(self)

    def rename_document(self):
        document=self.current_document
        if self.saving or document is None:return
        title=simpledialog.askstring('重命名补充稿','补充稿名称',initialvalue=document.title,parent=self)
        if title is None or title.strip()==document.title:return
        def saved(updated):
            self._refresh_documents()
            self.panel.notice.set(f'补充稿已重命名为「{updated.title}」')
        self.panel.work(lambda:update_persona_document(self.panel.directory,document.id,title=title),saved,
                        label='重命名补充稿',controls=(self.rename_button,))

    def disable_document(self):
        document=self.active_document
        if self.saving or document is None:return
        self.saving=True;self._draft_state()
        def saved(_):
            self.saving=False
            self._refresh_documents()
            self.panel.notice.set('已切换为仅用主稿')
        def failed(error):
            self.saving=False;self._draft_state();self.panel.notice.set(str(error))
        self.panel.work(lambda:update_persona_document(self.panel.directory,document.id,enabled=False),saved,
                        failed,label='切换为仅用主稿')

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
        if not self.previewing and not getattr(self,'section_preview',False):
            changed=changed or self.editor.get('1.0','end-1c')!=self.shown
        inactive=self.current_document is not None and not self.current_document.enabled
        if inactive:
            effect='替换当前补充稿' if self.active_document else '和主稿一起用于回复'
            hint=f'这份补充稿尚未生效。点「保存并启用」后会{effect}。'
        elif self.current_document:
            hint='这份补充稿正在和主稿一起用于回复。'
        else:
            hint='主稿始终用于回复。补充稿可选，最多启用一份。'
        self.document_hint.set(hint)
        state=('保存中…' if self.saving else '原稿读取失败' if not self.loaded else
               '有未保存修改' if changed else '未启用' if inactive else '已保存')
        self.draft_status.set(state)
        pending=changed or inactive
        self.draft_badge.configure(bg=SELECTED if pending else '#E2F1E8',fg=INK if pending else '#31663E')
        self.save_button.configure(text='保存并启用' if inactive else '保存修改',
                                   state='normal' if self.loaded and pending and not self.saving else 'disabled')
        for control in (self.rename_button,self.disable_button):
            control.configure(state='disabled' if self.saving else 'normal')

    def _load(self,raw):
        self.sections=split_persona(raw)
        # Editor navigation inherits the Markdown outline. This metadata is
        # never serialized into the author's text or the model's fixed prefix.
        parents={}
        fallback=getattr(getattr(self,'current_document',None),'category','custom')
        for index,section in enumerate(self.sections):
            heading=re.match(r'^\s{0,3}(#{1,6})[ \t]+',section.heading)
            if not heading:
                parents.clear()
                continue
            level=len(heading[1])
            parents={depth:category for depth,category in parents.items() if depth<level}
            inherited=parents[max(parents)] if parents else fallback
            category=section.category if section.category!='custom' else inherited
            self.sections[index]=replace(section,category=category)
            parents[level]=category
        self.previewing=False
        self.index=None
        self.active_by_category.clear()
        if not self.current_document:
            self.persona_name.set('基础人格')
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
        readable=next((i for i in indexes if self.sections[i].body.strip()),next(iter(indexes),None))
        self.index=preferred if preferred in indexes else readable

    def _stash(self):
        if self.previewing or getattr(self,'section_preview',False) or not self.loaded:return
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

    def _chapter_preview(self):
        if self.index is None or self.sections[self.index].body.strip():return None
        heading=re.match(r'^\s{0,3}(#{1,6})[ \t]+',self.sections[self.index].heading)
        if not heading:return None
        level=len(heading[1]);end=self.index+1
        while end<len(self.sections):
            following=re.match(r'^\s{0,3}(#{1,6})[ \t]+',self.sections[end].heading)
            if following and len(following[1])<=level:break
            end+=1
        if end==self.index+1:return None
        return join_persona(self.sections[self.index:end])

    def _paint(self):
        chapter=None if self.previewing else self._chapter_preview()
        self.section_preview=chapter is not None
        self.heading.set('完整人格预览' if self.previewing else
                         GROUPS[self.category][0]+(' · 章节预览' if self.section_preview else ''))
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
        raw=(join_persona(self.sections) if self.previewing else chapter if self.section_preview else
             self.sections[self.index].body if self.index is not None else '')
        self.shown=raw.replace('\r\n','\n').replace('\r','\n')
        self.editor.insert('1.0',self.shown)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        if self.previewing or self.section_preview:self.editor.configure(state='disabled')
        self._draft_state()

    def on_show(self):
        self._stash()
        if self.loaded and join_persona(self.sections)!=self.source:return
        try:
            self._refresh_documents()
            raw=self.current_document.text if self.current_document else load_persona(self.panel.directory)
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
        if self.document_id:return
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
        identity=self.document_id
        original_document=self.current_document
        enabling=original_document is not None and not original_document.enabled
        if raw==previous and not enabling:return
        invalid_revision=self.invalid_revision
        self.saving=True;self._draft_state()
        def write():
            document=get_persona_document(self.panel.directory,identity) if identity else None
            current=(document is not None and document.text==previous if identity else
                     self._file_revision()==invalid_revision if previous is None else
                     load_persona(self.panel.directory)==previous)
            if not current:
                raise ValueError('人格原稿已被其他编辑更新，请重新打开并核对草稿。')
            if identity:
                return update_persona_document(self.panel.directory,identity,text=raw if raw!=previous else None,
                                               enabled=True if enabling else None,expected_sha256=original_document.sha256)
            save_persona(self.panel.directory,raw)
        def saved(document):
            self.saving=False
            draft=self.document_drafts.pop(identity,None)
            if self.document_id==identity:
                self.source=raw;self.invalid_revision=None;self.before_default=None
                self.restore_button.configure(text='恢复默认稿')
            elif draft and draft[0]!=raw:
                self.document_drafts[identity]=(draft[0],raw)
            self._refresh_documents()
            if enabling:self.panel.notice.set(f'补充稿「{document.title}」已启用，下一次生成回复时生效')
            elif document:self.panel.notice.set(f'补充稿「{document.title}」已保存')
            else:self.panel.notice.set('基础人格已保存，下一次生成回复时生效')
        def failed(error):
            self.saving=False;self._draft_state();self.panel.notice.set(str(error))
        self.panel.work(write,saved,failed,label='保存人格')

    def _file_revision(self):
        try:info=(self.panel.directory/'config'/'persona.md').stat()
        except OSError:return None
        return info.st_size,info.st_mtime_ns


class PersonaImportDialog(tk.Toplevel):
    def __init__(self,page):
        super().__init__(page,bg=BG)
        self.page=page
        self.title('粘贴补充稿');self.geometry('720x540');self.minsize(560,380)
        self.transient(page.winfo_toplevel())
        self.grid_columnconfigure(0,weight=1);self.grid_rowconfigure(2,weight=1)
        fields=tk.Frame(self,bg=BG);fields.grid(row=0,column=0,sticky='ew',padx=16,pady=(15,8))
        fields.grid_columnconfigure(1,weight=1)
        tk.Label(fields,text='文档名称',bg=BG,fg=INK,font=(FONT,10)).grid(row=0,column=0,padx=(0,10))
        self.title_var=tk.StringVar(self,value='新补充稿')
        ttk.Entry(fields,textvariable=self.title_var).grid(row=0,column=1,sticky='ew')
        self.category_var=tk.StringVar(self,value=GROUPS[page.category][0])
        ttk.Combobox(fields,state='readonly',textvariable=self.category_var,
                     values=[value[0] for value in GROUPS.values()],width=16).grid(row=0,column=2,padx=(10,0))
        tk.Label(self,text='粘贴完整原文并填写名称，导入后可选中启用',bg=BG,fg=MUTED,font=(FONT,10),anchor='w'
                 ).grid(row=1,column=0,sticky='ew',padx=16)
        area=tk.Frame(self,bg=PAPER);area.grid(row=2,column=0,sticky='nsew',padx=16,pady=10)
        scrollbar=ttk.Scrollbar(area);scrollbar.pack(side='right',fill='y')
        self.editor=tk.Text(area,wrap='word',undo=True,font=(FONT,11),relief='flat',padx=10,pady=8,yscrollcommand=scrollbar.set)
        self.editor.pack(side='left',fill='both',expand=True);scrollbar.configure(command=self.editor.yview)
        actions=tk.Frame(self,bg=BG);actions.grid(row=3,column=0,sticky='ew',padx=16,pady=(0,15))
        self.save_button=_action(actions,'导入补充稿',self.save,True,'save');self.save_button.pack(side='left')
        _action(actions,'取消',self.destroy).pack(side='left',padx=8)
        self.status=tk.StringVar(self)
        tk.Label(actions,textvariable=self.status,bg=BG,fg=MUTED,font=(FONT,9)).pack(side='left',padx=8)
        self.editor.focus_set()

    def save(self):
        text=self.editor.get('1.0','end-1c');title=self.title_var.get()
        category=next((key for key,value in GROUPS.items() if value[0]==self.category_var.get()),'custom')
        try:validate_persona(text)
        except ValueError as error:self.status.set(str(error));return
        self.save_button.configure(state='disabled')
        def saved(document):
            self.page.select_document(document.id)
            self.page.panel.notice.set(f'补充稿「{document.title}」已导入，可在列表里选稿启用')
            self.destroy()
        def failed(error):
            self.save_button.configure(state='normal');self.status.set(str(error))
        self.page.panel.work(lambda:import_persona_text(self.page.panel.directory,text,title=title,category=category,enabled=False),
                             saved,failed,label='导入补充稿')
