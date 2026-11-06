"""Edit a saved sticker without adding it to the import queue."""
import re
import tkinter as tk
from tkinter import ttk
from PIL import Image,ImageTk
from .sticker_import import inspect_picture,annotation
from .workspace_ui import BG,PAPER,MUTED,GREEN,button,card,label


class StickerDetail(tk.Frame):
    def __init__(self,parent,owner):
        super().__init__(parent,bg=BG)
        self.owner=owner;self.item=None;self.picture=None;self.ticket=None
        self.animation=None;self.image=None;self.photo=None;self.saving=False
        self.baseline=None
        self.fields={k:tk.StringVar(self) for k in ('category','meaning','tags','source')}
        self.note=tk.StringVar(self);self.info=tk.StringVar(self)
        self.columnconfigure(0,weight=2,uniform='detail');self.columnconfigure(1,weight=3,uniform='detail')
        bar=tk.Frame(self,bg=BG);bar.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,12))
        button(bar,'返回素材库',owner.back_to_library,icon_name='back',compact=True).pack(side='left')
        label(bar,'素材详情 · 已入库',12,bold=True).pack(side='left',padx=12)
        left=card(self,pad=20);left.grid(row=1,column=0,sticky='nsew',padx=(0,6))
        self.preview=tk.Label(left,bg='#F4F7F7',text='正在加载预览…',fg=MUTED,height=14)
        self.preview.pack(fill='both',expand=True)
        label(left,textvariable=self.info,size=9,color=MUTED,wraplength=240).pack(fill='x',pady=(12,0))
        label(left,'查看不会重复导入，也不会发送到微信',9,color=MUTED,wraplength=230).pack(fill='x',pady=(8,0))
        right=card(self,pad=20);right.grid(row=1,column=1,sticky='nsew',padx=(6,0))
        label(right,'编辑素材信息',16,bold=True).pack(anchor='w',pady=(0,14))
        for key,title in [('category','分类'),('meaning','用途'),('tags','情绪标签'),('source','来源')]:
            label(right,title,10,bold=True).pack(anchor='w',pady=(7,4))
            field=ttk.Combobox(right,textvariable=self.fields[key],width=12) if key=='category' else ttk.Entry(right,textvariable=self.fields[key],width=12)
            field.pack(fill='x',ipady=4)
            if key=='category':self.category=field
        label(right,'标签用顿号分隔；修改后需要保存',9,color=MUTED).pack(anchor='w',pady=(9,12))
        self.save_button=button(right,'保存修改',self.save,primary=True,icon_name='save')
        self.save_button.pack(anchor='e',pady=(8,0))
        button(right,'撤销未保存修改',self.discard,compact=True).pack(anchor='e',pady=(7,0))
        label(self,textvariable=self.note,size=10,color=MUTED,wraplength=700).grid(row=2,column=0,columnspan=2,sticky='ew',pady=10)

    def values(self):return tuple(v.get().strip() for v in self.fields.values())
    def dirty(self):return self.baseline is not None and self.values()!=self.baseline

    def discard(self):
        if self.saving or self.baseline is None:return
        for var,value in zip(self.fields.values(),self.baseline):var.set(value)
        self.note.set('已撤销未保存修改')

    def open(self,item):
        self.stop();self.item=item;self.picture=None;self.baseline=None
        self.preview.configure(image='',text='正在加载预览…',height=14)
        values=(item.character,item.meaning,'、'.join(item.tags),self.owner.lib.sources.get(item.sha256,''))
        for var,value in zip(self.fields.values(),values):var.set(value)
        self.category.configure(values=self.owner.categories.cget('values')[1:])
        self.baseline=self.values();self.note.set('已入库素材，可查看或修改标注')
        self.info.set('');self.save_button.configure(state='disabled')
        ticket=self.ticket=object()
        def done(picture):
            if not self.winfo_exists() or self.ticket is not ticket:return
            self.picture=picture
            self.info.set(f'{picture.extension.upper()} · {picture.width}×{picture.height} · {picture.frames} 帧 · {picture.size//1024} KiB')
            self.save_button.configure(state='normal')
            self.resume()
        def failed(error):
            if self.winfo_exists() and self.ticket is ticket:
                self.note.set(str(error));self.preview.configure(text='预览不可用')
        self.owner.panel.work(lambda:inspect_picture(item.path),done,failed,mutates=False)

    def resume(self):
        if self.image or not self.picture or not self.owner.active or self.owner.tab!='detail':return
        try:self.image=Image.open(self.picture.path);self.animate()
        except OSError:self.note.set('原图不可用，请检查素材文件')

    def animate(self):
        if self.image is None:return
        try:
            im=self.image.convert('RGBA');im.thumbnail((260,260),Image.Resampling.LANCZOS)
            self.photo=ImageTk.PhotoImage(im,master=self);self.preview.configure(image=self.photo,text='',height=276)
            if getattr(self.image,'n_frames',1)>1:
                delay=max(60,min(2000,self.image.info.get('duration',120)))
                self.image.seek((self.image.tell()+1)%self.image.n_frames)
                self.animation=self.after(delay,self.animate)
        except (OSError,ValueError,EOFError):self.stop()

    def stop(self):
        if self.animation is not None:self.after_cancel(self.animation);self.animation=None
        if self.image is not None:self.image.close();self.image=None

    def save(self):
        if self.saving or not self.picture:return
        try:
            category,meaning,tags,source=self.values()
            checked=annotation(category,meaning,[s for s in re.split(r'[、,，;；\s]+',tags) if s],source)
        except ValueError as error:self.note.set(str(error));return
        details=dict(category=checked['character'],meaning=checked['meaning'],tags=checked['tags'],source=checked['source'])
        picture=self.picture;before=self.values();ticket=self.ticket
        self.saving=True;self.save_button.configure(state='disabled');self.note.set('正在保存修改…')
        def done(_):
            if not self.winfo_exists() or self.ticket is not ticket:return
            self.saving=False;self.save_button.configure(state='normal')
            self.baseline=before;self.owner.lib.refresh()
            self.note.set('修改已保存，后续选图使用新标注'+('；还有未保存修改' if self.dirty() else ''))
        def failed(error):
            if self.winfo_exists():
                self.saving=False;self.save_button.configure(state='normal');self.note.set(str(error))
        self.owner.panel.work(lambda:self.owner.lib.shelf.commit(picture,details),done,failed,label='保存素材修改')
