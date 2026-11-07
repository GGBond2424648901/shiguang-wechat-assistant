from dataclasses import asdict,replace
from pathlib import Path
import threading
import pytest


def config(tmp_path):
    from xiaomao.config import Settings
    from test_vision import binding
    return Settings(tmp_path,group='甲群',group_names=('甲群','乙群'),calibration=asdict(binding()))


def test_single_group_selection_keeps_favorites_but_only_monitors_target(tmp_path):
    chosen=config(tmp_path).select_group('乙群')
    assert chosen.saved_groups==('乙群','甲群')
    assert chosen.allowed_groups==('乙群',) and not chosen.monitor_all_groups
    assert chosen.reply_mode=='natural' and chosen.auto_on_launch
    assert chosen.for_group('乙群').binding.target_group=='乙群'
    with pytest.raises(ValueError):chosen.for_group('甲群')


def test_switch_checks_effective_recipient_rule_instead_of_global_default(tmp_path,monkeypatch):
    from xiaomao.selection import switch_group
    from xiaomao.config import Settings
    from xiaomao.contacts import Contact
    monkeypatch.setattr('xiaomao.selection.load_directory',lambda *args:[Contact('b@chatroom','a','乙群','group')])
    cfg=replace(config(tmp_path),conversation_targets={'乙群':{'account':'a','id':'b@chatroom','kind':'group'}})
    cfg=cfg.with_conversation_rules('乙群',{'reply_mode':'mentions'});cfg.save()
    class Client:
        def status(self):return {'running':True,'mode':'auto'}
        def stop(self):pass
        def wait_stopped(self):pass
        def start(self,initial_mode=None):
            active=Settings.load(tmp_path)
            return {'mode':initial_mode,'group':active.group,'group_names':list(active.allowed_groups),
                    'reply_mode':active.for_group(active.group).reply_mode}
        def pause(self):raise AssertionError('valid per-conversation rule must not pause')
    assert switch_group(tmp_path,'乙群',Client(),binding=cfg.binding)['reply_mode']=='mentions'


def test_current_group_scale_refresh_requires_same_process_and_uniform_size(tmp_path):
    from xiaomao.selection import refresh_binding
    cfg=config(tmp_path);b=cfg.binding
    get=lambda hwnd:{'pid':b.pid,'width':round(b.width*1.25),'height':round(b.height*1.25)}
    refreshed=refresh_binding(cfg,details=get)
    assert refreshed.width==1000 and refreshed.height==750
    assert cfg.binding.width==800
    with pytest.raises(ValueError):refresh_binding(cfg,details=lambda hwnd:{'pid':b.pid+1,'width':800,'height':600})
    with pytest.raises(ValueError):refresh_binding(cfg,details=lambda hwnd:{'pid':b.pid,'width':1000,'height':600})


def test_current_group_read_requires_two_matching_headers_and_unique_sidebar(tmp_path):
    from xiaomao.selection import read_current_group
    from xiaomao.vision import TextLine
    from PIL import Image
    cfg=config(tmp_path);cfg.save()
    class Desktop:
        def __init__(self,binding):self.calibration=binding
        def capture(self):return Image.new('RGB',(800,600))
        def validate_target(self,image):assert self.calibration.target_group=='项目群(2)'
    headers=iter(['项目群(2)(297)','项目群(2)(298)'])
    result=read_current_group(tmp_path,desktop_factory=Desktop,recognizer=lambda image:[TextLine(next(headers),(0,0,250,25),.99)],details=lambda hwnd:{'pid':1,'width':800,'height':600})
    assert result.name=='项目群(2)' and result.calibration.target_group==result.name
    bad=iter(['甲群(2)','乙群(3)'])
    with pytest.raises(ValueError,match='变化'):
        read_current_group(tmp_path,desktop_factory=lambda b:type('D',(),{'capture':lambda s:Image.new('RGB',(800,600)),'validate_target':lambda *a:None})(),recognizer=lambda image:[TextLine(next(bad),(0,0,250,25),.99)],details=lambda hwnd:{'pid':1,'width':800,'height':600})


@pytest.mark.parametrize('reply_mode', ['natural','all','mentions'])
def test_group_switch_stops_old_worker_and_preserves_mode_and_pacing(tmp_path,reply_mode):
    from xiaomao.selection import switch_group
    cfg=replace(config(tmp_path),reply_mode=reply_mode,min_interval=4,hourly_limit=180,minute_limit=10,pacing_jitter=4)
    cfg.save();events=[]
    class Client:
        def status(self):return {'running':True,'mode':'auto'}
        def stop(self):events.append('stop')
        def wait_stopped(self):events.append('wait')
        def start(self,initial_mode=None):
            from xiaomao.config import Settings
            active=Settings.load(tmp_path);events.append(('start',active.allowed_groups));return {'mode':initial_mode,'group':active.group,'group_names':list(active.allowed_groups),'reply_mode':active.reply_mode}
    result=switch_group(tmp_path,'乙群',Client(),binding=cfg.binding)
    assert result['mode']=='auto' and events==['stop','wait',('start',('乙群',))]
    from xiaomao.config import Settings
    saved=Settings.load(tmp_path)
    assert result['reply_mode']==reply_mode==saved.reply_mode
    assert (saved.min_interval,saved.hourly_limit,saved.minute_limit,saved.pacing_jitter)==(4,180,10,4)


def test_cancel_during_group_switch_cannot_start_new_auto_worker(tmp_path):
    from xiaomao.selection import switch_group
    cfg=config(tmp_path);cfg.save();cancel=threading.Event();events=[]
    class Client:
        def status(self):return {'running':True,'mode':'auto'}
        def stop(self):events.append('stop');cancel.set()
        def wait_stopped(self):pass
        def start(self):raise AssertionError('cancelled selection must not restart')
    result=switch_group(tmp_path,'乙群',Client(),binding=cfg.binding,cancelled=cancel)
    from xiaomao.config import Settings
    assert result['mode']=='stopped' and Settings.load(tmp_path).group=='甲群'


def test_stop_failure_does_not_save_new_group_or_claim_success(tmp_path):
    from xiaomao.selection import switch_group
    from xiaomao.client import ControlUnavailable
    from xiaomao.config import Settings
    cfg=config(tmp_path);cfg.save()
    class Client:
        def stop(self):raise ControlUnavailable('连接重置，停止未送达')
        def status(self):return {'running':True,'mode':'auto','group':'甲群'}
        def wait_stopped(self):raise AssertionError('不能把失败当成已停')
        def start(self):raise AssertionError('旧群未停止不得启动')
    with pytest.raises(ControlUnavailable):switch_group(tmp_path,'乙群',Client(),binding=cfg.binding)
    assert Settings.load(tmp_path).group=='甲群'


def test_cancel_cannot_report_stopped_when_stop_failed(tmp_path):
    from xiaomao.selection import switch_group
    from xiaomao.client import ControlUnavailable
    cfg=config(tmp_path);cfg.save();cancel=threading.Event();cancel.set()
    class Client:
        def stop(self):raise ControlUnavailable('停止未送达')
        def status(self):return {'running':True,'mode':'auto'}
        def wait_stopped(self):pass
    with pytest.raises(ControlUnavailable):switch_group(tmp_path,'乙群',Client(),binding=cfg.binding,cancelled=cancel)


def test_start_returning_old_worker_is_rejected_and_paused(tmp_path):
    from xiaomao.selection import switch_group
    cfg=config(tmp_path);cfg.save();events=[]
    class Client:
        def status(self):return {'running':True,'mode':'auto'}
        def stop(self):pass
        def wait_stopped(self):pass
        def start(self,initial_mode=None):return {'mode':initial_mode,'group':'甲群','group_names':['甲群'],'reply_mode':'all'}
        def pause(self):events.append('pause')
    with pytest.raises(ValueError,match='监听范围'):switch_group(tmp_path,'乙群',Client(),binding=cfg.binding)
    assert events==['pause']


def test_group_switch_rejects_worker_with_wrong_reply_mode(tmp_path):
    from xiaomao.selection import switch_group
    cfg=replace(config(tmp_path),reply_mode='natural');cfg.save();events=[]
    class Client:
        def status(self):return {'running':True,'mode':'auto'}
        def stop(self):pass
        def wait_stopped(self):pass
        def start(self,initial_mode=None):return {'mode':initial_mode,'group':'乙群','group_names':['乙群'],'reply_mode':'all'}
        def pause(self):events.append('pause')
    with pytest.raises(ValueError,match='不符'):switch_group(tmp_path,'乙群',Client(),binding=cfg.binding)
    assert events==['pause']
