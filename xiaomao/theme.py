"""Native single-window workspace and live reply dashboard."""
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .receipts import receipt_previews
from .workspace_ui import BG, FONT, GREEN, INK, LINE, MUTED, NAVY, PAPER, PEACH, HoverTip, button, card, glyph, label, logo, nav_button, styles

# The calibration dialog and older external callers still import these names.
TEXT, PANEL, GOLD, FIELD, AMBER = INK, BG, '#854E34', PAPER, PEACH
PAGES={'assistant':'工作台','sessions':'会话','persona':'人格','companion':'记忆与陪伴','stickers':'表情包','settings':'运行设置'}
TITLES={**PAGES,'contacts':'选择会话'}
MODES={'auto':'自动回复中','observe':'只观察','paused':'已暂停','stopped':'已停止',
       'starting':'正在启动后台','disconnected':'未连接助手','applying':'等待后台应用'}


def _install_resize_edges(root, view, hwnd):
    """Keep the normal system resize loop without painting a native frame at rest."""
    import ctypes
    from ctypes import wintypes as W
    import win32api
    import win32con as wc
    import win32gui

    class MinMaxInfo(ctypes.Structure):
        _fields_=[(name,W.POINT) for name in ('reserved','max_size','max_position','min_track','max_track')]

    proc_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM,ctypes.c_size_t,ctypes.c_size_t)
    common=ctypes.WinDLL('comctl32',use_last_error=True)
    common.SetWindowSubclass.argtypes=[W.HWND,proc_type,ctypes.c_size_t,ctypes.c_size_t]
    common.RemoveWindowSubclass.argtypes=[W.HWND,proc_type,ctypes.c_size_t]
    common.DefSubclassProc.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    common.DefSubclassProc.restype=ctypes.c_ssize_t

    @proc_type
    def frame(window,msg,wparam,lparam,ident,_data):
        if msg==wc.WM_NCDESTROY:
            common.RemoveWindowSubclass(window,frame,ident)
        elif msg==wc.WM_NCACTIVATE:
            # Let Windows update activation without painting a caption over
            # the Tk header. -1 is DefWindowProc's no-repaint sentinel.
            return common.DefSubclassProc(window,msg,wparam,-1)
        elif msg in (wc.WM_NCPAINT,0x00AE,0x00AF):
            # WM_NCUAHDRAWCAPTION / WM_NCUAHDRAWFRAME may follow activation.
            return 0
        elif msg==wc.WM_GETMINMAXINFO:
            result=common.DefSubclassProc(window,msg,wparam,lparam)
            monitor=win32api.GetMonitorInfo(win32api.MonitorFromWindow(window,wc.MONITOR_DEFAULTTONEAREST))
            left,top,right,bottom=monitor['Work']
            mx,my,*_=monitor['Monitor']
            bounds=MinMaxInfo.from_address(lparam)
            bounds.max_size=W.POINT(right-left,bottom-top)
            bounds.max_position=W.POINT(left-mx,top-my)
            return result
        elif msg==wc.WM_NCCALCSIZE:
            return 0
        elif msg==wc.WM_SYSCOMMAND and (wparam & 0xfff0)==wc.SC_SIZE:
            # DefWindowProc requires WS_THICKFRAME to enter native sizing.
            style=win32gui.GetWindowLong(window,wc.GWL_STYLE)
            win32gui.SetWindowLong(window,wc.GWL_STYLE,style | wc.WS_THICKFRAME)
            try:
                return common.DefSubclassProc(window,msg,wparam,lparam)
            finally:
                if win32gui.IsWindow(window):
                    current=win32gui.GetWindowLong(window,wc.GWL_STYLE)
                    win32gui.SetWindowLong(window,wc.GWL_STYLE,current & ~wc.WS_THICKFRAME)
                    win32gui.SetWindowPos(window,0,0,0,0,0,wc.SWP_FRAMECHANGED|wc.SWP_NOMOVE|
                                          wc.SWP_NOSIZE|wc.SWP_NOZORDER|wc.SWP_NOACTIVATE)
        return common.DefSubclassProc(window,msg,wparam,lparam)

    root._frame_proc=frame
    if not common.SetWindowSubclass(hwnd,frame,1,0):raise ctypes.WinError(ctypes.get_last_error())
    def resize(event,edge):
        if root.state()=='zoomed':return 'break'
        if win32gui.GetCapture():win32gui.ReleaseCapture()
        point=win32api.MAKELONG(event.x_root & 0xffff,event.y_root & 0xffff)
        win32gui.PostMessage(hwnd,wc.WM_SYSCOMMAND,wc.SC_SIZE|edge,point)
        return 'break'

    # The upper and lower grips use the colour of the pane beneath them.
    # All eight edges are mouse-accessible without reserving a row for them.
    def grip(parent,edge,cursor,**placement):
        widget=tk.Frame(parent,bg=parent.cget('bg'),cursor=cursor,takefocus=False)
        # Tk's default "inside" anchors grips to the *padded content*,
        # covering the last pixels of buttons rather than the window edge.
        widget.place(bordermode='outside',**placement)
        widget.bind('<ButtonPress-1>',lambda e:resize(e,edge))
        return widget

    for pane in (view.rail,view.main):
        grip(pane,3,'size_ns',relwidth=1,height=5,x=0,y=0)
        grip(pane,6,'size_ns',relwidth=1,height=5,x=0,rely=1,anchor='sw')
    for pane,side,anchor,edge,upper,lower in ((view.rail,0,'w',1,4,7),(view.main,1,'e',2,5,8)):
        grip(pane,edge,'size_we',relheight=1,width=5,relx=side,rely=.5,anchor=anchor)
        grip(pane,upper,'size_nw_se' if side==0 else 'size_ne_sw',width=7,height=7,
             relx=side,y=0,anchor='n'+anchor)
        grip(pane,lower,'size_ne_sw' if side==0 else 'size_nw_se',width=7,height=7,
             relx=side,rely=1,anchor='s'+anchor)


def _install_window_rounding(root,hwnd):
    """Release stale clipping during sizing, apply small corners once settled."""
    import win32gui
    state={'job':None,'observed':None,'clipped':False}
    def apply():
        state['job']=None
        if not root.winfo_exists():return
        zoomed=root.state()=='zoomed'
        left,top,right,bottom=win32gui.GetWindowRect(hwnd)
        region=0 if zoomed else win32gui.CreateRoundRectRgn(0,0,right-left+1,bottom-top+1,12,12)
        try:
            win32gui.SetWindowRgn(hwnd,region,False)
            state['clipped']=bool(region)
        except Exception:
            if region:win32gui.DeleteObject(region)
    def resized(event):
        if event.widget is not root:return
        size=(event.width,event.height,root.state())
        if size==state['observed']:return
        state['observed']=size
        if state['job'] is not None:root.after_cancel(state['job'])
        if state['clipped']:
            # An old smaller region crops growing windows until the timer runs.
            win32gui.SetWindowRgn(hwnd,0,False);state['clipped']=False
        state['job']=root.after(140,apply)
    def destroy(event):
        if event.widget is root and state['job'] is not None:
            root.after_cancel(state['job']);state['job']=None
    root.bind('<Configure>',resized,add='+')
    root.bind('<Destroy>',destroy,add='+')
    state['job']=root.after(0,apply)
    return resized


def titlebar(root, view):
    """Keep the normal Windows frame and put its controls in the page header."""
    if root.tk.call('tk', 'windowingsystem') != 'win32':
        return
    import win32api
    import win32con as wc
    import win32gui

    root.update_idletasks()
    hwnd = win32gui.GetParent(root.winfo_id())
    _, _, width, height = win32gui.GetClientRect(hwnd)
    style = win32gui.GetWindowLong(hwnd, wc.GWL_STYLE)
    # Keep the resize border, system menu, taskbar identity and window title.
    win32gui.SetWindowLong(hwnd, wc.GWL_STYLE, (style & ~(wc.WS_CAPTION|wc.WS_THICKFRAME))|wc.WS_POPUP)
    _install_resize_edges(root, view, hwnd)
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, wc.SWP_FRAMECHANGED | wc.SWP_NOMOVE |
                         wc.SWP_NOSIZE | wc.SWP_NOZORDER | wc.SWP_NOACTIVATE)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    _, _, expanded_width, expanded_height = win32gui.GetClientRect(hwnd)
    extra_width, extra_height = expanded_width-width, expanded_height-height
    # Tk calculates its minimum outer size using the original caption metrics.
    min_width, min_height = root.minsize()
    root.minsize(max(1, min_width-extra_width), max(1, min_height-extra_height))
    win32gui.SetWindowPos(hwnd, 0, 0, 0, right-left-extra_width, bottom-top-extra_height,
                         wc.SWP_NOMOVE | wc.SWP_NOZORDER | wc.SWP_NOACTIVATE)

    def command(action):
        win32gui.PostMessage(hwnd, wc.WM_SYSCOMMAND, action, 0)
        return 'break'

    def toggle(_event=None):
        return command(wc.SC_RESTORE if root.state() == 'zoomed' else wc.SC_MAXIMIZE)

    controls = tk.Frame(view.header, bg=BG)
    controls.grid(row=0, column=2, padx=(12, 0), sticky='e')
    icons = {}
    for name, paths in {
        'minimize': [[(2, 8), (13, 8)]],
        'maximize': [[(2, 2), (13, 2), (13, 13), (2, 13), (2, 2)]],
        'restore': [[(5, 4), (5, 1), (14, 1), (14, 10), (12, 10)],
                    [(1, 5), (10, 5), (10, 14), (1, 14), (1, 5)]],
        'close': [[(2, 2), (13, 13)], [(13, 2), (2, 13)]],
    }.items():
        icon = Image.new('RGBA', (16, 16))
        pen = ImageDraw.Draw(icon)
        for path in paths:
            pen.line(path, fill=INK)
        icons[name] = ImageTk.PhotoImage(icon, master=root)
    for name, caption, handler in (
        ('minimize', '最小化', lambda: command(wc.SC_MINIMIZE)),
        ('maximize', '最大化', toggle),
        ('close', '关闭', lambda: command(wc.SC_CLOSE)),
    ):
        control = tk.Button(controls, name=name, text=caption, image=icons[name], command=handler,
                            width=40, height=28, padx=0, pady=0, bd=0,
                            relief='flat', bg=BG, fg=INK, takefocus=True, highlightcolor=MUTED,
                            highlightbackground=BG,
                            activebackground='#F1B7B2' if name=='close' else '#DCE3E5')
        control.pack(side='left')
        control.bind('<Return>', lambda e: e.widget.invoke())
        control._hint=HoverTip(control,lambda w=control,n=name:
            '关闭选项：后台运行 / 完全退出' if n=='close' else str(w.cget('text')))

    last_zoomed=None
    def update_maximize_icon(event):
        nonlocal last_zoomed
        if event.widget is root:
            zoomed = root.state() == 'zoomed'
            if last_zoomed==zoomed:return
            last_zoomed=zoomed
            controls.children['maximize'].configure(text='还原' if zoomed else '最大化',
                                                    image=icons['restore' if zoomed else 'maximize'])

    root.bind('<Configure>', update_maximize_icon, add='+')
    _install_window_rounding(root,hwnd)
    root.bind('<Alt-F4>', lambda _: command(wc.SC_CLOSE), add='+')
    # Hand dragging to Windows so snapping and dragging from maximized work.
    # Wait for real movement so a double-click still reaches Tk as a double-click.
    origin = None
    def press(event):
        nonlocal origin
        origin = (event.x_root, event.y_root)

    def drag(event):
        nonlocal origin
        if origin is None:
            return
        if (abs(event.x_root - origin[0]) < win32api.GetSystemMetrics(wc.SM_CXDRAG)
                and abs(event.y_root - origin[1]) < win32api.GetSystemMetrics(wc.SM_CYDRAG)):
            return
        origin = None
        if win32gui.GetCapture():
            win32gui.ReleaseCapture()
        point = win32api.MAKELONG(event.x_root & 0xffff, event.y_root & 0xffff)
        win32gui.PostMessage(hwnd, wc.WM_NCLBUTTONDOWN, wc.HTCAPTION, point)

    def menu(event):
        win32gui.TrackPopupMenu(win32gui.GetSystemMenu(hwnd, False), wc.TPM_RIGHTBUTTON,
                                event.x_root, event.y_root, 0, hwnd, None)
        return 'break'

    for area in view.drag_areas:
        area.bind('<ButtonPress-1>', press, add='+')
        area.bind('<B1-Motion>', drag, add='+')
        area.bind('<Double-Button-1>', toggle, add='+')
        area.bind('<Button-3>', menu, add='+')


def recent_result(data,fallback):
    record=data.get('last_result') or {}
    reason=data.get('reason') or fallback
    state=record.get('state')
    if not record.get('summary'):
        return '最近状态',reason or '等待本地助手状态'
    title={'unknown':'上一条发送结果待确认','verified':'最近已确认发送'}.get(state,'上一条消息未发送')
    if record.get('reason')=='participation_declined':
        title='这条选择不插话'
    mode={'paused':'已暂停','auto':'继续监听'}.get(data.get('mode'),'')
    details=(record.get('group'),record['summary'],mode,reason)
    return title,' · '.join(dict.fromkeys(text for text in details if text))


def _queue_scope(panel):
    cfg=panel.settings
    return (cfg.memory_scope,tuple((name,cfg.conversation_targets.get(name,{}).get('account'),
                                    cfg.conversation_targets.get(name,{}).get('id')) for name in cfg.allowed_groups))


def _scroll_when_needed(parent,widget,*,stable_width=False):
    holder=parent
    if stable_width:
        holder=tk.Frame(parent,bg=parent.cget('bg'))
        holder.pack(side='right',fill='y',before=widget)
        holder.pack_propagate(False)
    bar=ttk.Scrollbar(holder,command=widget.yview)
    if stable_width:holder.configure(width=bar.winfo_reqwidth())
    def position(first,last):
        bar.set(first,last)
        needed=float(first)>0.0001 or float(last)<0.9999
        if needed and not bar.winfo_manager():
            if stable_width:bar.pack(fill='both',expand=True)
            else:bar.pack(side='right',fill='y',before=widget)
        elif not needed and bar.winfo_manager():bar.pack_forget()
    widget.configure(yscrollcommand=position)


class GlassView(tk.Frame):
    def __init__(self,panel):
        super().__init__(panel.root,bg=BG)
        self.panel=panel
        self.page=None
        self.pages={}
        self.status={}
        self.busy=False
        self._queue_request=self._queue_scope=None
        self._receipt_cache={}
        self._recent_display=None
        self._styled_queue=None
        self._watch_rows=None
        self.watch_controls={}
        self._watch_layout_job=None
        self._watch_row_height=38
        self.queue_group=None
        self._traces=[]
        self._shortcuts=[]
        self._compact=None
        self._stacked=None
        self._layout_job=None
        self._nav_job=None
        styles(panel.root)
        self.rowconfigure(0,weight=1);self.columnconfigure(1,weight=1)
        self._sidebar()
        main=self.main=tk.Frame(self,bg=BG,padx=20,pady=12);main.grid(row=0,column=1,sticky='nsew')
        main.grid_columnconfigure(0,weight=1);main.grid_rowconfigure(1,weight=1)
        header=self.header=tk.Frame(main,bg=BG);header.grid(row=0,column=0,sticky='ew',pady=(0,10))
        header.grid_columnconfigure(0,weight=1)
        self.heading=tk.StringVar(self)
        heading=label(header,textvariable=self.heading,size=25,bold=True)
        heading.grid(row=0,column=0,sticky='w')
        self.drag_areas.extend((header,heading))
        self.status_chip=card(header,pad=5,bg='#E8EEE9',radius=9,border='#CADACF')
        self.status_chip.grid(row=0,column=1,sticky='e',padx=(5,0))
        self.header_status=label(self.status_chip,size=10,bold=True,color=GREEN)
        self.header_status.pack(padx=6)
        self.drag_areas.extend((self.status_chip,self.header_status))
        self.body=tk.Frame(main,bg=BG);self.body.grid(row=1,column=0,sticky='nsew')
        self.body.grid_rowconfigure(0,weight=1);self.body.grid_columnconfigure(0,weight=1)
        self.footer=tk.StringVar(self)
        self.foot_label=label(main,textvariable=self.footer,size=9,color=MUTED)
        tk.Frame(main,bg=LINE,height=1).grid(row=2,column=0,sticky='ew',pady=(10,0))
        self.foot_label.grid(row=3,column=0,sticky='ew',pady=(6,0))
        self.foot_label.bind('<Configure>',lambda e:self.foot_label.configure(wraplength=max(150,e.width-2)))
        self.pages['assistant']=self._dashboard()
        self.select('assistant')
        self._traces=[(var,var.trace_add('write',lambda *_:self.update_text()))
                      for var in (panel.notice,panel.reason)]
        self.bind('<Destroy>',self._destroyed,add='+')
        self.bind('<Configure>',self._resize_shell,add='+')
        self._keyboard()

    def _sidebar(self):
        rail=self.rail=tk.Frame(self,bg=NAVY,width=198);rail.grid(row=0,column=0,sticky='ns')
        rail.pack_propagate(False)
        brand=tk.Frame(rail,bg=NAVY);brand.pack(fill='x',padx=12,pady=(10,8))
        self.logo=logo(brand,36)
        self.brand_logo=tk.Label(brand,image=self.logo,bg=NAVY)
        self.brand_logo.pack(side='left',padx=(0,7))
        from .brand import BRAND_LINES
        self.brand_title=label(brand,BRAND_LINES,11,bold=True,color=PAPER);self.brand_title.pack(side='left')
        self.drag_areas=[brand,self.brand_logo,self.brand_title]
        tk.Frame(rail,bg='#3B5265',height=1).pack(fill='x',padx=13,pady=(0,7))
        self.nav={}
        for key,name in PAGES.items():
            control=nav_button(rail,name,lambda k=key:self.select(k),icon_name=key)
            control.pack(fill='x',padx=9,pady=2)
            self.nav[key]=control
        self.nav_marker=tk.Frame(rail,bg=PEACH,width=3,height=22)
        bottom=tk.Frame(rail,bg=NAVY);bottom.pack(side='bottom',fill='x',padx=13,pady=13)
        tk.Frame(bottom,bg='#465B6A',height=1).pack(fill='x',pady=(0,8))
        self.local_status=label(bottom,'本地助手',10,bold=True,color=PAPER);self.local_status.pack(anchor='w')
        self.side_note=label(bottom,'读取连接状态…',8,color='#D5E1E8');self.side_note.pack(anchor='w',pady=(2,0))
        label(bottom,'Alt + 1–6 切换页面',8,color='#ACC0CD').pack(anchor='w',pady=(8,0))

    def set_exiting(self,exiting):
        if exiting:
            self.header_status.configure(text='●  正在完全退出',fg=MUTED)
            self.side_note.configure(text='正在结束后台进程…')
        else:self.update_text()

    def _paint_nav(self):
        active='sessions' if self.page=='contacts' else self.page
        for key,control in self.nav.items():control.set_selected(key==active)
        if self._nav_job is not None:self.after_cancel(self._nav_job)
        def begin():
            control=self.nav.get(active)
            if control is None:self._nav_job=None;return
            target=control.winfo_y()+max(0,(control.winfo_height()-22)//2)
            start=self.nav_marker.winfo_y() if self.nav_marker.winfo_ismapped() else target
            def step(index=1):
                progress=1-(1-index/8)**3
                self.nav_marker.place(x=2,y=round(start+(target-start)*progress),width=3,height=22)
                self._nav_job=self.after(16,lambda:step(index+1)) if index<8 else None
            step()
        self._nav_job=self.after_idle(begin)

    def _dashboard(self):
        viewport=tk.Frame(self.body,bg=BG)
        self.dashboard_canvas=tk.Canvas(viewport,bg=BG,bd=0,highlightthickness=0,takefocus=False)
        self.dashboard_canvas.pack(side='left',fill='both',expand=True)
        _scroll_when_needed(viewport,self.dashboard_canvas,stable_width=True)
        page=tk.Frame(self.dashboard_canvas,bg=BG)
        self.dashboard_window=self.dashboard_canvas.create_window(0,0,anchor='nw',window=page)
        self.dashboard_canvas.bind('<Configure>',self._request_layout)
        page.grid_columnconfigure(0,weight=1);page.grid_rowconfigure(2,weight=1)
        hero=self.hero=card(page,pad=14,bg=NAVY,radius=8,border=NAVY);hero.grid(row=0,column=0,sticky='ew')
        hero.columnconfigure(0,minsize=220)
        hero.columnconfigure(2,weight=1)
        label(hero,'当前查看的会话',10,bold=True,color='#E7EDF0').grid(row=0,column=0,sticky='w')
        main=tk.Frame(hero,bg=NAVY);main.grid(row=1,column=0,sticky='ew',pady=(5,1))
        self.hero_kind=tk.Label(main,bg=NAVY)
        self.hero_kind.pack(side='left',padx=(0,9))
        words=tk.Frame(main,bg=NAVY);words.pack(side='left',fill='x',expand=True)
        self.target_name=tk.StringVar(self)
        self.target_info=tk.StringVar(self)
        label(words,textvariable=self.target_name,size=16,bold=True,color=PAPER).pack(anchor='w')
        label(words,textvariable=self.target_info,size=10,color='#E1E9EE').pack(anchor='w')
        actions=self.hero_actions=tk.Frame(hero,bg=NAVY);actions.grid(row=0,column=1,rowspan=2,sticky='w',padx=(10,4))
        self.actions={}
        for key,name,icon_name,callback in [('enable','开启此会话','play',lambda:self.control_selected('enable')),
                                           ('pause','暂停此会话','pause',lambda:self.control_selected('pause')),
                                           ('choose','选择会话','list',self.panel.choose_contact)]:
            action=button(actions,name,callback,primary=key=='enable',dark=True,icon_name=icon_name)
            action.pack(side='left',padx=(0,7));self.actions[key]=action
        art=self.hero_art=tk.Canvas(hero,width=204,height=68,bg=NAVY,highlightthickness=0,bd=0)
        art.grid(row=0,column=2,rowspan=2,sticky='e')
        self._hero_art(art)

        stats=self.stats=tk.Frame(page,bg=BG);stats.grid(row=1,column=0,sticky='ew',pady=12)
        self.metrics={};self.unknown_note=tk.StringVar(self)
        for col,(key,name,mark,accent,tint) in enumerate((('selected','已选会话','people','#446D88','#E4EEF5'),
                                                          ('pending','待发送','send','#99532E','#FBE9DE'),
                                                          ('verified','已确认','check','#317458','#E2F0E8'))):
            cell=card(stats,pad=10);cell.grid(row=0,column=col,sticky='nsew',padx=(0,9 if col<2 else 0))
            stats.columnconfigure(col,weight=1,uniform='metric')
            tk.Label(cell,image=glyph(cell,mark,accent,36,disc=tint),bg=PAPER).pack(side='left',padx=(3,11))
            text=tk.Frame(cell,bg=PAPER);text.pack(side='left')
            label(text,name,10,bold=True,color=MUTED).pack(anchor='w')
            self.metrics[key]=tk.StringVar(self,value='—')
            label(text,textvariable=self.metrics[key],size=21,bold=True).pack(anchor='w')
            if key=='verified':label(cell,textvariable=self.unknown_note,size=9,color='#99512E').pack(side='right')

        detail=self.dashboard_detail=tk.Frame(page,bg=BG);detail.grid(row=2,column=0,sticky='nsew')
        detail.grid_columnconfigure(0,weight=3,uniform='section');detail.grid_columnconfigure(1,weight=2,uniform='section')
        detail.grid_rowconfigure(0,weight=1)
        queue=self.queue_card=card(detail,pad=12);queue.grid(row=0,column=0,sticky='nsew',padx=(0,6))
        watch=self.watch_card=card(detail,pad=12);watch.grid(row=0,column=1,sticky='nsew',padx=(6,0))
        qhead=tk.Frame(queue,bg=PAPER);qhead.pack(fill='x')
        tk.Label(qhead,image=glyph(qhead,'send',INK,20),bg=PAPER).pack(side='left',padx=(0,7))
        self.queue_title=label(qhead,'发送队列',14,bold=True)
        self.queue_title.pack(side='left')
        self.queue_refresh=button(qhead,'刷新',self.refresh_pending,compact=True,icon_name='refresh')
        self.queue_refresh.pack(side='right')
        self.all_queues=button(qhead,'全部会话',lambda:self._choose_queue(None),compact=True)
        self.all_queues.pack(side='right',padx=(3,4))
        self.queue_filter=label(queue,'全部监听会话',9,color=MUTED)
        self.queue_filter.pack(fill='x',pady=(3,0))
        qbottom=tk.Frame(queue,bg=PAPER);qbottom.pack(side='bottom',fill='x',pady=(6,0))
        self.actions['recover']=button(qbottom,'处理主会话最新消息',self._recover,compact=True,icon_name='clock')
        self.actions['recover'].pack(side='left')
        self.actions['observe']=button(qbottom,'只观察',lambda:self.panel.control('observe'),compact=True)
        self.actions['observe'].pack(side='right')
        lists=self.queue_lists=tk.Frame(queue,bg=PAPER);lists.pack(fill='both',expand=True,pady=(6,0))
        lists.columnconfigure(0,weight=1)
        lists.rowconfigure(1,weight=1,uniform='feed');lists.rowconfigure(3,weight=2,uniform='feed')
        for row,caption,fill,fg in ((0,'待处理','#FBF0E8','#855439'),(2,'最近已确认','#EAF3ED','#37664D')):
            strip=tk.Frame(lists,bg=fill,padx=8,pady=3);strip.grid(row=row,column=0,sticky='ew')
            label(strip,caption,10,color=fg,bold=True).pack(anchor='w')
        text_area=tk.Frame(lists,bg=PAPER);text_area.grid(row=1,column=0,sticky='nsew')
        self.panel.preview=tk.Text(text_area,state='normal',width=1,height=1,wrap='word',relief='flat',
                                   bg=PAPER,fg=INK,selectbackground=PEACH,borderwidth=0,font=(FONT,11),
                                   padx=6,pady=6,spacing3=4,takefocus=True)
        self.panel.preview.pack(side='left',fill='both',expand=True)
        self.panel.preview.insert('1.0','等待读取本地队列…');self.panel.preview.configure(state='disabled')
        self.panel.preview.tag_configure('sender',foreground=INK,font=(FONT,11,'bold'))
        self.panel.preview.tag_configure('progress',foreground='#795339',font=(FONT,10))
        _scroll_when_needed(text_area,self.panel.preview)
        receipt_area=tk.Frame(lists,bg=PAPER);receipt_area.grid(row=3,column=0,sticky='nsew')
        self.recent_text=tk.Text(receipt_area,bg=PAPER,fg=INK,wrap='word',width=1,height=1,
                                 relief='flat',highlightthickness=0,font=(FONT,10),padx=6,pady=4,
                                 spacing1=3,spacing3=3,takefocus=True)
        self.recent_text.pack(fill='both',expand=True)
        _scroll_when_needed(receipt_area,self.recent_text)
        self.recent_text.tag_configure('stamp',foreground=GREEN,font=(FONT,10,'bold'))
        self.recent_text.tag_configure('note',foreground=MUTED,font=(FONT,9))
        self._show_recent([],'等待监听范围')
        whead=tk.Frame(watch,bg=PAPER);whead.pack(fill='x')
        tk.Label(whead,image=glyph(whead,'eye',INK,20),bg=PAPER).pack(side='left',padx=(0,7))
        label(whead,'监听范围',14,bold=True).pack(side='left')
        self.actions['manage']=button(whead,'管理监听',lambda:self.select('sessions'),compact=True,icon_name='settings')
        self.actions['manage'].pack(side='right')
        self.actions['calibrate']=button(whead,'校准此会话',self.calibrate_selected,compact=True)
        self.actions['calibrate'].pack(side='right',padx=(4,5))
        global_actions=tk.Frame(watch,bg=PAPER);global_actions.pack(fill='x',side='bottom',pady=(7,0))
        label(global_actions,'全部会话',9,color=MUTED).pack(side='left')
        for key,text,op in (('pause_all','暂停全部','pause'),('enable_all','开启全部','enable')):
            self.actions[key]=button(global_actions,text,lambda operation=op:self.panel.control(operation),compact=True)
            self.actions[key].pack(side='right',padx=(5,0))
        tk.Frame(watch,bg=LINE,height=1).pack(fill='x',pady=8)
        self.outcome_title=tk.StringVar(self);self.outcome_detail=tk.StringVar(self)
        result=card(watch,pad=8,bg='#F2F5F4',radius=8,border='#E2E8E4');result.pack(side='bottom',fill='x',pady=(5,0))
        label(result,textvariable=self.outcome_title,size=10,bold=True).pack(anchor='w')
        recent=label(result,textvariable=self.outcome_detail,size=9,color=MUTED,wraplength=270)
        recent.pack(fill='x',pady=(3,1))
        recent.bind('<Configure>',lambda e:recent.configure(wraplength=max(150,e.width-1)))
        watch_area=tk.Frame(watch,bg=PAPER);watch_area.pack(fill='both',expand=True)
        table_style=ttk.Style(self)
        table_style.configure('Watch.Treeview',background=PAPER,fieldbackground=PAPER,borderwidth=0,
                              relief='flat',rowheight=38,font=(FONT,10))
        table_style.layout('Watch.Treeview',[('Treeview.treearea',{'sticky':'nsew'})])
        self.watch_table=ttk.Treeview(watch_area,columns=('kind','state','control'),show='tree',height=2,selectmode='browse',style='Watch.Treeview')
        self.watch_table.bind('<<TreeviewSelect>>',self._watch_selected)
        self.watch_table.tag_configure('even',background='#F4F7F6')
        for key,width in [('#0',118),('kind',50),('state',62),('control',62)]:
            self.watch_table.column(key,width=width,minwidth=32,stretch=key=='#0')
        self.watch_table.pack(side='left',fill='both',expand=True)
        _scroll_when_needed(watch_area,self.watch_table)
        previous_scroll=self.watch_table.cget('yscrollcommand')
        def scrolled(first,last):
            if previous_scroll:self.tk.call(previous_scroll,first,last)
            self._request_watch_layout()
        self.watch_table.configure(yscrollcommand=scrolled)
        self.watch_table.bind('<Configure>',lambda event:self._request_watch_layout(),add='+')
        return viewport

    def _hero_art(self,canvas):
        path=Path(__file__).resolve().parent/'ui_assets'/'whale-banner.png'
        if path.is_file():
            try:
                with Image.open(path) as source:
                    art=ImageOps.fit(source.convert('RGB'),(204,68),method=Image.Resampling.LANCZOS)
                self.texture=ImageTk.PhotoImage(art,master=self)
                canvas.create_image(0,0,anchor='nw',image=self.texture)
            except OSError:pass

    def _recover(self):
        if self.queue_group and self.queue_group!=self.panel.settings.group:return
        self.panel.work(self.panel.client.reply_latest,self.panel.show_status)

    def _watch_selected(self,_event):
        selected=self.watch_table.selection()
        if selected and selected[0]!=self.queue_group:self._choose_queue(selected[0])

    def control_selected(self,operation,name=None):
        if self.busy or getattr(self.panel,'switch_busy',False):return
        mode=self.status.get('mode')
        if mode=='starting':return
        if operation=='enable' and mode in ('disconnected','stopped'):
            self.panel.connect_worker()
            return
        chosen=name or self.queue_group or self.panel.settings.group
        if chosen not in self.panel.settings.allowed_groups:return
        if chosen not in self.status.get('group_names',self.panel.settings.allowed_groups):return
        self.panel.control_conversation(chosen,operation)

    def calibrate_selected(self):
        if self.busy or getattr(self.panel,'switch_busy',False):return
        self.panel.calibrate(self.queue_group or self.panel.settings.group,auto_match=True)

    def _watch_action(self,name):
        row=next((r for r in self.status.get('groups',[]) if r.get('name')==name),{})
        self.control_selected('pause' if row.get('mode',self.status.get('mode'))=='auto' else 'enable',name)

    def _request_watch_layout(self):
        if self._watch_layout_job is None:self._watch_layout_job=self.after_idle(self._place_watch_controls)

    def _place_watch_controls(self):
        self._watch_layout_job=None
        wanted=max((control.winfo_reqheight()+6 for control in self.watch_controls.values()),default=38)
        wanted=max(38,wanted)
        if wanted!=self._watch_row_height:
            self._watch_row_height=wanted
            ttk.Style(self).configure('Watch.Treeview',rowheight=wanted)
            self._request_watch_layout()
            return
        for name,control in self.watch_controls.items():
            box=self.watch_table.bbox(name,'control') if self.watch_table.exists(name) else ()
            if box:
                x,y,width,height=box
                control.place(x=x+2,y=y+3,width=max(1,width-5),height=max(1,height-6))
                control.lift()
            else:control.place_forget()

    def _choose_queue(self,name):
        if name is not None and name not in self.panel.settings.allowed_groups:return
        self.queue_group=name
        self.update_text()
        if name is None:
            self.watch_table.selection_remove(*self.watch_table.selection())
        elif self.watch_table.exists(name):
            self.watch_table.selection_set(name)
            self.watch_table.see(name)
        if self.page=='assistant':self.refresh_pending()

    def focus_conversation(self,name):
        self._choose_queue(name)
        self.select('assistant')

    def _resize_shell(self,event):
        compact=event.width<(1040 if self._compact is None else 1052 if self._compact else 1028)
        scale=float(self.tk.call('tk','scaling'))/(96/72)
        rail=max(174 if compact else 198,max(b.winfo_reqwidth() for b in self.nav.values())+20)
        stacked=(event.width-rail-36)/scale<(635 if self._stacked is None else 649 if self._stacked else 621)
        if (compact,stacked)==(self._compact,self._stacked):return
        self._compact,self._stacked=compact,stacked
        rail=max(rail,36+34+self.brand_title.winfo_reqwidth())
        self.rail.configure(width=rail)
        self.main.configure(padx=13 if compact else 20)
        self.hero.configure(pady=8 if compact else 14)
        self.queue_lists.rowconfigure(3,weight=1 if compact else 2)
        self.hero.columnconfigure(0,minsize=162 if compact else 220)
        if compact or stacked:self.hero_art.grid_remove()
        else:self.hero_art.grid()
        if stacked:
            self.hero_actions.grid_configure(row=2,column=0,columnspan=2,rowspan=1,padx=0,pady=(5,0))
            self.queue_card.grid_configure(padx=0)
            self.watch_card.grid_configure(row=1,column=0,padx=0,pady=(9,0))
            self.dashboard_detail.columnconfigure(1,weight=0,uniform='')
            self.dashboard_detail.rowconfigure(1,weight=1)
        else:
            self.hero_actions.grid_configure(row=0,column=1,columnspan=1,rowspan=2,padx=(10,4),pady=0)
            self.queue_card.grid_configure(padx=(0,6))
            self.watch_card.grid_configure(row=0,column=1,padx=(6,0),pady=0)
            self.dashboard_detail.columnconfigure(1,weight=2,uniform='section')
            self.dashboard_detail.rowconfigure(1,weight=0)
        self._request_layout()
        self.update_text()

    def _request_layout(self,_event=None):
        if self._layout_job is None:self._layout_job=self.after(16,self._fit_dashboard)

    def _fit_dashboard(self):
        self._layout_job=None
        canvas=self.dashboard_canvas
        if self.page!='assistant' or canvas.winfo_width()<2:return
        queue=self.queue_card.winfo_reqheight()+(66 if self._stacked else 34)
        watch=self.watch_card.winfo_reqheight()+12
        detail=(queue+watch+9) if self._stacked else max(queue,watch)
        height=max(canvas.winfo_height(),self.hero.winfo_reqheight()+self.stats.winfo_reqheight()+24+detail)
        width=canvas.winfo_width()
        if getattr(self,'_dashboard_fit',None)==(width,height):return
        self._dashboard_fit=(width,height)
        canvas.itemconfigure(self.dashboard_window,width=width,height=height)
        canvas.configure(scrollregion=(0,0,width,height))

    def _dashboard_wheel(self,event):
        canvas=self.dashboard_canvas
        if self.page!='assistant' or not str(event.widget).startswith(str(canvas)):return
        lo,hi=canvas.yview()
        if lo<=0 and hi>=1:return
        widget=event.widget
        if widget.winfo_class() in ('Text','Treeview'):
            inner_lo,inner_hi=widget.yview()
            if (event.delta>0 and inner_lo>0) or (event.delta<0 and inner_hi<1):return
        canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
        return 'break'

    def _dashboard_focus(self,event):
        canvas=self.dashboard_canvas
        if self.page!='assistant' or not str(event.widget).startswith(str(canvas)+'.'):return
        low,high=canvas.yview()
        region=canvas.cget('scrollregion').split()
        if high<=low or high-low>=.9999 or len(region)!=4:return
        height=float(region[-1])
        offset=event.widget.winfo_rooty()-canvas.winfo_rooty()
        bottom=offset+event.widget.winfo_height()-canvas.winfo_height()
        if offset<0:canvas.yview_moveto(max(0,low+(offset-4)/height))
        elif bottom>0:canvas.yview_moveto(low+(bottom+4)/height)

    def _keyboard(self):
        window=self.winfo_toplevel()
        def shortcut(sequence,action):
            def handle(_):
                if self.busy or window.grab_current():return None
                return action()
            token=window.bind(sequence,handle,add='+')
            self._shortcuts.append((window,sequence,token))
        keys=list(PAGES)
        def go(key):
            self.select(key)
            self.nav[key].focus_set()
            return 'break'
        for index,key in enumerate(keys):
            shortcut(f'<Alt-KeyPress-{index+1}>',lambda k=key:go(k))
            nav=self.nav[key]
            for sequence,k in (('<Up>',keys[(index-1)%len(keys)]),('<Down>',keys[(index+1)%len(keys)]),
                               ('<Home>',keys[0]),('<End>',keys[-1])):
                nav.bind(sequence,lambda _,k=k:go(k) if not self.busy else 'break',add='+')
        shortcut('<F5>',self._refresh_page)
        shortcut('<Control-s>',self._save_page)
        token=window.bind('<MouseWheel>',self._dashboard_wheel,add='+')
        self._shortcuts.append((window,'<MouseWheel>',token))
        token=window.bind('<FocusIn>',self._dashboard_focus,add='+')
        self._shortcuts.append((window,'<FocusIn>',token))

    def _refresh_page(self):
        page=self.pages[self.page]
        if self.page=='assistant':self.refresh_pending()
        elif self.page=='contacts':page.refresh()
        elif hasattr(page,'on_show'):page.on_show()
        return 'break'

    def _save_page(self):
        page=self.pages[self.page]
        if self.page not in ('sessions','settings','persona','companion','stickers'):return None
        if self.page=='companion' and page.tab!='policy':return None
        page.save()
        return 'break'

    def select(self,key):
        queue=key=='pending'
        if queue:key='assistant'
        if key not in TITLES:raise ValueError('未知页面')
        if self.busy:return
        if key==self.page:
            if queue:self.refresh_pending()
            return
        old=self.pages.get(self.page)
        if old and hasattr(old,'can_leave') and not old.can_leave():return
        if old and key!=self.page:
            if hasattr(old,'on_hide'):old.on_hide()
            old.grid_remove()
            self._queue_request=None
            self.queue_refresh.configure(state='normal',text='刷新')
        if key not in self.pages:self.pages[key]=self._new_page(key)
        self.page=key;page=self.pages[key]
        page.grid(row=0,column=0,sticky='nsew')
        self.heading.set(TITLES[key]);self._paint_nav()
        if hasattr(page,'on_show'):page.on_show()
        if key=='assistant':self._request_layout()
        self.update_text()
        if key=='assistant' and self.status and self.status.get('running',True) and self.status.get('mode') not in ('stopped','disconnected'):
            self.refresh_pending()

    def _new_page(self,key):
        if key=='sessions' or key=='contacts':
            from .sessions_ui import SessionsPage,DirectoryPage
            cls=SessionsPage if key=='sessions' else DirectoryPage
        elif key=='companion':
            from .companion_ui import CompanionPage
            cls=CompanionPage
        elif key=='stickers':
            from .sticker_ui import StickerPage
            cls=StickerPage
        elif key=='settings':
            from .settings_ui import SettingsPage
            cls=SettingsPage
        else:
            from .persona_modules import modules_revision
            if modules_revision(self.panel.directory) is not None:
                from .persona_modules_ui import PersonaModulesPage
                cls=PersonaModulesPage
            else:
                from .persona_ui import PersonaPage
                cls=PersonaPage
        page=cls(self.body,self.panel)
        feedback=getattr(self.panel,'feedback',None)
        if feedback:
            for name in ('notice','feedback'):
                var=getattr(page,name,None)
                if isinstance(var,tk.StringVar):feedback.watch(var)
        return page

    def render(self):
        if self._queue_scope is not None and self._queue_scope!=self._current_scope():
            self._queue_request=None;self._queue_scope=None
            self._receipt_cache={};self._show_recent([],'正在读取当前会话回执…')
            self.panel.set_preview('正在读取当前队列…')
        page=self.pages.get(self.page)
        if page and hasattr(page,'refresh_scope'):page.refresh_scope()
        self.update_text()

    def set_switch_busy(self,busy):
        self.busy=bool(busy)
        for control in self.nav.values():control.configure(state='disabled' if busy else 'normal')
        for name in ('enable','choose','observe','manage','recover'):
            self.actions[name].configure(state='disabled' if busy else 'normal')
        self.update_text()

    def _current_scope(self):
        saved=self.panel.settings.allowed_groups
        names=tuple(self.status.get('group_names') or saved)
        chosen=getattr(self,'queue_group',None)
        return _queue_scope(self.panel),(chosen,) if chosen in saved else names

    def _show_recent(self,rows,note):
        display=(tuple(rows),note)
        if self._recent_display==display:return
        self._recent_display=display
        self.recent_text.configure(state='normal')
        self.recent_text.delete('1.0','end')
        for when,name,body in rows:
            self.recent_text.insert('end',time.strftime('%m-%d %H:%M',time.localtime(when))+'  '+name+'   ','stamp')
            self.recent_text.insert('end',body+'\n')
        if note or not rows:self.recent_text.insert('end',note or '暂无可核对的发送回执','note')
        self.recent_text.configure(state='disabled')

    def _decorate_queue(self):
        preview=self.panel.preview
        raw=preview.get('1.0','end-1c')
        if raw==self._styled_queue:return
        self._styled_queue=raw
        for index,line in enumerate(raw.splitlines(),1):
            if line.startswith('[') and '：' in line:
                preview.tag_add('sender',f'{index}.0',f'{index}.{line.index("：")+1}')
            elif index>1 and line and not line.startswith('回复：'):
                preview.tag_add('progress',f'{index}.0',f'{index}.end')

    def refresh_pending(self,*,background=False):
        if self.status.get('mode') in ('starting','disconnected','stopped'):
            return
        p=self.panel
        scope=self._current_scope()
        same_scope=getattr(self,'_queue_scope',None)==scope
        if same_scope and getattr(self,'_queue_request',None) is not None:return
        request=self._queue_request=object()
        self._queue_scope=scope
        def current():return self.page=='assistant' and self._queue_request is request and self._current_scope()==scope
        cfg=p.settings
        chosen=getattr(self,'queue_group',None)
        previous=self._receipt_cache if same_scope else {}
        def read():
            data=p.client.pending()
            if len(scope[1])==1 and chosen:
                data={**data,'tasks':[row for row in data.get('tasks',[]) if row.get('group',cfg.group)==scope[1][0]]}
            recent=receipt_previews(cfg,scope[1],previous,cancelled=lambda:not current())
            return data,recent
        def done(result):
            if current():
                self._queue_request=None
                data,(rows,note,self._receipt_cache)=result
                p.show_pending(data)
                self._decorate_queue()
                self._show_recent(rows,note)
                self.queue_refresh.configure(text='刷新',state='normal')
                if not background:p.notice.set('队列已刷新，共 '+str(len(data.get('tasks',[])))+' 条待发送')
        def failed(error):
            if current():
                self._queue_request=None
                p.set_preview('暂时无法读取队列：'+error)
                self._show_recent([],'暂时无法读取当前监听范围')
                self.queue_refresh.configure(text='刷新',state='normal')
                if not background:p.show_error(error)
        if not same_scope:
            p.set_preview('正在读取当前队列…')
            self._show_recent([],'正在读取当前会话回执…')
        if not background:self.queue_refresh.configure(text='刷新中',state='disabled')
        p.work(read,done,failed,mutates=False)

    def update_text(self):
        p=self.panel;cfg=p.settings;data=self.status
        mode=data.get('mode','')
        online=bool(data) and data.get('running',True) and mode not in ('starting','stopped','disconnected')
        names=cfg.allowed_groups
        live_names=tuple(data.get('group_names',names))
        name=self.queue_group if self.queue_group in names else cfg.group
        selected_row=next((row for row in data.get('groups',[]) if row.get('name')==name),{})
        applying=online and name not in live_names
        selected_mode='applying' if applying else selected_row.get('mode',mode)
        auto=online and selected_mode=='auto'
        enable_text={'starting':'正在启动后台','disconnected':'重连后台','stopped':'重连后台'}.get(mode)
        if applying:enable_text='正在应用校准'
        self.actions['enable'].configure(text=enable_text or ('本会话回复中' if auto else '开启此会话'),
                                         state='disabled' if auto or applying or self.busy or mode=='starting' else 'normal',
                                         bg='#30495E' if auto else PEACH,highlightbackground='#30495E' if auto else PEACH,
                                         disabledforeground='#D1DEE5' if auto else MUTED)
        self.actions['pause'].configure(text='暂停此会话',state='normal' if online and selected_mode in ('auto','observe') and not self.busy else 'disabled')
        self.actions['pause_all'].configure(state='normal' if online and any(row.get('mode') in ('auto','observe') for row in data.get('groups',[])) and not self.busy else 'disabled')
        self.actions['enable_all'].configure(state='normal' if online and not self.busy and not data.get('window_reload_pending') else 'disabled')
        self.actions['observe'].configure(state='normal' if online and not self.busy else 'disabled')
        self.actions['calibrate'].configure(state='disabled' if self.busy or mode=='starting' else 'normal')
        if not online:
            self._queue_request=None;self.queue_refresh.configure(state='disabled',text='刷新')
            if self._receipt_cache:
                self._receipt_cache={};self._show_recent([],'等待读取当前监听范围')
        state=MODES.get(mode,'正在连接' if not data else '读取状态中')
        self.header_status.configure(text='●  '+state,fg=GREEN if online and mode=='auto' else MUTED)
        self.local_status.configure(text='●  本地运行中' if online else '○  '+state)
        self.side_note.configure(text='窗口已校准' if online and data.get('calibrated') else '本地助手在线' if online else '本地助手')
        kind={'private':'私聊','group':'群聊'}.get(cfg.conversation_targets.get(name,{}).get('kind'),'会话')
        self.hero_kind.configure(image=glyph(self,'persona' if kind=='私聊' else 'people',PAPER,38,disc='#354F64'))
        limit=13 if self._compact else 20
        self.target_name.set(name if len(name)<=limit else name[:limit-1]+'…')
        policy={'natural':'自然参与','all':'逐条回复','mentions':'仅点名'}.get(selected_row.get('reply_mode',data.get('reply_mode',cfg.reply_mode)),'')
        split=data.get('split_replies_effective',cfg.split_replies and cfg.receive_mode=='database')
        target_info=' · '.join((kind,policy,'多气泡' if split else '单气泡',MODES.get(selected_mode,'未连接')))
        if online and selected_mode in ('auto','observe'):
            observed=selected_row.get('last_observed')
            if selected_row.get('receive_retrying'):
                target_info+='\n微信消息接收重试中'
            elif observed:
                target_info+='\n最近读取 '+time.strftime('%H:%M:%S',time.localtime(observed))
            elif selected_row.get('receive_mode')=='database':
                target_info+='\n正在建立监听基线'
        self.target_info.set(target_info)
        counts=data.get('counts') or {}
        self.metrics['selected'].set(str(len(names)))
        self.metrics['pending'].set(str(data.get('pending_count','—')) if online else '—')
        self.metrics['verified'].set(str(counts.get('verified',0)) if online else '—')
        self.unknown_note.set(f"{counts['unknown']} 条待核对" if online and counts.get('unknown') else '')
        live={row['name']:row for row in data.get('groups',[]) if row.get('name')}
        def watch_state(n):
            if not online:return '已选择'
            if n not in live_names:return '待应用'
            row=live.get(n,{})
            mode_for_room=row.get('mode',mode)
            window=row.get('window_state')
            if mode_for_room in ('auto','observe') and window in ('需重新绑定','待校准'):return window
            if mode_for_room=='auto':
                if row.get('receive_retrying'):return '接收重试'
                if row.get('receive_mode')=='database':return '监听中' if row.get('last_observed') else '正在接收'
                if window in ('已最小化','在后台','窗口不可见'):return window
            return {'auto':'回复中','observe':'观察中','paused':'暂停'}.get(mode_for_room,'已选择')
        rows=tuple((n,{'group':'群聊','private':'个人'}.get(cfg.conversation_targets.get(n,{}).get('kind'),'会话'),
                    watch_state(n)) for n in names)
        rule_key=tuple((n,live.get(n,{}).get('reply_mode'),repr(cfg.conversation_rules),cfg.reply_mode) for n in names)
        if rows!=self._watch_rows or rule_key!=getattr(self,'_watch_rule_key',None):
            self._watch_rows=rows
            self._watch_rule_key=rule_key
            wanted={r[0] for r in rows}
            for old in self.watch_table.get_children():
                if old not in wanted:
                    self.watch_table.delete(old)
                    control=self.watch_controls.pop(old,None)
                    if control:control.destroy()
            if self.queue_group not in wanted:self.queue_group=None
            for index,(title,kind,status) in enumerate(rows):
                pic=glyph(self,'persona' if kind=='个人' else 'people','#496D7D',18)
                from .reply_preferences import REPLY_MODES
                target=cfg.conversation_targets.get(title,{})
                rule=cfg.conversation_rules.get(target.get('account','')+'/'+target.get('id',''),{})
                reply_mode=live.get(title,{}).get('reply_mode',rule.get('reply_mode',cfg.reply_mode))
                options=dict(text=' '+title,image=pic,values=('仅点名' if reply_mode=='mentions' else REPLY_MODES[reply_mode],status,''),tags=('even',) if index%2==0 else ())
                if self.watch_table.exists(title):
                    self.watch_table.item(title,**options);self.watch_table.move(title,'',index)
                else:self.watch_table.insert('','end',iid=title,**options)
                if title not in self.watch_controls:
                    self.watch_controls[title]=button(self.watch_table,'',lambda n=title:self._watch_action(n),compact=True)
            self._request_watch_layout()
        for title,control in self.watch_controls.items():
            active=live.get(title,{}).get('mode',mode)=='auto'
            pending=online and title not in live_names
            control.configure(text='等待' if pending else '暂停' if active else '开启',
                              state='disabled' if self.busy or not online or pending else 'normal')
        self.queue_filter.configure(text=self.queue_group or '全部监听会话 · 点击监听对象查看对应队列')
        if self.page=='settings':self.pages['settings'].refresh_route()
        self.all_queues.configure(state='normal' if self.queue_group else 'disabled')
        self.actions['recover'].configure(state='disabled' if not online or self.busy or self.queue_group not in (None,cfg.group) else 'normal')
        selected=live.get(self.queue_group)
        title,detail=recent_result({**data,**selected,'last_result':selected.get('last_result')} if selected else data,p.reason.get())
        self.outcome_title.set(title);self.outcome_detail.set(detail)
        observed=data.get('last_observed')
        moment=time.strftime('%H:%M:%S',time.localtime(observed)) if observed else ''
        self.footer.set(p.notice.get() or '    |    '.join(part for part in (
            '运行状态 '+state,('已校准' if data.get('calibrated') else '待校准') if online else '',
            '已选会话 '+str(len(names)),('最近观察 '+moment) if moment else '') if part))

    def _destroyed(self,event):
        if event.widget is not self:return
        self._queue_request=None
        if self._layout_job is not None:self.after_cancel(self._layout_job)
        if self._nav_job is not None:self.after_cancel(self._nav_job)
        if self._watch_layout_job is not None:self.after_cancel(self._watch_layout_job)
        for var,token in self._traces:var.trace_remove('write',token)
        for window,sequence,token in self._shortcuts:window.unbind(sequence,token)
