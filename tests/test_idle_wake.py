"""Activation may precede history navigation, but must never edit an unsafe chat."""
from types import SimpleNamespace

import pytest


def test_cloaked_window_waits_without_activating_or_restoring(monkeypatch):
    from xiaomao import windows
    from test_vision import binding
    d=windows.WindowsDesktop(binding());calls=[]
    monkeypatch.setattr(windows,'window_cloaked',lambda hwnd:True,raising=False)
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    d._bound_details=lambda:{'pid':d.calibration.pid}
    d.input_generation=lambda:100;d.idle_for=lambda:30
    monkeypatch.setattr(windows,'U',SimpleNamespace(GetForegroundWindow=lambda:99,
        IsIconic=lambda h:False,IsWindowVisible=lambda h:True,
        SetForegroundWindow=lambda h:calls.append(('activate',h))))
    assert not d.try_idle_wake(10)
    assert calls==[]
    assert '桌面' in d.wake_reason and '隐藏' in d.wake_reason
    assert '桌面' in d.route_state()


@pytest.mark.parametrize('fault', ['', 'unsafe', 'wrong_chat', 'draft', 'popup', 'disabled', 'user_input', 'moved'])
def test_detached_history_can_activate_without_bypassing_editor_guards(monkeypatch, fault):
    import win32gui
    from xiaomao import windows
    from test_vision import binding

    b = binding()
    b.window_kind = 'detached'
    b.target_group = '示例会话'
    desktop = windows.WindowsDesktop(b)
    events, frames = [], []
    info = {'rect': (100, 100, 900, 700)}

    def read(image, calibration):
        frames.append(image)
        return SimpleNamespace(safe=fault != 'unsafe',
                               group='其他会话' if fault == 'wrong_chat' else b.target_group,
                               at_bottom=False, draft_empty=fault != 'draft')

    desktop._wake_vision = SimpleNamespace(read_controls=read)
    monkeypatch.setattr(desktop, 'capture', lambda: object())
    monkeypatch.setattr(desktop, '_bound_details', lambda: info)
    monkeypatch.setattr(windows, 'U', SimpleNamespace(GetLastActivePopup=lambda h: -1 if fault == 'popup' else h))
    monkeypatch.setattr(win32gui, 'IsWindowEnabled', lambda h: fault != 'disabled')
    def to_client(h, point):
        if fault == 'moved':
            # The final geometry check must still match the click coordinates.
            desktop._bound_details = lambda: {'rect': (101, 100, 901, 700)}
        return (400, 520)
    monkeypatch.setattr(win32gui, 'ScreenToClient', to_client)
    monkeypatch.setattr(win32gui, 'SendMessageTimeout', lambda *args: events.append(args))
    desktop._activate_idle_editor(lambda: fault != 'user_input' or len(frames) < 2)
    if fault:
        assert events == []
    else:
        assert len(frames) == 2
        assert [event[1] for event in events] == [0x201, 0x202]
        assert all(event[0] == b.hwnd for event in events)
        # Only a mouse click; text input and send remain behind Engine checks.


@pytest.mark.parametrize('fault',['','client','client_unproved','busy','button','popup','covered','moved','held','racing_input'])
def test_idle_caption_fallback_requires_a_real_title_bar_and_keeps_user_input_priority(monkeypatch,fault):
    import win32gui
    from xiaomao import windows
    from test_vision import binding
    b=binding();b.window_kind='detached'
    d=windows.WindowsDesktop(b);info={'rect':(100,100,900,700),'pid':b.pid}
    generation=[100];front=[99];calls=[]
    d._bound_details=lambda:dict(info)
    d.input_generation=lambda:generation[0];d.idle_for=lambda:30
    d._dpi_scale=lambda:1;d.is_foreground=lambda:front[0]==b.hwnd
    if fault=='client':
        import time
        d._wake_caption=(400,16,800,600,time.monotonic())
    monkeypatch.setattr(windows,'K',SimpleNamespace(GetTickCount64=lambda:500))
    def raise_window(*args):
        calls.append('raise')
        if fault=='moved':info['rect']=(101,100,901,700)
    monkeypatch.setattr(win32gui,'SetWindowPos',raise_window)
    monkeypatch.setattr(win32gui,'SendMessageTimeout',lambda *args:(1,20 if fault=='button' else 1 if fault.startswith('client') else 2))
    monkeypatch.setattr(win32gui,'IsWindowEnabled',lambda h:True)
    monkeypatch.setattr(win32gui,'GetWindowLong',lambda *a:0)
    def send(count,events,size):
        calls.append('click')
        assert [e.value.mouse.flags for e in events]==[2,4]
        assert all(e.value.mouse.time==500 for e in events)
        generation[0]=501 if fault=='racing_input' else 500;front[0]=b.hwnd
        return count
    api=SimpleNamespace(GetLastActivePopup=lambda h:88 if fault=='popup' else h,
                        GetAsyncKeyState=lambda k:0x8000 if fault=='held' else 0,
                        WindowFromPoint=lambda p:77 if fault=='covered' else b.hwnd,
                        GetAncestor=lambda h,f:h,SetCursorPos=lambda x,y:True,SendInput=send)
    monkeypatch.setattr(windows,'U',api)
    result=d._click_idle_caption(lambda:fault!='busy')
    assert result==(500 if fault in ('','client') else None)
    if fault not in ('','client','racing_input'):assert 'click' not in calls
    if fault not in ('','client'):assert d.navigation.generation is None
    else:assert d.navigation.generation==500 and d.navigation.idle(500,0)>=30


@pytest.mark.parametrize('fault',['','input_during_raise','still_covered','send_failure'])
def test_idle_caption_can_surface_behind_foreground_lock_and_restores_z_order(monkeypatch,fault):
    """Ordinary HWND_TOP can be denied; a brief no-activate lift is reversible."""
    import time
    import win32gui
    from xiaomao import windows
    from test_vision import binding
    b=binding();b.window_kind='detached'
    d=windows.WindowsDesktop(b)
    generation=[100];front=[99];topmost=[False];clicks=[];positions=[]
    d._bound_details=lambda:{'rect':(100,100,900,700),'pid':b.pid}
    d.input_generation=lambda:generation[0];d.idle_for=lambda:30
    d._dpi_scale=lambda:1;d.is_foreground=lambda:front[0]==b.hwnd
    d._wake_caption=(400,16,800,600,time.monotonic())
    monkeypatch.setattr(win32gui,'IsWindowEnabled',lambda h:True)
    monkeypatch.setattr(win32gui,'GetWindowLong',lambda h,key:8 if topmost[0] else 0)
    monkeypatch.setattr(win32gui,'SendMessageTimeout',lambda *a:(1,1))
    def position(hwnd,after,x,y,w,h,flags):
        assert hwnd==b.hwnd and flags&0x13==0x13
        positions.append(after)
        if after==-1:
            topmost[0]=True
            if fault=='input_during_raise':generation[0]=101
        elif after==-2:topmost[0]=False
    monkeypatch.setattr(win32gui,'SetWindowPos',position)
    def send(count,events,size):
        assert topmost[0] and generation[0]==100
        clicks.append(count)
        if fault=='send_failure':return 0
        front[0]=b.hwnd;generation[0]=500
        return count
    monkeypatch.setattr(windows,'K',SimpleNamespace(GetTickCount64=lambda:500))
    monkeypatch.setattr(windows,'U',SimpleNamespace(GetLastActivePopup=lambda h:h,
        GetAsyncKeyState=lambda k:0,GetAncestor=lambda h,f:h,
        WindowFromPoint=lambda p:b.hwnd if topmost[0] and fault!='still_covered' else 99,
        SetCursorPos=lambda x,y:True,SendInput=send))
    result=d._click_idle_caption(lambda:generation[0]==100)
    assert result==(500 if not fault else None)
    assert positions[-1]==-2 and not topmost[0]
    assert bool(clicks)==(fault in ('','send_failure'))
    assert front[0]==(b.hwnd if not fault else 99)


def test_qt_caption_point_requires_plain_pixels_away_from_native_controls():
    from PIL import Image,ImageDraw
    from xiaomao.layout import idle_caption_point
    image=Image.new('RGB',(600,720),(237,237,237))
    assert idle_caption_point(image,1)==(300,16)
    ImageDraw.Draw(image).rectangle((295,12,305,20),fill='black')
    assert idle_caption_point(image,1) is None


def test_shared_main_can_use_verified_caption_after_foreground_request_denied(monkeypatch):
    from xiaomao import windows
    from test_vision import binding
    d=windows.WindowsDesktop(binding());front=[99];calls=[]
    d._bound_details=lambda:{'pid':d.calibration.pid}
    d.input_generation=lambda:100;d.idle_for=lambda:30
    d._prepare_idle_caption=lambda valid:calls.append('proof') or True
    def caption(valid):
        assert valid();calls.append('caption');front[0]=d.calibration.hwnd;return 100
    d._click_idle_caption=caption
    monkeypatch.setattr(windows,'window_cloaked',lambda h:False)
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'U',SimpleNamespace(GetForegroundWindow=lambda:front[0],
        IsIconic=lambda h:False,IsWindowVisible=lambda h:True,
        SetForegroundWindow=lambda h:calls.append('request')))
    import win32process
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda h:(1,42))
    assert d.try_idle_wake(2)
    assert calls==['request','proof','caption']


def test_private_window_can_use_verified_caption_when_background_editor_cannot_be_proved(monkeypatch):
    from PIL import Image, ImageDraw
    import win32gui
    import win32process
    from xiaomao import windows
    from test_vision import binding

    b = binding()
    b.window_kind = 'detached'
    b.target_kind = 'private'
    desktop = windows.WindowsDesktop(b)
    foreground = [99]
    events = []
    screenshot = Image.new('RGB', (b.width, b.height), (237, 237, 237))
    ImageDraw.Draw(screenshot).rectangle(b.rect('input'), fill='gray')
    desktop.capture = lambda: screenshot
    desktop._bound_details = lambda: {'pid': b.pid, 'kind': 'detached'}
    desktop.input_generation = lambda: 100
    desktop.idle_for = lambda: 30
    desktop._dpi_scale = lambda: 1
    desktop._wake_vision = SimpleNamespace(read_controls=lambda *_: SimpleNamespace(
        safe=True, group=b.target_group, draft_empty=False))

    def click_caption(valid):
        assert valid()
        assert desktop._wake_caption[:4] == (b.width // 2, 16, b.width, b.height)
        events.append('caption')
        foreground[0] = b.hwnd
        return 100

    desktop._click_idle_caption = click_caption
    monkeypatch.setattr(windows, 'window_cloaked', lambda hwnd: False)
    monkeypatch.setattr(windows, 'input_desktop_available', lambda: True)
    monkeypatch.setattr(windows, 'U', SimpleNamespace(
        GetForegroundWindow=lambda: foreground[0], GetLastActivePopup=lambda hwnd: hwnd,
        IsIconic=lambda hwnd: False, IsWindowVisible=lambda hwnd: True,
        SetForegroundWindow=lambda hwnd: events.append('request')))
    monkeypatch.setattr(win32gui, 'IsWindowEnabled', lambda hwnd: True)
    monkeypatch.setattr(win32gui, 'SendMessageTimeout', lambda *args: pytest.fail('background editor clicked'))
    monkeypatch.setattr(win32process, 'GetWindowThreadProcessId', lambda hwnd: (1, 42))

    assert desktop.try_idle_wake(2)
    assert events == ['request', 'caption']


def test_main_caption_proof_never_clicks_or_requires_current_chat_to_match(monkeypatch):
    from PIL import Image
    from xiaomao import windows
    from test_vision import binding
    d=windows.WindowsDesktop(binding());calls=[]
    d.capture=lambda:calls.append('capture') or Image.new('RGB',(800,600),(237,237,237))
    d._dpi_scale=lambda:1
    d._bound_details=lambda:{'pid':d.calibration.pid,'kind':'main'}
    monkeypatch.setattr(windows,'U',SimpleNamespace(GetLastActivePopup=lambda h:h))
    import win32gui
    monkeypatch.setattr(win32gui,'IsWindowEnabled',lambda h:True)
    assert d._prepare_idle_caption(lambda:True)
    assert calls==['capture','capture'] and d._wake_caption[:4]==(400,16,800,600)

