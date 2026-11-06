"""Shared native controls for the desktop workspace."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

NAVY, INK, BG = '#1B3044', '#203448', '#F5F2EB'
PAPER, PEACH, LINE = '#FFFFFF', '#F6B18C', '#DBE0E1'
MUTED, GREEN = '#647482', '#24764D'
FONT = 'Microsoft YaHei UI'
ASSETS = Path(__file__).resolve().parent/'ui_assets'


def label(parent, text='', size=10, *, bold=False, color=INK, **kw):
    return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=color,anchor='w',justify='left',
                    font=(FONT,size,'bold' if bold else 'normal'),**kw)


def _cache(parent):
    root=parent._root()
    if not hasattr(root,'_workspace_art'):
        root._workspace_art={'glyph':{},'element':{},'style':{},'corner':{}}
    return root._workspace_art


def _mix(widget,a,b,amount):
    first,second=widget.winfo_rgb(a),widget.winfo_rgb(b)
    return '#'+''.join(f'{round((x*(1-amount)+y*amount)/257):02x}' for x,y in zip(first,second))


def _surface(parent):
    try:color=str(parent.cget('background'))
    except tk.TclError:color=ttk.Style(parent).lookup(parent.winfo_class(),'background') or BG
    return '#'+''.join(f'{x//257:02x}' for x in parent.winfo_rgb(color))


_LINES={
    'stickers':[(3,4,21,4,21,20,3,20,3,4),(4,17,10,11,13,14,16,10,21,15)],
    'assistant':[(2,11,12,3,22,11),(5,10,5,21,19,21,19,10)],
    'sessions':[(4,5,19,5,21,7,21,16,19,18,11,18,6,22,6,18,3,16,3,7,4,5)],
    'persona':[(4,21,5,17,8,14,16,14,19,17,20,21)],
    'companion':[(12,21,3,12,2,9,3,6,6,4,9,5,12,8,15,5,18,4,21,6,22,10,21,13,12,21)],
    'settings':[(5,3,5,8),(5,12,5,21),(12,3,12,14),(12,18,12,21),(19,3,19,6),(19,10,19,21)],
    'play':[(7,4,20,12,7,20,7,4)],'pause':[(8,5,8,19),(16,5,16,19)],
    'send':[(2,11,22,3,16,22,11,15,2,11),(11,15,21,4)],
    'check':[(7,12,10,15,17,8)],'eye':[(2,12,5,8,9,6,15,6,19,8,22,12,19,16,15,18,9,18,5,16,2,12)],
    'people':[(2,21,3,17,6,15,11,15,14,17,15,21),(17,14,20,16,22,20)],
    'refresh':[(20,6,20,11,15,11),(20,11,19,7,16,4,11,3,6,5,3,10,4,16,8,20,14,21,19,18)],
    'clock':[(12,6,12,12,16,14)],'add':[(5,12,19,12),(12,5,12,19)],
    'back':[(15,5,8,12,15,19)],'next':[(9,5,16,12,9,19)],'list':[(8,6,21,6),(8,12,21,12),(8,18,21,18)],
    'save':[(4,3,17,3,21,7,21,21,3,21,3,4,4,3),(8,3,8,9,16,9,16,3),(7,20,7,13,17,13,17,20)],
}
_CIRCLES={'persona':[(8,3,16,11)],'settings':[(3,8,7,12),(10,14,14,18),(17,6,21,10)],
          'people':[(5,4,12,11),(14,5,19,10)],'check':[(2,2,22,22)],
          'eye':[(9,9,15,15)],'clock':[(2,2,22,22)],'list':[(2,5,4,7),(2,11,4,13),(2,17,4,19)]}


def glyph(parent,name,color=INK,size=18,*,disc=None,gap=0):
    """A cached antialiased outline icon; the adjacent native text supplies its name."""
    cache=_cache(parent)['glyph']
    key=(name,color,size,disc,gap)
    if key not in cache:
        scale=4
        art=Image.new('RGBA',(size*scale,size*scale));draw=ImageDraw.Draw(art)
        inset=4 if disc else 0
        if disc:draw.rounded_rectangle((0,0,size*scale-1,size*scale-1),radius=10*scale,fill=disc)
        rate=(size-2*inset)*scale/24
        def coords(points):return [round(inset*scale+x*rate) for x in points]
        stroke=max(2,round(rate*1.75))
        for path in _LINES.get(name,[]):draw.line(coords(path),fill=color,width=stroke,joint='curve')
        for bounds in _CIRCLES.get(name,[]):draw.ellipse(coords(bounds),outline=color,width=stroke)
        small=art.resize((size,size),Image.Resampling.LANCZOS)
        if gap:
            padded=Image.new('RGBA',(size+gap,size));padded.paste(small,(0,0));small=padded
        cache[key]=ImageTk.PhotoImage(small,master=parent)
    return cache[key]


def _rounded(size,face,edge,outside,radius,focused=False):
    scale=3
    art=Image.new('RGB',(size*scale,size*scale),outside)
    draw=ImageDraw.Draw(art)
    draw.rounded_rectangle((0,0,art.width-1,art.height-1),radius=radius*scale,
                           fill=face,outline='#4384A4' if focused else edge,width=(2 if focused else 1)*scale)
    return art.resize((size,size),Image.Resampling.LANCZOS)


def _button_style(parent,face,ink,outside,font,px,py,nav):
    store=_cache(parent)
    style=ttk.Style(parent)
    palette=(style.theme_use(),face,outside,nav)
    if palette not in store['element']:
        edge=face if nav else _mix(parent,face,INK,.22)
        dark=sum(parent.winfo_rgb(face)) < 3*32768
        hover=_mix(parent,face,PAPER,.14) if dark else _mix(parent,face,'#A6BBC7',.23)
        pressed=_mix(parent,face,NAVY,.28 if dark else .16)
        disabled=_mix(parent,outside,face,.48)
        states=[('normal',face,edge,False),('active',hover,edge,False),('pressed',pressed,edge,False),
                ('disabled',disabled,disabled,False),('focus',face,edge,True),
                ('active-focus',hover,edge,True),('pressed-focus',pressed,edge,True)]
        images={key:ImageTk.PhotoImage(_rounded(30,color,border,outside,6,focus),master=parent)
                for key,color,border,focus in states}
        element='XiaomaoSurface'+str(len(store['element']))
        style.element_create(element,'image',images['normal'],('disabled',images['disabled']),
                             ('pressed','focus',images['pressed-focus']),('pressed',images['pressed']),
                             ('active','focus',images['active-focus']),('focus',images['focus']),('active',images['active']),
                             border=10,padding=0,sticky='nsew')
        store['element'][palette]=(element,images)
    element=store['element'][palette][0]
    key=(palette,ink,str(font),px,py)
    if key not in store['style']:
        name=f"XM{len(store['style'])}.TButton"
        style.layout(name,[(element,{'sticky':'nsew','children':[
            ('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})])
        style.configure(name,font=font,foreground=ink,background=outside,
                        padding=(px,py),anchor='w' if nav else 'center',width=0,shiftrelief=0)
        muted='#B3C3CD' if sum(parent.winfo_rgb(outside))<3*32768 else '#6A7880'
        style.map(name,foreground=[('disabled',muted),('!disabled',ink)])
        store['style'][key]=name
    return store['style'][key]


class HoverTip:
    """Small in-window hint; never creates or focuses another native window."""
    def __init__(self,widget,text,delay=450):
        self.widget,self.text,self.delay=widget,text,delay
        self.job=None
        root=widget.winfo_toplevel()
        self.label=tk.Label(root,bg=NAVY,fg=PAPER,font=(FONT,9),padx=9,pady=5,
                            bd=0,wraplength=260,takefocus=False)
        for event in ('<Enter>','<FocusIn>'):widget.bind(event,self.schedule,add='+')
        for event in ('<Leave>','<FocusOut>','<ButtonPress>','<Escape>','<Return>','<space>'):
            widget.bind(event,self.hide,add='+')
        widget.bind('<Destroy>',self.destroy,add='+')

    def schedule(self,_event=None):
        self.hide()
        self.job=self.widget.after(self.delay,self.show)

    def show(self):
        self.job=None
        if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():return
        self.label.configure(text=self.text() if callable(self.text) else self.text)
        self.label.update_idletasks()
        root=self.widget.winfo_toplevel()
        x=self.widget.winfo_rootx()-root.winfo_rootx()
        y=self.widget.winfo_rooty()-root.winfo_rooty()+self.widget.winfo_height()+5
        x=max(4,min(x,root.winfo_width()-self.label.winfo_reqwidth()-4))
        y=max(4,min(y,root.winfo_height()-self.label.winfo_reqheight()-4))
        self.label.place(x=x,y=y);self.label.lift()

    def hide(self,_event=None):
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        if self.label.winfo_exists():self.label.place_forget()

    def destroy(self,event):
        if event.widget is not self.widget:return
        self.hide();self.label.destroy()


class ActionButton(ttk.Button):
    """The themed button handles activation, accessibility and state in Tk itself."""
    def __init__(self,parent,text,command,*,primary=False,dark=False,compact=False,icon_name=None,nav=False,selected=False):
        self.outside=_surface(parent)
        self.face=PEACH if primary or selected else NAVY if nav else '#354E63' if dark else '#FAFBFA'
        self.ink=PAPER if (dark or nav) and not (primary or selected) else INK
        self.font=(FONT,12 if nav else 10,'bold' if primary or selected else 'normal')
        self.px,self.py=(13,10) if nav else (9,5) if compact else (13,8)
        self.nav=nav;self.icon_name=icon_name;self.selected=selected
        skin=_button_style(parent,self.face,self.ink,self.outside,self.font,self.px,self.py,nav)
        image=glyph(parent,icon_name,self.ink,20 if nav else 16,gap=8 if nav else 5) if icon_name else ''
        super().__init__(parent,text=text,command=command,style=skin,image=image,compound='left',
                         takefocus=True,cursor='hand2',width=0)
        self.bind('<Return>',self._enter,add='+')
        self.bind('<KP_Enter>',self._enter,add='+')

    def _enter(self,_event):
        if not self.instate(('disabled',)):self.invoke()
        return 'break'

    def set_selected(self,value):
        if self.selected==value:return
        self.selected=value
        self.configure(bg=PEACH if value else NAVY,fg=INK if value else PAPER,
                       font=(FONT,12,'bold' if value else 'normal'))

    def configure(self,cnf=None,**kw):
        if isinstance(cnf,str) or (cnf is None and not kw):return super().configure(cnf)
        opts=dict(cnf or {},**kw)
        original=(self.face,self.ink,str(self.font),self.px,self.py)
        for key,field in (('bg','face'),('background','face'),('fg','ink'),('foreground','ink'),
                          ('font','font'),('padx','px'),('pady','py')):
            if key in opts:setattr(self,field,opts.pop(key))
        for key in ('highlightbackground','activebackground','activeforeground','disabledforeground'):
            opts.pop(key,None) # native theme supplies hover, focus and disabled palettes
        if original!=(self.face,self.ink,str(self.font),self.px,self.py):
            opts['style']=_button_style(self.master,self.face,self.ink,self.outside,self.font,self.px,self.py,self.nav)
            if self.icon_name:opts['image']=glyph(self,self.icon_name,self.ink,20 if self.nav else 16,gap=8 if self.nav else 5)
        for key in ('text','state'):
            if key in opts and self.cget(key)==opts[key]:opts.pop(key)
        if opts:return super().configure(**opts)

    config=configure

    def cget(self,key):
        names={'bg':'face','background':'face','fg':'ink','foreground':'ink','font':'font','padx':'px','pady':'py'}
        if key in names:return getattr(self,names[key])
        value=super().cget(key)
        return str(value) if key=='state' else value

    __getitem__=cget


def button(parent,text,command,*,primary=False,dark=False,compact=False,icon_name=None,selected=False):
    return ActionButton(parent,text,command,primary=primary,dark=dark,compact=compact,
                        icon_name=icon_name,selected=selected)


def nav_button(parent,text,command,*,icon_name):
    return ActionButton(parent,text,command,icon_name=icon_name,nav=True)


def card(parent, *, pad=15,bg=PAPER,radius=7,border=LINE):
    """A normal geometry container with rounded corners outside its content padding."""
    outside=_surface(parent)
    frame=tk.Frame(parent,bg=bg,padx=pad,pady=pad,highlightthickness=1,highlightbackground=border)
    corners=_cache(parent)['corner']
    key=(outside,bg,border,radius)
    if key not in corners:
        size=radius*2+4
        art=_rounded(size,bg,border,outside,radius)
        corners[key]=[ImageTk.PhotoImage(art.crop((x,y,x+radius,y+radius)),master=parent)
                      for x,y in ((0,0),(size-radius,0),(0,size-radius),(size-radius,size-radius))]
    for image,(x,y,anchor) in zip(corners[key],((0,0,'nw'),(1,0,'ne'),(0,1,'sw'),(1,1,'se'))):
        patch=tk.Label(frame,image=image,padx=0,pady=0,bd=0,highlightthickness=0,bg=outside,takefocus=False)
        patch.place(relx=x,rely=y,anchor=anchor,bordermode='outside')
    return frame


def logo(parent,size):
    with Image.open(ASSETS/'app.png') as im:
        return ImageTk.PhotoImage(im.convert('RGBA').resize((size,size),Image.Resampling.LANCZOS),master=parent)


def styles(root):
    style=ttk.Style(root)
    if style.theme_use()!='clam':style.theme_use('clam')
    style.configure('.',font=(FONT,10),background=PAPER,foreground=INK)
    style.configure('TFrame',background=PAPER)
    style.configure('TLabel',background=PAPER,foreground=INK)
    style.configure('TButton',padding=(10,6),background='#EAF0F1',foreground=INK,bordercolor=LINE)
    style.map('TButton',background=[('active','#D6E2E5')])
    for key in ('TEntry','TCombobox','TSpinbox','Glass.TCombobox'):
        style.configure(key,fieldbackground=PAPER,foreground=INK,arrowcolor=INK,bordercolor=LINE,padding=5)
        style.map(key,fieldbackground=[('readonly',PAPER)],foreground=[('readonly',INK)])
    for key in ('TCheckbutton','TRadiobutton'):
        style.configure(key,background=PAPER,foreground=INK)
        style.map(key,background=[('active','#F3F0E9')])
    style.configure('Treeview',background=PAPER,fieldbackground=PAPER,foreground=INK,rowheight=37,borderwidth=0)
    style.map('Treeview',background=[('selected','#FBE4D5')],foreground=[('selected',INK)])
    style.configure('Treeview.Heading',background='#EDF0EF',foreground=INK,padding=6,font=(FONT,9,'bold'))
    style.configure('Vertical.TScrollbar',background='#CFD6D7',troughcolor=BG,borderwidth=0,arrowsize=11)


def icon(canvas,kind,color):
    """Small outline symbols; the adjacent text supplies the accessible name."""
    line=lambda *pts:canvas.create_line(*pts,fill=color,width=1.6,capstyle='round',joinstyle='round')
    oval=lambda x,y,r:canvas.create_oval(x-r,y-r,x+r,y+r,outline=color,width=1.6)
    if kind=='assistant':
        line(2,11,11,3,20,11);line(5,10,5,20,17,20,17,10)
    elif kind=='sessions':
        line(4,5,18,5,20,7,20,15,17,17,10,17,5,21,5,17,3,15,3,7,4,5)
    elif kind=='persona':
        oval(11,7,3.6);line(3,20,4,16,7,14,15,14,18,16,19,20,3,20)
    elif kind=='companion':
        line(11,19,3,11,2,8,3,5,6,4,9,5,11,7,14,4,17,4,20,6,20,10,11,19)
    else:
        for x,y in ((5,8),(11,15),(17,6)):
            line(x,3,x,y-2);line(x,y+2,x,20);oval(x,y,2)
