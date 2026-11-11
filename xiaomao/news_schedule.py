"""Normalize and compile recurring public-news schedules in Beijing time."""

from datetime import datetime, timedelta, timezone
import re


BEIJING = timezone(timedelta(hours=8))
_CLOCK = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]')
_DAY_SECONDS = 86400


def _clock(value):
    if not isinstance(value, str) or not _CLOCK.fullmatch(value):
        raise ValueError('推送时间应为有效的 HH:MM 北京时间')
    return value


def _minutes(value):
    hour, minute = value.split(':')
    return int(hour) * 60 + int(minute)


def normalize_schedule(value):
    """Return an independent canonical schedule, accepting the old time field."""
    if not isinstance(value, dict):
        raise ValueError('推送计划必须为对象')
    times = value.get('times') if 'times' in value else [value.get('time', '09:00')]
    if not isinstance(times, list) or len(times) > 48:
        raise ValueError('固定推送时间必须为列表，最多四十八项')
    times = sorted({_clock(clock) for clock in times})
    ranges = value.get('timeRanges', [])
    if not isinstance(ranges, list) or len(ranges) > 8:
        raise ValueError('推送时间段必须为列表，最多八段')
    clean_ranges = []
    for item in ranges:
        if not isinstance(item, dict):
            raise ValueError('推送时间段必须为对象')
        start, end = _clock(item.get('start')), _clock(item.get('end'))
        if start == end:
            raise ValueError('时间段开始与结束不能相同')
        interval = item.get('intervalMinutes')
        if type(interval) is not int or not 1 <= interval <= 1440:
            raise ValueError('推送间隔应为一至一千四百四十分钟的整数')
        clean_ranges.append(dict(start=start, end=end, intervalMinutes=interval))
    if not times and not clean_ranges:
        raise ValueError('请至少设置一个固定时间或时间段')
    return dict(time=times[0] if times else clean_ranges[0]['start'],
                times=times, timeRanges=clean_ranges)


def _compile(config):
    schedule = normalize_schedule(config)
    weekdays = config.get('weekdays', list(range(7)))
    if (not isinstance(weekdays, list) or not 1 <= len(weekdays) <= 7
            or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)):
        raise ValueError('请至少选择一个有效星期')
    offsets = {_minutes(clock) for clock in schedule['times']}
    for item in schedule['timeRanges']:
        start, end = _minutes(item['start']), _minutes(item['end'])
        if end < start:
            end += 1440
        offsets.update(range(start, end + 1, item['intervalMinutes']))
    return sorted(offsets), set(weekdays)


def _occurrences(offsets, weekdays, start, end):
    if end < start:
        return []
    # An overnight range can begin on the preceding calendar day. Its whole
    # range still uses that anchor day's weekday, including post-midnight slots.
    anchor = datetime.fromtimestamp(start, BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    anchor -= timedelta(days=1)
    last_day = datetime.fromtimestamp(end, BEIJING).date()
    result = set()
    while anchor.date() <= last_day:
        if anchor.weekday() in weekdays:
            midnight = anchor.timestamp()
            result.update(due for offset in offsets
                          if start <= (due := midnight + offset * 60) <= end)
        anchor += timedelta(days=1)
    return sorted(result)


def schedule_occurrences(config, start, end):
    """Return sorted unique actual timestamps in the inclusive interval."""
    return _occurrences(*_compile(config), start, end)


def due_schedule(config, now, window=7200):
    """Select only the latest elapsed round from today or recoverable yesterday."""
    offsets, weekdays = _compile(config)
    midnight = datetime.fromtimestamp(now, BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    today = midnight.timestamp()
    elapsed = _occurrences(offsets, weekdays, today - _DAY_SECONDS, now)
    if not elapsed:
        return None
    due = elapsed[-1]
    if due < today and now - due > window:
        return None
    # The same actual occurrence repeats weekly. Include eight full days after
    # this timestamp so a sole late-day slot next week cannot fall off the scan.
    upcoming = _occurrences(offsets, weekdays, due + 1, due + 8 * _DAY_SECONDS)
    next_at = upcoming[0] if upcoming else None
    local_due = datetime.fromtimestamp(due, BEIJING)
    return dict(due=due, slot=local_due.strftime('%Y-%m-%dT%H:%M'),
                legacyDay=local_due.strftime('%Y-%m-%d'), time=local_due.strftime('%H:%M'),
                deadline=min(due + window, next_at) if next_at is not None else due + window,
                nextAt=next_at)
