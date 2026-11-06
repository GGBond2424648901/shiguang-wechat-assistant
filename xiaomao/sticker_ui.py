"""Native sticker gallery and review queue. Nothing on this page sends to WeChat."""
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import os
import re
import time
import tkinter as tk
from tkinter import filedialog,ttk

from PIL import Image,ImageTk

from .config import Settings
from .model import ChatModel
from .sticker_import import CATEGORIES,PRIORITIES,FILE_SUFFIXES,annotation,inspect_picture
from .stickers import StickerLibrary
from .workspace_ui import ASSETS,BG,INK,LINE,MUTED,PAPER,PEACH,button,card,label


ALL='全部分类'
AUTO='自动判断'


@dataclass
class QueueRow:
    path: Path
    picture: object=None
    category: str=''
    meaning: str=''
    tags: str=''
    source: str=''
    status: str='待识别'


class StickerPage(tk.Frame):
    def __init__(self,parent,panel):
        super().__init__(parent,bg=BG)
        self.panel=panel
        self.lib=None
        self.loading=False
        self.gallery_key=None
        self.search_job=None
        self.resize_job=None
        self.resize_width=None
        self.wheel_delta=0
        self.wheel_at=0
        self.last_page=0
        self.thumb_cache={}
        self.rows={}
        self.busy=False
        self.analysis_running=False
        self.cancel_analysis=False
        self.selected=None
        self.page=0
        self.tab='library'
        self.active=False
        self.animation=None
        self.preview_file=None
        self.photos=[]
        self.queue_photos={}
        self.thumb_edge=128
        self.filter=tk.StringVar(self,value=ALL)
        self.query=tk.StringVar(self)
        self.batch=tk.StringVar(self,value=AUTO)
        self.neutral=tk.BooleanVar(self)
        self.notice=tk.StringVar(self)
        self.fields={k:tk.StringVar(self) for k in ('category','meaning','tags','source')}
        self.info=tk.StringVar(self)
        self.priorities={}
        self.columnconfigure(0,weight=1);self.rowconfigure(1,weight=1)
        top=tk.Frame(self,bg=BG);top.grid(row=0,column=0,sticky='ew',pady=(0,8))
        self.tabs={}
        for key,title in [('library','素材库'),('import','导入与识别')]:
            control=button(top,title,lambda k=key:self.switch(k),compact=True)
            control.pack(side='left',padx=(0,5));self.tabs[key]=control
        button(top,'导入素材',self.pick_files,primary=True,icon_name='add').pack(side='right')
        button(top,'收集说明',self.open_guide,icon_name='list').pack(side='right',padx=6)
        # Keep a fixed scrollbar lane so switching tabs or resizing never changes card width.
        outer=tk.Frame(self,bg=BG);outer.grid(row=1,column=0,sticky='nsew')
        self.canvas=tk.Canvas(outer,bg=BG,highlightthickness=0)
        self.canvas.pack(side='left',expand=True,fill='both')
        bar=ttk.Scrollbar(outer,orient='vertical',command=self.canvas.yview)
        bar.pack(side='right',fill='y');self.canvas.configure(yscrollcommand=bar.set)
        self.body=tk.Frame(self.canvas,bg=BG)
        self.window=self.canvas.create_window((0,0),anchor='nw',window=self.body)
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',self._resize)
        self.body.columnconfigure(0,weight=1)
        self.pages={}
        for key in self.tabs:
            box=tk.Frame(self.body,bg=BG);box.grid(row=0,column=0,sticky='nsew')
            box.columnconfigure(0,weight=3,uniform='cards');box.columnconfigure(1,weight=2,uniform='cards')
            self.pages[key]=box
        self._build_library();self._build_queue()
        from .sticker_detail_ui import StickerDetail
        self.detail=StickerDetail(self.body,self)
        self.detail.grid(row=0,column=0,sticky='nsew');self.pages['detail']=self.detail
        self.detail_save=self.detail.save_button
        label(self,textvariable=self.notice,color=MUTED,size=9).grid(row=2,column=0,sticky='ew',pady=(4,0))
        self.query.trace_add('write',lambda *_:self._search())
        self._wheel_token=self.winfo_toplevel().bind('<MouseWheel>',self._wheel,add='+')
        self.bind('<Destroy>',self._destroyed,add='+')
        self.switch('library')

    def _wheel(self,event):
        if not self.active or not str(event.widget).startswith(str(self)+'.'):return
        if not event.delta:return
        if self.tab=='library' and (event.widget is self.gridbox or str(event.widget).startswith(str(self.gridbox)+'.')):
            now=time.monotonic()
            if now-self.wheel_at>.45 or self.wheel_delta*event.delta<0:self.wheel_delta=0
            self.wheel_at=now;self.wheel_delta+=event.delta
            if abs(self.wheel_delta)>=120:
                self._turn(-1 if self.wheel_delta>0 else 1)
                self.wheel_delta=0
            return 'break'
        if event.widget.winfo_class() in ('TCombobox','Combobox','Entry','TEntry','Spinbox','TSpinbox'):return
        if event.widget is self.queue:return
        if self.body.winfo_height()<=self.canvas.winfo_height():return
        self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
        return 'break'

    def _destroyed(self,event):
        if event.widget is not self:return
        self._stop_preview()
        self.detail.stop()
        if self.search_job is not None:self.after_cancel(self.search_job)
        if self.resize_job is not None:self.after_cancel(self.resize_job)
        try:self.winfo_toplevel().unbind('<MouseWheel>',self._wheel_token)
        except tk.TclError:pass

    def _resize(self,event):
        width=max(1,event.width)
        if width==self.resize_width:return
        self.resize_width=width
        self.canvas.itemconfigure(self.window,width=width)
        if self.resize_job is not None:self.after_cancel(self.resize_job)
        self.resize_job=self.after(120,self._resize_gallery)

    def _resize_gallery(self):
        self.resize_job=None
        width=self.resize_width
        if width is None:return
        # A small dead band avoids repeatedly swapping every tile at the breakpoint.
        edge=128 if width>=(844 if self.thumb_edge==128 else 876) else 92
        if edge!=self.thumb_edge:
            self.thumb_edge=edge
            if self.active and self.tab=='library':self._gallery()

    def on_show(self):
        self.active=True
        if self.lib is None and not self.loading:
            self.loading=True
            def loaded(lib):
                if not self.winfo_exists():return
                self.lib=lib;self.loading=False;self._gallery()
            def failed(error):
                if self.winfo_exists():self.loading=False;self.notice.set(str(error))
            self.panel.work(lambda:StickerLibrary(self.panel.directory),loaded,failed,mutates=False)
        elif self.lib is not None:self._gallery()
        if not self.frequency_editor.dirty():self.frequency_editor.select(None)
        if self.tab=='import':self._select_row()
        elif self.tab=='detail':self.detail.resume()

    def on_hide(self):
        self.active=False;self._stop_preview();self.detail.stop()
        self.wheel_delta=0

    def can_leave(self):
        if self.detail.saving:self.notice.set('素材正在保存，请稍候');return False
        if self.tab=='detail' and self.detail.dirty():
            self.detail.note.set('有未保存修改，请保存后再离开，或撤销修改');return False
        if self.frequency_editor.dirty():
            self.notice.set('默认表情频率尚未保存，请先保存');return False
        return True

    def back_to_library(self):
        self.switch('library')

    def switch(self,key):
        if self.tab=='detail' and key!='detail' and not self.can_leave():return
        self._stash()
        self._stop_preview();self.detail.stop()
        self.tab=key
        for name,box in self.pages.items():
            if name==key:box.grid()
            else:box.grid_remove()
            if name in self.tabs:self.tabs[name].configure(bg=PEACH if name==key else PAPER,fg=INK,
                                      font=('Microsoft YaHei UI',10,'bold' if name==key else 'normal'))
        self.canvas.yview_moveto(0)
        if key=='library':self._gallery()
        elif key=='import':self._select_row()

    def open_guide(self):
        try:os.startfile(str(ASSETS/'表情包收集说明.txt'))
        except OSError:self.notice.set('说明文件：'+str(ASSETS/'表情包收集说明.txt'))

    def _heading(self,parent,title,icon):
        from .workspace_ui import glyph
        head=tk.Frame(parent,bg=PAPER);head.pack(fill='x',pady=(0,7))
        art=glyph(head,icon);tk.Label(head,image=art,bg=PAPER).pack(side='left',padx=(0,6))
        label(head,title,13,bold=True).pack(side='left')
        tk.Frame(parent,bg=LINE,height=1).pack(fill='x',pady=(0,8))
        return head

    def _build_library(self):
        box=self.pages['library']
        left=card(box,pad=12);left.grid(row=0,column=0,sticky='nsew',padx=(0,5))
        self._heading(left,'素材库','list')
        label(left,'图片区域滚轮翻页 · 点击查看编辑 · 新图用「导入素材」',9,color=MUTED).pack(anchor='w',pady=(0,8))
        row=tk.Frame(left,bg=PAPER);row.pack(fill='x')
        self.categories=ttk.Combobox(row,textvariable=self.filter,state='readonly',width=10)
        self.categories.pack(side='left')
        self.categories.bind('<<ComboboxSelected>>',lambda _:self._filter())
        label(row,'搜索',9,color=MUTED).pack(side='left',padx=(7,3))
        ttk.Entry(row,textvariable=self.query,width=12).pack(side='left',expand=True,fill='x',ipady=2)
        self.gridbox=tk.Frame(left,bg=PAPER);self.gridbox.pack(fill='both',expand=True,pady=7)
        label(self.gridbox,'正在读取本地素材库…',color=MUTED).grid(columnspan=3,pady=52)
        for i in range(3):self.gridbox.columnconfigure(i,weight=1,uniform='thumb')
        footer=tk.Frame(left,bg=PAPER);footer.pack(side='bottom',fill='x')
        self.counter=label(footer,'',9,color=MUTED);self.counter.pack(side='left')
        self.next=button(footer,'下一页',lambda:self._turn(1),compact=True);self.next.pack(side='right')
        self.prev=button(footer,'上一页',lambda:self._turn(-1),compact=True);self.prev.pack(side='right',padx=4)
        right=card(box,pad=12);right.grid(row=0,column=1,sticky='nsew',padx=(5,0))
        from .reply_rules_ui import ReplyRulesEditor
        self.frequency_editor=ReplyRulesEditor(right,self.panel,defaults=True)
        self.frequency_editor.pack(fill='x',pady=(0,18))
        self._heading(right,'分类优先级','send')
        self.pref_box=tk.Frame(right,bg=PAPER);self.pref_box.pack(fill='x')
        tk.Checkbutton(right,text='不偏好任何分类',variable=self.neutral,bg=PAPER,fg=INK,
                       activebackground=PAPER).pack(anchor='w',pady=7)
        note=label(right,'备用分类仅在没有其他合适分类可选时参与。停用分类不会发送。',9,color=MUTED)
        note.configure(wraplength=205);note.pack(anchor='w',pady=(2,12))
        button(right,'保存偏好设置',self.save,primary=True,icon_name='save').pack(side='bottom',anchor='e')

    def _filter(self):
        self.page=0;self.wheel_delta=0;self._gallery()

    def _search(self):
        if self.search_job is not None:self.after_cancel(self.search_job)
        def run():self.search_job=None;self._filter()
        self.search_job=self.after(150,run)

    def _turn(self,delta):
        target=min(self.last_page,max(0,self.page+delta))
        if target==self.page:return
        self.page=target;self._gallery()

    def _gallery(self):
        if self.lib is None:return
        self.lib.refresh()
        key=(self.lib._stamp,self.filter.get(),self.query.get().strip().casefold(),self.page,self.thumb_edge)
        if key==self.gallery_key:return
        cats=sorted(set(CATEGORIES)|{s.character for s in self.lib.items}|set(self.lib.preferences))
        self.categories.configure(values=(ALL,*cats))
        if self.filter.get() not in (ALL,*cats):self.filter.set(ALL)
        self.batch_pick.configure(values=(AUTO,*cats))
        self.edit_pick.configure(values=cats)
        if tuple(self.priorities)!=tuple(cats):
            previous={k:v.get() for k,v in self.priorities.items()}
            for w in self.pref_box.winfo_children():w.destroy()
            self.priorities={}
            for name in cats:
                row=tk.Frame(self.pref_box,bg=PAPER);row.pack(fill='x',pady=8)
                text=label(row,name,bold=True);text.configure(wraplength=130);text.pack(side='left')
                var=self.priorities[name]=tk.StringVar(self,value=previous.get(name,self.lib.preferences.get(name,'优先' if name=='菲比' else '标准')))
                ttk.Combobox(row,textvariable=var,values=PRIORITIES,state='readonly',width=6).pack(side='right')
            if not previous:self.neutral.set(self.lib.neutral)
        query=self.query.get().strip().casefold()
        groups=defaultdict(list)
        for s in self.lib.items:
            if self.filter.get() not in (ALL,s.character):continue
            words=' '.join((s.meaning,*s.tags,s.character,self.lib.filenames.get(s.sha256,''))).casefold()
            if query and query not in words:continue
            groups[s.character].append(s)
        order=list(dict.fromkeys((*CATEGORIES,*sorted(groups))))
        # Interleave collections on the overview; imports appear before older entries in their own collection.
        for values in groups.values():values.sort(key=lambda s:s.path.parent!=self.lib.shelf.files)
        items=[groups[k][i] for i in range(max(map(len,groups.values()),default=0)) for k in order if i<len(groups[k])]
        last=self.last_page=max(0,(len(items)-1)//6);self.page=min(last,max(0,self.page))
        self.gallery_key=(self.lib._stamp,self.filter.get(),query,self.page,self.thumb_edge)
        self.counter.configure(text=f'{len(items)} 条 · {self.page+1}/{last+1}')
        for b,on in ((self.prev,self.page>0),(self.next,self.page<last)):b.configure(state='normal' if on else 'disabled')
        for w in self.gridbox.winfo_children():w.destroy()
        self.photos=[]
        for i,s in enumerate(items[self.page*6:self.page*6+6]):
            tile=tk.Frame(self.gridbox,bg='#F7F8F7',highlightbackground=LINE,highlightthickness=1)
            tile.grid(row=i//3,column=i%3,sticky='nsew',padx=2,pady=3)
            try:
                photo=self.thumb_cache.get((s.sha256,self.thumb_edge))
                if photo is None:
                    with Image.open(s.path) as image:
                        if image.width*image.height>4_000_000:raise ValueError()
                        im=image.convert('RGBA');im.thumbnail((self.thumb_edge,self.thumb_edge))
                    photo=ImageTk.PhotoImage(im,master=self)
                    if len(self.thumb_cache)>=72:self.thumb_cache.pop(next(iter(self.thumb_cache)))
                    self.thumb_cache[s.sha256,self.thumb_edge]=photo
                self.photos.append(photo)
                tk.Label(tile,image=photo,bg=tile.cget('bg'),height=self.thumb_edge+10).pack(fill='x',pady=5)
            except (OSError,ValueError):label(tile,'预览不可用',9).pack(pady=21)
            label(tile,s.character+(' · GIF' if s.animated else ''),8,color=MUTED).pack(anchor='w',padx=4)
            meaning=s.meaning if len(s.meaning)<=28 else s.meaning[:27]+'…'
            caption=label(tile,meaning,9);caption.configure(wraplength=self.thumb_edge+8);caption.pack(anchor='w',padx=4,pady=(0,5))
            for w in (tile,*tile.winfo_children()):w.bind('<Button-1>',lambda _,item=s:self.edit_item(item))
        if not items:label(self.gridbox,'还没有匹配的素材',color=MUTED).grid(columnspan=3,pady=52)

    def save(self):
        if self.tab=='detail':self.detail.save();return
        if self.lib is None:self.notice.set('素材库还在加载，请稍候');return
        if self.tab=='import':self.confirm();return
        preferences={k:v.get() for k,v in self.priorities.items()};neutral=self.neutral.get()
        def done(_):
            self.lib.refresh();self.notice.set('发送偏好已保存，对所有监听会话的后续选图生效')
            if hasattr(self.panel,'notice'):self.panel.notice.set(self.notice.get())
        self.panel.work(lambda:self.lib.shelf.save_preferences(preferences,neutral),done,
                        self.notice.set,label='保存表情偏好')

    def _build_queue(self):
        box=self.pages['import']
        left=card(box,pad=12);left.grid(row=0,column=0,sticky='nsew',padx=(0,5))
        self._heading(left,'上传队列','list')
        tools=tk.Frame(left,bg=PAPER);tools.pack(fill='x',pady=3)
        button(tools,'选择文件',self.pick_files,compact=True).pack(side='left')
        button(tools,'选择文件夹',self.pick_folder,compact=True).pack(side='left',padx=4)
        tools=tk.Frame(left,bg=PAPER);tools.pack(fill='x',pady=(4,7))
        label(tools,'分类',9).pack(side='left',padx=(0,5))
        self.batch_pick=ttk.Combobox(tools,textvariable=self.batch,width=9)
        self.batch_pick.pack(side='left',expand=True,fill='x')
        self.identify=button(tools,'AI 识别',self.analyze,primary=True,compact=True)
        self.identify.pack(side='right',padx=(5,0))
        table=tk.Frame(left,bg=PAPER);table.pack(fill='both',expand=True)
        self.queue=ttk.Treeview(table,columns=('name','state'),show='tree headings',height=9,selectmode='extended')
        self.queue.column('#0',width=40,minwidth=40,stretch=False)
        for k,t,w in [('name','文件名',140),('state','状态',98)]:
            self.queue.heading(k,text=t);self.queue.column(k,width=w,minwidth=48,stretch=k=='name')
        sb=ttk.Scrollbar(table,orient='vertical',command=self.queue.yview)
        self.queue.configure(yscrollcommand=sb.set);sb.pack(side='right',fill='y');self.queue.pack(fill='both',expand=True)
        self.queue.bind('<<TreeviewSelect>>',lambda _:self._select_row())
        foot=tk.Frame(left,bg=PAPER);foot.pack(fill='x',pady=(8,0))
        self.queue_total=label(foot,'0 个文件',9,color=MUTED);self.queue_total.pack(side='left')
        button(foot,'移出选中',self.remove,compact=True).pack(side='right')
        right=card(box,pad=12);right.grid(row=0,column=1,sticky='nsew',padx=(5,0))
        self._heading(right,'素材详情','stickers')
        self.preview=tk.Label(right,bg='#F5F6F4',text='选择素材查看',fg=MUTED)
        self.preview.pack(fill='x')
        label(right,textvariable=self.info,size=8,color=MUTED).pack(anchor='w',pady=3)
        for key,title in [('category','分类'),('meaning','用途'),('tags','情绪标签'),('source','来源')]:
            line=tk.Frame(right,bg=PAPER);line.pack(fill='x',pady=3)
            label(line,title,9,width=7).pack(side='left')
            if key=='category':control=self.edit_pick=ttk.Combobox(line,textvariable=self.fields[key],width=10)
            else:control=ttk.Entry(line,textvariable=self.fields[key],width=10)
            control.pack(fill='x',expand=True)
        self.accept=button(right,'确认入库',self.confirm,primary=True,icon_name='save')
        self.accept.pack(side='bottom',anchor='e',pady=(8,0))

    def _queue_thumbnail(self,key,picture):
        try:
            with Image.open(picture.path) as im:
                preview=im.convert('RGBA');preview.thumbnail((28,28))
            photo=ImageTk.PhotoImage(preview,master=self)
            self.queue_photos[key]=photo
            self.queue.item(key,image=photo)
        except (ValueError,OSError):pass

    def _stash(self):
        row=self.rows.get(self.selected)
        if row:
            for k,v in self.fields.items():setattr(row,k,v.get().strip())

    def _stop_preview(self):
        if self.animation is not None:self.after_cancel(self.animation);self.animation=None
        if self.preview_file is not None:self.preview_file.close();self.preview_file=None

    def _animate(self):
        im=self.preview_file
        if im is None:return
        try:
            frame=im.convert('RGBA');frame.thumbnail((144,100))
            self.preview_photo=ImageTk.PhotoImage(frame,master=self)
            self.preview.configure(image=self.preview_photo,text='',height=102)
            if getattr(im,'n_frames',1)>1 and self.active:
                delay=max(40,min(2000,im.info.get('duration',120)))
                im.seek((im.tell()+1)%im.n_frames)
                self.animation=self.after(delay,self._animate)
        except (ValueError,OSError):self._stop_preview()

    def _select_row(self):
        keys=self.queue.selection()
        key=self.queue.focus() if self.queue.focus() in keys else (keys[0] if keys else None)
        self._stash();self.selected=key
        self._stop_preview();self.preview.configure(image='',text='选择素材查看',height=3)
        row=self.rows.get(key)
        for k,v in self.fields.items():v.set(getattr(row,k,'') if row else '')
        self.info.set('')
        if row and row.picture:
            p=row.picture
            self.info.set(f'{p.extension.upper()} · {p.width}×{p.height} · {p.frames} 帧 · {p.size//1024} KiB')
            if self.active:
                try:self.preview_file=Image.open(row.path);self._animate()
                except OSError:pass
        if row:self.notice.set(row.status)

    def pick_files(self):
        if self.busy:return
        paths=filedialog.askopenfilenames(parent=self,title='选择表情包原件',
            filetypes=[('表情图片','*.gif *.png *.jpg *.jpeg *.webp')])
        if paths:self.add_paths(paths)

    def pick_folder(self):
        if self.busy:return
        folder=filedialog.askdirectory(parent=self,title='选择表情包文件夹')
        if not folder:return
        from itertools import islice
        paths=list(islice((p for p in Path(folder).rglob('*') if p.is_file() and p.suffix.lower() in FILE_SUFFIXES),101))
        if len(paths)>100:self.notice.set('每批最多 100 张，请选择更小的分类文件夹');return
        if paths:self.add_paths(sorted(paths))
        else:self.notice.set('这个文件夹中没有支持的图片')

    def add_paths(self,paths):
        if self.busy:return
        if self.lib is None:self.notice.set('素材库还在加载，请稍候再导入');return
        self.switch('import')
        if self.tab!='import':return
        paths=list(dict.fromkeys(Path(p).resolve() for p in paths))
        new=[p for p in paths if str(p) not in self.rows]
        if len(self.rows)+len(new)>100:self.notice.set('每批最多 100 张，请先移出已处理的素材');return
        for p in new:
            key=str(p);self.rows[key]=QueueRow(p)
            self.queue.insert('', 'end',iid=key,values=(p.name,'待识别'))
        self.queue_total.configure(text=f'{len(self.rows)} 个文件')
        if new:
            keys=[str(p) for p in new]
            self.queue.selection_set(keys);self.queue.focus(keys[0])
            self.analyze(keys)

    def analyze(self,keys=None):
        if self.lib is None:self.notice.set('素材库还在加载，请稍候再识别');return
        if self.busy:
            if self.analysis_running:
                self.cancel_analysis=True
                self.notice.set('当前图片返回后停止识别，已完成的结果会保留')
            return
        self._stash()
        keys=list(keys if keys is not None else self.queue.selection() or self.rows.keys())
        if not keys:self.notice.set('请先选择图片');return
        category=self.batch.get().strip()
        category='' if category==AUTO else category
        if len(category)>20:self.notice.set('分类最多 20 字');return
        model=ChatModel(Settings.load(self.panel.directory));known=self.lib.reviewed()
        self.busy=True;self.analysis_running=True;self.cancel_analysis=False
        self.identify.configure(text='停止识别');self.accept.configure(state='disabled')
        def next_file(index):
            if index==len(keys) or self.cancel_analysis:
                self.busy=False;self.analysis_running=False
                self.identify.configure(state='normal',text='AI 识别');self.accept.configure(state='normal')
                self.notice.set('识别已停止，已完成的结果保留' if self.cancel_analysis else '识别结束；可修改详情，确认入库将保存所选行');return
            key=keys[index];row=self.rows[key]
            before={k:getattr(row,k) for k in self.fields}
            self.queue.set(key,'state','识别中');self.notice.set(f'正在识别 {index+1}/{len(keys)} · {row.path.name}')
            def read():
                picture=inspect_picture(row.path)
                try:
                    details,status=self.lib.shelf.analyze(picture,category,model,known)
                    return picture,details,status
                except Exception as error:
                    return picture,None,'待填写：'+str(error)[:140]
            def done(result):
                if self.selected==key:self._stash()
                row.picture,details,row.status=result
                if details:
                    values=dict(category=details['character'],meaning=details['meaning'],
                                tags='、'.join(details['tags']),source=details['source'])
                    for k,value in values.items():
                        if getattr(row,k)==before[k]:setattr(row,k,value)
                elif category:row.category=category
                if row.picture:self._queue_thumbnail(key,row.picture)
                self.queue.set(key,'state',row.status.split('：')[0])
                if self.selected==key:
                    self.selected=None;self._select_row()
                next_file(index+1)
            def fail(error):done((None,None,'不支持：'+error))
            self.panel.work(read,done,fail,mutates=False)
        next_file(0)

    def edit_item(self,item):
        if self.busy:return
        self.switch('detail');self.detail.open(item)

    def remove(self):
        if self.busy:return
        keys=self.queue.selection()
        self.selected=None
        for key in keys:
            self.rows.pop(key,None);self.queue.delete(key);self.queue_photos.pop(key,None)
        self.queue_total.configure(text=f'{len(self.rows)} 个文件');self._select_row()

    def confirm(self):
        if self.busy:return
        self._stash();keys=self.queue.selection()
        if not keys:self.notice.set('请在队列中选择要入库的素材');return
        jobs=[]
        try:
            for key in keys:
                r=self.rows[key]
                if not r.picture:raise ValueError(r.path.name+'：原图尚未验证')
                tags=[s for s in re.split(r'[、,，;；\s]+',r.tags) if s]
                checked=annotation(r.category,r.meaning,tags,r.source)
                jobs.append((key,r.picture,dict(category=checked['character'],meaning=checked['meaning'],tags=checked['tags'],source=checked['source'])))
        except ValueError as e:self.notice.set(str(e));return
        self.busy=True;self.accept.configure(state='disabled')
        def save_files():
            result=[]
            for key,picture,details in jobs:
                try:result.append((key,'已入库' if self.lib.shelf.commit(picture,details) else '已更新'))
                except (ValueError,OSError) as e:result.append((key,'失败：'+str(e)))
            return result
        def done(result):
            self.busy=False;self.accept.configure(state='normal')
            for key,status in result:
                self.rows[key].status=status;self.queue.set(key,'state',status.split('：')[0])
            self.lib.refresh()
            ok=sum(not s.startswith('失败') for _,s in result)
            self.notice.set(f'已保存 {ok}/{len(result)} 张；同一原图不会重复入库')
        def fail(error):
            self.busy=False;self.accept.configure(state='normal');self.notice.set(error)
        self.panel.work(save_files,done,fail,label='保存表情素材')
