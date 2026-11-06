from datetime import datetime,timezone,timedelta
from dataclasses import replace
import pytest
from xiaomao.domain import Message


def at(hour,minute=0,day=4):
    return datetime(2026,11,day,hour,minute,tzinfo=timezone(timedelta(hours=8))).timestamp()


class Predictable:
    def uniform(self,a,b):return a
    def random(self):return 0


def planner():
    from xiaomao.companion import CompanionPreferences,Planner,fresh_state
    p=Planner(CompanionPreferences(enabled=True),fresh_state(),boot_at=at(10),rng=Predictable())
    p.observe(Message('今天学Python',message_id='in1',created_at=at(10),speaker_id='p_'+'a'*16),baseline=False)
    p.replied('out1',at(10))
    return p


def test_followup_samples_once_and_new_input_invalidates_candidate():
    p=planner()
    assert p.due_at==at(10,2)
    assert p.decide(at(10,1)) is None
    c=p.decide(at(10,2));assert c and c.kind=='followup'
    assert p.decide(at(10,2)) is None
    assert p.valid(c,at(10,2))
    p.observe(Message('换个问题',message_id='in2',created_at=at(10,2)+1),baseline=False)
    assert not p.valid(c,at(10,2)+2)


def test_quiet_hours_and_expiry_are_rechecked_before_sending():
    from xiaomao.companion import quiet_now
    p=planner();c=p.decide(at(10,2))
    assert quiet_now(at(23),p.preferences) and quiet_now(at(8,59),p.preferences)
    assert not quiet_now(at(9),p.preferences)
    assert not p.valid(c,at(10,5))
    p.preferences=replace(p.preferences,quiet_start=10*60,quiet_end=11*60)
    assert not p.valid(c,at(10,2))


def test_unanswered_proactive_does_not_repeat_across_days_or_restart():
    from xiaomao.companion import Planner
    p=planner();c=p.decide(at(10,2));p.delivered(c,at(10,2))
    again=Planner(p.preferences,p.state,boot_at=at(10,3),rng=Predictable())
    assert again.decide(at(17)) is None
    assert again.decide(at(12,day=5)) is None
    again.observe(Message('我回来了',message_id='in3',created_at=at(12,day=5)),baseline=False)
    assert not again.state['unanswered']


def test_response_received_while_offline_releases_guard_without_old_followup():
    from xiaomao.companion import Planner
    p=planner();c=p.decide(at(10,2));p.delivered(c,at(10,2))
    resumed=Planner(p.preferences,p.state,boot_at=at(12),rng=Predictable())
    resumed.observe(Message('谢谢，刚才在忙',message_id='offline-response',created_at=at(11)),baseline=True)
    assert not resumed.state['unanswered'] and not resumed.state['followup_at']
    assert resumed.decide(at(12,1)) is None


def test_old_baseline_never_creates_followup_and_boot_has_grace():
    from xiaomao.companion import CompanionPreferences,Planner,fresh_state
    p=Planner(CompanionPreferences(enabled=True),fresh_state(),boot_at=at(15),rng=Predictable())
    p.observe(Message('旧话',message_id='old',created_at=at(10)),baseline=True)
    assert p.decide(at(15)) is None
    c=p.decide(at(15,1));assert c and c.kind=='idle'
    assert c.created==at(15,1) and c.expires==at(15,3)


def test_rollback_and_disabled_mode_never_revive_candidate():
    p=planner();c=p.decide(at(10,2))
    assert p.decide(at(9)) is None
    assert not p.valid(c,at(10,2))
    p.preferences=replace(p.preferences,enabled=False)
    assert p.decide(at(17)) is None


def test_preferences_dates_validate_and_new_conversation_defaults_off():
    from xiaomao.companion import CompanionPreferences
    assert not CompanionPreferences().enabled
    for values in ({'daily_max':99},{'quiet_start':1440},{'dates':[{'date':'02-31','label':'纪念日'}]},
                   {'style':'x'*2001},{'enabled':'yes'}):
        with pytest.raises(ValueError):CompanionPreferences(**values)
    assert CompanionPreferences(dates=[{'date':'02-29','label':'生日'}]).dates


def test_companion_storage_is_encrypted_scoped_and_keeps_updates(tmp_path):
    from xiaomao.companion_store import CompanionStore
    a=CompanionStore(tmp_path,'account/private_a')
    a.data['preferences']['style']='只属于甲的称呼'
    a.data['planner']['revision']=17;a.save()
    assert '只属于甲'.encode() not in a.path.read_bytes()
    again=CompanionStore(tmp_path,'account/private_a')
    assert again.data['planner']['revision']==17
    assert again.data['preferences']['style']=='只属于甲的称呼'
    assert not CompanionStore(tmp_path,'account/private_b').data['preferences']['style']
    a.path.write_bytes(b'broken')
    with pytest.raises(ValueError):CompanionStore(tmp_path,'account/private_a')


def test_durable_proactive_budget_has_account_cap_and_unknown_blocks_retry(tmp_path):
    from xiaomao.store import StateStore
    store=StateStore(tmp_path)
    try:
        assert store.reserve_proactive('a1','account/a','2026-11-04',at(10),2)
        store.proactive_result('a1','unknown')
        assert not store.reserve_proactive('a1','account/a','2026-11-04',at(10,1),2)
        assert store.reserve_proactive('b1','account/b','2026-11-04',at(11),2)
        assert store.reserve_proactive('c1','account/c','2026-11-04',at(12),2)
        assert not store.reserve_proactive('a2','account/a','2026-11-04',at(13),2)
        assert not store.reserve_proactive('rollback','account/a','2026-11-03',at(13,day=3),2)
    finally:store.close()
    reopened=StateStore(tmp_path)
    try:assert not reopened.reserve_proactive('a3','account/a','2026-11-04',at(14),2)
    finally:reopened.close()
