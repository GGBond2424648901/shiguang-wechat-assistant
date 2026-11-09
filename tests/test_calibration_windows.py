"""Manual WeChat calibration does not grant background automation foreground privileges."""
from types import SimpleNamespace
import os
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

from xiaomao import windows, layout, vision
from test_vision import binding, screenshot


def fake_windows(monkeypatch):
    import win32gui
    # Native APIs are the boundary. Real discovery, window filtering and
    # capture use these records, including Qt helpers in the same process.
    records = {
        1: ('微信', 'Qt51514QWindowIcon', (900, 620), 0, False, True, 0),
        2: ('设计组', 'Qt51514QWindowIcon', (480, 620), 0, True, True, 0),
        3: ('微信', 'Qt51514QWindowIcon', (340, 380), 0, False, True, 0),
        4: ('预算.xlsx', 'Qt51514QWindowIcon', (900, 620), 0, False, True, 0),
        5: ('偏好设置', 'Qt51514QWindowIcon', (900, 620), 1, False, True, 0),
        6: ('微信', 'Chrome_WidgetWin_1', (900, 620), 0, False, True, 0),
        7: ('微信', 'Qt51514QWindowIcon', (900, 620), 0, False, True, 0),
        8: ('搜索', 'Qt51514QWindowToolSaveBits', (900, 620), 0, False, True, 0x80),
        9: ('Weixin', 'Qt51514QWindowIcon', (900, 620), 0, False, False, 0),
        10: ('图片查看器', 'Qt51514QWindowIcon', (900, 620), 0, False, True, 0),
        11: ('服务群', 'Qt51514QWindowIcon', (1100, 700), 0, False, True, 0),
    }

    def details(hwnd):
        if hwnd == 7 or hwnd not in records:
            raise windows.DesktopUnavailable('当前窗口不是微信 Weixin.exe')
        width, height = (160, 28) if records[hwnd][4] else records[hwnd][2]
        return dict(hwnd=hwnd, pid=100, width=width, height=height, rect=(0,0,width,height))

    monkeypatch.setattr(windows, '_process_window_details', details, raising=False)
    monkeypatch.setattr(windows, 'input_desktop_available', lambda: True)
    monkeypatch.setattr(win32gui, 'EnumWindows', lambda visit,arg: [visit(h,arg) for h in records])
    monkeypatch.setattr(win32gui, 'GetWindowText', lambda h: records[h][0])
    monkeypatch.setattr(win32gui, 'GetClassName', lambda h: records[h][1])
    monkeypatch.setattr(win32gui, 'GetWindow', lambda h,cmd: records[h][3])
    monkeypatch.setattr(win32gui, 'IsIconic', lambda h: records[h][4])
    monkeypatch.setattr(win32gui, 'IsWindowVisible', lambda h: records[h][5])
    monkeypatch.setattr(win32gui, 'GetWindowLong', lambda h,idx: records[h][6] if idx==-20 else 0x96c70000)
    monkeypatch.setattr(win32gui, 'GetWindowPlacement', lambda h:(0,2,(0,0),(0,0),(0,0,*records[h][2])))
    return records


def test_read_only_list_returns_distinct_chats_with_restored_dimensions(monkeypatch):
    fake_windows(monkeypatch)
    monkeypatch.setattr(windows, 'U', SimpleNamespace(GetDpiForWindow=lambda h:96))
    rows=windows.list_calibration_windows()
    assert {r['hwnd'] for r in rows} == {1,2,11}
    mini=next(r for r in rows if r['hwnd']==2)
    assert (mini['pid'],mini['kind'],mini['width'],mini['height'],mini['minimized'])==(100,'detached',480,620,True)
    assert next(r for r in rows if r['hwnd']==1)['kind']=='main'


def test_explicit_restore_verifies_pid_before_activation_and_checks_actual_foreground(monkeypatch):
    records = fake_windows(monkeypatch)
    events = []
    def restore(hwnd,cmd):
        assert hwnd == 2
        row=list(records[hwnd]);row[4]=False;records[hwnd]=tuple(row)
        events.append('restore')
        return 1
    def foreground(hwnd):
        events.append('activate')
        return 1  # Windows may still refuse to move focus.
    api=SimpleNamespace(ShowWindowAsync=restore,SetForegroundWindow=foreground,GetForegroundWindow=lambda:99)
    monkeypatch.setattr(windows,'U',api)
    with pytest.raises(windows.DesktopUnavailable):
        windows.activate_calibration_window(2,101)
    assert not events
    with pytest.raises(windows.DesktopUnavailable,match='前台'):
        windows.activate_calibration_window(2,100)
    assert events == ['restore','activate']
    api.GetForegroundWindow=lambda:2
    assert windows.activate_calibration_window(2,100)['hwnd']==2


def test_restore_waits_for_the_selected_window_to_finish_its_size_transition(monkeypatch):
    base=dict(hwnd=42,pid=100,kind='detached',title='设计组',width=480,height=620,minimized=False)
    states=iter([dict(base,minimized=True), None,base,base])
    def details(h,p):
        state=next(states)
        if state is None:raise windows.DesktopUnavailable('窗口尺寸正在恢复')
        return state
    monkeypatch.setattr(windows,'calibration_window_info',details)
    api=SimpleNamespace(ShowWindowAsync=lambda h,mode:True,GetForegroundWindow=lambda:42)
    monkeypatch.setattr(windows,'U',api)
    assert windows.activate_calibration_window(42,100)['width']==480


@pytest.mark.parametrize('changed_title', [False, True])
def test_desktop_calibration_requires_the_selected_window_but_not_foreground(tmp_path, monkeypatch, changed_title):
    from xiaomao import selection
    from xiaomao.desktop_calibration import DesktopCalibration

    records = fake_windows(monkeypatch)
    actions = []

    def restore(hwnd, command):
        actions.append(('restore', hwnd, command))
        row = list(records[hwnd])
        row[4] = False
        records[hwnd] = tuple(row)
        return True

    def activate(hwnd):
        actions.append(('foreground', hwnd))
        return False

    def render(hwnd, width, height):
        actions.append(('capture', hwnd))
        return Image.new('RGB', (width, height), 'white')

    def unrecognized(*_, **_kwargs):
        raise ValueError('测试中没有可识别的会话标题')

    monkeypatch.setattr(windows, 'U', SimpleNamespace(ShowWindowAsync=restore, GetForegroundWindow=lambda: 99,
                                                     SetForegroundWindow=activate))
    monkeypatch.setattr(windows, 'CalibrationWindowLease', lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(windows, 'capture_window', render)
    monkeypatch.setattr(selection, 'identify_reviewed_window', unrecognized)
    calibration = DesktopCalibration(tmp_path, SimpleNamespace())
    selected = next(row for row in calibration.discover()['windows'] if row['minimized'])

    try:
        if changed_title:
            records[2] = ('另一个会话', *records[2][1:])
            with pytest.raises(ValueError, match='窗口已改变'):
                calibration.capture(selected['id'])
            assert not actions
        else:
            preview = calibration.capture(selected['id'])
            assert preview['image'].startswith('data:image/jpeg;base64,')
            assert preview['identified'] is False
            assert (preview['width'], preview['height']) == (480, 620)
            assert actions == [('restore', 2, 9), ('foreground', 2), ('capture', 2)]
    finally:
        calibration.cancel()


def test_raw_capture_only_reads_requested_window_and_rechecks_identity(monkeypatch):
    records=fake_windows(monkeypatch)
    monkeypatch.setattr(windows,'U',SimpleNamespace())
    def paint(hwnd,w,h):
        assert (hwnd,w,h)==(11,1100,700)
        return Image.new('RGB',(w,h),'white')
    monkeypatch.setattr(windows,'capture_window',paint)
    image,info=windows.capture_calibration_window(11,100)
    assert image.size==(1100,700) and info['kind']=='detached'
    with pytest.raises(windows.DesktopUnavailable):
        windows.capture_calibration_window(2,100)
    def change(hwnd,w,h):
        image=paint(hwnd,w,h)
        records[hwnd]=('另一个会话',*records[hwnd][1:])
        return image
    monkeypatch.setattr(windows,'capture_window',change)
    with pytest.raises(windows.DesktopUnavailable):
        windows.capture_calibration_window(11,100)


def test_recovery_only_admits_the_unique_main_from_the_original_process(monkeypatch):
    records=fake_windows(monkeypatch)
    records[1]=('WeChat',*records[1][1:])
    monkeypatch.setattr(windows,'U',SimpleNamespace())
    assert windows.find_wechat_window(100)['hwnd']==1
    records[12]=('微信',*records[1][1:])
    with pytest.raises(windows.DesktopUnavailable):
        windows.find_wechat_window(100)


def test_runtime_rejects_a_reused_main_handle_that_now_belongs_to_a_detached_chat(monkeypatch):
    saved=binding();saved.hwnd=1;saved.pid=100;saved.width=900;saved.height=620
    monkeypatch.setattr(windows,'U',SimpleNamespace(IsIconic=lambda h:False))
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'window_details',lambda h:dict(hwnd=h,pid=100,width=900,height=620,
                        rect=(0,0,900,620),kind='detached',title='另一个会话'))
    with pytest.raises(windows.DesktopUnavailable,match='主窗口'):
        windows.WindowsDesktop(saved)._background_binding()


def test_detached_target_never_clicks_sidebar_or_search_on_a_mismatched_title(monkeypatch):
    saved=binding();saved.window_kind='detached'
    desktop=windows.WindowsDesktop(saved)
    desktop._sidebar_ocr=lambda im:[vision.TextLine('其他会话 (2)',(2,1,100,20),.99)]
    desktop._find_target=lambda *_:pytest.fail('独立窗口没有侧栏')
    with pytest.raises(windows.DesktopUnavailable,match='标题'):
        desktop.validate_target(screenshot())
    assert desktop.select_target() is False
    desktop._sidebar_ocr=lambda im:[vision.TextLine('示例会话 (2)',(2,1,100,20),.99)]
    desktop.validate_target(screenshot())


def test_account_bound_detached_group_can_omit_count_but_native_title_keeps_emoji_identity():
    from xiaomao.contacts import visual_name_key
    saved=binding();saved.target_group='设计🎨';saved.window_kind='detached'
    saved.ocr_title=visual_name_key(saved.target_group)
    assert vision.header_matches('设计',saved) and vision.header_matches('设计 (25)',saved)
    assert vision.detached_title_matches('设计🎨',saved)
    assert vision.detached_title_matches('设计🎨 (25)',saved)
    assert not vision.detached_title_matches('设计📷',saved)
    saved.window_kind='main'
    assert not vision.header_matches('设计',saved)
    saved.window_kind='detached';saved.ocr_title=''
    assert not vision.header_matches('设计🎨',saved)


def test_detached_layout_uses_full_width_and_preserves_operator_regions():
    original=binding();original.window_kind='detached'
    region=layout.default_calibration_regions('detached')
    assert region['header'][0]<.08 and region['messages'][0]<.05
    pixels=Image.new('RGB',(800,600),(237,237,237))
    draw=ImageDraw.Draw(pixels)
    draw.line((5,72,792,72), fill=(212,212,212))
    draw.line((5,400,792,400), fill=(212,212,212))
    result=layout.adapt_calibration(pixels,original)
    assert result is not original and result.rect('messages')[0]<10
    assert result.rect('messages')[1]>70 and result.rect('input')[1]>440
    original.manual_regions=True
    assert layout.adapt_calibration(pixels,original) is original


def test_scaled_layout_finds_double_pixel_toolbar_and_complete_detached_title():
    for kind,width,height,divider,header,toolbar in (
            ('main',1300,1082,398,136,816),('detached',766,809,0,125,544)):
        pixels=Image.new('RGB',(width,height),(237,237,237))
        draw=ImageDraw.Draw(pixels)
        if divider:draw.line((divider,0,divider,height-5),fill=(213,213,213))
        draw.line((divider+1,header,width-5,header),fill=(213,213,213))
        draw.rectangle((divider+1,toolbar,width-5,toolbar+1),fill=(213,213,213))
        original=binding();original.window_kind=kind;original.width=width;original.height=height
        result=layout.adapt_calibration(pixels,original,1.25)
        assert result is not original
        assert result.rect('input')[1]>toolbar+50
        if kind=='detached':assert 99<result.rect('header')[3]<header


def test_detached_title_fragments_exclude_pinned_banner():
    from xiaomao.vision import TextLine,read_header
    b=binding();b.window_kind='detached'
    lines=[TextLine('群公告', (12,60,82,81),.99),TextLine('(409)',(160,10,200,29),.99),
           TextLine('摄影交流',(10,11,86,29),.99),TextLine('(互勉拍照)',(90,12,153,30),.99)]
    out=read_header(lambda _:lines,Image.new('RGB',(340,92)),b)
    assert ''.join(line.text for line in out)=='摄影交流(互勉拍照)(409)'


def test_detached_controls_check_send_label_and_scroll_position_in_database_mode():
    original=binding();original.window_kind='detached'
    image=screenshot()
    ImageDraw.Draw(image).rounded_rectangle((791,150,796,190),2,fill=(174,174,174))
    controls={'header':'示例会话 (2)', 'send':'发送'}
    def read(crop):
        for key,text in controls.items():
            if crop.size==image.crop(original.rect(key)).size:
                return [vision.TextLine(text,(2,1,66,20),.99)]
        return []
    reader=vision.Vision(read)
    frame=reader.read_controls(image,original)
    assert frame.safe and frame.draft_empty and not frame.at_bottom
    controls['send']='打开'
    assert not reader.read_controls(image,original).safe


def test_wide_send_selection_with_text_only_at_far_edge_cannot_authorize_click():
    original=binding()
    original.regions['send']=[0,.89,1,.99]
    read=lambda crop:[vision.TextLine('发送',(crop.width-60,crop.height//3,crop.width-8,2*crop.height//3),.99)]
    assert not vision.calibration_send_matches(read,screenshot(),original)


@pytest.mark.parametrize('button, label, click_hits_button, accepted', [
    (True, '发送(S)', True, True),
    (True, 'Send (S)', True, True),
    (False, '发送(S)', True, False),
    (True, '打开', True, False),
    (True, '发送(S)', False, False),
])
def test_detached_send_crop_can_cut_label_only_if_click_still_hits_proven_button(
        button, label, click_hits_button, accepted):
    import numpy as np

    original = binding()
    original.window_kind = 'detached'
    original.regions['input'] = [.02, .80, .98, .89]
    original.regions['send'] = [.86 if click_hits_button else .77, .905, .952, .96]
    image = Image.new('RGB', (800, 600), (237, 237, 237))
    draw = ImageDraw.Draw(image)
    if button:
        draw.rounded_rectangle((704, 543, 787, 574), radius=5, fill=(224, 224, 224))
    # Two separate glyph markers. The saved crop misses the final mark.
    draw.rectangle((718, 552, 726, 565), fill=(140, 142, 145))
    draw.rectangle((766, 552, 773, 565), fill=(131, 133, 136))

    def recognize(crop):
        pixels = np.asarray(crop.convert('RGB'))
        marks = [np.where(np.all(pixels == color, axis=2))
                 for color in ((140, 142, 145), (131, 133, 136))]
        if not all(len(x) for _, x in marks):
            return []
        (ys1, xs1), (ys2, xs2) = marks
        return [vision.TextLine(label, (int(xs1.min()), int(ys1.min()),
            int(xs2.max()+1), int(ys2.max()+1)), .99)]

    assert vision.calibration_send_matches(recognize, image, original) is accepted


def test_detached_click_is_blocked_when_an_overlay_covers_the_exact_click_point(monkeypatch):
    original=binding();original.window_kind='detached'
    desktop=windows.WindowsDesktop(original)
    monkeypatch.setattr(desktop,'check',lambda:{'rect':(1,2,801,602)})
    api=SimpleNamespace(SetCursorPos=lambda x,y:True,WindowFromPoint=lambda point:456,
                        GetAncestor=lambda hwnd,which:456,SendInput=lambda *args:pytest.fail('不能点击遮挡窗'))
    monkeypatch.setattr(windows,'U',api)
    with pytest.raises(windows.DesktopTemporarilyHidden):
        desktop._click('send')


def test_background_detached_send_waits_without_activation(monkeypatch):
    original=binding();original.window_kind='detached'
    desktop=windows.WindowsDesktop(original)
    monkeypatch.setattr(windows,'U',SimpleNamespace(GetForegroundWindow=lambda:999))
    with pytest.raises(windows.DesktopTemporarilyHidden,match='前台'):
        desktop.prepare_send(2)


def test_calibration_lease_blocks_only_its_window_and_releases_across_processes(monkeypatch):
    hwnd=0x400000000+os.getpid()
    reader=windows.WindowsDesktop(SimpleNamespace(hwnd=hwnd))
    another=windows.WindowsDesktop(SimpleNamespace(hwnd=hwnd+1))
    class OtherWindow(Exception):
        pass
    monkeypatch.setattr(windows.WindowsDesktop,'_bound_details',lambda _:(_ for _ in ()).throw(OtherWindow()))
    lease=windows.CalibrationWindowLease(hwnd)
    try:
        with pytest.raises(windows.DesktopTemporarilyHidden,match='校准'):
            reader.check()
        with pytest.raises(windows.DesktopTemporarilyHidden,match='校准'):
            reader.prepare_send()
        with pytest.raises(OtherWindow):
            another.check()
        command=[sys.executable,'-c','from xiaomao.windows import CalibrationWindowLease; '
                 'CalibrationWindowLease('+str(hwnd)+')']
        assert subprocess.run(command,capture_output=True).returncode!=0
    finally:
        lease.close()
    lease.close()
    with pytest.raises(OtherWindow):
        reader.check()
    assert subprocess.run(command,capture_output=True).returncode==0
    # A process that exits with its handle open cannot strand a calibration.
    windows.CalibrationWindowLease(hwnd).close()


def test_open_wechat_uses_only_an_existing_verified_executable_no_login_arguments(tmp_path, monkeypatch):
    foreign=tmp_path/'foreign'/'Weixin.exe'
    genuine=tmp_path/'Tencent'/'Weixin.exe'
    for path in (foreign,genuine):
        path.parent.mkdir();path.touch()
    monkeypatch.setattr(windows,'_wechat_executable_paths',lambda:[foreign,genuine],raising=False)
    monkeypatch.setattr(windows,'_signed_wechat_executable',lambda path:path==genuine,raising=False)
    calls=[]
    monkeypatch.setattr(windows,'list_calibration_windows',lambda: [1] if calls else [])
    monkeypatch.setattr(windows,'_wechat_is_running',lambda: False,raising=False)
    monkeypatch.setattr(subprocess,'Popen',lambda args,**kwargs:calls.append(args))
    windows.open_wechat_for_calibration()
    assert calls==[[str(genuine)]]
    calls.clear()
    monkeypatch.setattr(windows,'_wechat_executable_paths',lambda:[foreign])
    with pytest.raises(windows.DesktopUnavailable,match='微信'):
        windows.open_wechat_for_calibration()
    assert calls==[]


def test_open_wechat_restores_an_existing_minimized_window_without_relaunch(tmp_path, monkeypatch):
    records = fake_windows(monkeypatch)
    selected = records[2]
    records.clear()
    records[2] = selected
    calls = []

    def restore(hwnd, command):
        calls.append((hwnd, command))
        row = list(records[hwnd])
        row[4] = False
        records[hwnd] = tuple(row)
        return True

    path = tmp_path / 'Weixin.exe'
    path.touch()
    monkeypatch.setattr(windows, 'U', SimpleNamespace(ShowWindowAsync=restore, GetForegroundWindow=lambda: 99,
                                                     SetForegroundWindow=lambda _: False))
    monkeypatch.setattr(windows, '_wechat_executable_paths', lambda: [path])
    monkeypatch.setattr(windows, '_signed_wechat_executable', lambda _: True)
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_kw: pytest.fail('tried to launch WeChat again'))

    windows.open_wechat_for_calibration()

    assert calls == [(2, 9)]
    assert not windows.list_calibration_windows()[0]['minimized']


def test_open_wechat_does_not_relaunch_an_instance_hidden_in_the_tray(tmp_path, monkeypatch):
    import psutil

    path = tmp_path / 'Weixin.exe'
    path.touch()
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [])
    monkeypatch.setattr(psutil, 'process_iter', lambda _: [SimpleNamespace(info={'name': 'Weixin.exe', 'exe': None})])
    monkeypatch.setattr(windows, '_wechat_executable_paths', lambda: [path])
    monkeypatch.setattr(windows, '_signed_wechat_executable', lambda _: True)
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_kw: pytest.fail('tried to launch WeChat again'))

    windows.open_wechat_for_calibration()


@pytest.mark.parametrize('ready_after', [3, None])
def test_open_wechat_waits_for_a_new_window_with_a_deadline(tmp_path, monkeypatch, ready_after):
    path = tmp_path / 'Weixin.exe'
    path.touch()
    scans = 0
    time = 0
    launched = []

    def discover():
        nonlocal scans
        if not launched:
            return []
        scans += 1
        assert scans <= 50, 'window discovery exceeded the deadline'
        return [{'kind': 'main'}] if ready_after is not None and scans >= ready_after else []

    def sleep(seconds):
        nonlocal time
        time += seconds

    monkeypatch.setattr(windows, 'time', SimpleNamespace(monotonic=lambda: time, sleep=sleep))
    monkeypatch.setattr(windows, 'list_calibration_windows', discover)
    monkeypatch.setattr(windows, '_wechat_is_running', lambda: False, raising=False)
    monkeypatch.setattr(windows, '_wechat_executable_paths', lambda: [path])
    monkeypatch.setattr(windows, '_signed_wechat_executable', lambda _: True)
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **_kw: launched.append(args))

    windows.open_wechat_for_calibration()

    assert launched == [[str(path)]]
    if ready_after is None:
        assert scans > 1
        assert 0 < time <= 10
    else:
        assert scans == ready_after
        assert 0 < time <= 2
