from dataclasses import asdict,replace
import pytest
from xiaomao.account_detection import AccountObservation
from xiaomao.config import Calibration,Settings
from xiaomao.contacts import Contact
from xiaomao.resident import Resident
from xiaomao.store import StateStore
from test_engine import Screen,IdentityVision,ReplyModel


class Profiles:
    def __init__(self):self.values={}
    def save(self,account,values):self.values[account]=values
    def load(self,account):return self.values.get(account)


class Detector:
    def __init__(self,observation):self.observation=observation;self.calls=0
    def poll(self,cfg):self.calls+=1;return self.observation
    def close(self):pass


def binding(account='a',pid=11):
    return Calibration(pid,pid,800,600,'群甲',
        {'header':[0,0,1,.1],'messages':[0,.1,1,.7],'input':[0,.8,.8,.95],'send':[.8,.95,1,1]},
        True,target_group='群甲')


@pytest.fixture
def worker(tmp_path,monkeypatch):
    cfg=Settings(tmp_path,group='群甲',group_names=('群甲',),
        conversation_targets={'群甲':{'account':'a','id':'shared@chatroom','kind':'group'}},
        calibration=asdict(binding()),calibration_account='a',account_process_id=11,
        auto_on_launch=True,model_base_url='http://localhost:11434')
    cfg=cfg.with_conversation_rules('群甲',{'reply_mode':'mentions'})
    cfg.save()
    obs=AccountObservation('b','新微信昵称',22,(Contact('shared@chatroom','b','群甲','group'),),())
    detector,profiles=Detector(obs),Profiles()
    def calibrate(fresh,observation,cancelled):
        if '群甲' not in fresh.conversation_targets:return fresh
        return fresh.with_window_binding('群甲',binding(observation.account,observation.pid))
    store=StateStore(tmp_path/'runtime')
    screen=Screen()
    resident=Resident(cfg,store,desktop_factory=lambda b:screen,vision=IdentityVision(),
        model_factory=lambda s:ReplyModel(),settle_seconds=0,
        account_detector=detector,account_profiles=profiles,account_binder=calibrate)
    monkeypatch.setattr(resident,'_observe_engine',lambda e:None)
    yield resident,detector,profiles,screen,store
    resident.stop();store.close()


def test_background_cycle_switches_and_cancels_old_unsent_tasks_without_panel(worker):
    from xiaomao.domain import Message
    resident,detector,profiles,screen,store=worker
    old=resident.engines[0]
    old._record([Message('小猫旧账号待回复',message_id='oldq')])
    old.tasks[0].state='generated';old.tasks[0].reply='不能串号发送'
    resident.observe_once()
    assert resident.settings.calibration_account=='b' and detector.calls==1
    assert resident.engines[0] is not old and old.tasks==[]
    assert not resident.pending() and not screen.events
    assert resident.engines[0].mode=='auto'
    assert resident.engines[0].source_rebaseline
    assert Settings.load(resident.settings.root)==resident.settings
    assert 'a' in profiles.values
    assert resident.status()['account_display_name']=='新微信昵称'


def test_account_detection_works_while_manually_paused_and_does_not_resume(worker):
    resident,detector,_,screen,_=worker
    resident.pause()
    resident.observe_once()
    assert detector.calls==1 and resident.settings.calibration_account=='b'
    assert resident.status()['mode']=='paused'
    assert resident.settings.is_conversation_paused('群甲')
    assert not screen.events


@pytest.mark.parametrize('state',['staged','sending','awaiting_receipt','unknown'])
def test_uncertain_or_staged_send_blocks_switch_and_preserves_draft(worker,state):
    from xiaomao.domain import Message
    resident,_,_,screen,_=worker
    old=resident.engines[0];old._record([Message('小猫问题',message_id='q')])
    old.tasks[0].state=state;old.tasks[0].reply='真实草稿'
    screen.frame=replace(screen.frame,draft='真实草稿',draft_empty=False)
    resident.observe_once()
    assert resident.settings.calibration_account=='a'
    assert old.tasks[0].state==state and screen.frame.draft=='真实草稿'
    assert old.mode=='paused' and old.cancelled.is_set() and not screen.events


def test_switch_back_restores_account_rules_instead_of_copying_other_account(worker):
    resident,detector,_,_,_=worker
    resident.observe_once()
    resident.configure_reply_rules('群甲','b/shared@chatroom',{'reply_mode':'all'})
    detector.observation=AccountObservation('a','旧账号',33,(Contact('shared@chatroom','a','群甲','group'),),())
    resident.observe_once()
    assert resident.settings.calibration_account=='a'
    assert resident.engines[0].settings.reply_mode=='mentions'
    assert resident.engines[0].settings.memory_scope=='a/shared@chatroom'
    assert resident.engines[0].settings.binding.pid==33


def test_concurrent_configuration_change_cannot_be_overwritten_by_detection(worker):
    resident,_,_,_,_=worker
    def conflict(cfg,observation,cancelled):
        replace(Settings.load(cfg.root),reply_mode='all').save()
        return cfg
    resident.account_binder=conflict
    resident.observe_once()
    assert resident.settings.calibration_account=='a'
    saved=Settings.load(resident.settings.root)
    assert saved.reply_mode=='all' and saved.calibration_account=='a'
    assert not resident.pending()


def test_same_account_new_process_rebinds_without_copying_old_hwnd(worker):
    resident,detector,_,_,_=worker
    detector.observation=AccountObservation('a','同账号',99,(Contact('shared@chatroom','a','群甲','group'),),())
    resident.observe_once()
    assert resident.settings.account_process_id==99
    assert resident.settings.binding.hwnd==99 and resident.engines[0].settings.reply_mode=='mentions'
    assert resident.engines[0].mode=='auto'


@pytest.mark.parametrize('defer_reload', (False, True))
def test_same_account_rebind_keeps_saved_pause_reason(worker, defer_reload):
    resident, detector, _, screen, _ = worker
    resident.pause_selected('群甲', 'a/shared@chatroom')
    detector.observation = AccountObservation('a', '同账号', 99,
        (Contact('shared@chatroom', 'a', '群甲', 'group'),), ())

    if defer_reload:
        factory = resident.desktop_factory

        def temporarily_unavailable(_binding):
            raise OSError('窗口暂不可用')

        resident.desktop_factory = temporarily_unavailable
        resident.observe_once()
        assert resident.reload_request is not None
        resident.desktop_factory = factory

    resident.observe_once()

    status = resident.status()
    assert resident.settings.binding.hwnd == 99
    assert status['account_status'] == '已自动识别当前微信账号'
    assert status['groups'][0]['mode'] == 'paused'
    assert status['groups'][0]['reason'] == '本会话已暂停'
    assert Settings.load(resident.settings.root).is_conversation_paused('群甲')
    assert not screen.events


def test_configuration_conflict_recovers_next_cycle_without_restart(worker):
    resident,_,_,_,_=worker
    binder=resident.account_binder
    def conflict(cfg,observation,cancelled):
        replace(Settings.load(cfg.root),reply_mode='all').save()
        return cfg
    resident.account_binder=conflict
    resident.observe_once()
    resident.account_binder=binder
    resident.observe_once()
    assert resident.settings.calibration_account=='b'
    assert resident.settings.reply_mode=='all'


def test_same_account_reconnect_waits_for_window_then_resumes_without_enable(worker):
    resident,detector,_,_,_=worker
    detector.observation=AccountObservation('a','同账号',99,(Contact('shared@chatroom','a','群甲','group'),),())
    binder=resident.account_binder
    resident.account_binder=lambda cfg,*args:cfg
    resident.observe_once()
    assert resident.settings.binding is None
    resident.account_binder=binder;resident._next_account_binding=0
    resident.observe_once()
    assert resident.settings.binding.pid==99
    assert resident.engines[0].mode=='auto' and not resident.engines[0].cancelled.is_set()


def test_initially_empty_directory_recovers_selected_ids_later(worker):
    resident,detector,_,_,_=worker
    ready=detector.observation
    detector.observation=replace(ready,rows=())
    resident.observe_once()
    assert resident.settings.calibration_account=='b'
    detector.observation=ready;resident._next_account_binding=0
    resident.observe_once()
    assert resident.settings.saved_groups==('群甲',)
    assert resident.engines[0].settings.reply_mode=='mentions'


def test_same_pid_recreated_main_hwnd_is_automatically_rebound(worker):
    resident,detector,_,_,_=worker
    detector.observation=AccountObservation('a','同账号',11,(Contact('shared@chatroom','a','群甲','group'),),
        ({'pid':11,'hwnd':999,'kind':'main'},))
    resident.account_binder=lambda cfg,obs,cancel:cfg.with_window_binding('群甲',replace(binding('a',11),hwnd=999))
    resident.observe_once()
    assert resident.settings.binding.hwnd==999
    assert resident.engines[0].mode=='auto'


def test_corrupt_pending_profile_does_not_kill_resident_observer(worker,monkeypatch):
    resident,_,profiles,_,_=worker
    def failed(account):raise ValueError('加密配置损坏')
    monkeypatch.setattr(profiles,'load',failed)
    resident.observe_once()
    assert resident.settings.calibration_account=='a'
    assert resident.account_detector.calls==1 and resident.engines[0].mode=='paused'


def test_directory_recovery_intent_survives_resident_restart(worker):
    resident,detector,profiles,screen,store=worker
    ready=detector.observation
    detector.observation=replace(ready,rows=())
    resident.observe_once()
    assert not resident.settings.conversation_targets
    resident.stop()
    detector.observation=ready
    new=Resident(Settings.load(resident.settings.root),store,desktop_factory=lambda b:screen,
        vision=IdentityVision(),model_factory=lambda cfg:ReplyModel(),
        account_detector=detector,account_profiles=profiles,account_binder=resident.account_binder)
    try:
        new._observe_engine=lambda e:None
        new.observe_once()
        assert new.settings.saved_groups==('群甲',) and new.settings.binding.pid==22
        assert new.engines[0].settings.reply_mode=='mentions'
    finally:new.stop()


def test_directory_recovery_uses_real_encrypted_profile_roundtrip(worker):
    from xiaomao.account_profiles import AccountProfiles
    resident,detector,_,_,_=worker
    resident.account_profiles=AccountProfiles(resident.settings.runtime)
    ready=detector.observation
    detector.observation=replace(ready,rows=())
    resident.observe_once()
    detector.observation=ready;resident._next_account_binding=0
    resident.observe_once()
    assert resident.settings.saved_groups==('群甲',)


def test_legacy_mixed_account_config_cannot_kill_observer(worker):
    resident,_,_,screen,_=worker
    resident.settings=replace(resident.settings,conversation_targets={
        **resident.settings.conversation_targets,'遗留对象':{'id':'legacy','account':'other','kind':'private'}})
    resident.observe_once()
    assert resident.engines[0].mode=='paused' and not screen.events
    assert '账号' in resident.account_status


def test_explicit_detached_target_replaces_shared_main_without_manual_calibration(worker):
    resident,detector,_,_,_=worker
    detector.observation=AccountObservation('a','同账号',11,(Contact('shared@chatroom','a','群甲','group'),),
        ({'pid':11,'hwnd':11,'kind':'main','title':'微信'},
         {'pid':11,'hwnd':55,'kind':'detached','title':'群甲'}))
    calls=[]
    def bind(cfg,obs,cancelled):
        calls.append(obs)
        return cfg.with_window_binding('群甲',replace(binding('a',11),hwnd=55,window_kind='detached'))
    resident.account_binder=bind
    resident.observe_once()
    assert calls and resident.settings.binding.hwnd==55
    assert resident.engines[0].mode=='auto'
