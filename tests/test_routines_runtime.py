from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from xiaomao.companion_store import CompanionStore
from xiaomao.companion_runtime import CompanionRuntime
from xiaomao.domain import Message
from xiaomao.store import StateStore
from test_companion_runtime import Deferred
from test_routines import at, morning, preferences


def build(tmp_path, *, scope='account/contact', store=None, now=None):
    now = now or [at(8, 58)]
    notes = CompanionStore(tmp_path, scope)
    notes.data['preferences'] = asdict(preferences())
    notes.save()
    store = store or StateStore(tmp_path)
    settings = SimpleNamespace(runtime=tmp_path, memory_scope=scope)
    model = SimpleNamespace(proactive=lambda *args: '早安呀，今天也慢慢来～')
    runtime = CompanionRuntime(settings, store, model, lambda: now[0], executor=Deferred())
    return runtime, store, now


def history(runtime):
    return CompanionStore(runtime.settings.runtime, runtime.settings.memory_scope).data['planner']['routine_history']


def generate(runtime, now):
    now[0] = at(9)
    runtime.tick([], epoch='live', eligible=True)
    runtime.executor.finish()
    return runtime.tick([], epoch='live', eligible=True)


def restart(runtime, store, now):
    runtime.close()
    return CompanionRuntime(runtime.settings, store, runtime.model, lambda: now[0], executor=Deferred())


def test_generation_queue_and_send_status_are_persisted_before_external_actions(tmp_path, monkeypatch):
    r, store, now = build(tmp_path)
    try:
        now[0] = at(8, 59)
        r.tick([], epoch='live', eligible=True)
        assert history(r)[0]['status'] == 'planned'
        now[0] = at(9)
        r.tick([], epoch='live', eligible=True)
        assert history(r)[0]['status'] == 'generating'
        r.executor.finish()
        candidate, text = r.tick([], epoch='live', eligible=True)
        assert text.startswith('早安') and history(r)[0]['status'] == 'queued'
        original = store.proactive_result

        def recorded(identity, state):
            if state == 'sending':
                assert history(r)[0]['status'] == 'sending'
            original(identity, state)

        monkeypatch.setattr(store, 'proactive_result', recorded)
        r.begin_send(candidate)
        assert not r.valid(candidate) and r.valid(candidate, continuing=True)
        r.finish(candidate, 'verified')
        assert history(r)[0]['status'] == 'verified'
        assert r.status()['routines'][0]['status'] == 'verified'
    finally:
        r.close(); store.close()


@pytest.mark.parametrize('preceding_parts', [0, 1, 2])
def test_final_verified_bubble_is_counted_once_in_durable_routine_history(tmp_path, preceding_parts):
    r, store, now = build(tmp_path)
    try:
        candidate, _ = generate(r, now)
        r.begin_send(candidate)
        for _ in range(preceding_parts):
            r.progress(candidate)
        r.finish(candidate, 'verified')
        assert history(r)[0]['confirmed_parts'] == preceding_parts + 1
        r.finish(candidate, 'verified')
        assert history(r)[0]['confirmed_parts'] == preceding_parts + 1
        r = restart(r, store, now)
        assert history(r)[0]['confirmed_parts'] == preceding_parts + 1
        assert history(r)[0]['status'] == 'verified'
    finally:
        r.close(); store.close()


@pytest.mark.parametrize('stage', ['generating', 'queued'])
def test_safe_pre_send_restart_retries_with_new_identity_after_thirty_seconds(tmp_path, stage):
    r, store, now = build(tmp_path)
    try:
        now[0] = at(9)
        r.tick([], epoch='live', eligible=True)
        old = r.executor.calls[-1][2][1]
        if stage == 'queued':
            r.executor.finish()
            r.tick([], epoch='live', eligible=True)
        now[0] += 10
        r = restart(r, store, now)
        r.tick([], epoch='after', eligible=True)
        assert not r.executor.calls
        now[0] += 20
        r.tick([], epoch='after', eligible=True)
        candidate = r.executor.calls[-1][2][1]
        assert candidate.id != old.id and history(r)[0]['attempts'] == 2
        if stage == 'queued':
            assert store.db.execute('SELECT state FROM proactive WHERE id=?', (old.id,)).fetchone()[0] == 'skipped'
    finally:
        r.close(); store.close()


@pytest.mark.parametrize('terminal', ['sending', 'verified', 'partial', 'unknown'])
def test_restart_never_repeats_a_sent_or_uncertain_local_day(tmp_path, terminal):
    r, store, now = build(tmp_path)
    try:
        candidate, _ = generate(r, now)
        r.begin_send(candidate)
        if terminal != 'sending':
            r.finish(candidate, terminal)
        now[0] += 30
        r = restart(r, store, now)
        r.tick([], epoch='after', eligible=True)
        assert not r.executor.calls
        assert history(r)[0]['status'] == ('unknown' if terminal == 'sending' else terminal)
        now[0] = at(9, day=8)
        r.tick([], epoch='after', eligible=True)
        assert r.executor.calls[-1][2][1].day == '2026-11-08'
    finally:
        r.close(); store.close()


def test_safe_model_failure_retries_at_most_three_times_inside_five_minute_window(tmp_path):
    r, store, now = build(tmp_path)
    try:
        now[0] = at(9)
        identities = []
        for index in range(3):
            r.tick([], epoch='live', eligible=True)
            future, _, args = r.executor.calls[-1]
            identities.append(args[1].id)
            future.set_exception(RuntimeError('PRIVATE_MODEL_DETAIL'))
            assert r.tick([], epoch='live', eligible=True) is None
            assert history(r)[0]['status'] == 'planned'
            now[0] += 29
            r.tick([], epoch='live', eligible=True)
            assert len(r.executor.calls) == index + 1
            now[0] += 1
        r.tick([], epoch='live', eligible=True)
        assert len(r.executor.calls) == 3 and len(set(identities)) == 3
        assert history(r)[0]['attempts'] == 3
        now[0] = at(9, 6)
        r.tick([], epoch='live', eligible=True)
        assert len(r.executor.calls) == 3
        assert history(r)[0]['status'] == 'missed'
    finally:
        r.close(); store.close()


def test_new_source_invalidates_only_current_content_and_preserves_future_schedule(tmp_path):
    r, store, now = build(tmp_path)
    try:
        now[0] = at(9)
        r.tick([], epoch='live', eligible=True)
        old = r.executor.calls[-1][2][1]
        r.observe(Message('今天去公园', message_id='fresh', created_at=now[0] + 1))
        r.executor.finish()
        assert r.tick([], epoch='live', eligible=True) is None
        r.configure(replace(r.planner.preferences, interests='散步'))
        assert CompanionStore(tmp_path, r.settings.memory_scope).data['preferences']['routines'] == [morning()]
        now[0] += 30
        r.tick([], epoch='live', eligible=True)
        candidate = r.executor.calls[-1][2][1]
        assert candidate.id != old.id and candidate.revision != old.revision
    finally:
        r.close(); store.close()


def test_pause_and_quiet_are_rechecked_and_expired_task_is_not_caught_up(tmp_path):
    r, store, now = build(tmp_path)
    try:
        now[0] = at(9)
        r.tick([], epoch='live', eligible=False)
        assert not r.executor.calls
        r.tick([], epoch='live', eligible=True)
        r.suspend()
        r.executor.finish()
        assert r.tick([], epoch='paused', eligible=False) is None
        assert not r.notes.data['planner']['unanswered']
        now[0] = at(9, 6)
        r.tick([], epoch='live', eligible=True)
        assert len(r.executor.calls) == 1
    finally:
        r.close(); store.close()


def test_same_routine_identity_on_two_accounts_is_scoped_and_encrypted(tmp_path):
    a, store, now = build(tmp_path, scope='first/contact')
    b, _, _ = build(tmp_path, scope='second/contact', store=store, now=now)
    try:
        ca, _ = generate(a, now)
        a.begin_send(ca); a.finish(ca, 'unknown')
        cb, _ = generate(b, now)
        assert cb.id != ca.id and b.valid(cb)
        b.begin_send(cb); b.finish(cb, 'verified')
        assert history(a)[0]['status'] == 'unknown' and history(b)[0]['status'] == 'verified'
        assert a.notes.path != b.notes.path
        assert '早安'.encode() not in a.notes.path.read_bytes()
    finally:
        a.close(); b.close(); store.close()
