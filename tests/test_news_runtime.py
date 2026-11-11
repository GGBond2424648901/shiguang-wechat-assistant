from concurrent.futures import Future
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from xiaomao.news_runtime import NewsRuntime


def at(hour=9, minute=0, day=11):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone(timedelta(hours=8))).timestamp()


class Deferred:
    def __init__(self): self.jobs = []
    def submit(self, fn, *args, **kwargs):
        future = Future(); self.jobs.append((future, fn, args, kwargs)); return future
    def finish(self):
        future, fn, args, kwargs = self.jobs[-1]
        if not future.cancelled(): future.set_result(fn(*args, **kwargs))
    def shutdown(self, **kwargs): pass


class MemoryStore:
    def __init__(self): self.subs = {}; self.editions = {}; self.seen = {}; self.uncertain = {}
    def list_subscriptions(self, account):
        return [deepcopy(s) for (a, _), s in self.subs.items() if a == account]
    def get_subscription(self, account, identity): return deepcopy(self.subs.get((account, identity)))
    def create_edition(self, account, subscription_id, snapshot, trigger, slot, now):
        if slot and any(e['account'] == account and e['subscriptionId'] == subscription_id and e['slot'] == slot for e in self.editions.values()): return None
        identity = f'edition{len(self.editions)}'
        row = dict(id=identity, account=account, subscriptionId=subscription_id, snapshot=deepcopy(snapshot), trigger=trigger, slot=slot, createdAt=now, updatedAt=now, state='collecting', reason='', articles=[], parts=[], warnings=[], deliveries=[])
        self.editions[identity] = row; return deepcopy(row)
    def get_edition(self, account, identity):
        row = self.editions.get(identity)
        return deepcopy(row) if row and row['account'] == account else None
    def update_edition(self, account, identity, **changes):
        row = self.editions[identity]; assert row['account'] == account
        if row['state'] == 'cancelled': changes = {k:v for k,v in changes.items() if k in ('deliveries','receiptPending')}
        row.update(deepcopy(changes))
        return deepcopy(row)
    def history(self, account, subscription_id=None, limit=20):
        return [deepcopy(e) for e in reversed(list(self.editions.values())) if e['account'] == account and (not subscription_id or e['subscriptionId'] == subscription_id)][:limit]
    def pending(self, account):
        return [deepcopy(e) for e in reversed(list(self.editions.values())) if e['account'] == account and (e['state'] in ('collecting', 'ready', 'waiting', 'publishing') or e.get('receiptPending'))]
    def seen_urls(self, account, scope): return self.seen.get((account, scope), set()).copy()
    def mark_seen(self, account, scope, urls, now):
        self.seen.setdefault((account, scope), set()).update(urls)
        self.uncertain.setdefault((account, scope), set()).difference_update(urls)
    def uncertain_urls(self, account, scope): return self.uncertain.get((account, scope), set()).copy()
    def mark_uncertain(self, account, scope, urls, now): self.uncertain.setdefault((account, scope), set()).update(set(urls)-self.seen_urls(account,scope))
    def close(self): pass


@pytest.fixture
def rig(tmp_path):
    store = MemoryStore(); executor = Deferred(); clock = [at()]
    context = {'account': 'account-a', 'targets': [dict(session='session-a', name='测试会话', scope='account-a/friend', mode='auto', ready=True, busy=False, reason='')]}
    config = dict(id='sub1', name='每日早报', enabled=True, revision=1, categories=['game'], time='09:00', weekdays=list(range(7)), itemCount=2, includeLinks=True, recipients=['session-a'], targets=[dict(session='session-a', name='测试会话', scope='account-a/friend')])
    store.subs[('account-a', 'sub1')] = config
    sent = []; states = {}; cancelled = []; searches = []
    articles = [dict(id=str(n), title=f'新闻{n}', summary='公开来源摘要', url=f'https://example.com/news/{n}', source='example.com', category='游戏', publishedAt=clock[0], timeLabel='今日') for n in (1, 2)]
    class Research:
        def collect(self, subscription, now, cancelled=lambda: False):
            searches.append(deepcopy(subscription)); return dict(articles=articles, parts=['测试新闻一', '测试新闻二'], warnings=[])
    def deliver(target, text, request_id):
        task_id = f'task{len(sent)}'; sent.append((target, text, request_id, task_id)); states[task_id] = dict(state='generated', attempted=False)
        return dict(queued=True, id=task_id)
    def cancel(ids):
        cancelled.extend(ids)
        for identity in ids:
            if identity in states and not states[identity]['attempted']: states[identity] = dict(state='skipped', attempted=False, reason='cancelled')
    def lookup(target, request_id):
        match = next((s for s in sent if s[2] == request_id and s[0]['scope'] == target['scope']), None)
        return {'id': match[3], **states[match[3]]} if match else {}
    runtime = NewsRuntime(tmp_path, context=lambda: deepcopy(context), deliver=deliver, delivery_status=lambda identity: states.get(identity, {}), delivery_lookup=lookup, cancel_delivery=cancel, research_factory=lambda: Research(), clock=lambda: clock[0], store=store, executor=executor)
    yield SimpleNamespace(runtime=runtime, store=store, executor=executor, clock=clock, context=context, config=config, sent=sent, states=states, cancelled=cancelled, searches=searches)
    runtime.close()


def preview(rig):
    rig.config['enabled'] = False
    result = rig.runtime.command('sub1', 'preview')
    rig.executor.finish(); rig.runtime.tick()
    return rig.store.get_edition('account-a', result['edition'])


def test_preview_does_not_send_until_push_and_each_part_waits_for_receipt(rig):
    edition = preview(rig)
    assert edition['state'] == 'ready' and not rig.sent
    rig.runtime.command('sub1', 'push', edition['id']); rig.runtime.tick()
    assert len(rig.sent) == 1
    rig.runtime.tick(); assert len(rig.sent) == 1
    rig.states[rig.sent[0][3]] = dict(state='verified', attempted=True)
    rig.runtime.tick(); assert len(rig.sent) == 2
    rig.states[rig.sent[1][3]] = dict(state='verified', attempted=True)
    rig.runtime.tick()
    assert rig.store.get_edition('account-a', edition['id'])['state'] == 'verified'
    assert rig.store.seen_urls('account-a', 'account-a/friend') == {'https://example.com/news/1', 'https://example.com/news/2'}
    rig.runtime.command('sub1', 'push', edition['id']); rig.runtime.tick()
    assert len(rig.sent) == 2


def test_schedule_is_beijing_time_once_per_day_and_late_jobs_are_not_sent(rig):
    rig.clock[0] = at(8, 59); rig.runtime.tick(); assert not rig.executor.jobs
    rig.clock[0] = at(9); rig.runtime.tick(); rig.runtime.tick()
    assert len(rig.executor.jobs) == 1
    rig.executor.finish(); rig.runtime.tick(); assert len(rig.sent) == 1
    rig.clock[0] = at(12, day=12); rig.runtime.tick()
    assert len(rig.executor.jobs) == 1
    assert rig.store.history('account-a')[0]['state'] == 'missed'


def test_paused_target_waits_but_another_ready_target_sends(rig):
    target = dict(session='session-b', name='另一个目标', scope='account-a/group', mode='auto', ready=True, busy=False, reason='')
    rig.context['targets'].append(target)
    rig.config['targets'].append({k: target[k] for k in ('session', 'name', 'scope')}); rig.config['recipients'].append('session-b')
    rig.context['targets'][0]['mode'] = 'paused'
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    assert len(rig.sent) == 1 and rig.sent[0][0]['session'] == 'session-b'
    edition = rig.store.history('account-a')[0]
    assert '暂停' in edition['deliveries'][0]['reason']
    rig.context['targets'][0]['mode'] = 'auto'; rig.runtime.tick()
    assert any(s[0]['session'] == 'session-a' for s in rig.sent)


def test_unknown_receipt_is_not_automatically_retried(rig):
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    task_id = rig.sent[0][3]; rig.states[task_id] = dict(state='unknown', attempted=True)
    for _ in range(3): rig.runtime.tick()
    assert len(rig.sent) == 1
    edition = rig.store.history('account-a')[0]
    assert edition['deliveries'][0]['state'] == 'unknown'
    assert not rig.store.seen_urls('account-a', 'account-a/friend')


def test_changed_revision_cancels_queued_send_and_stale_push(rig):
    edition = preview(rig)
    rig.config['revision'] = 2
    with pytest.raises(ValueError, match='设置|重新|更新'):
        rig.runtime.command('sub1', 'push', edition['id'])
    assert not rig.sent


def test_scope_change_does_not_send_to_same_named_other_identity(rig):
    rig.context['targets'][0]['scope'] = 'account-a/other'
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    assert not rig.sent
    edition = rig.store.history('account-a')[0]
    assert edition['deliveries'][0]['state'] == 'failed'


def test_cancel_stops_unsent_parts(rig):
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    edition = rig.store.history('account-a')[0]
    rig.runtime.command('sub1', 'cancel', edition['id']); rig.runtime.tick()
    assert rig.sent[0][3] in rig.cancelled
    assert rig.store.get_edition('account-a', edition['id'])['state'] == 'cancelled'
    assert len(rig.sent) == 1


def test_seen_articles_are_filtered_per_recipient_and_not_by_preview(rig):
    rig.store.mark_seen('account-a', 'account-a/friend', ['https://example.com/news/1'], rig.clock[0])
    edition = preview(rig)
    assert len(edition['articles']) == 2
    rig.runtime.command('sub1', 'push', edition['id']); rig.runtime.tick()
    assert len(rig.sent) == 1 and '新闻2' in rig.sent[0][1]


def test_async_completion_after_account_change_cannot_publish(rig):
    rig.runtime.tick()
    rig.context['account'] = 'account-b'
    rig.executor.finish(); rig.runtime.tick()
    assert not rig.sent
    assert rig.store.history('account-a')[0]['state'] == 'cancelled'


def test_pause_after_queue_preserves_unsent_part_and_resumes_with_new_id(rig):
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    first = rig.sent[0]
    rig.states[first[3]] = dict(state='skipped', attempted=False, reason='paused')
    rig.context['targets'][0]['mode'] = 'paused'
    rig.runtime.tick()
    assert len(rig.sent) == 1
    assert rig.store.history('account-a')[0]['deliveries'][0]['state'] == 'waiting'
    rig.context['targets'][0]['mode'] = 'auto'; rig.runtime.tick()
    assert len(rig.sent) == 2 and rig.sent[-1][2] != first[2]


def test_catchup_includes_previous_day_inside_window_and_previous_weekday(rig):
    rig.config.update(time='23:30', weekdays=[1])  # 2026-11-10 is Tuesday.
    rig.clock[0] = at(0, 30)
    rig.runtime.tick()
    assert len(rig.executor.jobs) == 1
    assert rig.store.history('account-a')[0]['slot'] == '2026-11-10'


def test_cancel_during_native_send_still_audits_later_receipt(rig):
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    edition = rig.store.history('account-a')[0]
    task = rig.sent[0][3]
    rig.states[task] = dict(state='sending', attempted=True)
    rig.runtime.command('sub1', 'cancel', edition['id'])
    rig.states[task] = dict(state='verified', attempted=True)
    rig.runtime.tick()
    assert 'https://example.com/news/1' in rig.store.seen_urls('account-a', 'account-a/friend')
    assert rig.store.get_edition('account-a', edition['id'])['state'] == 'cancelled'
    assert not rig.store.get_edition('account-a', edition['id']).get('receiptPending')


def test_concurrent_subscriptions_do_not_duplicate_a_source_after_waiting(rig):
    second = deepcopy(rig.config); second['id'] = 'sub2'
    rig.store.subs[('account-a', 'sub2')] = second
    rig.runtime.tick()
    for future, fn, args, kwargs in rig.executor.jobs: future.set_result(fn(*args, **kwargs))
    old_deliver = rig.runtime.deliver
    def deliver(*args):
        assert not rig.context['targets'][0]['busy']
        result = old_deliver(*args); rig.context['targets'][0]['busy'] = True
        return result
    rig.runtime.deliver = deliver
    for _ in range(8):
        rig.runtime.tick()
        for task_id in rig.states: rig.states[task_id] = dict(state='verified', attempted=True)
        rig.context['targets'][0]['busy'] = False
    assert len(rig.sent) == 2


def test_unknown_source_is_not_resent_by_a_new_edition(rig):
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    rig.states[rig.sent[0][3]] = dict(state='unknown', attempted=True)
    rig.runtime.tick()
    edition = preview(rig)
    rig.runtime.command('sub1', 'push', edition['id']); rig.runtime.tick()
    assert len(rig.sent) == 1


@pytest.mark.parametrize('outcome', ['verified','unknown'])
def test_expiration_during_send_still_records_a_late_receipt(rig, outcome):
    rig.runtime.tick(); rig.executor.finish(); rig.runtime.tick()
    edition = rig.store.history('account-a')[0]
    task_id = rig.sent[0][3]
    rig.states[task_id] = dict(state='sending', attempted=True)
    rig.clock[0] += 7300; rig.runtime.tick()
    rig.states[task_id] = dict(state=outcome, attempted=True)
    rig.runtime.tick()
    if outcome == 'verified': assert 'https://example.com/news/1' in rig.store.seen_urls('account-a','account-a/friend')
    else: assert 'https://example.com/news/1' in rig.store.uncertain_urls('account-a','account-a/friend')
    assert not rig.store.get_edition('account-a',edition['id']).get('receiptPending')


def test_newer_waiting_edition_cannot_overtake_older_unharvested_receipt(rig):
    rig.config['enabled'] = False
    old_deliver = rig.runtime.deliver
    def deliver(*args):
        result = old_deliver(*args); rig.context['targets'][0]['busy'] = True; return result
    rig.runtime.deliver = deliver
    first = preview(rig)
    rig.runtime.command('sub1','push',first['id']); rig.runtime.tick()
    assert len(rig.sent) == 1
    second = preview(rig)
    rig.runtime.command('sub1','push',second['id']); rig.runtime.tick()
    rig.states[rig.sent[0][3]] = dict(state='verified',attempted=True)
    rig.context['targets'][0]['busy'] = False
    rig.runtime.tick()
    assert len(rig.sent) == 1 or '新闻2' in rig.sent[-1][1]
    assert sum('https://example.com/news/1' in s[1] for s in rig.sent) == 1


def test_native_queue_acceptance_is_recovered_when_news_journal_commit_fails(rig):
    rig.config['enabled'] = False
    first = preview(rig)
    rig.runtime.command('sub1','push',first['id'])
    original = rig.store.update_edition
    armed = [True]
    def interrupted(account, identity, **changes):
        if armed[0] and changes.get('state') == 'publishing':
            armed[0] = False; raise OSError('journal interrupted after native queue accepted')
        return original(account, identity, **changes)
    rig.store.update_edition = interrupted
    with pytest.raises(OSError): rig.runtime.tick()
    assert len(rig.sent) == 1
    rig.states[rig.sent[0][3]] = dict(state='unknown', attempted=True)
    second = preview(rig)
    rig.runtime.command('sub1','push',second['id']); rig.runtime.tick()
    assert len(rig.sent) == 1
    assert 'https://example.com/news/1' in rig.store.uncertain_urls('account-a','account-a/friend')


def test_receipt_audit_is_not_hidden_by_more_than_64_ready_previews(rig):
    rig.config['enabled'] = False
    first = preview(rig)
    rig.runtime.command('sub1','push',first['id']);rig.runtime.tick()
    rig.context['targets'][0]['busy'] = True
    for _ in range(65):
        newer = rig.store.create_edition('account-a','sub1',rig.config,'preview','',rig.clock[0])
        rig.store.update_edition('account-a',newer['id'],state='ready',articles=first['articles'],parts=first['parts'])
    rig.runtime.command('sub1','push',newer['id'])
    rig.states[rig.sent[0][3]] = dict(state='unknown',attempted=True)
    rig.context['targets'][0]['busy'] = False;rig.runtime.tick()
    assert len(rig.sent) == 1
    assert 'https://example.com/news/1' in rig.store.uncertain_urls('account-a','account-a/friend')
