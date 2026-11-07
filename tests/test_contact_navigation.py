from xiaomao.vision import TextLine


def line(text,y,x=12,score=.99):return TextLine(text,(x,y,x+120,y+18),score)


def test_search_chooses_only_exact_row_in_correct_section_not_web_or_history():
    from xiaomao.contact_navigation import search_result
    lines=[line('搜索网络结果',0),line('朋友甲',30,50),line('联系人',65),line('朋友甲',105,50),
           line('群聊',150),line('朋友甲',190,50),line('聊天记录',240),line('朋友甲',280,50)]
    assert search_result(lines,'朋友甲','private').box[1]==105
    assert search_result(lines,'朋友甲','group').box[1]==190
    assert search_result(lines,'朋友','private') is None


def test_search_refuses_duplicate_low_confidence_or_missing_category():
    from xiaomao.contact_navigation import search_result
    for lines in ([line('朋友甲',40,50)],
                  [line('联系人',0),line('朋友甲',40,50),line('朋友甲',90,50)],
                  [line('联系人',0),line('朋友甲',40,50,.8)]):
        assert search_result(lines,'朋友甲','private') is None


def test_account_bound_search_can_join_emoji_split_title_without_matching_prefix():
    from xiaomao.contact_navigation import search_result,first_result_is
    lines=[line('群聊',0),TextLine('摄影交流',(50,40,130,58),.99),
           TextLine('(互勉拍照)',(150,41,250,59),.99)]
    found=search_result(lines,'摄影交流📸（互勉拍照）','group',ocr_title='摄影交流(互勉拍照)')
    assert found and first_result_is(lines,found,'group')
    assert search_result(lines,'摄影交流📸（互勉拍照）','group') is None
    assert search_result(lines,'摄影交流','group',ocr_title='摄影交流') is None


def test_enter_is_only_available_for_exact_first_contact_not_web_or_history():
    from xiaomao.contact_navigation import search_result,first_result_is
    safe=[line('联系人',0),line('朋友甲',40,50),line('搜索网络结果',90)]
    assert first_result_is(safe,search_result(safe,'朋友甲','private'),'private')
    unsafe=[line('搜索网络结果',0),line('朋友甲',40,50),line('联系人',90),line('朋友甲',130,50)]
    assert not first_result_is(unsafe,search_result(unsafe,'朋友甲','private'),'private')


def test_contact_with_self_same_name_is_ambiguous():
    from test_contacts import contacts
    from xiaomao.contacts import read_directory
    c=contacts();c.execute("UPDATE contact SET nick_name='朋友甲' WHERE username='self'")
    row=next(r for r in read_directory(c,'self','account') if r.id=='a')
    assert not row.selectable


def test_clipboard_failure_cleans_owned_search_and_remains_retryable(monkeypatch):
    import threading
    from types import SimpleNamespace
    from PIL import Image
    from test_vision import binding
    from dataclasses import replace
    import pytest
    from xiaomao.contact_navigation import search_target
    from xiaomao.windows import DesktopTemporarilyHidden
    from xiaomao.clipboard import ClipboardUnavailable
    state={'generation':0,'keys':[]}
    def send(n,events,size):
        state['keys'] += [e.value.keyboard.vk for e in events if e.type==1 and e.value.keyboard.flags==0]
        state['generation']+=1;return n
    fake=SimpleNamespace(GetForegroundWindow=lambda:1,WindowFromPoint=lambda p:1,
                         GetAncestor=lambda h,f:h,SetCursorPos=lambda *a:True,SendInput=send)
    monkeypatch.setattr('xiaomao.windows.U',fake)
    details={'pid':1,'rect':(0,0,800,600),'width':800,'height':600}
    monkeypatch.setattr('xiaomao.windows.window_details',lambda hwnd:details)
    def unavailable(*a,**k):raise ClipboardUnavailable('busy')
    monkeypatch.setattr('xiaomao.clipboard.NativeClipboard',lambda *a:None)
    monkeypatch.setattr('xiaomao.clipboard.copied_text',unavailable)
    cfg=replace(binding(),target_group='目标',regions={**binding().regions,'messages':[.4,.1,1,.75]})
    d=SimpleNamespace(calibration=cfg,_last_search=0,idle_for=lambda:30,prepare_send=lambda s:0,
        check=lambda:details,_dpi_scale=lambda:1,capture=lambda:Image.new('RGB',(800,600)),
        _sidebar_ocr=lambda im:[line('搜索',5,0)],cancelled=SimpleNamespace(is_set=lambda:False,wait=lambda t:False),
        input_generation=lambda:state['generation'],_input_snapshot=lambda:(123,state['generation']))
    with pytest.raises(DesktopTemporarilyHidden):search_target(d,2)
    assert state['keys'][-4:]==[0x11,0x41,0x08,0x1b]


def test_after_search_confirmation_never_backspaces_a_destination_draft(monkeypatch):
    from types import SimpleNamespace
    from dataclasses import replace
    from PIL import Image
    from test_vision import binding
    from xiaomao.contact_navigation import search_target
    state={'generation':0,'keys':[]}
    def send(n,events,size):
        state['keys'] += [e.value.keyboard.vk for e in events if e.type==1 and e.value.keyboard.flags==0]
        state['generation']+=1;return n
    fake=SimpleNamespace(GetForegroundWindow=lambda:1,WindowFromPoint=lambda p:2 if p.y>=60 else 1,
        GetAncestor=lambda h,f:h,SetCursorPos=lambda *a:True,SendInput=send,IsWindow=lambda h:True,IsWindowVisible=lambda h:True)
    monkeypatch.setattr('xiaomao.windows.U',fake)
    main={'pid':1,'rect':(0,0,800,600),'width':800,'height':600}
    popup={'pid':1,'rect':(70,60,430,260),'width':360,'height':200}
    monkeypatch.setattr('xiaomao.windows.window_details',lambda hwnd:popup if hwnd==2 else main)
    monkeypatch.setattr('win32gui.ClientToScreen',lambda *a:(70,60))
    monkeypatch.setattr('win32gui.GetClientRect',lambda *a:(0,0,360,200))
    monkeypatch.setattr('xiaomao.clipboard.NativeClipboard',lambda *a:None)
    monkeypatch.setattr('xiaomao.clipboard.copied_text',lambda *a,**k:'目标')
    monkeypatch.setattr('PIL.ImageGrab.grab',lambda **k:Image.new('RGB',(360,200)))
    def ocr(im):return [line('群聊',5),line('目标',50,70)] if im.height==200 else [line('搜索',5,0)]
    cfg=replace(binding(),target_group='目标',regions={**binding().regions,'messages':[.4,.1,1,.75]})
    d=SimpleNamespace(calibration=cfg,_last_search=0,idle_for=lambda:30,prepare_send=lambda s:0,
        check=lambda:main,_dpi_scale=lambda:1,capture=lambda:Image.new('RGB',(800,600)),
        _sidebar_ocr=ocr,cancelled=SimpleNamespace(is_set=lambda:False,wait=lambda t:False),
        input_generation=lambda:state['generation'],_input_snapshot=lambda:(123,state['generation']))
    assert not search_target(d,2)  # A popup whose disappearance is uncertain.
    assert 0x0d in state['keys'] and 0x08 not in state['keys']
    assert state['keys'][-1]==0x1b
