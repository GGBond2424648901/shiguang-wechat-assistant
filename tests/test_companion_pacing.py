from dataclasses import replace
from types import SimpleNamespace

import pytest

from xiaomao.companion import CompanionPreferences, Planner, fresh_state
from xiaomao.companion_store import CompanionStore
from xiaomao.companion_runtime import CompanionRuntime
from xiaomao.domain import Message
from test_companion import at, Predictable
from test_companion_runtime import build, Deferred


class MissOnce(Predictable):
    def __init__(self):
        self.samples = 0

    def random(self):
        self.samples += 1
        return .9 if self.samples == 1 else 0


def test_configurable_intervals_and_probabilities_drive_each_stage():
    prefs = CompanionPreferences(enabled=True, daily_max=24,
        idle_min_minutes=10, idle_max_minutes=12, idle_probability=1,
        followup_min_minutes=3, followup_max_minutes=4, followup_probability=1,
        checkin_min_minutes=6, checkin_max_minutes=8,
        signoff_min_minutes=20, signoff_max_minutes=25)
    p = Planner(prefs, fresh_state(), boot_at=at(10), rng=Predictable())
    p.observe(Message('开始聊', message_id='in', created_at=at(10)))
    p.replied('reply', at(10))
    assert p.due_at == at(10,3)
    c = p.decide(at(10,3))
    assert c.kind == 'followup'
    p.confirmed(c, at(10,3))
    assert p.due_at == at(10,9)
    c = p.decide(at(10,9))
    assert c.kind == 'checkin'
    p.confirmed(c, at(10,9))
    assert p.due_at == at(10,23)
    c = p.decide(at(10,23))
    assert c.kind == 'signoff'
    p.confirmed(c, at(10,23))
    assert p.decide(at(20)) is None


@pytest.mark.parametrize('values', [
    {'idle_min_minutes':0}, {'idle_max_minutes':1441},
    {'followup_min_minutes':6, 'followup_max_minutes':5},
    {'checkin_min_minutes':True}, {'signoff_min_minutes':1.5},
    {'checkin_max_minutes':60, 'signoff_min_minutes':50},
    {'idle_probability':float('nan')}, {'idle_probability':True},
    {'followup_probability':1.1}, {'daily_max':25},
])
def test_frequency_values_validate_before_saving(values):
    with pytest.raises(ValueError):
        CompanionPreferences(**values)


def test_idle_probability_miss_is_persisted_and_not_resampled_after_restart(tmp_path):
    r, store, queue, now = build(tmp_path)
    try:
        r.configure(replace(r.planner.preferences, followup=False))
        r.planner.rng = MissOnce()
        now[0] = r.planner.state['next_idle']
        r.tick([], epoch='live', eligible=True)
        assert not queue.calls
        due = r.planner.state['next_idle']
        assert due > now[0]
        assert CompanionStore(tmp_path, r.settings.memory_scope).data['planner']['next_idle'] == due
        r.close()
        resumed = CompanionRuntime(r.settings, store, r.model, lambda:now[0], executor=Deferred())
        try:
            resumed.planner.rng = MissOnce()
            now[0] += 61
            resumed.tick([], epoch='restart', eligible=True)
            assert not resumed.executor.calls
            assert resumed.planner.rng.samples == 0
            assert resumed.planner.state['next_idle'] == due
        finally:
            resumed.close()
    finally:
        r.close(); store.close()


def test_restart_keeps_not_yet_due_followup_but_invalidates_old_candidates(tmp_path):
    r, store, queue, now = build(tmp_path)
    try:
        original_revision = r.planner.state['revision']
        due = r.planner.state['followup_at']
        now[0] = at(10,0)+30
        r.close()
        resumed = CompanionRuntime(r.settings, store, r.model, lambda:now[0], executor=Deferred())
        try:
            assert resumed.planner.state['followup_at'] == due
            assert resumed.planner.state['revision'] > original_revision
            now[0] = due
            resumed.planner.rng = Predictable()
            resumed.tick([], epoch='restart', eligible=True)
            assert resumed.executor.calls[0][2][1].kind == 'followup'
        finally:
            resumed.close()
    finally:
        r.close(); store.close()


def test_past_followup_after_focus_gap_expires_once_and_does_not_resurrect(tmp_path):
    r, store, queue, now = build(tmp_path)
    try:
        r.tick([], epoch='live', eligible=False)
        assert not queue.calls
        now[0] += 121
        r.tick([], epoch='live', eligible=True)
        assert not queue.calls and not r.planner.state['followup_at']
        assert not CompanionStore(tmp_path, r.settings.memory_scope).data['planner']['followup_at']
    finally:
        r.close(); store.close()


def test_baseline_outgoing_activity_updates_persisted_idle_schedule(tmp_path):
    r, store, queue, now = build(tmp_path)
    try:
        now[0] += 600
        r.observe(Message('晚点聊', message_id='manual-out', direction='out',
                          created_at=now[0]), baseline=True)
        persisted = CompanionStore(tmp_path, r.settings.memory_scope).data['planner']
        assert persisted['last_activity'] == now[0]
        assert persisted['next_idle'] == r.planner.state['next_idle']
    finally:
        r.close(); store.close()


def test_editing_interests_preserves_waiting_sequence_and_new_input_cancels_it(tmp_path):
    r, store, queue, now = build(tmp_path)
    try:
        r.tick([], epoch='live', eligible=True); queue.finish()
        c, _ = r.tick([], epoch='live', eligible=True)
        r.begin_send(c); r.finish(c, 'verified')
        due = r.planner.state['checkin_at']
        r.configure(replace(r.planner.preferences, interests='咖啡'))
        assert r.planner.state['checkin_at'] == due
        assert r.planner.state['unanswered']
        r.observe(Message('回来了', message_id='response', created_at=now[0]+1))
        assert not r.planner.state['checkin_at']
        assert not r.planner.state['unanswered']
    finally:
        r.close(); store.close()


def test_frequency_edit_rearms_future_attempt_and_isolated_room_keeps_its_values(tmp_path):
    r, store, queue, now = build(tmp_path)
    other_cfg = SimpleNamespace(runtime=tmp_path, memory_scope='account/other')
    other = CompanionRuntime(other_cfg, store, r.model, lambda:now[0], executor=Deferred())
    try:
        r.configure(replace(r.planner.preferences, idle_min_minutes=5, idle_max_minutes=5,
                            followup_min_minutes=1, followup_max_minutes=1))
        assert r.planner.state['next_idle'] == now[0]+300
        assert r.planner.state['followup_at'] == now[0]+60
        assert other.planner.preferences.idle_min_minutes == 50
        assert not other.planner.preferences.enabled
        assert r.status()['next_candidate_at'] == now[0]+60
        assert other.status()['next_candidate_at'] == 0
    finally:
        other.close(); r.close(); store.close()


def test_expanded_daily_budget_remains_bounded_and_scope_isolated(tmp_path):
    from xiaomao.store import StateStore
    store = StateStore(tmp_path)
    try:
        for i in range(24):
            assert store.reserve_proactive('a'+str(i), 'account/a', '2026-11-04', at(10)+i, 24, account_max=192)
        assert not store.reserve_proactive('over', 'account/a', '2026-11-04', at(11), 24, account_max=192)
        assert store.reserve_proactive('b', 'account/b', '2026-11-04', at(11), 24, account_max=192)
        assert not store.reserve_proactive('bad', 'account/b', '2026-11-04', at(11), 24, account_max=193)
    finally:
        store.close()


@pytest.mark.parametrize('operation', ['scheduled', 'requested', 'preview'])
def test_proactive_generation_callback_accepts_media_plan_and_preview_is_plain_text(tmp_path, operation):
    from xiaomao.stickers import ReplyPlan
    r, store, queue, now = build(tmp_path)
    plan = ReplyPlan(text='下午有空喝杯茶吗？', sticker='approved-local')
    r.proactive_generate = lambda *args: plan
    try:
        if operation == 'requested':
            r.request_once('live'); now[0] += 30
        elif operation == 'preview':
            r.preview([], 'live')
        if operation != 'preview':
            r.tick([], epoch='live', eligible=True)
        queue.finish()
        result = r.tick([], epoch='live', eligible=True)
        if operation == 'preview':
            assert result is None
            assert r.preview_text == plan.text+'（配一张表情，仅预览）'
            assert isinstance(r.status()['preview'], str)
        else:
            assert result[1] == plan
    finally:
        r.close(); store.close()


def test_probability_zero_keeps_quiet_and_one_attempt_is_not_redrawn_in_poll_loop():
    prefs=CompanionPreferences(enabled=True,followup=False,idle_min_minutes=1,
                               idle_max_minutes=1,idle_probability=0)
    rng=MissOnce()
    p=Planner(prefs,fresh_state(),boot_at=at(10),rng=rng)
    p.observe(Message('有空再聊',message_id='in',created_at=at(10)))
    for when in (at(10,1),at(10,1)+1,at(10,1)+10,at(10,1)+59):
        assert p.decide(when) is None
    assert rng.samples==1 and p.due_at==0


def test_checkin_survives_restart_at_same_time_without_redrawing_delay(tmp_path):
    r,store,queue,now=build(tmp_path)
    try:
        r.tick([],epoch='live',eligible=True);queue.finish()
        c,_=r.tick([],epoch='live',eligible=True)
        r.begin_send(c);r.finish(c,'verified')
        due=r.planner.state['checkin_at']
        r.close();now[0]+=60
        resumed=CompanionRuntime(r.settings,store,r.model,lambda:now[0],executor=Deferred())
        try:
            assert resumed.planner.state['checkin_at']==due
            resumed.planner.rng=Predictable();now[0]=due
            resumed.tick([],epoch='restart',eligible=True)
            assert resumed.executor.calls[0][2][1].kind=='checkin'
            resumed.executor.finish()
            c,_=resumed.tick([],epoch='restart',eligible=True)
            resumed.begin_send(c);resumed.finish(c,'unknown')
            assert resumed.planner.decide(at(20)) is None
            assert not resumed.planner.state['signoff_at']
        finally:
            resumed.close()
    finally:
        r.close();store.close()


def test_quiet_hours_cancel_pending_sequence_and_persist_that_decision(tmp_path):
    r,store,queue,now=build(tmp_path)
    try:
        r.tick([],epoch='live',eligible=True);queue.finish()
        c,_=r.tick([],epoch='live',eligible=True)
        r.begin_send(c);r.finish(c,'verified')
        r.configure(replace(r.planner.preferences,quiet_start=10*60+3,quiet_end=11*60))
        now[0]=at(10,3)
        r.tick([],epoch='live',eligible=True)
        assert not r.planner.state['checkin_at']
        assert not CompanionStore(tmp_path,r.settings.memory_scope).data['planner']['checkin_at']
        assert r.status()['next_candidate_at']==0
        assert r.status()['reason']=='免打扰时段'
    finally:
        r.close();store.close()
