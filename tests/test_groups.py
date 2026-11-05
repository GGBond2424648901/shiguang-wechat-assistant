from dataclasses import replace
import pytest
from test_engine import system


def test_new_configuration_defaults_to_natural_participation(tmp_path):
    from xiaomao.config import Settings
    assert Settings(tmp_path).reply_mode=='natural'
    assert Settings.load(tmp_path).reply_mode=='natural'


@pytest.mark.parametrize('reply_mode', ['natural','all','mentions'])
def test_saved_reply_mode_survives_reload_and_conversation_selection(tmp_path,reply_mode):
    from xiaomao.config import Settings
    from xiaomao.contacts import Contact
    cfg=Settings(tmp_path,reply_mode=reply_mode,min_interval=4,hourly_limit=180,minute_limit=10,pacing_jitter=4)
    cfg.save()
    loaded=Settings.load(tmp_path)
    assert loaded.reply_mode==reply_mode
    selected=loaded.select_group('另一个群')
    assert selected.reply_mode==reply_mode
    selected.save()
    assert Settings.load(tmp_path).reply_mode==reply_mode
    assert (selected.min_interval,selected.hourly_limit,selected.minute_limit,selected.pacing_jitter)==(4,180,10,4)
    private=selected.select_conversation(Contact('friend-id','account','朋友','private'))
    assert private.reply_mode==reply_mode
    assert private.allowed_groups==('朋友',)


def test_unknown_reply_mode_is_rejected(tmp_path):
    from xiaomao.config import Settings
    with pytest.raises(ValueError,match='回复模式'):
        Settings(tmp_path,reply_mode='sometimes')


def test_explicit_group_configuration_roundtrips_and_validates(tmp_path):
    from xiaomao.config import Settings
    cfg=Settings(tmp_path,group='读书群',group_names=('读书群','示例会话'),reply_mode='all',min_interval=10,hourly_limit=60)
    cfg.save();loaded=Settings.load(tmp_path)
    assert loaded.allowed_groups==('读书群','示例会话') and loaded.reply_mode=='all'
    for names in (('a','a'),('   ',),('a\nb',)):
        with pytest.raises(ValueError):Settings(tmp_path,group_names=names)


def test_all_mode_replies_only_to_complete_incoming_text():
    from xiaomao.domain import Message
    from xiaomao.policy import TriggerPolicy
    policy=TriggerPolicy('小猫','小玲',mode='all')
    assert policy.matches(Message('今晚吃什么？','群友'))
    for changes in ({'direction':'out'},{'kind':'image'},{'kind':'partial'},{'quoted':True},{'quality':.8},{'text':' '}):
        assert not policy.matches(replace(Message('普通文字','群友'),**changes))


def test_recovery_claims_are_group_scoped(tmp_path):
    from xiaomao.store import StateStore
    store=StateStore(tmp_path)
    try:
        key=('in','群友','text',False,'你好')
        assert store.claim_latest(key,100,group='甲群')
        assert store.claim_latest(key,100,group='乙群')
        assert not store.claim_latest(key,101,group='甲群')
    finally:store.close()


def test_rebinding_same_display_name_keeps_new_task_ids_distinct_and_old_sends_blocked(system):
    from xiaomao.domain import Message
    engine, _, _, clock, store = system
    cfg = engine.settings
    display = cfg.group
    old_id = 'same-peer-id/s/15'
    message_id = 'same-peer-id/s/16'
    prior = store.create(store.fingerprint(f'{display}:{old_id}'), clock())
    store.mark_sending(prior,clock(),group=display)
    for account in ('account-a','account-b'):
        cfg.conversation_targets={display:{'account':account,'id':'same-peer-id','kind':'private'}}
        engine.received_ids.clear()
        engine._record([Message('小猫',message_id=old_id),Message('小猫你好',message_id=message_id)])
    assert [t.messages[0].message_id for t in engine.tasks] == [message_id,message_id]
    assert store.delivery_status('account-a/same-peer-id',message_id) == 'unconfirmed'
    assert store.delivery_status('account-b/same-peer-id',old_id) == ''


def test_claim_in_old_display_name_journal_blocks_replay_after_binding(system):
    from xiaomao.domain import Message
    from dataclasses import replace
    engine,screen,_,clock,store=system
    cfg=engine.settings
    msg=Message('小猫请回复',message_id='old-id')
    cfg.conversation_targets={cfg.group:{'account':'a','id':'peer','kind':'private'}}
    assert store.claim_latest(msg.key,clock(),group=cfg.group)
    frame=replace(screen.frame,messages=(msg,))
    engine.latest_requested=True
    engine.latest_requested_at=clock()
    engine._recover_latest(frame);engine._recover_latest(frame)
    assert not engine.tasks


def test_vision_uses_configured_group_identity_and_allows_member_count_changes():
    from test_vision import binding,screenshot,RecordedOCR
    from xiaomao.vision import Vision
    b=replace(binding(),header_label='读书群(3)')
    assert Vision(RecordedOCR(header='读书群 (8)')).read(screenshot(),b).safe
    assert not Vision(RecordedOCR(header='其他群 (8)')).read(screenshot(),b).safe


def test_literal_group_name_ending_in_number_parentheses_is_not_member_count(tmp_path):
    from dataclasses import asdict
    from test_vision import binding,screenshot,RecordedOCR
    from xiaomao.config import Settings
    from xiaomao.vision import Vision
    cfg=Settings(tmp_path,group='项目群(2)',calibration=asdict(binding()))
    b=cfg.for_group('项目群(2)').binding
    assert Vision(RecordedOCR(header='项目群(2)(8)')).read(screenshot(),b).safe
    ocr=RecordedOCR(header='项目群(8)')
    assert not Vision(ocr).read(screenshot(),b).safe and len(ocr.outputs)==2
