from dataclasses import asdict, replace
from datetime import datetime, timedelta

import pytest

from xiaomao.companion import CompanionPreferences, LOCAL, Planner, fresh_state
from xiaomao.domain import Message


def at(hour=9, minute=0, *, day=7, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=LOCAL).timestamp()


def morning(**changes):
    return dict(id='morning', time='09:00', weekdays=list(range(7)), enabled=True,
                intent='早安，关心今天的安排，配一张大肥鱼表情', prefer_sticker=True, **changes)


def preferences(**changes):
    values = dict(enabled=True, idle=False, followup=False, routines=[morning()])
    values.update(changes)
    return CompanionPreferences(**values)


def planner(**changes):
    return Planner(preferences(**changes), fresh_state(), boot_at=at(8, 58))


def test_new_conversations_and_presets_do_not_enable_unsolicited_schedules():
    from xiaomao.routines import routine_preset
    assert CompanionPreferences().routines == []
    rows = [routine_preset(name) for name in ('morning', 'noon', 'afternoon', 'evening')]
    assert [row['time'] for row in rows] == ['09:00', '12:00', '15:00', '20:00']
    assert all(not row['enabled'] for row in rows)
    assert len({row['id'] for row in rows}) == 4
    assert all(row['weekdays'] == [0, 1, 2, 3, 4, 5, 6] for row in rows)


@pytest.mark.parametrize('change', [
    {'id': ''}, {'id': 'a/b'}, {'time': '9:00'}, {'time': '24:00'},
    {'time': '09:60'}, {'weekdays': []}, {'weekdays': [True]},
    {'weekdays': [7]}, {'weekdays': [1, 1]}, {'weekdays': 'daily'},
    {'enabled': 1}, {'prefer_sticker': 'yes'}, {'intent': ''},
    {'intent': 'x' * 161}, {'unknown': 'field'},
])
def test_schedule_fields_are_validated_before_any_save(change):
    row = morning()
    row.update(change)
    with pytest.raises(ValueError):
        preferences(routines=[row])


def test_schedule_count_and_identity_are_bounded():
    with pytest.raises(ValueError):
        preferences(routines=[morning(), morning()])
    with pytest.raises(ValueError):
        preferences(routines=[dict(morning(), id=str(i)) for i in range(9)])


@pytest.mark.parametrize('unanswered', [False, True])
def test_nine_oclock_routine_needs_no_inbound_and_ignores_old_unanswered_opener(unanswered):
    p = planner()
    p.state['unanswered'] = unanswered
    p.state['unanswered_at'] = at(20, day=6) if unanswered else 0
    assert p.decide(at(8, 59)) is None
    assert p.due_at == at(9)
    candidate = p.decide(at(9))
    assert candidate.kind == 'routine' and candidate.routine_id == 'morning'
    assert candidate.label == '早安，关心今天的安排，配一张大肥鱼表情'
    assert candidate.prefer_sticker and candidate.expires == at(9, 5)
    assert p.valid(candidate, at(9))
    assert p.decide(at(9)) is None


def test_routine_does_not_wait_out_generic_sixty_second_boot_grace():
    p = Planner(preferences(), fresh_state(), boot_at=at(8, 59, second=55))
    assert p.decide(at(9)).kind == 'routine'


def test_weekday_and_quiet_hours_are_respected_without_hours_late_catchup():
    weekday = dict(morning(), weekdays=[0, 1, 2, 3, 4])
    assert planner(routines=[weekday]).decide(at(9)) is None  # Saturday.
    p = planner(quiet_start=8 * 60, quiet_end=10 * 60)
    assert p.decide(at(9)) is None
    assert p.decide(at(10)) is None
    assert p.decide(at(18)) is None
    assert p.decide(at(9, day=8)) is None  # Still within that quiet interval.


def test_missed_window_is_recorded_and_tomorrows_routine_remains_due():
    p = planner()
    assert p.decide(at(9, 5, second=1)) is None
    record = p.state['routine_history'][-1]
    assert record['day'] == '2026-11-07' and record['status'] == 'missed'
    assert p.decide(at(9, day=8)).kind == 'routine'


def test_restart_before_due_keeps_plan_and_invalidates_old_candidates():
    p = planner()
    p.decide(at(8, 59))
    assert p.state['routine_history'][0]['status'] == 'planned'
    resumed = Planner(p.preferences, p.state, boot_at=at(8, 59))
    candidate = resumed.decide(at(9))
    assert candidate.kind == 'routine' and resumed.valid(candidate, at(9))
    again = Planner(resumed.preferences, resumed.state, boot_at=at(9, 0, second=10))
    assert not again.valid(candidate, at(9, 0, second=30))
    retry = again.decide(at(9, 0, second=30))
    assert retry.id != candidate.id


def test_clock_rollback_invalidates_candidate_without_replaying_sent_day():
    p = planner()
    candidate = p.decide(at(9))
    p.delivered(candidate, at(9))
    assert p.decide(at(8, 59)) is None
    assert not p.valid(candidate, at(9), continuing=True)
    assert p.decide(at(9, 1)) is None
    assert p.decide(at(9, day=8)).kind == 'routine'


def test_new_inbound_and_unrelated_preferences_cancel_stale_content_not_future_routines():
    p = planner()
    candidate = p.decide(at(9))
    p.observe(Message('新的安排', message_id='new', created_at=at(9, 0, second=1)))
    assert not p.valid(candidate, at(9, 0, second=2))
    p.configure(replace(p.preferences, interests='咖啡'), at(9, 0, second=3))
    assert p.preferences.routines == [morning()]
    retry = p.decide(at(9, 0, second=30))
    assert retry and retry.id != candidate.id and p.valid(retry, at(9, 0, second=30))
    assert p.decide(at(9, day=8)).kind == 'routine'


def test_routine_continuations_still_require_the_delivered_identity_and_revision():
    p = planner()
    p.state['unanswered'] = True
    candidate = p.decide(at(9))
    assert p.valid(candidate, at(9)) and not p.valid(candidate, at(9), continuing=True)
    p.delivered(candidate, at(9))
    assert p.valid(candidate, at(9), continuing=True)
    assert not p.valid(candidate, at(9))
    p.observe(Message('先等等', message_id='wait', created_at=at(9, 0, second=1)))
    assert not p.valid(candidate, at(9, 0, second=2), continuing=True)


def test_editing_a_missed_unsent_routine_preserves_its_new_future_time_today():
    p = planner()
    assert p.decide(at(9, 6)) is None
    later = dict(morning(), time='10:00')
    p.configure(replace(p.preferences, routines=[later]), at(9, 7))
    assert p.decide(at(9, 59)) is None
    candidate = p.decide(at(10))
    assert candidate and candidate.kind == 'routine' and p.valid(candidate, at(10))


def test_editing_weekdays_cannot_generate_a_candidate_on_the_excluded_day():
    p = planner()
    p.decide(at(8, 59))
    weekday = dict(morning(), weekdays=[0, 1, 2, 3, 4])
    p.configure(replace(p.preferences, routines=[weekday]), at(8, 59))
    assert p.decide(at(9)) is None


def test_history_is_bounded_and_json_canonical_after_many_local_days():
    import json
    rows = [dict(morning(), id=str(i), time=f'09:{i:02}') for i in range(8)]
    p = planner(routines=rows)
    start = datetime(2026, 11, 7, 9, tzinfo=LOCAL)
    for offset in range(15):
        p.decide((start + timedelta(days=offset, hours=1)).timestamp())
    history = p.state['routine_history']
    assert len(history) == 64
    assert history[-1]['day'] == '2026-11-21'
    assert json.loads(json.dumps(asdict(p.preferences)))['routines'] == rows
    json.dumps(p.state, allow_nan=False)
