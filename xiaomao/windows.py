"""独立产品的 Windows 普通界面适配；不访问微信内部内存或数据库。"""
import ctypes
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import threading
import time
import unicodedata
import re
import subprocess
import weakref
from .config import Calibration,group_name
from .capture import capture_window,CaptureError


class DesktopUnavailable(RuntimeError):
    pass


class DesktopTemporarilyHidden(DesktopUnavailable):
    """窗口仍有绑定，但当前不可见；只允许在尚未输入的观察阶段等待。"""


class DesktopInputNotStarted(DesktopTemporarilyHidden):
    """Text preflight failed before any edit; the same task may safely retry."""


class StickerClipboardBlocked(DesktopInputNotStarted):
    """The sticker adapter has not issued a paste or edited the input."""
    def __init__(self,reason):
        super().__init__('表情尚未输入，正在等待剪贴板')
        self.reason=reason


class NavigationActivity:
    """仅识别程序自己的选群点击，避免把它误计为新的人工活动。"""
    def __init__(self):
        self.generation=None
        self.credit=0
        self.at=0

    def note(self,generation,idle_credit):
        self.generation,self.credit,self.at=generation,idle_credit,time.monotonic()

    def idle(self,generation,native_idle):
        return self.credit+time.monotonic()-self.at if generation==self.generation else native_idle


def unicode_units(text: str) -> list[int]:
    if not text or any(unicodedata.category(c).startswith("C") for c in text):
        raise ValueError("输入文字含控制字符或为空")
    encoded = text.encode("utf-16-le")
    return [int.from_bytes(encoded[i:i+2], "little") for i in range(0, len(encoded), 2)]


if os.name == "nt":
    U = ctypes.WinDLL("user32", use_last_error=True)
    K = ctypes.WinDLL("kernel32", use_last_error=True)
    U.GetForegroundWindow.restype = W.HWND
    U.GetAncestor.argtypes = [W.HWND, W.UINT]
    U.GetAncestor.restype = W.HWND
    U.GetLastActivePopup.argtypes=[W.HWND]
    U.GetLastActivePopup.restype=W.HWND
    U.WindowFromPoint.argtypes = [W.POINT]
    U.WindowFromPoint.restype = W.HWND
    U.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    U.IsWindow.argtypes = [W.HWND]
    U.IsWindowVisible.argtypes = [W.HWND]
    U.IsIconic.argtypes = [W.HWND]
    U.ShowWindowAsync.argtypes = [W.HWND, ctypes.c_int]
    U.SetForegroundWindow.argtypes = [W.HWND]
    U.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    class GUITHREADINFO(ctypes.Structure):
        _fields_=[('size',W.DWORD),('flags',W.DWORD),('active',W.HWND),('focus',W.HWND),('capture',W.HWND),('menu',W.HWND),('move',W.HWND),('caret',W.HWND),('rect',W.RECT)]
    U.GetGUIThreadInfo.argtypes=[W.DWORD,ctypes.POINTER(GUITHREADINFO)]
    if hasattr(U,'GetDpiForWindow'):
        U.GetDpiForWindow.argtypes=[W.HWND]
        U.GetDpiForWindow.restype=W.UINT
    U.OpenInputDesktop.argtypes=[W.DWORD,W.BOOL,W.DWORD];U.OpenInputDesktop.restype=W.HANDLE
    U.GetUserObjectInformationW.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
    U.CloseDesktop.argtypes=[W.HANDLE]
    K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    K.OpenProcess.restype = W.HANDLE
    K.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    K.CloseHandle.argtypes = [W.HANDLE]
    K.GetTickCount64.restype = ctypes.c_ulonglong
    K.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
    K.CreateMutexW.restype = W.HANDLE
    K.OpenMutexW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    K.OpenMutexW.restype = W.HANDLE
    try:
        U.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        U.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD), ("time", W.DWORD), ("extra", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("data", W.DWORD), ("flags", W.DWORD), ("time", W.DWORD), ("extra", ctypes.c_size_t)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("keyboard", KEYBDINPUT), ("mouse", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", W.DWORD), ("value", INPUTUNION)]

    U.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    U.SendInput.restype = W.UINT


def _process_window_details(hwnd: int) -> dict:
    if os.name != "nt" or not U.IsWindow(hwnd):
        raise DesktopUnavailable("微信窗口不存在")
    pid = W.DWORD()
    U.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = K.OpenProcess(0x1000, False, pid.value)  # 仅查询程序路径。
    if not handle:
        raise DesktopUnavailable("无法核实微信窗口所属程序")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = W.DWORD(len(buffer))
        if not K.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise DesktopUnavailable("无法核实微信程序路径")
        if os.path.basename(buffer.value).lower() != "weixin.exe":
            raise DesktopUnavailable("当前窗口不是微信 Weixin.exe")
    finally:
        K.CloseHandle(handle)
    rect = W.RECT()
    if not U.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise DesktopUnavailable("无法获取微信窗口范围")
    return {"hwnd": int(hwnd), "pid": pid.value, "width": rect.right-rect.left, "height": rect.bottom-rect.top, "rect": (rect.left, rect.top, rect.right, rect.bottom)}


def foreground_details() -> dict:
    if os.name != "nt":
        raise DesktopUnavailable("仅支持 Windows")
    return window_details(U.GetForegroundWindow())


_MAIN_TITLES = {'微信','wechat','weixin'}
_UTILITY_TITLES = {
    '登录','微信登录','log in','login','设置','偏好设置','settings','关于微信',
    '图片','图片查看','图片查看器','图片和视频','视频','文件','文件管理','文件预览',
    '聊天文件','聊天记录','合并的聊天记录','收藏','朋友圈','通讯录管理','搜索',
    'image viewer','file preview','chat history','moments','favorites',
}
_ATTACHMENT_TITLE = re.compile(
    r'\.(?:png|jpe?g|webp|bmp|gif|tiff?|heic|svg|mp[34]|mov|avi|mkv|wmv|webm|pdf|'
    r'docx?|xlsx?|pptx?|wps|txt|csv|rtf|zip|rar|7z|exe)(?:\s*[-–—|].*)?$', re.I)


def _calibration_candidate(hwnd,info):
    """Inspect only public HWND attributes. Qt uses the same native class for both chat shells."""
    import win32gui
    cls=win32gui.GetClassName(hwnd)
    if not re.fullmatch(r'Qt\d+QWindowIcon',cls):
        return None
    if win32gui.GetWindow(hwnd,4) or win32gui.GetWindowLong(hwnd,-20)&(0x80|0x08000000):
        return None  # owned popup, WS_EX_TOOLWINDOW or WS_EX_NOACTIVATE
    minimized=bool(win32gui.IsIconic(hwnd))
    if not minimized and not win32gui.IsWindowVisible(hwnd):
        return None
    title=win32gui.GetWindowText(hwnd).strip()
    if not title or title.casefold() in _UTILITY_TITLES or _ATTACHMENT_TITLE.search(title):
        return None
    # WeChat's independent, resizable chat frames have a thick frame and a
    # minimize box. Fixed login dialogs, tooltips and attachment popups do not.
    style=win32gui.GetWindowLong(hwnd,-16)
    if style & 0x00060000 != 0x00060000:
        return None
    if minimized:
        l,t,r,b=win32gui.GetWindowPlacement(hwnd)[4]
        info=dict(info,width=r-l,height=b-t,rect=(l,t,r,b))
    kind='main' if title.casefold() in _MAIN_TITLES else 'detached'
    min_width,min_height=(600,450) if kind=='main' else (360,360)
    if info['width']<min_width or info['height']<min_height:
        return None
    dpi=U.GetDpiForWindow(hwnd) if hasattr(U,'GetDpiForWindow') else 96
    return dict(info,title=title,kind=kind,minimized=minimized,scale=(dpi or 96)/96)


def window_details(hwnd: int) -> dict:
    """Return native window identity, including chat kind when this is an eligible shell."""
    info=_process_window_details(hwnd)
    import win32gui
    try:candidate=_calibration_candidate(hwnd,info)
    except win32gui.error:raise DesktopUnavailable('微信窗口已关闭，请重新选择') from None
    return candidate or dict(info,kind=None)


def list_calibration_windows():
    """List eligible windows in z order, without restoring or activating any of them."""
    if os.name!='nt':raise DesktopUnavailable('仅支持 Windows')
    import win32gui
    if not input_desktop_available():raise DesktopUnavailable('桌面已锁定，请解锁后校准')
    rows=[]
    def visit(hwnd,_):
        try:
            if not re.fullmatch(r'Qt\d+QWindowIcon',win32gui.GetClassName(hwnd)):return
            candidate=window_details(hwnd)
        except (win32gui.error,DesktopUnavailable):return
        if candidate.get('kind'):rows.append(candidate)
    win32gui.EnumWindows(visit,None)
    return rows


def calibration_window_info(hwnd,pid):
    """Recheck the specific selected window at each manual operation."""
    if os.name!='nt':raise DesktopUnavailable('仅支持 Windows')
    import win32gui
    if not input_desktop_available():raise DesktopUnavailable('桌面已锁定，请解锁后校准')
    try:info=window_details(hwnd)
    except win32gui.error:info=None
    if not info or not info.get('kind') or info['pid']!=pid:
        raise DesktopUnavailable('所选微信窗口已关闭或身份改变，请刷新窗口列表')
    return info


def activate_calibration_window(hwnd,pid):
    """User-requested calibration only. Never synthesize keys to bypass focus policy."""
    original=calibration_window_info(hwnd,pid)
    def recheck():
        info=calibration_window_info(hwnd,pid)
        if (info['kind'],info['title'])!=(original['kind'],original['title']):
            raise DesktopUnavailable('所选微信窗口已改变，请重新选择')
        return info
    if original['minimized']:
        if not U.ShowWindowAsync(hwnd,9):  # SW_RESTORE is requested only by this manual API.
            raise DesktopUnavailable('微信窗口未能恢复，请手动还原后重试')
        restore_error=None
        for _ in range(20):
            try:
                if not recheck()['minimized']:break
                restore_error=None
            except DesktopUnavailable as error:
                # IsIconic may clear before the normal-sized Qt surface exists.
                # Do not request foreground until the selected identity matches again.
                restore_error=error
            time.sleep(.05)
        else:
            if restore_error:raise restore_error
            raise DesktopUnavailable('微信窗口仍处于最小化状态，请手动还原后重试')
    if U.GetForegroundWindow()!=hwnd:
        U.SetForegroundWindow(hwnd)
    for _ in range(15):
        info=recheck()
        if not info['minimized'] and U.GetForegroundWindow()==hwnd:return info
        time.sleep(.04)
    raise DesktopUnavailable('Windows 未允许微信切到前台，请手动点选该微信窗口后重试')


def capture_calibration_window(hwnd,pid):
    """Raw window-only snapshot without an existing calibration or a focus change."""
    info=calibration_window_info(hwnd,pid)
    if info['minimized']:raise DesktopUnavailable('所选微信窗口已最小化，请先唤起后截图')
    try:image=capture_window(hwnd,info['width'],info['height'])
    except (CaptureError,OSError):
        raise DesktopUnavailable('微信窗口暂未绘制完整画面，请显示聊天后重新截图') from None
    fresh=calibration_window_info(hwnd,pid)
    if image.size!=(info['width'],info['height']) or any(
            fresh[key]!=info[key] for key in ('kind','title','rect','width','height','minimized','scale')):
        raise DesktopUnavailable('截图期间微信窗口发生变化，请重新截图')
    return image,fresh


def _wechat_executable_paths():
    import psutil
    import winreg
    for process in psutil.process_iter(['name','exe']):
        if (process.info['name'] or '').casefold()=='weixin.exe' and process.info['exe']:
            yield Path(process.info['exe'])
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            for key,value in ((r'Software\Tencent\Weixin','InstallPath'),
                              (r'Software\Microsoft\Windows\CurrentVersion\App Paths\Weixin.exe','')):
                try:
                    with winreg.OpenKey(hive,key,0,winreg.KEY_READ|view) as handle:
                        raw,_=winreg.QueryValueEx(handle,value)
                    path=Path(os.path.expandvars(raw.strip('"')))
                except (OSError,AttributeError):continue
                yield path/'Weixin.exe' if value else path


def _signed_wechat_executable(path):
    # Pass the file path as data. The Windows PowerShell script and executable
    # are fixed, regardless of registry values or the install location.
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    script=(
        "$sig=Get-AuthenticodeSignature -LiteralPath $env:XIAOMAO_CALIBRATION_EXE; "
        "if ($sig.Status -ne 'Valid' -or !$sig.SignerCertificate) { exit 1 }; "
        "$name=$sig.SignerCertificate.GetNameInfo('SimpleName',$false); "
        "if ($name -ne 'Tencent Technology (Shenzhen) Company Limited') { exit 1 }; exit 0"
    )
    try:
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',script],
                              env={**os.environ,'PSMODULEPATH':str(powershell.parent/'Modules'),
                                   'XIAOMAO_CALIBRATION_EXE':str(path)},
                              stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                              creationflags=subprocess.CREATE_NO_WINDOW,timeout=8)
        return result.returncode==0
    except (OSError,subprocess.TimeoutExpired):return False


def open_wechat_for_calibration():
    """Open the installed signed WeChat client, leaving login to the operator."""
    if os.name!='nt':raise DesktopUnavailable('仅支持 Windows')
    seen=set()
    for candidate in _wechat_executable_paths():
        if not candidate.is_absolute():continue
        try:path=candidate.resolve(strict=True)
        except (OSError,RuntimeError):continue
        if path in seen or path.name.casefold()!='weixin.exe' or not path.is_file():continue
        seen.add(path)
        if not _signed_wechat_executable(path):continue
        try:subprocess.Popen([str(path)],cwd=str(path.parent),close_fds=True)
        except OSError:raise DesktopUnavailable('微信客户端未能启动，请从开始菜单打开微信并登录') from None
        return
    raise DesktopUnavailable('未找到可核实腾讯签名的微信客户端，请从开始菜单打开微信，登录后刷新')


def find_wechat_window(pid):
    """Read-only recovery of one main window in the already-bound process.

    Login dialogs, image viewers, other accounts/processes and ambiguous
    candidates are not eligible. Never activate a window during discovery.
    """
    candidates=[w for w in list_calibration_windows() if w['kind']=='main' and w['pid']==pid]
    if len(candidates)!=1:raise DesktopUnavailable('未能唯一找到原账号的微信主窗口，请打开已登录的微信后重试')
    return candidates[0]


def input_desktop_available() -> bool:
    handle=U.OpenInputDesktop(0,False,1)
    if not handle:return False
    try:
        name=ctypes.create_unicode_buffer(256);needed=W.DWORD()
        return bool(U.GetUserObjectInformationW(handle,2,ctypes.byref(name),ctypes.sizeof(name),ctypes.byref(needed)) and name.value.casefold()=='default')
    finally:U.CloseDesktop(handle)


class WindowsDesktop:
    def __init__(self, calibration: Calibration | None, cancelled: threading.Event | None = None, navigation=None):
        self.calibration = calibration
        self.cancelled = cancelled or threading.Event()
        self._input_cookie = None
        self._visible_capture = None
        self.typed_without_clipboard = False
        self.last_editor_proof = ''
        self._sticker_snapshot_blocked = False
        self._sidebar_ocr=None
        self._last_wake=-float('inf')
        self._return_window=None
        self._wake_generation=None
        self._sent_generation=None
        self.navigation=navigation or NavigationActivity()

    def _bound_details(self):
        binding=self.calibration
        if getattr(binding,'window_kind','main')!='detached':
            info=window_details(binding.hwnd)
            if info.get('kind','main')!='main':
                raise DesktopUnavailable('绑定的微信主窗口已改变，请重新校准')
            return info
        info=calibration_window_info(binding.hwnd,binding.pid)
        from .vision import detached_title_matches
        if info['kind']!='detached' or not detached_title_matches(info['title'],binding):
            raise DesktopUnavailable('绑定的独立聊天窗口标题已改变，请重新校准')
        return info

    def check(self) -> dict:
        binding = self.calibration
        if not binding:
            raise DesktopUnavailable("尚未校准微信窗口")
        if self.cancelled.is_set():
            raise DesktopUnavailable("助手已暂停")
        _check_calibration_lease(binding.hwnd)
        info = self._bound_details()
        if U.IsIconic(binding.hwnd) or not U.IsWindowVisible(binding.hwnd) or U.GetForegroundWindow() != binding.hwnd:
            raise DesktopTemporarilyHidden("微信目标群需要保持前台可见")
        if info["pid"] != binding.pid or info["width"] != binding.width or info["height"] != binding.height:
            raise DesktopUnavailable("微信窗口已重建或尺寸改变，请重新校准")
        left, top, right, bottom = info["rect"]
        screen_left, screen_top = U.GetSystemMetrics(76), U.GetSystemMetrics(77)
        if left < screen_left or top < screen_top or right > screen_left + U.GetSystemMetrics(78) or bottom > screen_top + U.GetSystemMetrics(79):
            raise DesktopUnavailable("微信窗口部分超出桌面")
        # 检查顶层遮挡，菜单和其他同进程弹窗也不能借用主窗口的绑定。
        for name in ("header", "messages", "input", "send"):
            x1, y1, x2, y2 = binding.rect(name)
            for y in range(y1+2, y2, 32):
                for x in range(x1+2, x2, 32):
                    covering = U.WindowFromPoint(W.POINT(left+x, top+y))
                    if U.GetAncestor(covering, 2) != binding.hwnd:
                        raise DesktopTemporarilyHidden("微信目标区域被其他窗口遮挡")
        return info

    def capture(self):
        self._visible_capture=None
        info=self._background_binding()
        foreground=self.is_foreground()
        try:
            if foreground:
                from PIL import ImageGrab
                info=self.check()
                image=ImageGrab.grab(bbox=info['rect'],all_screens=True)
                captured_at=time.monotonic()
                if self.check()['rect']!=info['rect']:
                    raise DesktopTemporarilyHidden('截图时微信窗口移动，等待重新核验')
            else:image=capture_window(self.calibration.hwnd,info['width'],info['height'])
        except (CaptureError,OSError):raise DesktopTemporarilyHidden('微信画面暂时无法读取，等待重试') from None
        self._background_binding()
        from .layout import adapt_calibration
        self.calibration=adapt_calibration(image,self.calibration,self._dpi_scale())
        if foreground:
            self._visible_capture=(weakref.ref(image),self.calibration,info['rect'],captured_at)
        return image

    def confirm_empty_editor(self,visible,vision):
        """Return the fresh native image only for a corroborated desktop halo."""
        from PIL import ImageGrab
        from .vision import possible_editor_halo,editor_halo_verdict
        capture,self._visible_capture=self._visible_capture,None
        self.last_editor_proof='stale_capture'
        if not capture:return None
        reference,binding,rect,captured_at=capture
        if (reference() is not visible or binding is not self.calibration or
                not 0<=time.monotonic()-captured_at<=8):return None
        try:
            self.last_editor_proof='window_changed'
            if not self.is_foreground() or not input_desktop_available() or self.check()['rect']!=rect:return None
            generation=self.input_generation()
            scale=self._dpi_scale()
            self.last_editor_proof='not_clipped_halo'
            if not possible_editor_halo(visible,binding,scale):return None
            observed=vision.read_controls(visible,binding)
            self.last_editor_proof='visible_control_mismatch'
            if not observed.safe or observed.draft or observed.draft_empty:return None
            self.last_editor_proof='focus_or_render_changed'
            if self._input_cookie is not None:self.assert_input_focus()
            painted=capture_window(binding.hwnd,binding.width,binding.height)
            native=vision.read_controls(painted,binding)
            self.last_editor_proof='native_control_mismatch'
            if not native.safe or native.group!=observed.group or not native.draft_empty:return None
            # PrintWindow can lag the visible desktop. Corroborate the same
            # native pixels with a new foreground grab after rendering/OCR.
            self.last_editor_proof='window_or_input_changed'
            if self.check()['rect']!=rect or generation!=self.input_generation():return None
            closing=ImageGrab.grab(bbox=rect,all_screens=True)
            verdict=editor_halo_verdict(visible,painted,binding,scale,closing=closing)
            if verdict=='motion_unconfirmed':
                # Two desktop exposures may coincide in the GIF loop. Sample
                # one further bounded exposure without moving focus or input.
                # Each earlier exposure must agree with it, including editor.
                if (self.cancelled.wait(.18) or self.check()['rect']!=rect
                        or generation!=self.input_generation()):return None
                latest=ImageGrab.grab(bbox=rect,all_screens=True)
                for earlier in (visible,closing):
                    verdict=editor_halo_verdict(earlier,painted,binding,scale,closing=latest)
                    if verdict!='confirmed':break
            if verdict!='confirmed':
                self.last_editor_proof=verdict
                return None
            if self._input_cookie is not None:self.assert_input_focus()
            if (binding is not self.calibration or not input_desktop_available()
                    or self.check()['rect']!=rect or generation!=self.input_generation()):return None
            self.last_editor_proof='confirmed'
            return painted
        except (CaptureError,OSError,ValueError,DesktopUnavailable):
            return None

    def _dpi_scale(self):
        if os.name=='nt' and hasattr(U,'GetDpiForWindow'):
            dpi=U.GetDpiForWindow(self.calibration.hwnd)
            if dpi:return dpi/96
        return 1.0

    def _background_binding(self):
        binding=self.calibration
        if not binding:raise DesktopUnavailable('尚未校准微信窗口')
        if self.cancelled.is_set():raise DesktopUnavailable('助手已暂停')
        if not input_desktop_available():raise DesktopTemporarilyHidden('桌面已锁定，等待解锁')
        info=self._bound_details()
        if info['pid']!=binding.pid:raise DesktopUnavailable('微信窗口已重建，请重新校准')
        if U.IsIconic(binding.hwnd):
            raise DesktopTemporarilyHidden('微信已最小化，等待微信回到前台')
        if info['width']!=binding.width or info['height']!=binding.height:
            self._rebind_size(info)
        return info

    def _rebind_size(self,info):
        from dataclasses import replace
        from .layout import adapt_calibration
        if self._input_cookie is not None:
            raise DesktopUnavailable('输入过程中微信尺寸改变，已保留草稿')
        original=self.calibration
        # Never scale an old manual crop into a differently sized window.
        # Recover from full separators and two independent identity/button
        # checks instead; unchanged manual geometry remains authoritative.
        candidate=replace(original,width=info['width'],height=info['height'],manual_regions=False)
        if original.manual_regions:
            from .vision import LocalOCR,read_header,header_matches,calibration_send_matches
            if self._sidebar_ocr is None:self._sidebar_ocr=LocalOCR()
        previous=None
        for _ in range(2):
            try:image=capture_window(original.hwnd,info['width'],info['height'])
            except (CaptureError,OSError):
                raise DesktopTemporarilyHidden('窗口布局正在变化，等待画面稳定后自动继续') from None
            current=self._bound_details()
            if any(current[key]!=info[key] for key in ('pid','width','height','rect')):
                raise DesktopTemporarilyHidden('窗口仍在调整，等待布局稳定后自动继续')
            detected=adapt_calibration(image,candidate,self._dpi_scale())
            if detected is candidate or (previous and previous.regions!=detected.regions):
                raise DesktopTemporarilyHidden('窗口布局暂不完整，等待重新识别后自动继续')
            if original.manual_regions:
                lines=read_header(self._sidebar_ocr,image.crop(detected.rect('header')),detected)
                if (not lines or any(line.score<.92 for line in lines)
                        or not header_matches(''.join(line.text for line in lines),detected)
                        or not calibration_send_matches(self._sidebar_ocr,image,detected)):
                    raise DesktopTemporarilyHidden('窗口缩放后标题或发送按钮未能核实，等待清晰画面')
            previous=detected
        if self.cancelled.is_set():raise DesktopUnavailable('助手已暂停')
        self.calibration=previous

    def finish_input(self):
        """Release geometry only after the engine verifies a send receipt."""
        self._input_cookie=None
        returning,self._return_window=self._return_window,None
        self._wake_generation=None
        generation,self._sent_generation=self._sent_generation,None
        if not returning or generation is None:return
        original,original_pid,minimized=returning
        try:
            import win32process
            if (self.input_generation()!=generation or not self.is_foreground() or
                    not input_desktop_available() or self.cancelled.is_set()):return
            if (not U.IsWindow(original) or not U.IsWindowVisible(original)
                    or win32process.GetWindowThreadProcessId(original)[1]!=original_pid):return
            U.SetForegroundWindow(original)
            if minimized:
                for _ in range(10):
                    if self.input_generation()!=generation or self.cancelled.is_set():break
                    front=U.GetForegroundWindow()
                    if front==original:
                        U.ShowWindowAsync(self.calibration.hwnd,7)  # SW_SHOWMINNOACTIVE
                        break
                    if front!=self.calibration.hwnd:break
                    self.cancelled.wait(.025)
        except Exception:
            # Window restoration is optional. A vanished foreground process
            # cannot invalidate an already confirmed WeChat receipt.
            pass

    def prepare_send(self,idle_seconds=10):
        # Only called before a new editor transaction. Once input starts,
        # check()/capture() keep strict geometry and focus guards.
        self._input_cookie=None
        if not self.calibration:raise DesktopUnavailable('尚未校准微信窗口')
        _check_calibration_lease(self.calibration.hwnd)
        if not self.is_foreground():raise DesktopTemporarilyHidden('等待微信自然回到前台，不切换当前应用')
        self._background_binding()
        generation=self.input_generation()
        if self._return_window and generation!=self._wake_generation:
            self._return_window=None
            self._wake_generation=None
        if self.idle_for()<idle_seconds:raise DesktopTemporarilyHidden('正在操作电脑，等待空闲后发送')
        if self.input_generation()!=generation:raise DesktopTemporarilyHidden('检测到人工输入，等待空闲后发送')
        self.check()
        if self.input_generation()!=generation:raise DesktopTemporarilyHidden('窗口核对期间检测到人工输入，等待空闲后发送')
        return generation

    def is_foreground(self):
        return bool(self.calibration and U.GetForegroundWindow()==self.calibration.hwnd)

    def route_state(self):
        """Metadata only: no capture, restore, navigation or clipboard read."""
        b=self.calibration
        if not b:return '待校准'
        try:
            info=self._bound_details()
            if info['pid']!=b.pid:return '需重新绑定'
            if U.IsIconic(b.hwnd):return '已最小化'
            if not U.IsWindowVisible(b.hwnd):return '窗口不可见'
            return '在前台' if self.is_foreground() else '在后台'
        except (DesktopUnavailable,OSError):return '需重新绑定'

    def try_idle_wake(self,idle_seconds):
        """Opt-in only. Restore the verified window during idle time."""
        b=self.calibration
        now=time.monotonic()
        if not b or now-self._last_wake<20 or not input_desktop_available():return False
        original=U.GetForegroundWindow()
        generation=self.input_generation()
        self._return_window=None
        self._wake_generation=None
        def valid():
            if (self.cancelled.is_set() or not input_desktop_available() or
                    self.input_generation()!=generation or self.idle_for()<idle_seconds or
                    U.GetForegroundWindow() not in (original,b.hwnd)):return False
            _check_calibration_lease(b.hwnd)
            return self._bound_details()['pid']==b.pid
        if not valid():return False
        self._last_wake=now
        was_minimized=bool(U.IsIconic(b.hwnd))
        if was_minimized:
            U.ShowWindowAsync(b.hwnd,4)  # SW_SHOWNOACTIVATE: restore before requesting focus.
            for _ in range(10):
                if not valid():return False
                if not U.IsIconic(b.hwnd):break
                if self.cancelled.wait(.05):return False
            if U.IsIconic(b.hwnd):return False
        if not valid() or not U.IsWindowVisible(b.hwnd):return False
        if not self.is_foreground():U.SetForegroundWindow(b.hwnd)
        if not self.is_foreground() and b.window_kind=='detached' and valid():
            checked=self._activate_idle_editor(valid)
            if checked and not self.is_foreground() and valid():
                own_generation=self._click_idle_caption(valid)
                if own_generation is not None:generation=own_generation
        acquired=self.is_foreground() and valid()
        if acquired and original and original!=b.hwnd:
            import win32process
            try:
                self._return_window=(original,win32process.GetWindowThreadProcessId(original)[1],was_minimized)
                self._wake_generation=generation
            except OSError:self._return_window=None
        return acquired

    def _activate_idle_editor(self,valid):
        """A normal click in this chat may activate Qt. Never enter text here."""
        import win32gui
        from .vision import Vision
        if not hasattr(self,'_wake_vision'):self._wake_vision=Vision()
        b=self.calibration
        self._wake_caption=None
        caption_points=[]
        self.wake_reason='等待目标微信允许激活'
        if U.GetLastActivePopup(b.hwnd)!=b.hwnd or not win32gui.IsWindowEnabled(b.hwnd):
            self.wake_reason='微信有弹窗或窗口不可操作';return
        for _ in range(2):
            if not valid():return
            image=self.capture();b=self.calibration
            frame=self._wake_vision.read_controls(image,b)
            # Activation must precede go_to_latest: a detached window left in
            # history cannot scroll until foreground. No text is entered here;
            # Engine._frame still requires latest position before any edit/send.
            if not frame.safe or frame.group!=b.target_group:
                self.wake_reason=getattr(frame,'reason','') or '后台微信标题或布局尚未核实';return
            if not frame.draft_empty:
                self.wake_reason='后台输入框未能确认空白，保留草稿';return
            from .layout import idle_caption_point
            caption_points.append(idle_caption_point(image,self._dpi_scale()))
        info=self._bound_details()
        left,top=info['rect'][:2]
        x1,y1,x2,y2=b.rect('input')
        x,y=win32gui.ScreenToClient(b.hwnd,(left+(x1+x2)//2,top+(y1+y2)//2))
        point=(y<<16)|(x&0xffff)
        if (not valid() or self._bound_details()['rect']!=info['rect']
                or U.GetLastActivePopup(b.hwnd)!=b.hwnd or not win32gui.IsWindowEnabled(b.hwnd)):return
        win32gui.SendMessageTimeout(b.hwnd,0x201,1,point,2,500)
        # Release the same window's button even if focus changed on button-down.
        win32gui.SendMessageTimeout(b.hwnd,0x202,0,point,2,500)
        if caption_points[0] is not None and caption_points[0]==caption_points[1]:
            self._wake_caption=(*caption_points[0],b.width,b.height,time.monotonic())
        self.wake_reason='微信未接受后台窗口激活'
        return True

    def _click_idle_caption(self,valid):
        """Opt-in idle fallback: normal click on a verified native caption.

        Never uses Alt, thread-input attachment or an editor shortcut. Human
        input after the timestamped click cancels the idle credit/transaction.
        """
        import win32gui
        b=self.calibration;info=self._bound_details();idle_credit=self.idle_for()
        left,top,right,bottom=info['rect']
        point=W.POINT((left+right)//2,top+round(16*self._dpi_scale()))
        packed=((point.y&0xffff)<<16)|(point.x&0xffff)
        def caption():
            result=win32gui.SendMessageTimeout(b.hwnd,0x84,0,packed,2,500)
            hit=result[-1] if isinstance(result,tuple) else result
            if hit==2:return True
            proof=getattr(self,'_wake_caption',None)
            return (hit==1 and proof is not None and proof[:4]==(point.x-left,point.y-top,right-left,bottom-top)
                    and 0<=time.monotonic()-proof[4]<=8)
        def guarded():
            return (valid() and self._bound_details()['rect']==info['rect']
                    and U.GetLastActivePopup(b.hwnd)==b.hwnd and win32gui.IsWindowEnabled(b.hwnd)
                    and not any(U.GetAsyncKeyState(key)&0x8000 for key in (1,2,4,16,17,18)))
        if not guarded():return None
        if not caption():
            self.wake_reason='未能确认微信标题栏，等待窗口可交互';return None
        if not guarded():return None
        win32gui.SetWindowPos(b.hwnd,0,0,0,0,0,0x13)  # no move/resize/activate, never topmost
        if not guarded() or U.GetAncestor(U.WindowFromPoint(point),2)!=b.hwnd:return None
        if not caption():return None
        if not U.SetCursorPos(point.x,point.y) or not guarded():return None
        own_generation=K.GetTickCount64()&0xffffffff
        events=(INPUT*2)()
        events[0].value.mouse.flags=2;events[1].value.mouse.flags=4
        for event in events:event.value.mouse.time=own_generation
        sent=U.SendInput(2,events,ctypes.sizeof(INPUT))
        if sent!=2:
            if sent==1:U.SendInput(1,(INPUT*1)(events[1]),ctypes.sizeof(INPUT))
            return None
        # Activation can be delivered asynchronously by Qt.
        for _ in range(8):
            if self.cancelled.is_set() or self.input_generation()!=own_generation:return None
            if self.is_foreground():break
            self.cancelled.wait(.025)
        if (not self.is_foreground() or self._bound_details()['rect']!=info['rect']
                or self.input_generation()!=own_generation):return None
        self.navigation.note(own_generation,idle_credit)
        return own_generation

    def park_editor_pointer(self,idle_seconds):
        """Move an idle pointer halo away; the caller must take a fresh draft reading."""
        if not self.is_foreground() or self.idle_for()<idle_seconds:return False
        generation=self.input_generation()
        info=self.check()
        point=W.POINT()
        if not U.GetCursorPos(ctypes.byref(point)):return False
        left,top=info['rect'][:2]
        x1,y1,x2,y2=self.calibration.rect('input')
        pad=round(64*self._dpi_scale())
        if not (x1-pad<=point.x-left<=x2+pad and y1-pad<=point.y-top<=y2+pad):return False
        dest=W.POINT(left+round(self.calibration.width*.03),top+round(self.calibration.height*.5))
        hwnd=self.calibration.hwnd
        if any(U.GetAncestor(U.WindowFromPoint(p),2)!=hwnd for p in (point,dest)):return False
        if (self.input_generation()!=generation or not self.is_foreground() or
                any(U.GetAsyncKeyState(key)&0x8000 for key in (1,2,4))):return False
        return bool(U.SetCursorPos(dest.x,dest.y))

    def _find_target(self,image):
        if getattr(self.calibration,'window_kind','main')=='detached':
            raise DesktopUnavailable('独立聊天窗口没有会话列表')
        from .vision import LocalOCR,title_key
        from .contacts import visual_name_key
        key=visual_name_key if self.calibration.ocr_title else title_key
        target=key(self.calibration.target_group or group_name(self.calibration.header_label))
        if self._sidebar_ocr is None:self._sidebar_ocr=LocalOCR()
        w,h=image.size
        scale=self._dpi_scale()
        region=(round(68*scale),round(60*scale),self.calibration.rect('messages')[0]-6,round(h-12*scale))
        if region[2]<=region[0]:raise DesktopUnavailable('聊天列表校准区域无效，请重新校准')
        # OCR sometimes joins a row's title and timestamp. Only known clock/date
        # suffixes are allowed; arbitrary previews or partial names never match.
        suffix=r'(?:\d{1,2}:\d{2}|昨天|星期[一二三四五六日天]|周[一二三四五六日天]|\d{1,2}/\d{1,2})?'
        found=[line for line in self._sidebar_ocr(image.crop(region)) if line.score>=.92 and re.fullmatch(re.escape(target)+suffix,key(line.text))]
        return region,found

    def validate_target(self,image):
        detached=getattr(self.calibration,'window_kind','main')=='detached'
        if detached or self.calibration.ocr_title:
            from .vision import LocalOCR,read_header,header_matches
            if self._sidebar_ocr is None:self._sidebar_ocr=LocalOCR()
            lines=read_header(self._sidebar_ocr,image.crop(self.calibration.rect('header')),self.calibration)
            if lines and all(r.score>=.92 for r in lines) and header_matches(''.join(r.text for r in lines),self.calibration):return
            if detached:raise DesktopTemporarilyHidden('独立聊天窗口标题与所选会话不符，请显示绑定的聊天')
        _,found=self._find_target(image)
        if len(found)>1:raise DesktopUnavailable('最近列表存在同名目标群，无法唯一确认，请调整群名')
        if not found:raise DesktopTemporarilyHidden('微信已连接，但尚未定位所选群；正在等待清晰的群标题')

    def prepare_baseline(self,idle_seconds=10):
        idle_credit=self.idle_for()
        generation=self.prepare_send(idle_seconds);info=self.check()
        region=self.calibration.rect('messages')
        point=W.POINT(info['rect'][0]+(region[0]+region[2])//2,info['rect'][1]+(region[1]+region[3])//2)
        if self.input_generation()!=generation:raise DesktopTemporarilyHidden('人工操作中，等待核验最新位置')
        if U.GetAncestor(U.WindowFromPoint(point),2)!=self.calibration.hwnd:return False
        if not U.SetCursorPos(point.x,point.y):raise DesktopUnavailable('无法显示消息区滚动位置')
        # 微信仅在实际滚动后绘制滑块，单纯抬窗/移动鼠标仍可能隐藏它。
        if self.input_generation()!=generation:raise DesktopTemporarilyHidden('人工操作中，稍后核验最新位置')
        events=(INPUT*1)();events[0].value.mouse.flags=0x800;events[0].value.mouse.data=(-120)&0xffffffff
        if U.SendInput(1,events,ctypes.sizeof(INPUT))!=1:raise DesktopUnavailable('最新位置检查未成功')
        generation=self.input_generation()
        self.cancelled.wait(.18);self.check()
        if self.input_generation()!=generation:raise DesktopTemporarilyHidden('人工操作中，稍后核验最新位置')
        self.navigation.note(generation,idle_credit)
        return True

    def go_to_latest(self,idle_seconds=10):
        from .vision import LocalOCR,title_key,navigation_lines,scrollbar_thumb,Vision,read_header,header_matches
        if self.idle_for()<idle_seconds:return False
        if self._sidebar_ocr is None:self._sidebar_ocr=LocalOCR()
        idle_credit=self.idle_for()
        generation=self.prepare_send(idle_seconds)
        image=self.capture();self.validate_target(image)
        lines=read_header(self._sidebar_ocr,image.crop(self.calibration.rect('header')),self.calibration)
        if not lines or any(r.score<.92 for r in lines) or not header_matches(''.join(r.text for r in lines),self.calibration):return False
        region=self.calibration.rect('messages');crop=image.crop(region)
        lines=self._sidebar_ocr(crop);messages=Vision._messages(crop,lines)
        found=navigation_lines(crop,lines,messages)
        thumb=scrollbar_thumb(crop,messages)
        drag=not found and thumb is not None and thumb[3]<crop.height-12
        if (len(found)!=1 and not drag) or self.input_generation()!=generation:return False
        info=self.check()
        box=thumb if drag else found[0].box
        x,y=(box[0]+box[2])//2,(box[1]+box[3])//2
        point=W.POINT(info['rect'][0]+region[0]+x,info['rect'][1]+region[1]+y)
        if U.GetAncestor(U.WindowFromPoint(point),2)!=self.calibration.hwnd:return False
        U.SetCursorPos(point.x,point.y)
        events=(INPUT*2)()
        events[0].value.mouse.flags=0x2;events[1].value.mouse.flags=0x4
        if drag:
            # 微信会合并大滚轮事件；直接普通拖动可见滑块到底，下一帧再核验。
            if U.SendInput(1,events,ctypes.sizeof(INPUT))!=1:raise DesktopUnavailable('滚动条按下未成功')
            try:
                own_generation=self.input_generation()
                self.cancelled.wait(.06);self.check()
                if self.input_generation()!=own_generation:raise DesktopUnavailable('拖动期间检测到人工输入，已停止')
                if not U.SetCursorPos(point.x,info['rect'][1]+region[3]-3):raise DesktopUnavailable('无法定位滚动条底部')
                self.cancelled.wait(.06)
                if self.input_generation()!=own_generation:raise DesktopUnavailable('拖动期间检测到人工输入，已停止')
            finally:
                release=(INPUT*1)(events[1])
                if U.SendInput(1,release,ctypes.sizeof(INPUT))!=1:raise DesktopUnavailable('滚动条释放结果不明确')
        else:
            if U.SendInput(2,events,ctypes.sizeof(INPUT))!=2:raise DesktopUnavailable('最新消息定位结果不明确')
        self.navigation.note(self.input_generation(),idle_credit)
        self._input_cookie=None
        return True

    def select_target(self,idle_seconds=10):
        if getattr(self.calibration,'window_kind','main')=='detached':return False
        if self.idle_for()<idle_seconds:return False
        idle_credit=self.idle_for()
        _,found=self._find_target(self.capture())
        if len(found)>1:return False
        if not found:
            from .contact_navigation import search_target
            return search_target(self,idle_seconds)
        self.prepare_send(idle_seconds)
        generation=self.input_generation()
        region,found=self._find_target(self.capture())
        if len(found)!=1:return False
        if self.input_generation()!=generation:raise DesktopTemporarilyHidden('人工操作中，稍后选择目标群')
        info=self.check();line=found[0]
        point=W.POINT(info['rect'][0]+region[0]+(line.box[0]+line.box[2])//2,info['rect'][1]+region[1]+(line.box[1]+line.box[3])//2)
        if U.GetAncestor(U.WindowFromPoint(point),2)!=self.calibration.hwnd:return False
        U.SetCursorPos(point.x,point.y)
        events=(INPUT*2)();events[0].type=events[1].type=0
        events[0].value.mouse.flags=0x2;events[1].value.mouse.flags=0x4
        if U.SendInput(2,events,ctypes.sizeof(INPUT))!=2:raise DesktopUnavailable('目标群选择结果不明确')
        self.navigation.note(self.input_generation(),idle_credit)
        self._input_cookie=None
        return True

    def idle_for(self) -> float:
        if os.name != "nt":
            return 0
        now = K.GetTickCount64() & 0xffffffff
        generation=self.input_generation()
        native=((now-generation) & 0xffffffff) / 1000
        return self.navigation.idle(generation,native)

    def input_generation(self) -> int:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_=[('size',W.UINT),('tick',W.DWORD)]
        value=LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO),0)
        if not U.GetLastInputInfo(ctypes.byref(value)):
            raise DesktopUnavailable('无法确认人工输入状态')
        return int(value.tick)

    def _input_snapshot(self):
        info=GUITHREADINFO();info.size=ctypes.sizeof(info)
        thread=U.GetWindowThreadProcessId(self.calibration.hwnd,None)
        if not U.GetGUIThreadInfo(thread,ctypes.byref(info)) or not info.focus or U.GetAncestor(info.focus,2) != self.calibration.hwnd:
            raise DesktopUnavailable('无法确认微信编辑焦点')
        return int(info.focus),self.input_generation()

    def assert_input_focus(self):
        self.check()
        if self._input_cookie is None or self._input_snapshot() != self._input_cookie:
            raise DesktopUnavailable('编辑焦点或人工输入发生变化，已停止输入发送')

    def _acknowledge_own_input(self):
        current=self._input_snapshot()
        if self._input_cookie is not None and current[0] != self._input_cookie[0]:
            raise DesktopUnavailable('编辑焦点发生变化')
        self._input_cookie=current

    def _click(self, region: str, expected_generation=None) -> None:
        info = self.check()
        if expected_generation is not None and self.input_generation()!=expected_generation:
            raise DesktopTemporarilyHidden('人工输入发生变化，未点击输入框')
        x1, y1, x2, y2 = self.calibration.rect(region)
        x, y = (x1 + x2)//2, (y1 + y2)//2
        if not U.SetCursorPos(info["rect"][0]+x, info["rect"][1]+y):
            raise DesktopUnavailable("无法定位输入位置")
        current=self.check()
        if getattr(self.calibration,'manual_regions',False) or getattr(self.calibration,'window_kind','main')=='detached':
            point=W.POINT(info['rect'][0]+x,info['rect'][1]+y)
            if current['rect']!=info['rect'] or U.GetAncestor(U.WindowFromPoint(point),2)!=self.calibration.hwnd:
                raise DesktopTemporarilyHidden('微信点击位置已移动或被遮挡')
            cursor=W.POINT()
            if not U.GetCursorPos(ctypes.byref(cursor)) or (cursor.x,cursor.y)!=(point.x,point.y):
                raise DesktopTemporarilyHidden('鼠标位置发生变化，等待重新核验')
        events = (INPUT * 2)()
        events[0].type = events[1].type = 0
        events[0].value.mouse.flags = 0x0002
        events[1].value.mouse.flags = 0x0004
        if U.SendInput(2, events, ctypes.sizeof(INPUT)) != 2:
            raise DesktopUnavailable("鼠标操作结果未知")

    def click_input(self,expected_generation=None) -> None:
        self._click("input",expected_generation)
        self._input_cookie=self._input_snapshot()
        # 光标高亮/鼠标指针可能进入截图；移到裁剪区域外，避免把它当作草稿。
        info = self.check()
        U.SetCursorPos(info["rect"][0]+round(self.calibration.width*.03), info["rect"][1]+round(self.calibration.height*.5))
        self._acknowledge_own_input()
        self.cancelled.wait(.1)
        self.assert_input_focus()

    def type_text(self, text: str) -> None:
        # VK_PACKET delivery can substitute characters after punctuation in
        # WeChat's IME editor. Paste literal Unicode, then let Engine perform
        # exact clipboard readback before the separate Send click.
        from .clipboard import NativeClipboard,pasted_text,ClipboardUnavailable,ClipboardSnapshotUnavailable,RichClipboard,snapshot_for_paste
        # Layout characters go only through clipboard paste, never SendInput.
        unicode_units(text.replace('\n', ' ').replace('\t', ' '))
        clipboard=NativeClipboard(self.calibration.pid)
        self.typed_without_clipboard=False
        snapshot=None
        input_started=False
        def paste():
            nonlocal input_started
            input_started=True  # even a partial SendInput must not be replayed
            self._shortcut(0x11,0x56)
        if text.replace('@',''):
            try:snapshot=snapshot_for_paste(clipboard,self.assert_input_focus,self.cancelled.wait)
            except RichClipboard:
                if '\n' in text or '\t' in text:
                    raise DesktopInputNotStarted('正在保留原剪贴板，等待能原样粘贴排版文字') from None
                self.typed_without_clipboard=True
                # Some IMEs convert the next character when punctuation and
                # the following word arrive in one VK_PACKET batch.
                for part in re.findall(r'[^，。！？、；：,.!?;:]+|.',text):
                    self._type_unicode(part)
                return
            except ClipboardSnapshotUnavailable:
                raise DesktopInputNotStarted('剪贴板暂不可用，稍后自动重试；尚未输入，仍在后台接收') from None
        try:
            for part in re.split('(@)',text):
                if not part:continue
                if part=='@':
                    input_started=True
                    self._type_unicode(part)  # cancel picker before more text
                else:
                    pasted_text(clipboard,part,paste,
                                self.assert_input_focus,self.cancelled.wait,snapshot=snapshot)
                    snapshot=None
        except ClipboardUnavailable as error:
            if not input_started:
                raise DesktopInputNotStarted('剪贴板暂不可用，尚未输入，稍后自动重试；仍在后台接收') from None
            raise DesktopUnavailable('文字粘贴未完成，已保留草稿：'+str(error)) from None

    def _type_unicode(self, text: str) -> None:
        units = unicode_units(text)
        for start in range(0, len(units), 16):
            self.assert_input_focus()
            chunk = units[start:start+16]
            # A literal @ is plain reply text, never an instruction to select a
            # group member. Cancel its picker in the SAME ordered input batch,
            # before any more characters or the next occlusion/focus check.
            # Paste is not a workaround: WeChat also opens the picker on paste.
            events = (INPUT * (2*len(chunk)+2*chunk.count(ord('@'))))()
            index=0
            for unit in chunk:
                for keyup in (0, 1):
                    entry = events[index];index+=1
                    entry.type = 1
                    entry.value.keyboard.scan = unit
                    entry.value.keyboard.flags = 0x0004 | (0x0002 if keyup else 0)
                if unit==ord('@'):
                    for flags in (0,0x0002):
                        entry=events[index];index+=1
                        entry.type=1
                        entry.value.keyboard.vk=0x1b
                        entry.value.keyboard.flags=flags
            if U.SendInput(len(events), events, ctypes.sizeof(INPUT)) != len(events):
                raise DesktopUnavailable("文字输入结果未知，请检查草稿")
            self._acknowledge_own_input()
            self.cancelled.wait(.04 if ord('@') in chunk else .02)
            self.assert_input_focus()

    def _shortcut(self,*keys):
        self.assert_input_focus()
        events=(INPUT*(len(keys)*2))()
        sequence=[(key,0) for key in keys]+[(key,2) for key in reversed(keys)]
        for event,(key,flags) in zip(events,sequence):
            event.type=1;event.value.keyboard.vk=key;event.value.keyboard.flags=flags
        if U.SendInput(len(events),events,ctypes.sizeof(INPUT))!=len(events):
            raise DesktopUnavailable('快捷键未完整执行，已保留草稿')
        self._acknowledge_own_input()

    def read_input_text(self,expected=None):
        from .clipboard import NativeClipboard,copied_text
        clipboard=NativeClipboard(self.calibration.pid)
        def copy():
            self._shortcut(0x11,0x41)
            self.cancelled.wait(.05);self.assert_input_focus()
            self._shortcut(0x11,0x43)
        text=copied_text(clipboard,copy,self.assert_input_focus,self.cancelled.wait,expected=expected)
        self._shortcut(0x27)  # collapse selection; never delete or replace the draft
        self.cancelled.wait(.05);self.assert_input_focus()
        return text

    def sticker_clipboard_status(self):
        from .clipboard import NativeClipboard,RichClipboard,ClipboardUnavailable
        try:
            clipboard=NativeClipboard(self.calibration.pid)
            clipboard.check_formats()
            if self._sticker_snapshot_blocked:
                # The formats looked safe, but their data could not be
                # preserved. Recheck off-editor and retain no bytes.
                clipboard.snapshot()
                self._sticker_snapshot_blocked=False
        except RichClipboard:return 'rich'
        except ClipboardUnavailable:return 'busy'
        return ''

    def type_sticker(self,prepared):
        from .clipboard import NativeClipboard,pasted_file,ClipboardSnapshotUnavailable,RichClipboard,ClipboardUnavailable
        prepared.verified_bytes()
        input_started=False
        def paste():
            nonlocal input_started
            input_started=True
            self._shortcut(0x11,0x56)
        try:
            pasted_file(NativeClipboard(self.calibration.pid),prepared.path,
                        paste,self.assert_input_focus,self.cancelled.wait)
        except ClipboardUnavailable as error:
            if not input_started:
                self._sticker_snapshot_blocked=isinstance(error,ClipboardSnapshotUnavailable) and not isinstance(error,RichClipboard)
                raise StickerClipboardBlocked('rich' if isinstance(error,RichClipboard) else 'busy') from None
            raise DesktopUnavailable('表情粘贴未能完成，已保留草稿') from None

    def verify_sticker_input(self,prepared):
        from .stickers import matches_draft
        self.assert_input_focus()
        image=self.capture()
        self.assert_input_focus()
        result=matches_draft(image.crop(self.calibration.rect('input')),prepared,self._dpi_scale())
        self.assert_input_focus()
        return result

    def sticker_preview_fits(self,prepared):
        """Refuse an uncheckable attachment before changing the editor."""
        import io
        from PIL import Image
        x1,y1,x2,y2=self.calibration.rect('input')
        scale=self._dpi_scale()
        with Image.open(io.BytesIO(prepared.verified_bytes())) as source:
            thumb=source.copy();thumb.thumbnail((round(120*scale)+1,round(120*scale)+1))
        return x2-x1>=max(40,thumb.width) and y2-y1>=max(40,round(thumb.height*.7))

    def click_send(self) -> None:
        self.assert_input_focus()
        self._click("send")
        self._sent_generation=self.input_generation()


_WORKER_MUTEX = "Local\\XiaomaoWechatVisibleWorker"


def _scope_marker(root):
    normalized = os.path.normcase(str(Path(root).resolve()))
    return _WORKER_MUTEX + "-" + hashlib.sha256(os.fsencode(normalized)).hexdigest()


def _mutex_exists(name):
    handle = K.OpenMutexW(0x100000, False, name)
    if not handle:
        return ctypes.get_last_error() == 5  # Exists but belongs to another security context.
    K.CloseHandle(handle)
    return True


def _calibration_marker(hwnd):
    return f'Local\\XiaomaoWechatCalibration-{int(hwnd):x}'


def _check_calibration_lease(hwnd):
    if os.name=='nt' and _mutex_exists(_calibration_marker(hwnd)):
        raise DesktopTemporarilyHidden('此微信窗口正在手动校准，完成或关闭校准后自动继续')


class CalibrationWindowLease:
    """An HWND-scoped marker shared by panel and worker. Windows cleans it on exit."""
    def __init__(self,hwnd):
        if os.name!='nt' or not hwnd:raise DesktopUnavailable('微信窗口无效，无法锁定校准')
        self.handle=K.CreateMutexW(None,False,_calibration_marker(hwnd))
        if not self.handle:raise DesktopUnavailable('无法保护正在校准的微信窗口')
        if ctypes.get_last_error()==183:
            self.close()
            raise DesktopUnavailable('此微信窗口已在其他校准界面中打开')

    def close(self):
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle=None


def worker_scope(root):
    """The single desktop worker owns the markers until it releases its global lock."""
    if os.name != "nt" or not _mutex_exists(_WORKER_MUTEX):
        return None
    if not _mutex_exists(_WORKER_MUTEX + "-scoped"):
        return "unidentified"  # Older worker or a worker still opening its scope.
    return "current" if _mutex_exists(_scope_marker(root)) else "other"


class SingleInstance:
    def __init__(self, root=None):
        if os.name != "nt":
            raise DesktopUnavailable("仅支持 Windows")
        self.markers = []
        self.handle = K.CreateMutexW(None, False, _WORKER_MUTEX)
        if not self.handle:
            raise DesktopUnavailable("无法创建助手单实例锁")
        if ctypes.get_last_error() == 183:
            K.CloseHandle(self.handle)
            self.handle = None
            raise DesktopUnavailable("已有一个小猫助手正在运行")
        if root is not None:
            # Publish the profile before advertising that scope detection is supported.
            for name in (_scope_marker(root), _WORKER_MUTEX + "-scoped"):
                marker = K.CreateMutexW(None, False, name)
                if not marker:
                    break  # The global exclusion still works; clients can wait as for a legacy worker.
                self.markers.append(marker)

    def close(self):
        while self.markers:
            K.CloseHandle(self.markers.pop())
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle = None


class PauseChord:
    """只保留 Ctrl、Alt、P 的按下状态，不接收或保存其他键的内容。"""
    ALLOWED=frozenset((0x11,0xA2,0xA3,0x12,0xA4,0xA5,0x50))

    def __init__(self):
        self.keys=set()
        self.latched=False

    def update(self,key,down):
        if key not in self.ALLOWED:return False
        if down:self.keys.add(key)
        else:self.keys.discard(key)
        complete=bool(self.keys & {0x11,0xA2,0xA3}) and bool(self.keys & {0x12,0xA4,0xA5}) and 0x50 in self.keys
        fire=complete and not self.latched
        self.latched=complete
        return fire


def _poll_pause_chord(stop,pause):
    # 只检查固定的三个暂停键，不记录任何键入内容，也不独占系统快捷键。
    was_down = False
    while not stop.wait(.04):
        down = all(U.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x50))
        if down and not was_down:
            pause("用户通过 Ctrl+Alt+P 暂停")
        was_down = down


def hotkey_loop(stop: threading.Event, pause) -> None:
    if os.name != "nt":return
    # Windows 自己投递事件；回调留在本进程，不注入微信，不吞键、不记录文字。
    # 避免已被其他程序注册的快捷键冲突，也避免轮询漏掉短按。
    callback_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_int,W.WPARAM,W.LPARAM)
    chord=PauseChord()
    requested=threading.Event()
    try:
        U.SetWindowsHookExW.argtypes=[ctypes.c_int,callback_type,W.HINSTANCE,W.DWORD]
        U.SetWindowsHookExW.restype=W.HANDLE
        U.CallNextHookEx.argtypes=[W.HANDLE,ctypes.c_int,W.WPARAM,W.LPARAM]
        U.CallNextHookEx.restype=ctypes.c_ssize_t
        U.UnhookWindowsHookEx.argtypes=[W.HANDLE]
        U.PeekMessageW.argtypes=[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
        U.TranslateMessage.argtypes=[ctypes.POINTER(W.MSG)]
        U.DispatchMessageW.argtypes=[ctypes.POINTER(W.MSG)]
        K.GetModuleHandleW.argtypes=[W.LPCWSTR];K.GetModuleHandleW.restype=W.HMODULE
    except AttributeError:
        _poll_pause_chord(stop,pause);return

    @callback_type
    def callback(code,message,data):
        if code==0 and message in (0x100,0x101,0x104,0x105):
            key=ctypes.cast(data,ctypes.POINTER(W.DWORD)).contents.value
            if chord.update(key,message in (0x100,0x104)):
                requested.set()
        return U.CallNextHookEx(None,code,message,data)

    handle=U.SetWindowsHookExW(13,callback,K.GetModuleHandleW(None),0)
    if not handle:
        _poll_pause_chord(stop,pause);return
    try:
        message=W.MSG()
        while not stop.wait(.01):
            while U.PeekMessageW(ctypes.byref(message),None,0,0,1):
                U.TranslateMessage(ctypes.byref(message))
                U.DispatchMessageW(ctypes.byref(message))
            if requested.is_set():
                requested.clear()
                pause("用户通过 Ctrl+Alt+P 暂停")
    finally:
        U.UnhookWindowsHookEx(handle)
