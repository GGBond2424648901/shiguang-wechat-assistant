"""Read-only account observations must never trust names or a stale process."""
from dataclasses import FrozenInstanceError, replace
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from xiaomao.config import Settings
from xiaomao.contacts import Contact


ACCOUNT = 'fixture-current-account'
SELF = "self' -- bound user"
PEER = Contact('fixture-peer', ACCOUNT, '朋友', 'private')
MAIN = dict(pid=77, hwnd=101, kind='main', title='微信', minimized=True)
CHAT = dict(pid=77, hwnd=102, kind='detached', title='朋友', minimized=False)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from xiaomao import database, windows

    cfg = Settings(tmp_path, conversation_targets={
        '旧对象': dict(id='old-peer', account='fixture-old-account', kind='private')})
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE contact(username TEXT, nick_name TEXT, remark TEXT)')
    conn.executemany('INSERT INTO contact VALUES(?,?,?)', [
        ('another-person', '朋友昵称', ''), (SELF, '示例米饭同学', '这不是我的昵称')])
    state = dict(rows=[PEER], account=dict(id=ACCOUNT, wxid=SELF), windows=[MAIN, CHAT],
                 hubs=[], force=None, directory_error=None)

    class Hub:
        def __init__(self, settings, cancelled, *, process_id):
            self.settings = settings
            self.cancelled = cancelled
            self.process_id = process_id
            self.account = None
            self.closed = False
            self.forced = False
            state['hubs'].append(self)

        def directory(self):
            if state['directory_error']:
                raise state['directory_error']
            self.account = dict(state['account'])
            return list(state['rows'])

        def _ensure(self, *, force_identity=False):
            self.forced = force_identity
            if state['force']:
                state['force'](self)

        def _contact_metadata(self):
            return conn, {}

        def close(self):
            self.closed = True

    monkeypatch.setattr(database, 'DatabaseHub', Hub)
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: list(state['windows']))
    yield cfg, state, conn
    conn.close()


def test_probe_uses_verified_current_account_and_self_nickname_without_old_config_pin(environment):
    # Removing process binding or interpolating SELF into SQL breaks this read.
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    result = probe_logged_in_account(cfg, threading.Event())
    assert (result.account, result.display_name, result.pid) == (ACCOUNT, '示例米饭同学', 77)
    assert result.rows == (PEER,)
    assert result.windows == (MAIN, CHAT)
    assert isinstance(result.rows, tuple) and isinstance(result.windows, tuple)
    assert state['hubs'][0].process_id == 77
    assert state['hubs'][0].forced and state['hubs'][0].closed
    assert cfg.conversation_targets['旧对象']['account'] == 'fixture-old-account'
    with pytest.raises(FrozenInstanceError):
        result.account = 'changed'


def test_authenticated_empty_directory_is_a_valid_account(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    state['rows'] = []
    result = probe_logged_in_account(cfg, threading.Event())
    assert result.account == ACCOUNT and result.rows == ()


@pytest.mark.parametrize('nickname', ['', 'wxid_private_identifier', 'bad\nname', 'a'*81, None])
def test_untrusted_self_nickname_uses_safe_generic_display(environment, nickname):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, _state, conn = environment
    conn.execute('UPDATE contact SET nick_name=? WHERE username=?', (nickname, SELF))
    assert probe_logged_in_account(cfg, threading.Event()).display_name == '当前微信'


def test_missing_self_record_never_borrows_another_contacts_nickname(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, _state, conn = environment
    conn.execute('DELETE FROM contact WHERE username=?', (SELF,))
    assert probe_logged_in_account(cfg, threading.Event()).display_name == '当前微信'


def test_probe_rejects_any_cross_account_directory_row_and_closes_hub(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    state['rows'].append(replace(PEER, id='another-peer', account='foreign-account'))
    with pytest.raises(ValueError, match='账号') as error:
        probe_logged_in_account(cfg, threading.Event())
    assert 'foreign-account' not in str(error.value)
    assert state['hubs'][0].closed


@pytest.mark.parametrize('changed', ['account', 'window'])
def test_probe_rejects_identity_changes_during_read(environment, changed):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    def change(hub):
        if changed == 'account':
            hub.account = dict(id='foreign-account', wxid='other-self')
        else:
            state['windows'] = [dict(MAIN, pid=88)]
    state['force'] = change
    with pytest.raises(ValueError, match='账号|窗口'):
        probe_logged_in_account(cfg, threading.Event())
    assert state['hubs'][0].forced and state['hubs'][0].closed


def test_explicit_process_must_be_listed_and_returns_only_its_windows(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    state['windows'].append(dict(MAIN, pid=88, hwnd=201))
    with pytest.raises(ValueError, match='窗口|进程'):
        probe_logged_in_account(cfg, threading.Event(), process_id=99)
    assert not state['hubs']
    result = probe_logged_in_account(cfg, threading.Event(), process_id=77)
    assert result.pid == 77 and result.windows == (MAIN, CHAT)


@pytest.mark.parametrize('windows, message', [([], '登录|窗口'),
    ([MAIN, dict(MAIN, pid=88, hwnd=201)], '多个|唯一')])
def test_ambiguous_or_missing_process_never_guesses_from_saved_settings(environment, windows, message):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    state['windows'] = windows
    with pytest.raises(ValueError, match=message):
        probe_logged_in_account(cfg, threading.Event())
    assert not state['hubs']


def test_database_authentication_failure_hides_ids_and_keys_and_closes_hub(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    state['directory_error'] = RuntimeError('wxid_secret-account key=deadbeef')
    with pytest.raises(ValueError, match='账号|验证') as error:
        probe_logged_in_account(cfg, threading.Event())
    assert 'wxid_secret' not in str(error.value) and 'deadbeef' not in str(error.value)
    assert state['hubs'][0].closed


def test_cancelled_probe_never_opens_database(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(ValueError, match='取消'):
        probe_logged_in_account(cfg, stopped)
    assert not state['hubs']


def test_probe_cancellation_after_authentication_discards_observation_and_closes(environment):
    from xiaomao.account_detection import probe_logged_in_account
    cfg, state, _conn = environment
    stopped = threading.Event()
    state['force'] = lambda _hub: stopped.set()
    with pytest.raises(ValueError, match='取消'):
        probe_logged_in_account(cfg, stopped)
    assert state['hubs'][0].closed


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class ControlledProbe:
    """Block actual background calls at the external account-probe boundary."""
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.started = [threading.Event() for _ in outcomes]
        self.release = [threading.Event() for _ in outcomes]
        self.lock = threading.Lock()
        self.active = self.maximum_active = 0

    def __call__(self, settings, cancelled):
        with self.lock:
            index = len(self.calls)
            self.calls.append((settings, cancelled, threading.current_thread().daemon))
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        self.started[index].set()
        try:
            assert self.release[index].wait(3), 'test did not release account probe'
            outcome = self.outcomes[index]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self.lock:
                self.active -= 1

    def close(self):
        for released in self.release:
            released.set()


def observation(account=ACCOUNT, pid=77, name='示例米饭同学'):
    from xiaomao.account_detection import AccountObservation
    return AccountObservation(account, name, pid, (replace(PEER, account=account),),
                              (dict(MAIN, pid=pid),))


def eventually(action, predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        value = action()
        if predicate(value):
            return value
        threading.Event().wait(.001)
    pytest.fail('background probe did not produce the expected state')


def start_next(detector, settings, clock, probe, index):
    clock.now += 5
    results = []
    def poll():
        result = detector.poll(settings)
        results.append(result)
        return result
    eventually(poll, lambda _result: probe.started[index].is_set())
    return results[-1]


def test_detector_requires_two_successes_and_throttles_probe_starts(tmp_path):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    first, second = observation(), observation(name='更新昵称')
    probe = ControlledProbe([first, second])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        assert detector.poll(cfg) is None
        assert probe.started[0].wait(1)
        probe.release[0].set()
        for _ in range(10):
            assert detector.poll(cfg) is None
        assert len(probe.calls) == 1
        assert start_next(detector, cfg, clock, probe, 1) is None
        probe.release[1].set()
        assert eventually(lambda: detector.poll(cfg), lambda value: value is not None) is second
        assert detector.poll(cfg) is second  # The resident may retry a safe apply boundary.
        assert all(call[2] for call in probe.calls)
    finally:
        detector.close()
        probe.close()


def test_detector_failure_resets_consecutive_successes(tmp_path):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    found = observation()
    probe = ControlledProbe([found, RuntimeError('untrusted ID/key'), found, found])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        assert detector.poll(cfg) is None and probe.started[0].wait(1)
        probe.release[0].set()
        for index in (1, 2, 3):
            assert start_next(detector, cfg, clock, probe, index) is None
            probe.release[index].set()
        assert eventually(lambda: detector.poll(cfg), lambda value: value is not None) is found
    finally:
        detector.close()
        probe.close()


@pytest.mark.parametrize('changed', [dict(account='other-account'), dict(pid=88)])
def test_detector_account_or_process_change_requires_two_new_successes(tmp_path, changed):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    old, new = observation(), observation(**changed)
    probe = ControlledProbe([old, old, new, new])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        detector.poll(cfg)
        assert probe.started[0].wait(1)
        probe.release[0].set()
        assert start_next(detector, cfg, clock, probe, 1) is None
        probe.release[1].set()
        assert eventually(lambda: detector.poll(cfg), lambda value: value is not None) is old
        assert start_next(detector, cfg, clock, probe, 2) is old
        probe.release[2].set()
        assert start_next(detector, cfg, clock, probe, 3) is None
        probe.release[3].set()
        assert eventually(lambda: detector.poll(cfg), lambda value: value is new) is new
    finally:
        detector.close()
        probe.close()


def test_failure_after_stability_discards_previous_stable_result(tmp_path):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    found = observation()
    probe = ControlledProbe([found, found, InterruptedError(), found, found])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        detector.poll(cfg)
        assert probe.started[0].wait(1)
        probe.release[0].set()
        start_next(detector, cfg, clock, probe, 1)
        probe.release[1].set()
        eventually(lambda: detector.poll(cfg), lambda value: value is found)
        start_next(detector, cfg, clock, probe, 2)
        probe.release[2].set()
        assert start_next(detector, cfg, clock, probe, 3) is None
        probe.release[3].set()
        assert start_next(detector, cfg, clock, probe, 4) is None
        probe.release[4].set()
        assert eventually(lambda: detector.poll(cfg), lambda value: value is found) is found
    finally:
        detector.close()
        probe.close()


def test_poll_is_nonblocking_and_concurrent_calls_never_overlap_probes(tmp_path):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    probe = ControlledProbe([observation()])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        with ThreadPoolExecutor(max_workers=8) as callers:
            pending = [callers.submit(detector.poll, cfg) for _ in range(30)]
            assert [call.result(timeout=.5) for call in pending] == [None] * 30
        assert probe.started[0].wait(1)
        clock.now = 50
        assert detector.poll(cfg) is None
        assert len(probe.calls) == 1 and probe.maximum_active == 1
    finally:
        detector.close()
        probe.close()


@pytest.mark.parametrize('scope_field', ['targets', 'calibration'])
def test_changed_config_account_scope_rejects_inflight_result(tmp_path, scope_field):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    current = replace(cfg, conversation_targets={
        '朋友': dict(id='peer', account='new-config-account', kind='private')}) if scope_field == 'targets' else replace(
        cfg, calibration_account='new-config-account')
    found = observation()
    probe = ControlledProbe([found, found, found])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        assert detector.poll(cfg) is None and probe.started[0].wait(1)
        assert detector.poll(current) is None
        assert probe.calls[0][1].is_set()
        probe.release[0].set()
        assert start_next(detector, current, clock, probe, 1) is None
        probe.release[1].set()
        assert start_next(detector, current, clock, probe, 2) is None
        probe.release[2].set()
        assert eventually(lambda: detector.poll(current), lambda value: value is not None) is found
    finally:
        detector.close()
        probe.close()


def test_scope_change_clears_stable_result_and_probe_uses_settings_snapshot(tmp_path):
    from xiaomao.account_detection import AccountDetector
    cfg, clock = Settings(tmp_path), Clock()
    found = observation()
    probe = ControlledProbe([found, found])
    detector = AccountDetector(threading.Event(), probe=probe, clock=clock)
    try:
        detector.poll(cfg)
        assert probe.started[0].wait(1)
        probe.release[0].set()
        start_next(detector, cfg, clock, probe, 1)
        probe.release[1].set()
        eventually(lambda: detector.poll(cfg), lambda value: value is found)
        cfg.calibration_account = 'a-new-config-account'
        assert detector.poll(cfg) is None
        assert probe.calls[0][0].calibration_account == ''
    finally:
        detector.close()
        probe.close()


@pytest.mark.parametrize('action', ['close', 'stop'])
def test_detector_cancellation_discards_inflight_result_without_owning_resident_stop(tmp_path, action):
    from xiaomao.account_detection import AccountDetector
    cfg, clock, stopped = Settings(tmp_path), Clock(), threading.Event()
    probe = ControlledProbe([observation()])
    detector = AccountDetector(stopped, probe=probe, clock=clock)
    try:
        detector.poll(cfg)
        assert probe.started[0].wait(1)
        detector.close() if action == 'close' else stopped.set()
        assert probe.calls[0][1].is_set()
        assert stopped.is_set() == (action == 'stop')
        probe.release[0].set()
        clock.now = 100
        assert detector.poll(cfg) is None and len(probe.calls) == 1
    finally:
        detector.close()
        probe.close()
