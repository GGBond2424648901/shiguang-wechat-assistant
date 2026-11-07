"""Pure Shanghai-local recurring care plans and bounded durable attempt ledger."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
import uuid


LOCAL = timezone(timedelta(hours=8))
MAX_ROUTINES = 8
MAX_HISTORY = 64
MAX_ATTEMPTS = 3
RETRY_SECONDS = 30
DUE_WINDOW = 5 * 60
TERMINAL = {'verified', 'unknown', 'partial', 'missed'}
PRESETS = {
    'morning': ('09:00', '轻松说早安，关心早餐和今天的安排'),
    'noon': ('12:00', '午间问候，关心午饭和休息'),
    'afternoon': ('15:00', '午后关心，轻松聊聊此刻的小事'),
    'evening': ('20:00', '晚间问候，关心今天过得如何，自然收尾'),
}


def routine_preset(name):
    when, intent = PRESETS[name]
    return dict(id=uuid.uuid4().hex, time=when, weekdays=list(range(7)),
                enabled=False, intent=intent, prefer_sticker=True)


def validate_routines(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROUTINES:
        raise ValueError('每个会话最多设置 8 条定时关怀')
    identities = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'time', 'weekdays', 'enabled', 'intent', 'prefer_sticker'}:
            raise ValueError('定时关怀字段无效')
        if not isinstance(row['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', row['id']) or row['id'] in identities:
            raise ValueError('定时关怀编号无效或重复')
        identities.add(row['id'])
        if not isinstance(row['time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', row['time']):
            raise ValueError('关怀时间请使用 HH:MM 格式')
        days = row['weekdays']
        if (not isinstance(days, list) or not 1 <= len(days) <= 7
                or any(type(day) is not int or not 0 <= day <= 6 for day in days)
                or len(set(days)) != len(days)):
            raise ValueError('请至少选择一个星期，星期不能重复')
        if type(row['enabled']) is not bool or type(row['prefer_sticker']) is not bool:
            raise ValueError('关怀开关和表情偏好必须是布尔值')
        if not isinstance(row['intent'], str) or not row['intent'].strip() or len(row['intent']) > 160:
            raise ValueError('关怀意图请填写 1 至 160 个字符')


def _due(row, value):
    hour, minute = map(int, row['time'].split(':'))
    return value.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


class RoutineScheduler:
    """Lives inside a conversation's encrypted planner state; never performs I/O."""

    def __init__(self, state):
        self.state = state
        self.history = state.setdefault('routine_history', [])
        if not isinstance(self.history, list) or any(not isinstance(row, dict) for row in self.history):
            raise ValueError('定时关怀记录格式无效')
        self.history[:] = self.history[-MAX_HISTORY:]
        self.recovered = []
        for row in self.history:
            status, identity = row.get('status'), row.get('candidate_id')
            if status == 'sending':
                row['status'] = 'partial' if row.get('confirmed_parts') else 'unknown'
                if identity:
                    self.recovered.append((identity, row['status']))
            elif status in ('planned', 'generating', 'queued') and identity:
                self.recovered.append((identity, 'skipped'))
                row.update(status='planned', candidate_id='')

    def invalidate(self):
        for row in self.history:
            if row['status'] in ('planned', 'generating', 'queued'):
                row.update(status='planned', candidate_id='')

    def _record(self, identity, day):
        return next((row for row in self.history if row['routine_id'] == identity and row['day'] == day), None)

    def maintain(self, rows, now):
        value = datetime.fromtimestamp(now, LOCAL)
        day = value.strftime('%Y-%m-%d')
        for row in self.history:
            if row['status'] in ('planned', 'generating', 'queued') and now > row['expires_at']:
                row['status'] = 'missed'
        for routine in rows:
            if not routine['enabled'] or value.weekday() not in routine['weekdays']:
                continue
            record = self._record(routine['id'], day)
            due = _due(routine, value)
            if record is None:
                record = dict(routine_id=routine['id'], day=day, due_at=due, expires_at=due+DUE_WINDOW,
                              status='planned', attempts=0, last_attempt_at=0, candidate_id='',
                              revision=0, confirmed_parts=0, definition=deepcopy(routine))
                self.history.append(record)
            elif (record['status'] not in {'verified','unknown','partial','sending'}
                  and record['definition'] != routine):
                record.update(due_at=due, expires_at=due+DUE_WINDOW, status='planned', attempts=0,
                              last_attempt_at=0, candidate_id='', definition=deepcopy(routine))
            if record['status'] == 'planned' and now > record['expires_at']:
                record['status'] = 'missed'
        self.history.sort(key=lambda row: (row['day'], row['due_at'], row['routine_id']))
        del self.history[:-MAX_HISTORY]

    def plan(self, rows, now, revision):
        self.maintain(rows, now)
        value = datetime.fromtimestamp(now, LOCAL)
        day = value.strftime('%Y-%m-%d')
        active = {row['id']: row for row in rows if row['enabled'] and value.weekday() in row['weekdays']}
        for record in self.history:
            if (record['day'] != day or record['routine_id'] not in active
                    or record['status'] != 'planned' or record['candidate_id']
                    or record['attempts'] >= MAX_ATTEMPTS or now < record['due_at']
                    or now > record['expires_at']
                    or record['last_attempt_at'] and now < record['last_attempt_at']+RETRY_SECONDS):
                continue
            record.update(candidate_id=uuid.uuid4().hex, revision=revision,
                          attempts=record['attempts']+1, last_attempt_at=now)
            return record

    def valid(self, candidate, rows, now, *, continuing=False):
        record = self._record(candidate.routine_id, candidate.day)
        routine = next((row for row in rows if row['id'] == candidate.routine_id), None)
        return bool(record and routine and routine['enabled'] and record['definition'] == routine
                    and record['candidate_id'] == candidate.id and record['revision'] == candidate.revision
                    and record['due_at'] <= now <= record['expires_at']
                    and record['status'] in ({'sending'} if continuing else {'planned', 'generating', 'queued'}))

    def mark(self, candidate, status):
        record = self._record(candidate.routine_id, candidate.day)
        if not record or record['candidate_id'] != candidate.id or record['status'] in TERMINAL:
            return False
        record['status'] = status
        return True

    def progress(self, candidate):
        record = self._record(candidate.routine_id, candidate.day)
        if record and record['candidate_id'] == candidate.id and record['status'] == 'sending':
            record['confirmed_parts'] += 1

    def finish(self, candidate, status):
        record = self._record(candidate.routine_id, candidate.day)
        if not record or record['candidate_id'] != candidate.id:
            return
        if record['status'] in TERMINAL:
            return record['status']
        if status in ('verified', 'unknown', 'partial'):
            # Intermediate receipts call progress; the final receipt calls
            # finish directly. Count it here once before sealing the record.
            if status == 'verified':
                record['confirmed_parts'] += 1
            record['status'] = status
        elif record['status'] == 'sending':
            record['status'] = 'partial' if record['confirmed_parts'] else 'unknown'
        else:
            record.update(status='planned', candidate_id='')
        return record['status']

    def due_at(self, rows, now):
        value = datetime.fromtimestamp(now, LOCAL)
        choices = []
        for row in rows:
            if not row['enabled']:
                continue
            for offset in range(8):
                date = value + timedelta(days=offset)
                if date.weekday() not in row['weekdays']:
                    continue
                due = _due(row, date)
                record = self._record(row['id'], date.strftime('%Y-%m-%d'))
                if due+DUE_WINDOW < now or record and (record['status'] in TERMINAL or record['status'] == 'sending'):
                    continue
                if record and not record['candidate_id']:
                    if record['attempts'] >= MAX_ATTEMPTS:
                        continue
                    due = max(due, record['last_attempt_at']+RETRY_SECONDS if record['last_attempt_at'] else due)
                choices.append(due)
                break
        return min(choices) if choices else 0

    def summary(self, rows, now):
        result = []
        for routine in rows:
            records = [row for row in self.history if row['routine_id'] == routine['id']]
            last = records[-1] if records else {}
            result.append(dict(id=routine['id'], time=routine['time'], intent=routine['intent'],
                               enabled=routine['enabled'], next_due_at=self.due_at([routine], now),
                               status=last.get('status', 'planned'), day=last.get('day', ''),
                               attempts=last.get('attempts', 0)))
        return result
