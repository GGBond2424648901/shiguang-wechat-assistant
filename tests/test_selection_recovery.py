from dataclasses import asdict, replace
import pytest
from PIL import Image
from xiaomao.config import Settings
from xiaomao.contacts import Contact
from test_vision import binding, screenshot, RecordedOCR


def test_recreated_window_is_rebound_only_in_same_process(tmp_path):
    from xiaomao.selection import refresh_binding
    from xiaomao.windows import DesktopUnavailable
    cfg=Settings(tmp_path,calibration=asdict(binding()))
    def gone(hwnd):raise DesktopUnavailable('微信窗口不存在')
    recovered=refresh_binding(cfg,details=gone,recover=lambda pid:{'hwnd':33,'pid':pid,'width':800,'height':600})
    assert recovered.hwnd==33 and recovered.pid==cfg.binding.pid
    with pytest.raises(ValueError,match='身份'):
        refresh_binding(cfg,details=gone,recover=lambda pid:{'hwnd':33,'pid':pid+1,'width':800,'height':600})


def test_resized_window_needs_detected_layout_not_proportional_guess(tmp_path):
    from xiaomao.selection import refresh_binding
    cfg=Settings(tmp_path,calibration=asdict(binding()))
    class Desktop:
        def __init__(self,b):self.calibration=b
        def capture(self):
            self.calibration=replace(self.calibration,regions={**self.calibration.regions,'header':[.3,.02,.8,.12]})
            return Image.new('RGB',(1000,600))
    result=refresh_binding(cfg,details=lambda h:{'pid':1,'width':1000,'height':600},desktop_factory=Desktop)
    assert result.width==1000 and result.regions['header']==[.3,.02,.8,.12]
    class NoLayout(Desktop):
        def capture(self):return Image.new('RGB',(1000,600))
    with pytest.raises(ValueError,match='布局'):
        refresh_binding(cfg,details=lambda h:{'pid':1,'width':1000,'height':600},desktop_factory=NoLayout)


def test_unique_contact_accepts_omitted_emoji_but_not_truncated_or_different_title(tmp_path):
    from xiaomao.vision import Vision
    name='摄影交流📸（互勉拍照）'
    contact=Contact('r@chatroom','account',name,'group')
    b=Settings(tmp_path,calibration=asdict(binding())).select_conversation(contact).binding
    assert Vision(RecordedOCR(header='摄影交流(互勉拍照)(407)')).read(screenshot(),b).safe
    for header in ('摄影交流(互勉拍...','摄影交流(其他)(407)','摄影交流(互勉拍照)'):
        assert not Vision(RecordedOCR(header=header)).read(screenshot(),b).safe
    # Legacy text-only bindings may not silently relax emoji identity.
    plain=replace(binding(),header_label=name,target_group=name)
    assert not Vision(RecordedOCR(header='摄影交流(互勉拍照)(407)')).read(screenshot(),plain).safe


def test_emoji_only_contact_collisions_cannot_be_selected(tmp_path):
    from test_contacts import contacts
    from xiaomao.contacts import read_directory,resolve_contact
    conn=contacts()
    conn.executemany('INSERT INTO contact VALUES(?,?,?,?,0,0,1)',[
        ('c@chatroom','拍照📸','',''),('d@chatroom','拍照🐟','','')])
    rows=read_directory(conn,'self','account')
    assert not any(r.selectable for r in rows if r.name.startswith('拍照'))
    selected=Contact('c@chatroom','account','拍照📸','group')
    cfg=Settings(tmp_path).select_conversation(selected)
    with pytest.raises(ValueError):resolve_contact(rows,cfg,selected.name)


def test_full_verified_header_does_not_require_truncated_sidebar(monkeypatch,tmp_path):
    from xiaomao.windows import WindowsDesktop
    from xiaomao.vision import TextLine
    contact=Contact('r@chatroom','account','摄影交流📸（互勉拍照）','group')
    b=Settings(tmp_path,calibration=asdict(binding())).select_conversation(contact).binding
    desktop=WindowsDesktop(b)
    desktop._sidebar_ocr=lambda im:[TextLine('摄影交流(互勉拍照)(407)',(0,0,200,20),.99)]
    monkeypatch.setattr(desktop,'_find_target',lambda im: (_ for _ in ()).throw(AssertionError('truncated sidebar must not override full title')))
    desktop.validate_target(screenshot())


def test_header_tokens_are_in_reading_order_even_when_boxes_have_different_y():
    from xiaomao.vision import read_header,TextLine
    lines=[TextLine('(407)',(220,0,270,20),.99),TextLine('摄影交流',(0,2,90,22),.99),TextLine('(互勉拍照)',(100,1,210,21),.99)]
    assert ''.join(r.text for r in read_header(lambda im:lines,Image.new('RGB',(300,30))))=='摄影交流(互勉拍照)(407)'


def test_calibration_uses_selected_contact_and_keeps_live_config_until_saved(tmp_path,monkeypatch):
    from xiaomao import selection
    from xiaomao.vision import TextLine
    cfg=Settings(tmp_path,group='旧群',calibration=asdict(binding()));cfg.save()
    target=Contact('person','account','朋友甲','private')
    monkeypatch.setattr(selection,'load_directory',lambda root:[target])
    monkeypatch.setattr(selection,'refresh_binding',lambda cfg:cfg.binding)
    class Desktop:
        def __init__(self,b):self.calibration=b
        def capture(self):return screenshot()
        def validate_target(self,im):assert self.calibration.target_kind=='private'
    image,b=selection.capture_calibration(tmp_path,'朋友甲',desktop_factory=Desktop,
        recognizer=lambda im:[TextLine('朋友甲',(0,0,100,20),.99)])
    assert b.target_group=='朋友甲' and b.target_kind=='private' and not b.bottom_confirmed
    assert Settings.load(tmp_path).group=='旧群'
    with pytest.raises(ValueError,match='标题'):
        selection.capture_calibration(tmp_path,'朋友甲',desktop_factory=Desktop,
            recognizer=lambda im:[TextLine('另一个人',(0,0,100,20),.99)])
