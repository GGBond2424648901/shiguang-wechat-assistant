"""No native windows, notification icons, or message boxes are created here."""
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeWin32:
    """Model only the external HWND, shell-icon, and menu resources we own."""

    WM_APP = 0x8000
    WM_CLOSE = 0x10
    WM_DESTROY = 2
    WM_COMMAND = 0x111
    WM_NULL = 0
    WM_LBUTTONDBLCLK = 0x203
    WM_RBUTTONUP = 0x205
    WM_CONTEXTMENU = 0x7B
    WS_POPUP = 0x80000000
    WS_EX_TOOLWINDOW = 0x80
    WS_VISIBLE = 0x10000000
    CS_DBLCLKS = 8
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x10
    SM_CXSMICON = 49
    SM_CYSMICON = 50
    MF_STRING = 0
    MF_SEPARATOR = 0x800
    TPM_RIGHTBUTTON = 2
    TPM_RETURNCMD = 0x100
    TPM_NONOTIFY = 0x80
    MB_OK = 0
    MB_ICONINFORMATION = 0x40
    NIF_MESSAGE = 1
    NIF_ICON = 2
    NIF_TIP = 4
    NIM_ADD = 0
    NIM_DELETE = 2
    NIM_SETVERSION = 4

    def __init__(self):
        self.gui = self.con = self.kernel = self
        self.messages = queue.Queue()
        self.windows = {}
        self.classes = {}
        self.icons = set()
        self.shell_icons = {}
        self.menus = {}
        self.owner_calls = []
        self.adds = []
        self.add_attempts = 0
        self.versions = []
        self.shown_menus = []
        self.dialogs = []
        self.loaded_paths = []
        self.selected_label = None
        self.add_error = None
        self.add_result = None  # pywin32 BOOLAPI returns None on success.
        self.version_error = None
        self.version_result = None
        self.load_result = 401
        self.menu_error = None
        self.quit = False
        self.class_spec = None
        self.window_spec = None

    def _owned(self, name):
        self.owner_calls.append((name, threading.get_ident()))

    def WNDCLASS(self):
        return SimpleNamespace()

    def GetModuleHandle(self, _):
        return 10

    def RegisterWindowMessage(self, name):
        assert name == 'TaskbarCreated'
        self.taskbar_message = 0xC001
        return self.taskbar_message

    def RegisterClass(self, spec):
        self._owned('register')
        self.class_spec = spec
        self.classes[spec.lpszClassName] = spec.lpfnWndProc
        return 51

    def CreateWindowEx(self, *args):
        self._owned('create')
        self.window_spec = args
        hwnd = 301
        self.windows[hwnd] = self.classes[args[1]]
        return hwnd

    def GetSystemMetrics(self, _):
        return 16

    def LoadImage(self, instance, path, kind, width, height, flags):
        self._owned('load_icon')
        assert instance == 0 and kind == self.IMAGE_ICON
        assert flags & self.LR_LOADFROMFILE and width > 0 and height > 0
        self.loaded_paths.append(path)
        if self.load_result:
            self.icons.add(self.load_result)
        return self.load_result

    def Shell_NotifyIcon(self, operation, data):
        self._owned('shell')
        key = data[:2]
        if operation == self.NIM_ADD:
            self.add_attempts += 1
            if self.add_error:
                raise self.add_error
            if self.add_result is False:
                return False
            if key in self.shell_icons:
                raise OSError('DUPLICATE_ICON')
            assert data[4] in self.icons
            self.shell_icons[key] = data
            self.adds.append(data)
        elif operation == self.NIM_DELETE:
            self.shell_icons.pop(key, None)
        elif operation == self.NIM_SETVERSION:
            assert key in self.shell_icons
            if self.version_error:
                raise self.version_error
            self.versions.append(data[7])
            return self.version_result
        else:
            assert operation == 3  # NIM_SETFOCUS is absent in some pywin32 builds.
        return self.add_result if operation == self.NIM_ADD else None

    def PumpMessages(self):
        self._owned('pump')
        while not self.quit:
            hwnd, message, wparam, lparam = self.messages.get(timeout=4)
            if hwnd in self.windows:
                self.windows[hwnd](hwnd, message, wparam, lparam)

    def PostMessage(self, hwnd, message, wparam, lparam):
        self.messages.put((hwnd, message, wparam, lparam))

    def DestroyWindow(self, hwnd):
        self._owned('destroy_window')
        procedure = self.windows.pop(hwnd)
        procedure(hwnd, self.WM_DESTROY, 0, 0)

    def PostQuitMessage(self, _):
        self._owned('quit')
        self.quit = True

    def UnregisterClass(self, class_name, instance):
        self._owned('unregister')
        del self.classes[class_name]

    def DestroyIcon(self, icon):
        self._owned('destroy_icon')
        self.icons.remove(icon)

    def DefWindowProc(self, *args):
        return 0

    def CreatePopupMenu(self):
        self._owned('menu')
        self.menus[501] = []
        return 501

    def AppendMenu(self, menu, flags, identifier, text):
        self.menus[menu].append((flags, identifier, text))

    def SetMenuDefaultItem(self, menu, item, by_position):
        assert any(row[1] == item for row in self.menus[menu])

    def GetCursorPos(self):
        return (90, 100)

    def SetForegroundWindow(self, hwnd):
        assert hwnd in self.windows

    def TrackPopupMenu(self, menu, flags, x, y, reserved, hwnd, rect):
        self._owned('track_menu')
        assert flags & self.TPM_RETURNCMD
        assert (x, y, reserved, rect) == (90, 100, 0, None)
        self.shown_menus.append(tuple(self.menus[menu]))
        if self.menu_error:
            raise self.menu_error
        return next((row[1] for row in self.menus[menu] if row[2] == self.selected_label), 0)

    def DestroyMenu(self, menu):
        self._owned('destroy_menu')
        del self.menus[menu]

    def MessageBox(self, hwnd, text, title, flags):
        self.dialogs.append((hwnd, text, title, flags, threading.get_ident()))
        return 1

    def notify(self, event, *, identifier=1):
        hwnd, _, _, callback, *_ = self.adds[-1]
        # NOTIFYICON_VERSION_4 packs the icon id into lParam's upper word.
        self.PostMessage(hwnd, callback, 0, (identifier << 16) | event)

    def restart_explorer(self):
        self.shell_icons.clear()
        self.PostMessage(next(iter(self.windows)), self.taskbar_message, 0, 0)


def eventually(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def native(tmp_path):
    icon = tmp_path / 'approved.ico'
    icon.write_bytes(b'FAKE_ICON_NOT_LOADED_BY_WINDOWS')
    return FakeWin32(), icon


def make_tray(native, **callbacks):
    from xiaomao.tray import TrayIcon
    api, icon = native
    for name in ('on_open', 'on_pause', 'on_exit'):
        callbacks.setdefault(name, lambda: None)
    return TrayIcon(icon, win32=api, **callbacks)


def test_start_owns_one_hidden_top_level_window_and_stop_only_cleans_tray(native):
    calls = []
    api, icon = native
    tray = make_tray(native, on_exit=lambda: calls.append('exit'))
    try:
        assert tray.start() is tray
        assert tray.start() is tray
        assert tray.running and len(api.shell_icons) == 1 and len(api.adds) == 1
        assert api.loaded_paths == [str(icon.resolve())]
        assert api.window_spec[0] & api.WS_EX_TOOLWINDOW
        assert api.window_spec[3] & api.WS_POPUP
        assert not api.window_spec[3] & api.WS_VISIBLE
        assert api.window_spec[8] == 0  # Top-level, receives shell broadcasts.
        assert api.versions == [4]
    finally:
        tray.stop()
    tray.stop()
    assert not tray.running and not calls
    assert not api.shell_icons and not api.windows and not api.icons and not api.classes
    owner_threads = {thread for _, thread in api.owner_calls}
    assert len(owner_threads) == 1 and threading.get_ident() not in owner_threads


def test_double_click_coalesces_open_without_resuming_or_blocking_pause(native):
    api, _ = native
    entered, release, paused = threading.Event(), threading.Event(), threading.Event()
    opens = []

    def open_panel():
        opens.append(threading.get_ident())
        entered.set()
        release.wait(3)

    tray = make_tray(native, on_open=open_panel, on_pause=paused.set)
    try:
        tray.start()
        assert not paused.is_set() and not opens
        api.notify(api.WM_LBUTTONDBLCLK)
        assert entered.wait(1)
        for _ in range(4):
            api.notify(api.WM_LBUTTONDBLCLK)
        api.selected_label = '暂停全部'
        api.notify(api.WM_RBUTTONUP)
        assert paused.wait(1)
        assert len(opens) == 1
        assert opens[0] != next(thread for name, thread in api.owner_calls if name == 'pump')
        api.notify(api.WM_LBUTTONDBLCLK, identifier=2)
        release.set()
        assert len(opens) == 1
    finally:
        release.set()
        tray.stop()


def test_keyboard_activation_opens_panel_and_ignores_other_icon_ids(native):
    api, _ = native
    calls = []
    tray = make_tray(native, on_open=lambda: calls.append('open'))
    try:
        tray.start()
        api.notify(api.WM_LBUTTONDBLCLK, identifier=2)
        api.notify(0x401)  # NIN_KEYSELECT: keyboard activation.
        eventually(lambda: calls)
        assert calls == ['open']
    finally:
        tray.stop()


@pytest.mark.parametrize('label,expected', [('打开助手', 'open'), ('暂停全部', 'pause'), ('完全退出', 'exit')])
def test_context_menu_dispatches_only_the_selected_command(native, label, expected):
    api, _ = native
    calls = []
    tray = make_tray(native, on_open=lambda: calls.append('open'),
                     on_pause=lambda: calls.append('pause'), on_exit=lambda: calls.append('exit'))
    try:
        tray.start()
        api.selected_label = label
        api.notify(api.WM_CONTEXTMENU)
        eventually(lambda: calls)
        assert calls == [expected]
        assert [row[2] for row in api.shown_menus[0] if row[0] != api.MF_SEPARATOR] == [
            '打开助手', '运行状态', '暂停全部', '完全退出']
        assert tray.running and api.shell_icons
        eventually(lambda: not api.menus)
    finally:
        tray.stop()


def test_status_is_fresh_and_runs_off_window_thread(native):
    api, _ = native
    state = {'text': '全部暂停'}
    reads = []

    def status():
        reads.append(threading.get_ident())
        return state['text']

    tray = make_tray(native, status_text=status)
    try:
        tray.start()
        assert not reads  # Starting/reopening never probes or changes worker mode.
        state['text'] = '观察中 · 3 个会话'
        api.selected_label = '运行状态'
        api.notify(api.WM_RBUTTONUP)
        eventually(lambda: api.dialogs)
        assert api.dialogs[0][1] == '观察中 · 3 个会话'
        native_thread = next(thread for name, thread in api.owner_calls if name == 'pump')
        assert reads[0] == api.dialogs[0][4] != native_thread
    finally:
        tray.stop()


def test_explorer_restart_readds_same_icon_and_restores_callback_version(native):
    api, _ = native
    tray = make_tray(native)
    try:
        tray.start()
        first = api.adds[0]
        api.restart_explorer()
        eventually(lambda: len(api.adds) == 2)
        assert list(api.shell_icons.values()) == [first]
        assert api.versions == [4, 4]
        assert api.loaded_paths == [str(native[1].resolve())]
    finally:
        tray.stop()


def test_missing_approved_icon_is_an_explicit_startup_error(native):
    from xiaomao.tray import TrayUnavailable
    api, icon = native
    icon.unlink()
    tray = make_tray(native)
    with pytest.raises(TrayUnavailable, match='图标'):
        tray.start()
    assert not tray.running and not api.shell_icons and not api.windows


@pytest.mark.parametrize('failure', ['raise', 'false', 'bad_icon', 'version_raise', 'version_false'])
def test_failed_native_start_cleans_owned_resources_and_reports_failure(native, failure):
    from xiaomao.tray import TrayUnavailable
    api, _ = native
    if failure == 'raise':
        api.add_error = OSError('PRIVATE_NATIVE_DETAIL')
    elif failure == 'false':
        api.add_result = False
    elif failure == 'bad_icon':
        api.load_result = 0
    elif failure == 'version_raise':
        api.version_error = OSError('VERSION_SETUP_FAILED')
    else:
        api.version_result = False
    tray = make_tray(native)
    with pytest.raises(TrayUnavailable):
        tray.start()
    assert not tray.running and not api.shell_icons and not api.windows and not api.icons and not api.classes


def test_callback_failure_keeps_entry_alive_and_reports_sanitized_error(native, caplog):
    api, _ = native
    reports, calls = [], []

    def open_panel():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('PRIVATE_CHAT_OR_CREDENTIAL_MUST_NOT_BE_LOGGED')

    tray = make_tray(native, on_open=open_panel, on_error=reports.append)
    try:
        tray.start()
        api.notify(api.WM_LBUTTONDBLCLK)
        eventually(lambda: reports)
        assert tray.running and api.shell_icons and tray.last_error is reports[0]
        assert 'PRIVATE_CHAT' not in str(reports[0]) + caplog.text
        api.notify(api.WM_LBUTTONDBLCLK)
        eventually(lambda: len(calls) == 2)
    finally:
        tray.stop()


def test_recreation_failure_is_reported_and_later_broadcast_can_recover(native):
    api, _ = native
    reports = []
    tray = make_tray(native, on_error=reports.append)
    try:
        tray.start()
        api.add_error = OSError('SHELL_NOT_READY')
        api.restart_explorer()
        eventually(lambda: reports)
        assert tray.running and tray.last_error is reports[0]
        api.add_error = None
        api.restart_explorer()
        eventually(lambda: api.shell_icons)
    finally:
        tray.stop()


def test_recreation_retries_when_explorer_is_not_ready_at_its_first_broadcast(native):
    api, _ = native
    reports = []
    tray = make_tray(native, on_error=reports.append)
    try:
        tray.start()
        api.add_error = OSError('SHELL_STARTING')
        api.restart_explorer()
        eventually(lambda: reports)
        api.add_error = None
        eventually(lambda: api.shell_icons)
        assert len(api.adds) == 2 and api.versions == [4, 4]
        owner_threads = {thread for _, thread in api.owner_calls}
        assert len(owner_threads) == 1
    finally:
        tray.stop()


def test_stop_cancels_pending_explorer_recovery(native):
    api, _ = native
    reports = []
    tray = make_tray(native, on_error=reports.append)
    tray.start()
    api.add_error = OSError('SHELL_STARTING')
    api.restart_explorer()
    eventually(lambda: reports)
    tray.stop()
    api.add_error = None
    time.sleep(0.3)  # Past the first scheduled retry; no real shell calls exist.
    assert len(api.adds) == 1 and not api.shell_icons and not api.windows


def test_recovery_after_version_failure_removes_partial_icon_before_retry(native):
    api, _ = native
    reports = []
    tray = make_tray(native, on_error=reports.append)
    try:
        tray.start()
        api.version_error = OSError('SHELL_STARTING')
        api.restart_explorer()
        eventually(lambda: reports)
        assert not api.shell_icons
        api.version_error = None
        eventually(lambda: len(api.versions) == 2)
        assert api.shell_icons and api.add_attempts == 3
    finally:
        tray.stop()


def test_recovery_has_a_bound_and_reports_when_shell_remains_unavailable(native, monkeypatch):
    from xiaomao.tray import TrayIcon
    api, _ = native
    reports = []
    monkeypatch.setattr(TrayIcon, '_RESTORE_DELAYS', (0.005, 0.005))
    tray = make_tray(native, on_error=reports.append)
    try:
        tray.start()
        api.add_error = OSError('SHELL_UNAVAILABLE')
        api.restart_explorer()
        eventually(lambda: len(reports) == 2)
        assert '恢复失败' in str(reports[-1])
        assert api.add_attempts == 4  # Initial registration, restoration, two retries.
        time.sleep(0.03)
        assert api.add_attempts == 4 and tray.running
    finally:
        tray.stop()


def test_unexpected_message_loop_exit_is_reported_instead_of_silently_losing_entry(native):
    api, _ = native
    reports = []
    tray = make_tray(native, on_error=reports.append)
    try:
        tray.start()
        api.quit = True
        api.PostMessage(next(iter(api.windows)), api.WM_NULL, 0, 0)
        eventually(lambda: not tray.running)
        eventually(lambda: reports)
        assert tray.last_error is reports[-1]
        assert not api.windows and not api.shell_icons and not api.icons
    finally:
        tray.stop()


def test_stop_does_not_wait_for_a_blocked_open_callback_or_invoke_exit(native):
    api, _ = native
    entered, release = threading.Event(), threading.Event()
    exits = []

    def open_panel():
        entered.set()
        release.wait(3)

    tray = make_tray(native, on_open=open_panel, on_exit=lambda: exits.append(1))
    tray.start()
    api.notify(api.WM_LBUTTONDBLCLK)
    assert entered.wait(1)
    try:
        tray.stop(timeout=1)
        assert not tray.running and not api.windows and not api.shell_icons and not exits
    finally:
        release.set()
        tray.stop()


def test_explicit_exit_callback_can_stop_tray_without_deadlocking(native):
    api, _ = native
    exited = threading.Event()

    def exit_app():
        tray.stop(timeout=1)
        exited.set()

    tray = make_tray(native, on_exit=exit_app)
    try:
        tray.start()
        api.selected_label = '完全退出'
        api.notify(api.WM_RBUTTONUP)
        assert exited.wait(2)
        assert not tray.running and not api.shell_icons
    finally:
        tray.stop()


def test_menu_failure_releases_menu_and_keeps_native_loop_alive(native):
    api, _ = native
    reports = []
    tray = make_tray(native, on_error=reports.append)
    try:
        tray.start()
        api.menu_error = OSError('PRIVATE_MENU_ERROR')
        api.notify(api.WM_RBUTTONUP)
        eventually(lambda: reports)
        assert not api.menus and tray.running and api.shell_icons
    finally:
        tray.stop()


def test_default_icon_is_bundled_approved_app_icon(native):
    from xiaomao.tray import TrayIcon
    api, _ = native
    tray = TrayIcon(on_open=lambda: None, on_pause=lambda: None, on_exit=lambda: None, win32=api)
    try:
        tray.start()
        assert Path(api.loaded_paths[0]) == Path(__file__).resolve().parents[1] / 'xiaomao' / 'ui_assets' / 'app.ico'
    finally:
        tray.stop()
