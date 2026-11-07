"""Read-only, process-authenticated observations of the current WeChat account."""
from dataclasses import dataclass
from copy import deepcopy
from queue import Empty, SimpleQueue
import threading
import time
from types import MappingProxyType

from .member_names import safe_name


@dataclass(frozen=True)
class AccountObservation:
    account: str
    display_name: str
    pid: int
    rows: tuple
    windows: tuple


class _ProbeError(ValueError):
    """A deliberately displayable message, without identifiers or credentials."""


def _check_cancelled(cancelled):
    if cancelled.is_set():
        raise _ProbeError('账号检测已取消')


def _window_processes(windows):
    return {window['pid'] for window in windows
            if type(window.get('pid')) is int and window['pid'] > 0}


def probe_logged_in_account(settings, cancelled, *, process_id=None):
    """Authenticate one listed process independently of stale configured targets.

    The account identity comes only from DatabaseHub's live process/database
    checks. Nicknames are presentation data read from that account's self row.
    No window is restored, activated, calibrated, or used to select a chat.
    """
    from .database import DatabaseHub
    from .windows import list_calibration_windows

    try:
        _check_cancelled(cancelled)
        windows = tuple(list_calibration_windows())
        _check_cancelled(cancelled)
        pids = _window_processes(windows)
        if process_id is None:
            if len(pids) > 1:
                raise _ProbeError('找到多个微信登录进程，暂时无法唯一确认当前账号')
            if not pids:
                raise _ProbeError('等待已登录的微信窗口')
            pid = next(iter(pids))
        else:
            if type(process_id) is not int or process_id not in pids:
                raise _ProbeError('所选微信进程没有可确认的登录窗口')
            pid = process_id
        hub = DatabaseHub(settings, cancelled, process_id=pid)
        try:
            _check_cancelled(cancelled)
            rows = tuple(hub.directory())
            _check_cancelled(cancelled)
            account = hub.account.get('id') if isinstance(hub.account, dict) else None
            self_id = hub.account.get('wxid') if isinstance(hub.account, dict) else None
            if (not isinstance(account, str) or not account.strip()
                    or not isinstance(self_id, str) or not self_id.strip()):
                raise _ProbeError('当前微信登录账号尚未能验证，请稍后重试')
            if any(row.account != account for row in rows):
                raise _ProbeError('微信联系人目录与当前登录账号不一致，等待重新确认')
            hub._ensure(force_identity=True)
            _check_cancelled(cancelled)
            if (not isinstance(hub.account, dict) or hub.account.get('id') != account
                    or hub.account.get('wxid') != self_id):
                raise _ProbeError('微信登录账号已改变，等待重新确认')
            conn, _cache = hub._contact_metadata()
            names = conn.execute('SELECT nick_name FROM contact WHERE username=? LIMIT 2',
                                 (self_id,)).fetchall()
            name = safe_name(names[0][0]) if len(names) == 1 else ''
            if name in (account, self_id):
                name = ''
            _check_cancelled(cancelled)
            current_windows = tuple(list_calibration_windows())
            _check_cancelled(cancelled)
            current_pids = _window_processes(current_windows)
            if pid not in current_pids or process_id is None and current_pids != {pid}:
                raise _ProbeError('微信登录窗口已改变，等待重新确认账号')
            selected_windows = tuple(MappingProxyType(dict(window)) for window in current_windows
                                     if window.get('pid') == pid)
            return AccountObservation(account, name or '当前微信', pid, rows, selected_windows)
        finally:
            hub.close()
    except _ProbeError as error:
        raise ValueError(str(error)) from None
    except Exception:
        # Errors from OS, SQLite or credential providers can contain raw paths,
        # account IDs and keys. They must never become display/log messages.
        raise ValueError('当前微信登录账号尚未能验证，请稍后重试') from None


def _account_scope(settings):
    accounts = {target['account'] for target in settings.conversation_targets.values()}
    if settings.calibration_account:
        accounts.add(settings.calibration_account)
    return str(settings.root), frozenset(accounts)


class _Cancellation:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)


class AccountDetector:
    """Poll without waiting for database work; require two stable observations.

    Only this class's short bookkeeping is serialized. A single daemon owns
    the slow probe, and completed results retain the originating config scope.
    """
    def __init__(self, stop_event, *, probe=probe_logged_in_account, interval=5,
                 clock=time.monotonic):
        self._stop_event = stop_event
        self._closed = threading.Event()
        self._probe = probe
        self._interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._completed = SimpleQueue()
        self._worker = None
        self._probe_cancel = None
        self._next_start = float('-inf')
        self._scope = None
        self._generation = 0
        self._candidate = None
        self._stable = None

    def _reset(self):
        self._candidate = None
        self._stable = None

    def _run_probe(self, settings, generation, cancelled):
        try:
            result = self._probe(settings, cancelled)
            if cancelled.is_set() or not isinstance(result, AccountObservation):
                result = None
        except Exception:
            # Providers can expose private IDs/keys in errors. Failed probes
            # only reset stability; no provider exception leaves this worker.
            result = None
        self._completed.put((generation, result))

    def poll(self, settings) -> AccountObservation | None:
        with self._lock:
            if self._closed.is_set() or self._stop_event.is_set():
                self._reset()
                return None
            scope = _account_scope(settings)
            if scope != self._scope:
                self._scope = scope
                self._generation += 1
                self._reset()
                if self._probe_cancel is not None:
                    self._probe_cancel.set()
            while True:
                try:
                    generation, result = self._completed.get_nowait()
                except Empty:
                    break
                self._worker = None
                self._probe_cancel = None
                if generation != self._generation or result is None:
                    self._reset()
                    continue
                identity = result.account, result.pid
                if identity == self._candidate:
                    self._stable = result
                else:
                    self._candidate = identity
                    self._stable = None
            now = self._clock()
            if self._worker is None and now >= self._next_start:
                snapshot = deepcopy(settings)
                self._probe_cancel = threading.Event()
                cancelled = _Cancellation(self._stop_event, self._closed, self._probe_cancel)
                self._worker = threading.Thread(target=self._run_probe,
                    args=(snapshot, self._generation, cancelled), daemon=True,
                    name='wechat-account-observer')
                self._next_start = now + self._interval
                self._worker.start()
            return self._stable

    def close(self):
        """Cancel owned work without setting the resident's shared stop event."""
        with self._lock:
            self._closed.set()
            if self._probe_cancel is not None:
                self._probe_cancel.set()
            self._reset()
