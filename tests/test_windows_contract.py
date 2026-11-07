import pytest


@pytest.mark.parametrize('height,fits',[(45,False),(90,True),(200,True)])
def test_sticker_editor_capacity_checked_before_pasting(monkeypatch,height,fits):
    import io
    from PIL import Image
    from types import SimpleNamespace
    from xiaomao.windows import WindowsDesktop
    buffer=io.BytesIO();Image.new('RGB',(120,120),'blue').save(buffer,format='GIF')
    # Keep the entire editor at the requested height, including its footer.
    d=WindowsDesktop(SimpleNamespace(height=height+8,
        rect=lambda name:(0,0,400,height) if name=='input' else (420,height-30,480,height)))
    monkeypatch.setattr(d,'_dpi_scale',lambda:1)
    sticker=SimpleNamespace(verified_bytes=lambda:buffer.getvalue())
    assert d.sticker_preview_fits(sticker) is fits


@pytest.mark.parametrize('text,started',[('正常回复',False),('前文@后文',True)])
def test_clipboard_write_contention_only_retries_when_no_editor_input(monkeypatch,text,started):
    from types import SimpleNamespace
    from xiaomao import windows,clipboard
    from test_clipboard import PasteClipboard
    c=PasteClipboard();events=[];original=c.replace_text
    def replace_text(value,sequence):
        if not started or events:raise clipboard.ClipboardUnavailable('busy')
        return original(value,sequence)
    c.replace_text=replace_text
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:c)
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    desktop.cancelled=SimpleNamespace(wait=lambda seconds:False)
    monkeypatch.setattr(desktop,'assert_input_focus',lambda:None)
    monkeypatch.setattr(desktop,'_shortcut',lambda *args:events.append('paste'))
    monkeypatch.setattr(desktop,'_type_unicode',lambda text:events.append('@'))
    with pytest.raises(windows.DesktopUnavailable) as error:desktop.type_text(text)
    assert isinstance(error.value,windows.DesktopInputNotStarted) is (not started)
    assert bool(events) is started


@pytest.mark.parametrize('layout,editing,changed_identity',[(True,False,False),(False,False,False),(True,True,False),(True,False,True)])
def test_resized_window_rebinds_only_complete_layout_before_edit(monkeypatch,layout,editing,changed_identity):
    from types import SimpleNamespace
    from PIL import Image
    from xiaomao import windows
    from test_layout import old_binding,layout_image
    binding=old_binding();desktop=windows.WindowsDesktop(binding)
    desktop._input_cookie='editing' if editing else None
    info={'hwnd':1,'pid':2 if changed_identity else 1,'width':1024,'height':720,'rect':(0,0,1024,720)}
    monkeypatch.setattr(windows,'window_details',lambda hwnd:info)
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'U',SimpleNamespace(IsIconic=lambda hwnd:False))
    pixels=layout_image(divider=312,toolbar=514,width=1024,height=720) if layout else Image.new('RGB',(1024,720),'white')
    monkeypatch.setattr(windows,'capture_window',lambda *args:pixels)
    monkeypatch.setattr(desktop,'_dpi_scale',lambda:1)
    if layout and not editing and not changed_identity:
        assert desktop._background_binding()==info
        assert desktop.calibration.width==1024 and desktop.calibration.rect('messages')[0]==314
        assert binding.width==1431
    else:
        with pytest.raises(windows.DesktopUnavailable):desktop._background_binding()
        assert desktop.calibration is binding


def test_no_binding_means_no_input():
    from xiaomao.windows import WindowsDesktop, DesktopUnavailable
    desktop = WindowsDesktop(None)
    with pytest.raises(DesktopUnavailable):
        desktop.click_input()


def test_background_window_is_never_raised_even_after_a_long_idle(monkeypatch):
    from xiaomao import windows
    from test_vision import binding
    desktop=windows.WindowsDesktop(binding())
    class Api:
        def GetForegroundWindow(self):return 2
        def SetForegroundWindow(self,*args):pytest.fail('a reply must never activate WeChat')
        def SetWindowPos(self,*args):pytest.fail('do not raise the background window')
        def WindowFromPoint(self,*args):return 2
        def GetAncestor(self,*args):return 2
        def SendInput(self,*args):raise AssertionError('目标被遮挡时不得输入')
    monkeypatch.setattr(windows,'U',Api())
    monkeypatch.setattr(desktop,'_background_binding',lambda:{'rect':(0,0,800,600)})
    monkeypatch.setattr(desktop,'input_generation',lambda:100);monkeypatch.setattr(desktop,'idle_for',lambda:60)
    with pytest.raises(windows.DesktopTemporarilyHidden):desktop.prepare_send()


def test_minimized_bound_window_is_never_restored_for_capture(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows
    desktop=windows.WindowsDesktop(SimpleNamespace(hwnd=1,pid=1,width=800,height=600))
    api=SimpleNamespace(IsIconic=lambda hwnd:True,ShowWindow=lambda *args:pytest.fail('do not restore minimized WeChat'))
    monkeypatch.setattr(windows,'U',api)
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'window_details',lambda hwnd:{'pid':1,'width':199,'height':34})
    with pytest.raises(windows.DesktopTemporarilyHidden):desktop._background_binding()


@pytest.mark.parametrize('case,expected',[('allowed',['restore','focus']),('new_input',['restore']),
                                           ('denied',['restore','focus']),('wrong_pid',[])])
def test_opt_in_wake_checks_identity_input_and_os_result_without_repeated_activation(monkeypatch,case,expected):
    from xiaomao import windows
    from test_vision import binding
    b=binding();d=windows.WindowsDesktop(b)
    events=[];state={'foreground':222,'minimized':True,'generation':111}
    class Api:
        def GetForegroundWindow(self):return state['foreground']
        def IsIconic(self,h):return state['minimized']
        def IsWindowVisible(self,h):return True
        def ShowWindowAsync(self,h,mode):
            assert h==b.hwnd and mode==4
            events.append('restore');state['minimized']=False
            if case=='new_input':state['generation']+=1
        def SetForegroundWindow(self,h):
            assert h==b.hwnd
            events.append('focus')
            if case=='allowed':state['foreground']=h
    monkeypatch.setattr(windows,'U',Api())
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'_check_calibration_lease',lambda _:None)
    monkeypatch.setattr(d,'input_generation',lambda:state['generation'])
    monkeypatch.setattr(d,'idle_for',lambda:30)
    monkeypatch.setattr(d,'_bound_details',lambda:{'pid':-1 if case=='wrong_pid' else b.pid})
    # This contract isolates OS activation; pixel/caption fallback has its
    # separate two-frame and input-generation regression tests.
    monkeypatch.setattr(d,'_prepare_idle_caption',lambda valid:False)
    assert d.try_idle_wake(10)==(case=='allowed')
    assert events==expected
    if case=='allowed':
        # Successful delivery is not a denied activation. Its next bubble may
        # activate after the configured pacing rather than waiting 20 seconds.
        state['foreground']=222
        assert d.try_idle_wake(10)
        assert events==expected+['focus']
    if case!='allowed':
        assert not d.try_idle_wake(10)
        assert events==expected


@pytest.mark.parametrize('touched',[False,True])
def test_verified_wake_returns_focus_and_minimizes_only_while_still_idle(monkeypatch,touched):
    import win32process
    from xiaomao import windows
    from test_vision import binding
    from types import SimpleNamespace
    b=binding();d=windows.WindowsDesktop(b)
    d._return_window=(888,40,True);d._sent_generation=100
    actions=[];foreground=[b.hwnd]
    api=SimpleNamespace(IsWindow=lambda h:True,IsWindowVisible=lambda h:True,
        SetForegroundWindow=lambda h:(actions.append(('focus',h)),foreground.__setitem__(0,h)),
        GetForegroundWindow=lambda:foreground[0],ShowWindowAsync=lambda h,n:actions.append(('minimize',h,n)))
    monkeypatch.setattr(windows,'U',api)
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda h:(100,40))
    monkeypatch.setattr(d,'input_generation',lambda:101 if touched else 100)
    d.finish_input()
    assert actions==([] if touched else [('focus',888),('minimize',b.hwnd,7)])
    assert d._return_window is None


def test_return_focus_while_awaiting_receipt_keeps_editor_transaction_guard(monkeypatch):
    import win32process
    from xiaomao import windows
    from test_vision import binding
    from types import SimpleNamespace
    d=windows.WindowsDesktop(binding())
    cookie=object();d._input_cookie=cookie
    d._return_window=(888,40,False);d._sent_generation=100
    front=[d.calibration.hwnd];actions=[]
    def focus(h):actions.append(h);front[0]=h
    monkeypatch.setattr(windows,'U',SimpleNamespace(IsWindow=lambda h:True,
        IsWindowVisible=lambda h:True,SetForegroundWindow=focus,
        GetForegroundWindow=lambda:front[0]))
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda h:(1,40))
    d.input_generation=lambda:100
    d.release_send_focus()
    assert front[0]==888 and d._input_cookie is cookie
    d.finish_input()
    assert actions==[888] and d._input_cookie is None


def test_observation_capture_uses_bound_window_without_foreground_check(monkeypatch):
    from types import SimpleNamespace
    from PIL import Image
    from xiaomao import windows
    desktop=windows.WindowsDesktop(SimpleNamespace(hwnd=1,width=800,height=600,pid=2))
    monkeypatch.setattr(desktop,'_background_binding',lambda:{'hwnd':1,'width':800,'height':600},raising=False)
    def forbidden():raise AssertionError('观察不应要求前台')
    monkeypatch.setattr(desktop,'check',forbidden)
    monkeypatch.setattr(windows,'capture_window',lambda hwnd,w,h:Image.new('RGB',(w,h),'white'),raising=False)
    assert desktop.capture().size==(800,600)


def test_prepare_send_does_not_absorb_manual_input_during_final_check(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows
    class Api:
        def GetForegroundWindow(self):return 1
    monkeypatch.setattr(windows,'U',Api())
    desktop=windows.WindowsDesktop(SimpleNamespace(hwnd=1))
    generation=[100]
    monkeypatch.setattr(desktop,'_background_binding',lambda:None)
    monkeypatch.setattr(desktop,'idle_for',lambda:60)
    monkeypatch.setattr(desktop,'input_generation',lambda:generation[0])
    def check():generation[0]+=1
    monkeypatch.setattr(desktop,'check',check)
    with pytest.raises(windows.DesktopTemporarilyHidden,match='人工输入'):
        desktop.prepare_send()


def test_prepare_send_returns_verified_input_generation(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows
    class Api:
        def GetForegroundWindow(self):return 1
    monkeypatch.setattr(windows,'U',Api())
    desktop=windows.WindowsDesktop(SimpleNamespace(hwnd=1))
    monkeypatch.setattr(desktop,'_background_binding',lambda:None)
    monkeypatch.setattr(desktop,'idle_for',lambda:60)
    monkeypatch.setattr(desktop,'input_generation',lambda:100)
    monkeypatch.setattr(desktop,'check',lambda:None)
    assert desktop.prepare_send()==100


@pytest.mark.parametrize('fault',['none','old_copy','consumed','background','moved','manual','editor_changed','black','expired'])
def test_draft_corroboration_requires_current_bound_visible_capture(monkeypatch,fault):
    from PIL import Image,ImageDraw
    from test_vision import overlay_pair
    from xiaomao import windows
    from xiaomao.vision import Vision,TextLine
    visible,painted,b=overlay_pair()
    desktop=windows.WindowsDesktop(b)
    info={'hwnd':1,'pid':1,'rect':(5,10,805,610),'width':800,'height':600}
    generation=[12];now=[100.];current=[visible]
    desktop._background_binding=desktop.check=lambda:dict(info)
    desktop.is_foreground=lambda:True
    desktop._dpi_scale=lambda:1
    desktop.input_generation=lambda:generation[0]
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows.time,'monotonic',lambda:now[0])
    monkeypatch.setattr('PIL.ImageGrab.grab',lambda **kw:current[0].copy())
    frame=desktop.capture()
    if fault=='old_copy':frame=frame.copy()
    if fault=='background':desktop.is_foreground=lambda:False
    if fault=='expired':now[0]+=30
    def print_now(*args,**kwargs):
        if fault=='manual':generation[0]+=1
        if fault=='moved':info['rect']=(6,10,806,610)
        if fault=='editor_changed':
            current[0]=visible.copy()
            ImageDraw.Draw(current[0]).text((35,518),'someone typing',fill='black')
        return Image.new('RGB',visible.size) if fault=='black' else painted
    monkeypatch.setattr(windows,'capture_window',print_now)
    vision=Vision(lambda image:[TextLine('示例会话 (2)',(5,5,88,22),.99)] if image.size==(800,60) else [])
    if fault=='consumed':assert desktop.confirm_empty_editor(frame,vision) is painted
    result=desktop.confirm_empty_editor(frame,vision)
    assert (result is painted) if fault=='none' else (result is None)


@pytest.mark.parametrize('phase',['moves','stays','draft'])
def test_one_bounded_live_motion_sample_can_resolve_equal_gif_frames(monkeypatch,phase):
    from PIL import Image,ImageDraw
    import numpy as np
    from test_vision import overlay_pair
    from xiaomao import windows
    from xiaomao.vision import Vision,TextLine
    visible,painted,b=overlay_pair()
    later=visible.copy()
    yy,xx=np.indices((64,110))
    def animate(image,offset):
        pixels=np.stack([(xx+2*yy)%130+22,(2*xx+yy)%150+32,(xx+3*yy)%140+40],axis=2)+offset
        image.paste(Image.fromarray(pixels.astype('uint8')),(411,211))
    animate(visible,0);animate(painted,12);animate(later,25 if phase=='moves' else 0)
    if phase=='draft':ImageDraw.Draw(later).text((22,515),'draft',fill='black')
    shots=[visible,visible,later]
    d=windows.WindowsDesktop(b)
    d._background_binding=d.check=lambda:{'rect':(1,2,801,602)}
    d._dpi_scale=lambda:1;d.is_foreground=lambda:True;d.input_generation=lambda:12
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'capture_window',lambda *args:painted)
    monkeypatch.setattr('PIL.ImageGrab.grab',lambda **kwargs:shots.pop(0).copy())
    monkeypatch.setattr(d.cancelled,'wait',lambda timeout:False)
    vision=Vision(lambda image:[TextLine('示例会话 (2)',(2,2,88,22),.99)] if image.size==(800,60) else [])
    result=d.confirm_empty_editor(d.capture(),vision)
    assert not shots
    assert (result is painted) if phase=='moves' else (result is None)


def test_duplicate_sidebar_targets_are_rejected_even_when_group_already_open(monkeypatch):
    from xiaomao import windows
    from xiaomao.vision import TextLine
    from test_vision import binding
    from PIL import Image
    b=binding();b.regions['messages'][0]=.279
    desktop=windows.WindowsDesktop(b)
    desktop._sidebar_ocr=lambda image:[TextLine('示例会话',(0,0,120,20),.99),TextLine('示例会话',(0,50,120,70),.99)]
    with pytest.raises(windows.DesktopUnavailable,match='同名'):
        desktop.validate_target(Image.new('RGB',(800,600)))


def test_first_recent_row_is_visible_and_timestamp_does_not_hide_group():
    from PIL import Image
    from xiaomao.windows import WindowsDesktop
    from xiaomao.vision import TextLine
    from test_layout import old_binding
    desktop=WindowsDesktop(old_binding())
    def rows(crop):
        # The first title is at y=88; the old 9%-height crop cut it away.
        return [TextLine('示例成员交流讨论分享群 09:13',(40,12,240,32),.99)] if crop.height>990 else []
    desktop._sidebar_ocr=rows
    region,found=desktop._find_target(Image.new('RGB',(1431,1102)))
    assert region[1]<88 and len(found)==1


def test_sidebar_preview_containing_name_does_not_match_target():
    from PIL import Image
    from xiaomao.windows import WindowsDesktop
    from xiaomao.vision import TextLine
    from test_layout import old_binding
    desktop=WindowsDesktop(old_binding())
    desktop._sidebar_ocr=lambda crop:[TextLine('示例成员交流讨论分享群怎么了',(40,12,240,32),.99)]
    assert not desktop._find_target(Image.new('RGB',(1431,1102)))[1]


def test_navigation_idle_credit_is_revoked_by_new_manual_input():
    from xiaomao.windows import NavigationActivity
    activity=NavigationActivity();activity.note(100,60)
    assert activity.idle(100,0)>=60
    assert activity.idle(101,0)==0


def test_latest_navigation_does_not_click_ordinary_outgoing_text(monkeypatch):
    from PIL import Image,ImageDraw
    from xiaomao import windows
    from xiaomao.vision import TextLine
    from test_vision import binding
    b=binding();b.regions['messages'][0]=.279
    image=Image.new('RGB',(800,600),(237,237,237));draw=ImageDraw.Draw(image)
    left,top,right,bottom=b.rect('messages');width=right-left;height=bottom-top
    draw.rounded_rectangle((right-170,bottom-65,right-25,bottom-25),5,fill=(149,236,105))
    draw.polygon([(right-26,bottom-55),(right-18,bottom-50),(right-26,bottom-45)],fill=(149,236,105))
    desktop=windows.WindowsDesktop(b)
    desktop._sidebar_ocr=lambda im:[TextLine('示例会话(2)',(0,0,180,30),.99)] if im.size==image.crop(b.rect('header')).size else [TextLine('16条新消息',(width-158,height-57,width-30,height-34),.99)]
    monkeypatch.setattr(desktop,'idle_for',lambda:60);monkeypatch.setattr(desktop,'prepare_send',lambda seconds:100)
    monkeypatch.setattr(desktop,'input_generation',lambda:100);monkeypatch.setattr(desktop,'capture',lambda:image)
    monkeypatch.setattr(desktop,'validate_target',lambda im:None);monkeypatch.setattr(desktop,'check',lambda:{'rect':(0,0,800,600)})
    clicks=[]
    class Api:
        def WindowFromPoint(self,point):return 1
        def GetAncestor(self,hwnd,flag):return 1
        def SetCursorPos(self,x,y):clicks.append((x,y));return 1
        def SendInput(self,count,*args):return count
    monkeypatch.setattr(windows,'U',Api())
    assert not desktop.go_to_latest() and clicks==[]


@pytest.mark.parametrize('manual_input',[False,True])
def test_latest_navigation_scrolls_visible_history_bar_without_badge(monkeypatch,manual_input):
    from PIL import Image,ImageDraw
    from xiaomao import windows
    from xiaomao.vision import TextLine
    from test_vision import binding
    b=binding();image=Image.new('RGB',(800,600),(237,237,237))
    ImageDraw.Draw(image).rounded_rectangle((791,150,796,190),2,fill=(174,174,174))
    desktop=windows.WindowsDesktop(b)
    desktop._sidebar_ocr=lambda im:[TextLine('示例会话(2)',(0,0,180,30),.99)] if im.size==image.crop(b.rect('header')).size else []
    monkeypatch.setattr(desktop,'idle_for',lambda:60);monkeypatch.setattr(desktop,'prepare_send',lambda seconds:100)
    generation=[100]
    monkeypatch.setattr(desktop,'input_generation',lambda:generation[0]);monkeypatch.setattr(desktop,'capture',lambda:image)
    monkeypatch.setattr(desktop,'validate_target',lambda im:None);monkeypatch.setattr(desktop,'check',lambda:{'rect':(0,0,800,600)})
    events=[];moves=[]
    class Pause:
        def wait(self,seconds):
            if manual_input:generation[0]+=1
    desktop.cancelled=Pause()
    class Api:
        def WindowFromPoint(self,point):return 1
        def GetAncestor(self,hwnd,flag):return 1
        def SetCursorPos(self,x,y):moves.append((x,y));return 1
        def SendInput(self,count,values,size):
            events.extend(values[i].value.mouse.flags for i in range(count));return count
    monkeypatch.setattr(windows,'U',Api())
    if manual_input:
        with pytest.raises(windows.DesktopUnavailable,match='人工输入'):desktop.go_to_latest()
        assert len(moves)==1 and desktop.navigation.generation is None
    else:
        assert desktop.go_to_latest() and len(moves)==2
    assert events==[0x2,0x4]  # 异常时也必须释放鼠标。


def test_initial_position_check_reveals_scrollbar_without_clicking_body(monkeypatch):
    from xiaomao import windows
    from test_vision import binding
    desktop=windows.WindowsDesktop(binding());moves=[];events=[]
    monkeypatch.setattr(desktop,'idle_for',lambda:60);monkeypatch.setattr(desktop,'prepare_send',lambda seconds:100)
    monkeypatch.setattr(desktop,'input_generation',lambda:100);monkeypatch.setattr(desktop,'check',lambda:{'rect':(0,0,800,600)})
    class Api:
        def WindowFromPoint(self,point):return 1
        def GetAncestor(self,hwnd,flag):return 1
        def SetCursorPos(self,x,y):moves.append((x,y));return 1
        def SendInput(self,count,values,size):events.extend(values[i].value.mouse.flags for i in range(count));return count
    monkeypatch.setattr(windows,'U',Api())
    assert desktop.prepare_baseline() and moves==[(400,255)] and events==[0x800]


def test_minimized_window_does_not_report_false_recalibration_requirement(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows
    class MinimizedApi:
        def IsIconic(self,hwnd):return True
    monkeypatch.setattr(windows,'U',MinimizedApi())
    monkeypatch.setattr(windows,'window_details',lambda hwnd:{'pid':1,'width':199,'height':34})
    desktop=windows.WindowsDesktop(SimpleNamespace(hwnd=1,pid=1,width=1146,height=882))
    with pytest.raises(windows.DesktopUnavailable,match='前台可见'):
        desktop.check()


def test_unicode_input_units_preserve_emoji_but_reject_controls():
    from xiaomao.windows import unicode_units
    assert unicode_units("喵🙂") == [0x55b5, 0xd83d, 0xde42]
    for text in ("你好\n", "\r", "\t", "\x00"):
        with pytest.raises(ValueError):
            unicode_units(text)


@pytest.mark.parametrize('text',['@小玲你好','前面的内容刚好占满十五个字符喵@后续','test@example.com，喵🙂 @@','最后一个字符@','普通正文🙂'])
def test_literal_at_cancels_picker_before_more_text_or_focus_check(monkeypatch,text):
    from xiaomao import windows
    # Model the native editor boundary: @ opens a picker, Escape cancels it.
    # Any later text while open would select/filter members instead of editing.
    class Editor:
        popup=False
        units=[]
        def SendInput(self,count,events,size):
            for event in events:
                key=event.value.keyboard
                if key.flags & 2:continue
                if key.flags & 4:
                    assert not self.popup, '文字被输入到了 @ 成员选择框'
                    self.units.append(key.scan)
                    if key.scan==ord('@'):self.popup=True
                else:
                    assert key.vk==0x1b, '不允许选中成员或发送消息'
                    assert self.popup
                    self.popup=False
            return count
    api=Editor();api.units=[]
    monkeypatch.setattr(windows,'U',api)
    desktop=windows.WindowsDesktop(None)
    def focus():assert not api.popup, '弹窗被当作遮挡，输入将卡住'
    monkeypatch.setattr(desktop,'assert_input_focus',focus)
    monkeypatch.setattr(desktop,'_acknowledge_own_input',lambda:None)
    desktop._type_unicode(text)
    assert not api.popup
    assert b''.join(unit.to_bytes(2,'little') for unit in api.units).decode('utf-16-le')==text


def test_literal_at_partial_input_is_never_retried(monkeypatch):
    from xiaomao import windows
    calls=[]
    class Api:
        def SendInput(self,count,events,size):calls.append(count);return count-1
    monkeypatch.setattr(windows,'U',Api())
    desktop=windows.WindowsDesktop(None)
    monkeypatch.setattr(desktop,'assert_input_focus',lambda:None)
    monkeypatch.setattr(desktop,'_acknowledge_own_input',lambda:None)
    with pytest.raises(windows.DesktopUnavailable):desktop._type_unicode('@小玲继续聊')
    assert len(calls)==1


def test_literal_at_never_dismisses_unrelated_popup_after_focus_loss(monkeypatch):
    from xiaomao import windows
    batches=[]
    class Api:
        def SendInput(self,count,events,size):batches.append(list(events));return count
    monkeypatch.setattr(windows,'U',Api())
    desktop=windows.WindowsDesktop(None)
    def focus():
        if batches:raise windows.DesktopTemporarilyHidden('用户切换窗口')
    monkeypatch.setattr(desktop,'assert_input_focus',focus)
    monkeypatch.setattr(desktop,'_acknowledge_own_input',lambda:None)
    with pytest.raises(windows.DesktopTemporarilyHidden):desktop._type_unicode('正常文字'*4+'@小玲')
    assert len(batches)==1
    assert all(event.value.keyboard.vk!=0x1b for event in batches[0])


def test_normal_text_uses_literal_paste_without_ime_character_substitution(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows,clipboard
    from test_clipboard import PasteClipboard
    c=PasteClipboard();output=[]
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    monkeypatch.setattr(windows,'U',SimpleNamespace(SendInput=lambda *args:pytest.fail('普通正文不得逐字模拟输入')))
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:c)
    monkeypatch.setattr(desktop,'assert_input_focus',lambda:None)
    desktop.cancelled=SimpleNamespace(wait=lambda seconds:False)
    def shortcut(*keys):
        assert keys==(0x11,0x56)
        assert '@' not in c.data
        output.append(c.data)
    monkeypatch.setattr(desktop,'_shortcut',shortcut)
    def unicode_input(text):
        assert text=='@'  # only @ uses the tested Unicode+Escape batch
        output.append('@')
    monkeypatch.setattr(desktop,'_type_unicode',unicode_input,raising=False)
    text='美元计价，20刀。@小玲，你们试过吗？🙂 @test@example.com'
    desktop.type_text(text)
    assert ''.join(output)==text and c.data=='原来的剪贴板'


def test_unreadable_clipboard_before_leading_at_does_not_edit_any_text(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows,clipboard
    from test_clipboard import PasteClipboard
    c=PasteClipboard();events=[]
    def snapshot():raise clipboard.ClipboardUnavailable('截图格式尚未就绪')
    c.snapshot=snapshot
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:c)
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    desktop.cancelled=SimpleNamespace(wait=lambda seconds:False)
    monkeypatch.setattr(desktop,'assert_input_focus',lambda:None)
    monkeypatch.setattr(desktop,'_shortcut',lambda *keys:events.append('paste'))
    monkeypatch.setattr(desktop,'_type_unicode',lambda text:events.append('type'))
    with pytest.raises(windows.DesktopInputNotStarted):desktop.type_text('@小玲，继续聊')
    assert not events


def test_rich_clipboard_is_untouched_while_plain_text_uses_guarded_unicode(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows,clipboard
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    parts=[]
    class Rich:
        def snapshot(self):raise clipboard.RichClipboard('保留办公文档剪贴板')
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:Rich())
    monkeypatch.setattr(desktop,'assert_input_focus',lambda:None)
    monkeypatch.setattr(desktop,'_type_unicode',parts.append)
    desktop.type_text('借到了，灯塔见')
    assert ''.join(parts)=='借到了，灯塔见'
    with pytest.raises(windows.DesktopInputNotStarted):desktop.type_text('第一行\n第二行')


def test_clipboard_failure_after_a_partial_reply_cannot_be_retried_as_no_input(monkeypatch):
    from types import SimpleNamespace
    from xiaomao import windows,clipboard
    from test_clipboard import PasteClipboard
    c=PasteClipboard();original=c.snapshot;events=[]
    def snapshot():
        if events:raise clipboard.ClipboardUnavailable('clipboard changed')
        return original()
    c.snapshot=snapshot
    monkeypatch.setattr(clipboard,'NativeClipboard',lambda pid:c)
    desktop=windows.WindowsDesktop(SimpleNamespace(pid=1))
    desktop.cancelled=SimpleNamespace(wait=lambda seconds:False)
    monkeypatch.setattr(desktop,'assert_input_focus',lambda:None)
    monkeypatch.setattr(desktop,'_shortcut',lambda *keys:events.append('paste'))
    monkeypatch.setattr(desktop,'_type_unicode',lambda text:events.append('@'))
    with pytest.raises(windows.DesktopUnavailable) as raised:desktop.type_text('前文@后文')
    assert not isinstance(raised.value,windows.DesktopInputNotStarted)
    assert events==['paste','@']


def test_pause_chord_works_without_exclusive_hotkey_registration(monkeypatch):
    import threading
    from xiaomao import windows
    class ExistingShortcutOwner:
        def RegisterHotKey(self, *args): return 0
        def GetAsyncKeyState(self, key): return 0x8000 if key in (0x11,0x12,0x50) else 0
    monkeypatch.setattr(windows, 'U', ExistingShortcutOwner())
    stop=threading.Event()
    reasons=[]
    def pause(reason):
        reasons.append(reason)
        stop.set()
    windows.hotkey_loop(stop,pause)
    assert reasons == ['用户通过 Ctrl+Alt+P 暂停']


def test_pause_chord_catches_fast_tap_and_ignores_unrelated_keys():
    from xiaomao.windows import PauseChord
    chord=PauseChord()
    assert not chord.update(0x41,True)
    assert not chord.update(0xA2,True)
    assert not chord.update(0xA4,True)
    assert chord.update(0x50,True)
    assert not chord.update(0x50,True)  # 键盘重复不反复暂停。
    assert not chord.update(0x50,False)
    assert chord.update(0x50,True)
    assert not chord.update(0xA2,False)
    assert not chord.update(0xA4,False)
    assert not chord.update(0x50,False)
    assert not chord.keys


def test_input_click_parks_pointer_outside_draft_before_readback(monkeypatch):
    from xiaomao import windows
    from xiaomao.config import Calibration
    class InputApi:
        position=None
        def SetCursorPos(self,x,y):
            self.position=(x,y)
            return 1
        def SendInput(self,count,events,size): return count
    api=InputApi()
    monkeypatch.setattr(windows,'U',api)
    b=Calibration(1,1,800,600,'示例会话(2)',{'header':[0,0,1,.1],'messages':[0,.1,1,.7],'input':[0,.8,.8,.95],'send':[.8,.95,1,1]},True)
    desktop=windows.WindowsDesktop(b)
    monkeypatch.setattr(desktop,'check',lambda:{'rect':(0,0,800,600)})
    monkeypatch.setattr(desktop,'_input_snapshot',lambda:(1,100))
    desktop.click_input()
    x,y=api.position
    assert not (0 <= x < 640 and 480 <= y < 570)
