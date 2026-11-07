"""Resident-owned Windows notification-area entry, independent of Tk's lifetime.

The daemon creates one ``TrayIcon`` after acquiring its desktop singleton and
calls ``stop`` in its cleanup. Callbacks run on separate daemon threads: an open
callback must focus/launch the application's single panel, and an exit callback
must stop the resident. Neither opening nor removing this icon changes run mode.

``start`` returns only after the icon is registered, or raises ``TrayUnavailable``.
Runtime failures are logged without exception contents and exposed through
``last_error`` and the optional ``on_error`` callback. The owning process remains
responsible for presenting those errors to the user.
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Callable
import uuid

from .brand import APP_NAME


class TrayUnavailable(RuntimeError):
    """A notification-area entry could not be created or maintained."""


def _load_win32():
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError:
        raise TrayUnavailable('系统托盘入口需要 Windows 和 pywin32，请检查安装环境') from None
    return SimpleNamespace(gui=win32gui, con=win32con, kernel=win32api)


class TrayIcon:
    """One thread-owned hidden HWND and its shell notification icon.

    ``on_open``, ``on_pause``, ``on_exit``, ``status_text``, and ``on_error`` must
    be thread-safe. They must marshal any Tk operations onto Tk's thread. Repeated
    commands are coalesced while their callback is running. ``on_exit`` is only
    called for the explicit menu action; ``stop`` only releases tray resources
    and does not wait for arbitrary application callbacks. An instance cannot be
    restarted after it has stopped; repeated starts while running are harmless.

    ``win32`` is an optional bundle of ``gui``, ``con``, and ``kernel`` APIs, so
    tests can exercise lifecycle behavior without modifying the real desktop.
    """

    _ICON_ID = 1
    _VERSION = 4
    _NIF_SHOWTIP = 0x80
    _NIM_SETFOCUS = 3
    _NIN_SELECT = 0x400
    _NIN_KEYSELECT = 0x401
    _RESTORE_DELAYS = (0.25, 1, 2, 4)
    _COMMANDS = ((1001, '打开助手', 'open'), (1002, '运行状态', 'status'),
                 (1003, '暂停全部', 'pause'), (1004, '停止并退出', 'exit'))

    def __init__(self, icon_path: Path | str | None = None, *,
                 on_open: Callable[[], object], on_pause: Callable[[], object],
                 on_exit: Callable[[], object], status_text: Callable[[], str] | None = None,
                 on_error: Callable[[TrayUnavailable], object] | None = None, win32=None):
        self.icon_path = (Path(icon_path) if icon_path is not None else
                          Path(__file__).with_name('ui_assets') / 'app.ico').resolve()
        self._actions = {'open': on_open, 'pause': on_pause, 'exit': on_exit,
                         'status': self._show_status}
        self._status_text = status_text or (lambda: '助手正在后台运行')
        self._on_error = on_error
        self._win32 = win32
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        self._hwnd = self._icon = None
        self._registered = False
        self._icon_added = False
        self._window_destroyed = False
        self._pending = set()
        self._class_name = 'ShiguangTray_' + uuid.uuid4().hex
        self._startup_error = None
        self._restore_generation = 0
        self._restore_attempt = 0
        self._recovery_timer = None
        self.last_error: TrayUnavailable | None = None

    @property
    def available(self) -> bool:
        """A live message loop is not proof that Explorer still has the icon."""
        with self._lock:return self.running and self._icon_added

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive() and self._ready.is_set()
                        and self._startup_error is None and not self._stopping.is_set())

    def start(self, timeout: float = 5) -> TrayIcon:
        """Create the entry synchronously; never leave an unnoticed failed start."""
        with self._lock:
            if self._stopping.is_set():
                raise TrayUnavailable('系统托盘入口已关闭，请创建新的入口')
            if self._thread is None:
                if not self.icon_path.is_file():
                    raise TrayUnavailable('系统托盘图标文件缺失，请修复安装包')
                self._win32 = self._win32 or _load_win32()
                self._thread = threading.Thread(target=self._run, daemon=True, name='xiaomao-tray')
                self._thread.start()
        if not self._ready.wait(timeout):
            self._request_stop()
            raise TrayUnavailable('系统托盘入口启动超时，请检查 Windows 通知区域')
        if self._startup_error is not None:
            self._thread.join(timeout)
            raise self._startup_error from None
        if not self.running:
            raise TrayUnavailable('系统托盘入口未能保持运行，请重新启动助手')
        return self

    def stop(self, timeout: float = 5) -> None:
        """Remove this entry on its owner thread; never stop the resident itself."""
        self._request_stop()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise TrayUnavailable('系统托盘入口尚未关闭，请检查后台进程')

    def _request_stop(self):
        self._stopping.set()
        with self._lock:
            hwnd = self._hwnd
        if hwnd:
            try:
                self._win32.gui.PostMessage(hwnd, self._win32.con.WM_CLOSE, 0, 0)
            except Exception as error:
                # The owner may already be destroying its HWND.
                if self._thread and self._thread.is_alive() and not self._window_destroyed:
                    self._report_error('系统托盘入口关闭请求未能送达', error)

    def _run(self):
        gui, con, kernel = self._win32.gui, self._win32.con, self._win32.kernel
        try:
            self._instance = kernel.GetModuleHandle(None)
            self._callback_message = con.WM_APP + 20
            self._recovery_message = con.WM_APP + 21
            self._taskbar_message = gui.RegisterWindowMessage('TaskbarCreated')
            spec = gui.WNDCLASS()
            spec.hInstance = self._instance
            spec.lpszClassName = self._class_name
            spec.lpfnWndProc = self._window_proc
            spec.style = con.CS_DBLCLKS
            gui.RegisterClass(spec)
            self._registered = True
            # A hidden top-level HWND receives TaskbarCreated broadcasts;
            # an HWND_MESSAGE window would miss them after Explorer restarts.
            self._hwnd = gui.CreateWindowEx(con.WS_EX_TOOLWINDOW, self._class_name, APP_NAME,
                                            con.WS_POPUP, 0, 0, 0, 0, 0, 0, self._instance, None)
            self._icon = gui.LoadImage(0, str(self.icon_path), con.IMAGE_ICON,
                                       kernel.GetSystemMetrics(con.SM_CXSMICON),
                                       kernel.GetSystemMetrics(con.SM_CYSMICON), con.LR_LOADFROMFILE)
            if not self._icon:
                raise TrayUnavailable('系统托盘图标加载失败，请修复安装包')
            if self._stopping.is_set():
                return
            self._add_icon()
            self._ready.set()
            if not self._stopping.is_set():
                gui.PumpMessages()
                if not self._stopping.is_set():
                    raise TrayUnavailable('系统托盘消息循环意外结束')
        except Exception as error:
            if not self._ready.is_set():
                self._startup_error = TrayUnavailable('系统托盘入口创建失败，请检查图标文件和 Windows 通知区域')
                self.last_error = self._startup_error
            else:
                self._report_error('系统托盘入口已中断，请重新打开助手', error)
        finally:
            self._stopping.set()
            self._cleanup()
            self._ready.set()

    def _add_icon(self):
        gui = self._win32.gui
        data = (self._hwnd, self._ICON_ID,
                gui.NIF_MESSAGE | gui.NIF_ICON | gui.NIF_TIP | self._NIF_SHOWTIP,
                self._callback_message, self._icon, APP_NAME)
        self._notify(gui.NIM_ADD, data)
        self._icon_added = True
        # pywin32's tuple timeout field is the uTimeout/uVersion union.
        self._notify(gui.NIM_SETVERSION,
                     (self._hwnd, self._ICON_ID, 0, 0, 0, '', '', self._VERSION))

    def _notify(self, operation, data):
        result = self._win32.gui.Shell_NotifyIcon(operation, data)
        # pywin32 raises on failure and returns None on success; accepting a
        # literal False also keeps adapters from silently hiding shell failures.
        if result is False or result == 0:
            raise TrayUnavailable('系统托盘入口未能加入 Windows 通知区域')

    def _restore_icon(self):
        try:
            self._add_icon()
        except Exception as error:
            if self._icon_added:
                try:
                    self._win32.gui.Shell_NotifyIcon(self._win32.gui.NIM_DELETE,
                                                    (self._hwnd, self._ICON_ID))
                except Exception:
                    pass  # Explorer may have disappeared again during recovery.
                self._icon_added = False
            if self._restore_attempt == 0:
                self._report_error('系统托盘入口暂不可用，正在自动恢复', error)
            if self._restore_attempt >= len(self._RESTORE_DELAYS):
                self._report_error('系统托盘入口恢复失败，请重新打开助手', error)
                return
            with self._lock:
                if self._stopping.is_set():
                    return
                delay = self._RESTORE_DELAYS[self._restore_attempt]
                self._restore_attempt += 1
                self._recovery_timer = threading.Timer(delay, self._post_recovery,
                                                        (self._restore_generation,))
                self._recovery_timer.daemon = True
                self._recovery_timer.start()

    def _post_recovery(self, generation):
        # Timers post work; only the HWND's thread calls Shell_NotifyIcon.
        if self._stopping.is_set() or generation != self._restore_generation:
            return
        try:
            self._win32.gui.PostMessage(self._hwnd, self._recovery_message, generation, 0)
        except Exception as error:
            if not self._stopping.is_set():
                self._report_error('系统托盘入口恢复请求未能送达，请重新打开助手', error)

    def _window_proc(self, hwnd, message, wparam, lparam):
        gui, con = self._win32.gui, self._win32.con
        try:
            if message == con.WM_CLOSE:
                self._stopping.set()
                if not self._window_destroyed:
                    gui.DestroyWindow(hwnd)
                return 0
            if message == con.WM_DESTROY:
                self._window_destroyed = True
                gui.PostQuitMessage(0)
                return 0
            if self._stopping.is_set():
                return gui.DefWindowProc(hwnd, message, wparam, lparam)
            if message == self._taskbar_message:
                self._restore_generation += 1
                self._restore_attempt = 0
                if self._recovery_timer:
                    self._recovery_timer.cancel()
                self._icon_added = False
                self._restore_icon()
                return 0
            if message == self._recovery_message:
                if wparam == self._restore_generation and not self._icon_added:
                    self._restore_icon()
                return 0
            if message == self._callback_message:
                identifier, event = (lparam >> 16) & 0xFFFF, lparam & 0xFFFF
                if identifier != self._ICON_ID:
                    return 0
                if event in (con.WM_LBUTTONDBLCLK, self._NIN_KEYSELECT, self._NIN_SELECT):
                    self._dispatch('open')
                elif event in (con.WM_RBUTTONUP, con.WM_CONTEXTMENU):
                    self._show_menu(hwnd)
                return 0
            return gui.DefWindowProc(hwnd, message, wparam, lparam)
        except Exception as error:
            self._report_error('系统托盘操作未能完成，请从助手窗口重试', error)
            return 0

    def _show_menu(self, hwnd):
        gui, con = self._win32.gui, self._win32.con
        menu = gui.CreatePopupMenu()
        try:
            for identifier, label, action in self._COMMANDS:
                if action == 'exit':
                    gui.AppendMenu(menu, con.MF_SEPARATOR, 0, '')
                gui.AppendMenu(menu, con.MF_STRING, identifier, label)
            gui.SetMenuDefaultItem(menu, self._COMMANDS[0][0], False)
            x, y = gui.GetCursorPos()
            gui.SetForegroundWindow(hwnd)
            selected = gui.TrackPopupMenu(menu, con.TPM_RIGHTBUTTON | con.TPM_RETURNCMD | con.TPM_NONOTIFY,
                                          x, y, 0, hwnd, None)
            for identifier, _, action in self._COMMANDS:
                if selected == identifier:
                    self._dispatch(action)
                    break
        finally:
            gui.DestroyMenu(menu)
            gui.PostMessage(hwnd, con.WM_NULL, 0, 0)
            gui.Shell_NotifyIcon(self._NIM_SETFOCUS, (hwnd, self._ICON_ID))

    def _dispatch(self, action):
        with self._lock:
            if self._stopping.is_set() or action in self._pending:
                return
            self._pending.add(action)
        threading.Thread(target=self._invoke, args=(action,), daemon=True,
                         name='xiaomao-tray-' + action).start()

    def _invoke(self, action):
        error = None
        try:
            if not self._stopping.is_set():
                self._actions[action]()
        except Exception as caught:
            error = caught
        finally:
            with self._lock:
                self._pending.discard(action)
        if error is not None:
            self._report_error('系统托盘请求未能完成，请从助手窗口重试', error)

    def _show_status(self):
        text = str(self._status_text())
        if not self._stopping.is_set():
            self._win32.gui.MessageBox(0, text, APP_NAME,
                                       self._win32.con.MB_OK | self._win32.con.MB_ICONINFORMATION)

    def _report_error(self, message, error):
        reported = TrayUnavailable(message)
        self.last_error = reported
        logging.getLogger(__name__).error('%s (%s)', message, type(error).__name__)
        if self._on_error:
            def notify():
                try:
                    self._on_error(reported)
                except Exception as notification_error:
                    logging.getLogger(__name__).error('托盘错误提示未能完成 (%s)', type(notification_error).__name__)
            threading.Thread(target=notify, daemon=True, name='xiaomao-tray-error').start()

    def _cleanup(self):
        gui = self._win32.gui
        if self._recovery_timer:
            self._recovery_timer.cancel()
        operations = []
        if self._icon_added and self._hwnd:
            operations.append(lambda: gui.Shell_NotifyIcon(gui.NIM_DELETE, (self._hwnd, self._ICON_ID)))
        if self._hwnd and not self._window_destroyed:
            operations.append(lambda: gui.DestroyWindow(self._hwnd))
        if self._icon:
            operations.append(lambda: gui.DestroyIcon(self._icon))
        if self._registered:
            operations.append(lambda: gui.UnregisterClass(self._class_name, self._instance))
        for operation in operations:
            try:
                operation()
            except Exception as error:
                self._report_error('系统托盘资源清理未能完成', error)
        with self._lock:
            self._hwnd = self._icon = None
            self._registered = self._icon_added = False
