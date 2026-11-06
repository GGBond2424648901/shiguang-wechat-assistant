from dataclasses import asdict, replace
from types import SimpleNamespace
import tkinter as tk

from PIL import Image
import pytest

from xiaomao import selection
from xiaomao.config import Calibration, Settings
from xiaomao.contacts import Contact
from xiaomao.layout import default_calibration_regions
from xiaomao.gui import Panel


def test_calibrating_secondary_preselects_its_detached_window_not_shared_main(tmp_path,native_root):
    from xiaomao.calibration_ui import CalibrationFlow
    from test_window_routing import selected,window,ROOM,PEER
    cfg=selected(tmp_path,ROOM,PEER).with_window_binding(ROOM.name,window(ROOM,11,'main'))
    callbacks=[]
    p=SimpleNamespace(root=native_root,settings=cfg,work=lambda fn,ok,*args,**kw:callbacks.append(ok))
    flow=CalibrationFlow(p,PEER.name)
    try:
        callbacks.pop()([dict(kind='main',hwnd=11,title='微信',minimized=False,pid=71),
                         dict(kind='detached',hwnd=22,title=PEER.name,minimized=False,pid=71)])
        assert flow.picker.current()==1
    finally:flow.close()


def test_recalibration_opens_even_when_old_main_geometry_cannot_be_detected(tmp_path,monkeypatch):
    old=Calibration(111,22,900,680,'群甲(3)',default_calibration_regions(),True,'群甲')
    cfg=Settings(tmp_path,group='群甲',calibration=asdict(old));cfg.save()
    info=dict(hwnd=111,pid=22,width=1300,height=1082,kind='main',scale=1,title='微信')
    pixels=Image.new('RGB',(1300,1082),'white')
    import xiaomao.windows as windows
    monkeypatch.setattr(windows,'capture_calibration_window',lambda *a:(pixels,info))
    image,draft=selection.preview_window(cfg,info,'群甲')
    assert image is pixels and draft.width==1300 and draft.height==1082
    assert not draft.bottom_confirmed and Settings.load(tmp_path).binding.width==900


def test_calibration_click_immediately_displays_dialog_without_pausing_worker(tmp_path,native_root):
    panel=Panel.__new__(Panel)
    panel.root=native_root;panel.directory=tmp_path;panel.settings=Settings(tmp_path)
    panel.switch_busy=False;panel.target_group=tk.StringVar(native_root,value=panel.settings.group)
    callbacks=[]
    panel.work=lambda action,success,failure,**kwargs:callbacks.append((action,success,failure))
    panel.calibrate()
    dialog=panel.calibration_flow
    try:
        assert dialog.window.winfo_exists() and callbacks
        callbacks[0][2]('暂时无法读取窗口')
        assert '暂时无法读取窗口' in dialog.status.get()
        panel.calibrate()
        assert panel.calibration_flow is dialog
    finally:dialog.close()


def test_saving_second_window_keeps_primary_scope_and_existing_mode(tmp_path):
    a=Contact('a@chatroom','account','群甲','group')
    b=Contact('b@chatroom','account','群乙','group')
    cfg=Settings(tmp_path,group=a.name,group_names=(a.name,b.name),conversation_targets={c.name:c.binding for c in (a,b)})
    main=Calibration(11,87,900,680,'群甲(3)',default_calibration_regions(),True,a.name)
    cfg=cfg.with_window_binding(a.name,main)
    dedicated=replace(main,hwnd=23,target_group=b.name,header_label='群乙(5)',window_kind='detached')
    saved=selection.bind_reviewed_window(cfg,dedicated,b)
    assert saved.allowed_groups==(a.name,b.name) and saved.group==a.name
    assert saved.binding_for(a.name)==replace(main,ocr_title=a.name)
    assert saved.binding_for(b.name)==replace(dedicated,ocr_title=b.name)
    assert saved.auto_on_launch==cfg.auto_on_launch
    with pytest.raises(ValueError):
        selection.bind_reviewed_window(cfg,dedicated,replace(b,id='different@chatroom'))


def test_directory_can_use_a_secondary_bound_windows_process_without_routing_primary_to_it(tmp_path,monkeypatch):
    from xiaomao import database
    a=Contact('a@chatroom','account','甲群','group')
    b=Contact('b@chatroom','account','乙群','group')
    cfg=Settings(tmp_path,group=a.name,group_names=(a.name,b.name),conversation_targets={c.name:c.binding for c in (a,b)})
    bound=Calibration(23,87,900,680,'乙群(5)',default_calibration_regions('detached'),True,
                      b.name,window_kind='detached')
    cfg=cfg.with_window_binding(b.name,bound)
    assert cfg.binding is None
    class Hub:
        def __init__(self,cfg,event,*,process_id):assert process_id==87
        def directory(self):return [b,a]
        def close(self):pass
    monkeypatch.setattr(database,'DatabaseHub',Hub)
    assert selection.directory_for_settings(cfg)==[b,a]


def test_switch_primary_to_bound_detached_chat_preserves_shared_main_window(tmp_path,monkeypatch):
    primary=Contact('a@chatroom','account','群甲','group')
    secondary=Contact('b@chatroom','account','群乙','group')
    cfg=Settings(tmp_path,group=primary.name,group_names=(primary.name,secondary.name),
                 conversation_targets={c.name:c.binding for c in (primary,secondary)})
    main=Calibration(31,87,900,680,'群甲(3)',default_calibration_regions(),True,primary.name)
    detached=replace(main,hwnd=32,target_group=secondary.name,header_label='群乙(3)',window_kind='detached')
    cfg=cfg.with_window_binding(primary.name,main).with_window_binding(secondary.name,detached)
    cfg.save()
    monkeypatch.setattr(selection,'load_directory',lambda *args:[primary,secondary])
    monkeypatch.setattr(selection,'refresh_binding',lambda cfg:cfg.binding)
    client=SimpleNamespace(stop=lambda:None,wait_stopped=lambda:None,
                           start=lambda:{'group':secondary.name,'group_names':[secondary.name],'reply_mode':'natural'})
    selection.switch_group(tmp_path,secondary.name,client,contact=secondary)
    saved=Settings.load(tmp_path)
    assert saved.calibration['hwnd']==main.hwnd
    assert saved.binding_for(secondary.name).hwnd==detached.hwnd
