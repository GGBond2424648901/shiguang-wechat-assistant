from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest


BEIJING = timezone(timedelta(hours=8))


def stamp(day, clock):
    return datetime.fromisoformat(f'2026-11-{day:02d}T{clock}:00').replace(tzinfo=BEIJING).timestamp()


def test_normalization_migrates_legacy_time_without_mutating_input():
    from xiaomao.news_schedule import normalize_schedule

    config = {'time': '13:30'}
    before = deepcopy(config)
    assert normalize_schedule(config) == {'time': '13:30', 'times': ['13:30'], 'timeRanges': []}
    assert config == before
    assert normalize_schedule({}) == {'time': '09:00', 'times': ['09:00'], 'timeRanges': []}


def test_explicit_times_are_sorted_unique_and_take_priority_over_legacy_time():
    from xiaomao.news_schedule import normalize_schedule

    assert normalize_schedule({'time': '22:00', 'times': ['18:00', '09:00', '13:30', '09:00']}) == {
        'time': '09:00', 'times': ['09:00', '13:30', '18:00'], 'timeRanges': []}


def test_ranges_only_schedule_keeps_first_range_as_compatibility_time():
    from xiaomao.news_schedule import normalize_schedule

    config = {'time': '09:00', 'times': [], 'timeRanges': [
        {'start': '23:30', 'end': '01:00', 'intervalMinutes': 30},
        {'start': '10:00', 'end': '11:00', 'intervalMinutes': 1440}]}
    assert normalize_schedule(config) == {**config, 'time': '23:30'}


@pytest.mark.parametrize('value', [None, [], True, '09:00'])
def test_normalization_requires_an_object(value):
    from xiaomao.news_schedule import normalize_schedule

    with pytest.raises(ValueError, match=r'[\u3400-\u9fff]'):
        normalize_schedule(value)


@pytest.mark.parametrize('changes', [
    {'time': None}, {'time': '9:00'}, {'time': '24:00'}, {'time': '09:60'},
    {'times': None}, {'times': '09:00'}, {'times': ('09:00',)}, {'times': True},
    {'times': []}, {'times': ['09:00'] * 49}, {'times': [None]}, {'times': [True]},
    {'times': ['9:00']}, {'times': ['24:00']}, {'times': ['０９:00']}, {'times': ['09:00\n']},
    {'timeRanges': None}, {'timeRanges': {}}, {'timeRanges': ()}, {'timeRanges': [True]},
    {'timeRanges': [{'start': '09:00', 'end': '10:00', 'intervalMinutes': 30}] * 9},
    {'timeRanges': [{'start': '09:00', 'end': '09:00', 'intervalMinutes': 30}]},
    {'timeRanges': [{'start': '09:00', 'end': '10:00'}]},
    {'timeRanges': [{'end': '10:00', 'intervalMinutes': 30}]},
    {'timeRanges': [{'start': '09:00', 'intervalMinutes': 30}]},
    {'timeRanges': [{'start': '9:00', 'end': '10:00', 'intervalMinutes': 30}]},
    {'timeRanges': [{'start': '09:00', 'end': '24:00', 'intervalMinutes': 30}]},
])
def test_invalid_schedule_fields_are_rejected_with_readable_errors(changes):
    from xiaomao.news_schedule import normalize_schedule

    with pytest.raises(ValueError, match=r'[\u3400-\u9fff]'):
        normalize_schedule(changes)


@pytest.mark.parametrize('interval', [None, True, False, '30', 30.0, 0, -1, 1441])
def test_range_interval_is_a_bounded_integer_and_rejects_booleans(interval):
    from xiaomao.news_schedule import normalize_schedule

    with pytest.raises(ValueError, match=r'[\u3400-\u9fff]'):
        normalize_schedule({'times': [], 'timeRanges': [
            {'start': '09:00', 'end': '10:00', 'intervalMinutes': interval}]})


def test_maximum_schedule_rule_counts_are_supported():
    from xiaomao.news_schedule import normalize_schedule

    config = {'times': [f'00:{minute:02d}' for minute in range(48)], 'timeRanges': [
        {'start': f'{hour:02d}:00', 'end': f'{hour:02d}:01', 'intervalMinutes': 1}
        for hour in range(8)]}
    assert normalize_schedule(config) == {**config, 'time': '00:00'}


def test_occurrences_return_three_actual_fixed_times_in_an_inclusive_window():
    from xiaomao.news_schedule import schedule_occurrences

    config = {'times': ['18:00', '09:00', '13:30', '09:00']}
    before = deepcopy(config)
    assert schedule_occurrences(config, stamp(11, '09:00'), stamp(11, '18:00')) == [
        stamp(11, '09:00'), stamp(11, '13:30'), stamp(11, '18:00')]
    assert config == before
    assert schedule_occurrences(config, stamp(11, '18:00'), stamp(11, '09:00')) == []


@pytest.mark.parametrize(('end', 'expected'), [
    ('11:00', ['09:00', '10:00', '11:00']),
    ('10:30', ['09:00', '10:00']),
])
def test_range_end_is_included_only_when_it_lands_on_the_interval(end, expected):
    from xiaomao.news_schedule import schedule_occurrences

    config = {'times': [], 'timeRanges': [
        {'start': '09:00', 'end': end, 'intervalMinutes': 60}]}
    assert schedule_occurrences(config, stamp(11, '00:00'), stamp(12, '00:00')) == [
        stamp(11, clock) for clock in expected]


def test_overlapping_ranges_and_fixed_times_trigger_once_per_actual_minute():
    from xiaomao.news_schedule import schedule_occurrences

    config = {'times': ['10:00', '09:00'], 'timeRanges': [
        {'start': '09:00', 'end': '10:00', 'intervalMinutes': 30},
        {'start': '09:30', 'end': '10:30', 'intervalMinutes': 30}]}
    assert schedule_occurrences(config, stamp(11, '09:30'), stamp(11, '10:30')) == [
        stamp(11, '09:30'), stamp(11, '10:00'), stamp(11, '10:30')]


def test_overnight_range_uses_its_start_days_weekday_and_actual_slot_dates():
    from xiaomao.news_schedule import schedule_occurrences

    config = {'times': [], 'weekdays': [1], 'timeRanges': [
        {'start': '23:30', 'end': '01:30', 'intervalMinutes': 60}]}
    assert schedule_occurrences(config, stamp(10, '00:00'), stamp(12, '02:00')) == [
        stamp(10, '23:30'), stamp(11, '00:30'), stamp(11, '01:30')]
    assert schedule_occurrences(config, stamp(11, '00:00'), stamp(11, '01:30')) == [
        stamp(11, '00:30'), stamp(11, '01:30')]


def test_sunday_range_survives_the_week_boundary_and_large_interval_does_not_add_endpoint():
    from xiaomao.news_schedule import schedule_occurrences

    config = {'times': [], 'weekdays': [6], 'timeRanges': [
        {'start': '23:30', 'end': '01:30', 'intervalMinutes': 60},
        {'start': '20:00', 'end': '21:00', 'intervalMinutes': 1440}]}
    assert schedule_occurrences(config, stamp(8, '20:00'), stamp(9, '02:00')) == [
        stamp(8, '20:00'), stamp(8, '23:30'), stamp(9, '00:30'), stamp(9, '01:30')]


@pytest.mark.parametrize('weekdays', [None, [], [True], [7], [1.0], '1'])
def test_schedule_compiler_rejects_invalid_weekdays_without_subscription_validation(weekdays):
    from xiaomao.news_schedule import schedule_occurrences

    with pytest.raises(ValueError, match=r'[\u3400-\u9fff]'):
        schedule_occurrences({'time': '09:00', 'weekdays': weekdays}, stamp(11, '00:00'), stamp(12, '00:00'))


def test_due_schedule_selects_latest_round_and_preserves_expired_today_for_missed_recording():
    from xiaomao.news_schedule import due_schedule

    config = {'times': ['09:00', '12:00', '18:00']}
    expected = {'due': stamp(11, '12:00'), 'slot': '2026-11-11T12:00',
                'legacyDay': '2026-11-11', 'time': '12:00',
                'deadline': stamp(11, '14:00'), 'nextAt': stamp(11, '18:00')}
    assert due_schedule(config, stamp(11, '14:00')) == expected
    assert due_schedule(config, stamp(11, '17:00')) == expected
    assert due_schedule(config, stamp(11, '18:00')) == {
        'due': stamp(11, '18:00'), 'slot': '2026-11-11T18:00', 'legacyDay': '2026-11-11',
        'time': '18:00', 'deadline': stamp(11, '20:00'), 'nextAt': stamp(12, '09:00')}


def test_recovery_returns_yesterday_only_inside_the_window_and_never_older_days():
    from xiaomao.news_schedule import due_schedule

    config = {'time': '23:30'}
    assert due_schedule(config, stamp(11, '01:30')) == {
        'due': stamp(10, '23:30'), 'slot': '2026-11-10T23:30', 'legacyDay': '2026-11-10',
        'time': '23:30', 'deadline': stamp(11, '01:30'), 'nextAt': stamp(11, '23:30')}
    assert due_schedule(config, stamp(11, '01:30') + 1) is None
    assert due_schedule({'time': '09:00'}, stamp(11, '08:00')) is None
    assert due_schedule({'time': '09:00', 'weekdays': [0]}, stamp(11, '12:00'), window=7 * 86400) is None


def test_next_round_shortens_recovery_deadline_across_midnight():
    from xiaomao.news_schedule import due_schedule

    config = {'times': ['00:45', '23:30']}
    assert due_schedule(config, stamp(11, '00:30')) == {
        'due': stamp(10, '23:30'), 'slot': '2026-11-10T23:30', 'legacyDay': '2026-11-10',
        'time': '23:30', 'deadline': stamp(11, '00:45'), 'nextAt': stamp(11, '00:45')}


def test_due_overnight_slots_use_actual_day_and_next_enabled_week_is_found():
    from xiaomao.news_schedule import due_schedule

    config = {'times': [], 'weekdays': [1], 'timeRanges': [
        {'start': '23:30', 'end': '00:30', 'intervalMinutes': 60}]}
    assert due_schedule(config, stamp(11, '00:30')) == {
        'due': stamp(11, '00:30'), 'slot': '2026-11-11T00:30', 'legacyDay': '2026-11-11',
        'time': '00:30', 'deadline': stamp(11, '02:30'), 'nextAt': stamp(17, '23:30')}
    assert due_schedule({'time': '19:00', 'weekdays': [2]}, stamp(11, '19:00')) == {
        'due': stamp(11, '19:00'), 'slot': '2026-11-11T19:00', 'legacyDay': '2026-11-11',
        'time': '19:00', 'deadline': stamp(11, '21:00'), 'nextAt': stamp(18, '19:00')}


def test_recovery_only_returns_the_latest_elapsed_interval_round():
    from xiaomao.news_schedule import due_schedule

    config = {'times': [], 'timeRanges': [
        {'start': '09:00', 'end': '11:00', 'intervalMinutes': 20}]}
    assert due_schedule(config, stamp(11, '10:35')) == {
        'due': stamp(11, '10:20'), 'slot': '2026-11-11T10:20', 'legacyDay': '2026-11-11',
        'time': '10:20', 'deadline': stamp(11, '10:40'), 'nextAt': stamp(11, '10:40')}
