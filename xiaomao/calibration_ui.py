"""Native calibration editor. The captured frame stays in this window's memory."""
from copy import deepcopy
import threading
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .companion_ui import BG, FONT, INK, LINE, MUTED, PAPER, button, label
from .config import TARGET_GROUP


def present_calibration(window):
    """An explicitly opened calibration dialog must stay above the WeChat capture target."""
    window.deiconify()
    window.attributes('-topmost',True)
    window.lift()
    window.focus_force()


class CalibrationFlow:
    """The picker is visible during discovery and remains usable after a capture error."""
    AUTO='识别窗口中的会话'

    def __init__(self,panel,target):
        self.panel=panel
        self.cancelled=threading.Event()
        self.lease=None
        self.editor=None
        self.windows=[]
        self.busy=False
        self.window=tk.Toplevel(panel.root)
        self.window.title('选择微信校准窗口')
        self.window.transient(panel.root)
        self.window.configure(bg=BG)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Escape>',lambda _:self.close())
        self.window.bind('<Destroy>',self._destroyed,add='+')
        self.status=tk.StringVar(self.window)
        box=tk.Frame(self.window,bg=PAPER,padx=18,pady=14)
        box.pack(fill='both',expand=True,padx=10,pady=10)
        label(box,'校准微信窗口',size=16,bold=True).pack(fill='x',pady=(0,8))
        label(box,'回复对象',bold=True).pack(anchor='w')
        names=list(panel.settings.saved_groups)
        if names==[TARGET_GROUP] and not panel.settings.conversation_targets:names=[]
        self.target=ttk.Combobox(box,state='readonly',values=[self.AUTO,*names],width=36)
        self.target.set(target if target in names else self.AUTO)
        self.target.pack(fill='x',pady=(3,10))
        label(box,'已打开的微信窗口',bold=True).pack(anchor='w')
        self.picker=ttk.Combobox(box,state='readonly',width=45)
        self.picker.pack(fill='x',pady=(3,8))
        label(box,textvariable=self.status,color=MUTED,wraplength=420).pack(fill='x',pady=(0,8))
        bar=tk.Frame(box,bg=PAPER);bar.pack(fill='x')
        self.reload=button(bar,'刷新',lambda:self.discover(auto=False),compact=True)
        self.reload.pack(side='left')
        self.launch=button(bar,'打开微信',self.open_wechat,compact=True)
        self.launch.pack(side='left',padx=5)
        self.next=button(bar,'唤起并校准',self.capture,primary=True)
        self.next.pack(side='right')
        self.window.update_idletasks()
        present_calibration(self.window)
        self.discover(auto=True)

    def _pending(self,busy):
        self.busy=busy
        for control in (self.reload,self.launch):
            control.configure(state='disabled' if busy else 'normal')
        self.next.configure(state='disabled' if busy or not self.windows else 'normal')
        self.target.configure(state='disabled' if busy else 'readonly')
        self.picker.configure(state='disabled' if busy or not self.windows else 'readonly')

    def fail(self,error):
        if self.cancelled.is_set():return
        self.release_lease()
        self._pending(False)
        self.status.set(error)
        present_calibration(self.window)

    def discover(self,auto):
        from .windows import list_calibration_windows
        self.windows=[]
        self.picker.set('');self.picker.configure(values=())
        self._pending(True);self.status.set('正在查找微信主窗口和独立聊天窗口…')
        def found(windows):
            if self.cancelled.is_set():return
            self.windows=windows
            titles=[('主窗口' if w['kind']=='main' else '独立聊天')+' · '+w['title']+
                    ('（已最小化）' if w['minimized'] else '') for w in windows]
            if len(set(titles))!=len(titles):
                titles=[f'{title} · 窗口 {w["hwnd"]}' for title,w in zip(titles,windows)]
            self.picker.configure(values=titles)
            if windows:
                existing=self.panel.settings.binding_for(self.target.get())
                index=next((i for i,w in enumerate(windows) if existing and w['hwnd']==existing.hwnd and w['pid']==existing.pid),0)
                self.picker.current(index)
            self._pending(False)
            self.status.set('选择要校准的微信窗口。其他会话保留现有绑定。' if windows else '没有可校准的聊天窗口。可打开微信，进入聊天后刷新。')
            if auto and len(windows)==1:self.capture()
        self.panel.work(list_calibration_windows,found,self.fail,mutates=False)

    def open_wechat(self):
        from .windows import open_wechat_for_calibration
        self._pending(True);self.status.set('正在打开本机微信…')
        self.panel.work(open_wechat_for_calibration,lambda _:self.discover(False) if not self.cancelled.is_set() else None,
                        self.fail,mutates=False)

    def capture(self):
        from .selection import preview_window
        from .windows import activate_calibration_window,CalibrationWindowLease,DesktopUnavailable
        i=self.picker.current()
        if self.cancelled.is_set() or self.busy or not 0<=i<len(self.windows):return
        choice=self.windows[i]
        target=self.target.get()
        if target==self.AUTO:target=None
        cfg=self.panel.settings
        try:self.lease=CalibrationWindowLease(choice['hwnd'])
        except (OSError,DesktopUnavailable) as error:
            self.fail(str(error));return
        self._pending(True);self.status.set('正在唤起所选窗口并读取当前尺寸…')
        self.window.attributes('-topmost',False)
        def grab():
            if self.cancelled.is_set():raise ValueError('校准已取消')
            activate_calibration_window(choice['hwnd'],choice['pid'])
            if self.cancelled.is_set():raise ValueError('校准已取消')
            return preview_window(cfg,choice,target)
        def ready(value):
            if self.cancelled.is_set():return
            self.window.withdraw()
            self.editor=CalibrationDialog(self.panel,*value,flow=self)
            present_calibration(self.editor.window)
        self.panel.work(grab,ready,self.fail,mutates=False)

    def release_lease(self):
        if self.lease:
            self.lease.close();self.lease=None

    def pick_again(self):
        if self.editor and self.editor.validating:return
        if self.editor:self.editor.window.destroy();self.editor=None
        self.release_lease()
        present_calibration(self.window)
        self.discover(False)

    def present(self):
        win=self.editor.window if self.editor else self.window
        present_calibration(win)

    def _destroyed(self,event):
        if event.widget is self.window:
            self.cancelled.set()
            self.release_lease()
            if getattr(self.panel,'calibration_flow',None) is self:self.panel.calibration_flow=None

    def close(self):
        self.cancelled.set()
        if self.editor and self.editor.alive():self.editor.window.destroy()
        self.window.destroy()


class CalibrationDialog:
    NAMES = {'header': '会话标题', 'messages': '消息区域', 'input': '输入区域', 'send': '发送按钮'}
    BLUE = '#246AE0'

    def __init__(self, panel, image, calibration,*,flow=None):
        self.panel, self.image = panel, image
        self.flow=flow
        self.cancelled=flow.cancelled if flow else threading.Event()
        self.calibration = deepcopy(calibration)
        self.scope = panel.settings.memory_scope
        self.open_group = panel.settings.group
        self.validating = False
        self.start = None
        self.undo_stack = []
        self.drag_snapshot = None
        self.preview = None
        self.base_image = None
        self.assignment = {key: idx for idx, key in enumerate(self.NAMES, 1)}
        self.render_job = None
        self.window = tk.Toplevel(panel.root)
        self.window.title('校准微信窗口')
        self.window.configure(bg=BG)
        self.window.transient(panel.root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda _: self.close())
        sw, sh = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        width, height = min(985, max(300, sw - 64)), min(770, max(360, sh - 80))
        x = max(0, min(sw - width, panel.root.winfo_rootx() + 55))
        y = max(0, min(sh - height - 40, panel.root.winfo_rooty() + 30))
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        self.window.minsize(min(width, 510), min(height, 430))
        self.chosen = tk.StringVar(self.window, 'header')
        self.confirmed = tk.BooleanVar(self.window, False)
        self.message = tk.StringVar(self.window, '按 1–4 选择区域，在截图上拖动重画。Ctrl+Z 可撤销。')
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        head = tk.Frame(self.window, bg=INK)
        head.grid(row=0, column=0, sticky='ew')
        label(head, '校准微信窗口', size=17, color=PAPER, bold=True).pack(side='left', padx=16, pady=8)
        tk.Button(head, text='×', font=(FONT, 16), command=self.close,
                  bg=INK, fg=PAPER, bd=0, highlightthickness=0, cursor='hand2',
                  activebackground=MUTED, activeforeground=PAPER).pack(side='right', padx=(2, 10))
        self.target_label = label(head, '当前目标　' + (calibration.target_group or calibration.header_label),
                                  color='#D9E4EA', size=10)
        self.target_label.pack(side='right', padx=(10, 0))
        toolbar = tk.Frame(self.window, bg=BG)
        toolbar.grid(row=2, column=0, sticky='ew', padx=18, pady=(2, 7))
        self.region_labels, self.pick_regions = {}, {}
        options = [f'矩形区域 {i}' for i in range(1, 5)]
        for col, (name, title) in enumerate(self.NAMES.items()):
            toolbar.columnconfigure(col, weight=1, uniform='region')
            title_label = label(toolbar, title, size=11, bold=True, cursor='hand2')
            title_label.grid(row=0, column=col, sticky='w', padx=2, pady=(0, 3))
            title_label.bind('<Button-1>', lambda _, key=name: self.choose(key))
            self.region_labels[name] = title_label
            picker = ttk.Combobox(toolbar, values=options, width=8, state='readonly', font=(FONT, 10))
            picker.current(col)
            picker.grid(row=1, column=col, sticky='ew', padx=(0, 8 if col < 3 else 0), ipady=3)
            picker.bind('<ButtonPress-1>', lambda _, key=name: self.choose(key), add='+')
            picker.bind('<FocusIn>', lambda _, key=name: self.choose(key), add='+')
            picker.bind('<<ComboboxSelected>>', lambda _, key=name: self.assign(key))
            self.pick_regions[name] = picker
        self.canvas = tk.Canvas(self.window, bg='#E4E8E8', bd=0, highlightthickness=1,
                                highlightbackground=LINE, cursor='crosshair', height=1, width=1)
        self.canvas.grid(row=1, column=0, sticky='nsew', padx=18, pady=(12, 8))
        self.canvas.bind('<Configure>', self.resize)
        self.canvas.bind('<ButtonPress-1>', self.begin)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.end)
        self.window.bind('<Control-z>',self.undo)
        for number, key in enumerate(self.NAMES,1):
            self.window.bind(f'<KeyPress-{number}>',lambda e,k=key:self._choose_shortcut(e,k))
        footer = tk.Frame(self.window, bg=PAPER)
        footer.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 12))
        self.check = tk.Checkbutton(footer, text='已确认目标会话唯一、聊天位于底部，并且输入框没有草稿',
                              variable=self.confirmed, bg=PAPER, fg=INK, selectcolor=BG,
                              activebackground=PAPER, activeforeground=INK, anchor='w',
                              justify='left', font=(FONT, 10), padx=4, pady=5, bd=0)
        self.check.pack(fill='x')
        self.help_label = label(footer, textvariable=self.message, color=MUTED)
        self.help_label.pack(fill='x', padx=8, pady=(0, 4))
        actions = tk.Frame(footer, bg=PAPER)
        actions.pack(fill='x', padx=8, pady=(1, 6))
        self.undo_button=button(actions,'撤销调整',self.undo,icon_name='back',compact=True)
        self.undo_button.pack(side='left')
        if flow:button(actions,'重新选窗口',flow.pick_again,compact=True).pack(side='left',padx=6)
        self.undo_button.configure(state='disabled')
        self.save_button = button(actions, '保存校准', self.save, primary=True,icon_name='save')
        self.save_button.pack(side='right')
        button(actions, '取消', self.close).pack(side='right', padx=6)
        self.choose('header')

    def _choose_shortcut(self,event,key):
        if isinstance(event.widget,ttk.Combobox):return
        self.choose(key)
        return 'break'

    def _snapshot(self):
        return deepcopy(self.calibration.regions),self.assignment.copy()

    def _remember(self,snapshot):
        self.undo_stack=(self.undo_stack+[snapshot])[-16:]
        self.undo_button.configure(state='normal')

    def undo(self,_event=None):
        if not self.validating and self.undo_stack:
            self.calibration.regions,self.assignment=self.undo_stack.pop()
            self.start=self.drag_snapshot=None
            for key,number in self.assignment.items():
                self.pick_regions[key].current(number-1)
            self.confirmed.set(False)
            self.undo_button.configure(state='normal' if self.undo_stack else 'disabled')
            self.message.set('已撤销上次调整，请核对选区后保存。')
            if self.preview:self.draw()
        return 'break'

    def resize(self, event):
        if self.render_job is not None:
            self.window.after_cancel(self.render_job)
        self.render_job = self.window.after_idle(self._render)
        self.help_label.configure(wraplength=max(120, event.width - 18))
        self.check.configure(wraplength=max(120, event.width - 40))
        self.target_label.configure(wraplength=max(100, event.width // 2))

    def _render(self):
        self.render_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        scale = min((width-12) / self.image.width, (height-12) / self.image.height, 1)
        if scale <= 0:
            return
        size = (max(1, round(self.image.width * scale)), max(1, round(self.image.height * scale)))
        if self.preview is None or size != (self.preview.width(), self.preview.height()):
            self.base_image = self.image.convert('RGB').resize(size, Image.Resampling.LANCZOS)
            self.preview = ImageTk.PhotoImage(self.base_image, master=self.window)
        self.origin = ((width - size[0]) // 2, (height - size[1]) // 2)
        self.draw()

    def choose(self, name):
        if self.validating:
            return
        self.chosen.set(name)
        for key, item in self.region_labels.items():
            item.configure(fg=self.BLUE if key == name else INK)
        if self.preview:
            self.draw()

    def assign(self, name):
        if self.validating:
            return
        number = self.pick_regions[name].current()+1
        other = next((key for key, value in self.assignment.items() if value == number), name)
        if other != name:
            self._remember(self._snapshot())
            self.assignment[name], self.assignment[other] = self.assignment[other], self.assignment[name]
            regions = self.calibration.regions
            regions[name], regions[other] = regions[other], regions[name]
            self.pick_regions[other].current(self.assignment[other]-1)
            self.confirmed.set(False)
        self.choose(name)

    def draw(self):
        self.canvas.delete('all')
        left, top = self.origin
        width, height = self.preview.width(), self.preview.height()
        surface = self.base_image.copy()
        pen = ImageDraw.Draw(surface, 'RGBA')
        for key, ratios in self.calibration.regions.items():
            if key not in self.NAMES:
                continue
            x1, y1, x2, y2 = ratios
            pen.rectangle((x1*width, y1*height, x2*width, y2*height),
                          fill=(60, 132, 241, 35 if key == self.chosen.get() else 22))
        self.preview.paste(surface)
        self.canvas.create_image(left, top, image=self.preview, anchor='nw')
        for key, ratios in self.calibration.regions.items():
            if key not in self.NAMES:
                continue
            x1, y1, x2, y2 = ratios
            coords = (left + x1*width, top + y1*height, left + x2*width, top + y2*height)
            selected=key==self.chosen.get()
            stroke=self.BLUE if selected else '#5E768D'
            self.canvas.create_rectangle(*coords,outline=stroke,dash=() if selected else (5,3),
                                         width=2 if selected else 1)
            self.canvas.create_text(coords[2]-5, coords[1]+2, anchor='ne',
                                    text=f'{self.assignment[key]} · {self.NAMES[key]}' if selected else str(self.assignment[key]), fill=stroke,
                                    font=(FONT, 8, 'bold'))
            for x in (coords[0], coords[2]) if selected else ():
                for y in (coords[1], coords[3]):
                    self.canvas.create_rectangle(x-3, y-3, x+3, y+3, outline=self.BLUE, fill=PAPER)

    def point(self, event, *, clamp):
        if not self.preview:
            return None
        x, y = event.x - self.origin[0], event.y - self.origin[1]
        width, height = self.preview.width(), self.preview.height()
        if not clamp and (x < 0 or y < 0 or x > width or y > height):
            return None
        return (min(width, max(0, x))/width, min(height, max(0, y))/height)

    def begin(self, event):
        if not self.validating:
            self.start = self.point(event, clamp=False)
            self.drag_snapshot=self._snapshot() if self.start is not None else None

    def drag(self, event):
        if self.validating or self.start is None:
            return
        pos = self.point(event, clamp=True)
        x1, x2 = sorted([self.start[0], pos[0]])
        y1, y2 = sorted([self.start[1], pos[1]])
        if (x2-x1)*self.preview.width() <= 4 or (y2-y1)*self.preview.height() <= 4:
            return
        if self.drag_snapshot is not None:
            self._remember(self.drag_snapshot)
            self.drag_snapshot=None
        self.calibration.regions[self.chosen.get()] = [x1, y1, x2, y2]
        self.confirmed.set(False)
        self.draw()

    def end(self, event):
        self.drag(event)
        self.start = None
        self.drag_snapshot=None

    def alive(self):
        return bool(self.window.winfo_exists())

    def close(self):
        self.cancelled.set()
        if self.render_job is not None:
            self.window.after_cancel(self.render_job)
            self.render_job = None
        if self.flow:self.flow.close()
        else:self.window.destroy()

    def save(self):
        if self.validating:
            return
        if not self.confirmed.get():
            self.message.set('请先确认目标、聊天底部和空白输入框。')
            return
        frozen = deepcopy(self.calibration)
        frozen.bottom_confirmed = True
        frozen.manual_regions = True
        try:
            frozen.validate()
        except ValueError as error:
            self.message.set(str(error))
            return
        self.validating = True
        self.undo_button.configure(state='disabled')
        self.save_button.configure(state='disabled')
        self.check.configure(state='disabled')
        for picker in self.pick_regions.values():
            picker.configure(state='disabled')
        self.message.set('正在本机核对目标、聊天底部和草稿…')

        def failed(error):
            if self.alive():
                self.validating = False
                self.undo_button.configure(state='normal' if self.undo_stack else 'disabled')
                self.check.configure(state='normal')
                self.save_button.configure(state='normal')
                for picker in self.pick_regions.values():
                    picker.configure(state='readonly')
                self.message.set(error)

        self.panel.save_calibration(frozen,self.cancelled,self.close,failed)
