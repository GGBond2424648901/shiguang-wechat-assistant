"""Private, account-scoped news state with transactional optimistic writes."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
import time
import unicodedata
import uuid

from .news import validate_subscription
from .privacy import make_private, private_dir
from .web_search import _citation_identity, _public_url


_TERMINAL = ('cancelled', 'sent', 'failed', 'missed', 'empty', 'completed', 'verified', 'unknown', 'partial')
_PENDING = ('collecting', 'ready', 'waiting', 'publishing')


def _account(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 200 or value != value.strip()
            or any(unicodedata.category(char).startswith('C') for char in value)):
        raise ValueError('必须明确指定有效账号')
    return value


def _json(value):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError):
        raise ValueError('资讯数据格式无效') from None
    if len(data) > 512 * 1024:
        raise ValueError('资讯数据超过大小限制')
    return data


def _urls(value):
    if (not isinstance(value, (list, tuple, set)) or len(value) > 96
            or any(not _public_url(url) for url in value)):
        raise ValueError('资讯来源链接无效')
    return list(dict.fromkeys(_citation_identity(url) for url in value))


def _targets(value, recipients):
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError('目标身份快照无效')
    result, seen = [], set()
    for row in value:
        if not isinstance(row, dict):
            raise ValueError('目标身份快照无效')
        clean = {}
        for key, limit in (('session', 160), ('name', 160), ('scope', 300)):
            item = row.get(key)
            if (not isinstance(item, str) or not 1 <= len(item) <= limit
                    or any(unicodedata.category(char).startswith('C') for char in item)):
                raise ValueError('目标身份快照无效')
            clean[key] = item
        if clean['session'] not in recipients or clean['session'] in seen:
            raise ValueError('目标身份快照与会话不一致')
        seen.add(clean['session'])
        result.append(clean)
    return result


class NewsStore:
    def __init__(self, runtime):
        directory = private_dir(Path(runtime))
        self.path = directory / 'news.sqlite3'
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        make_private(self.path)
        with self._connection:
            self._connection.execute('PRAGMA journal_mode=WAL')
            self._connection.execute('PRAGMA busy_timeout=10000')
            self._connection.executescript('''
                CREATE TABLE IF NOT EXISTS news_subscriptions (
                    account TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS news_editions (
                    account TEXT NOT NULL, id TEXT NOT NULL, subscription TEXT NOT NULL,
                    slot TEXT, created REAL NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(account,id));
                CREATE UNIQUE INDEX IF NOT EXISTS news_slot
                    ON news_editions(account,subscription,slot) WHERE slot IS NOT NULL;
                CREATE INDEX IF NOT EXISTS news_history
                    ON news_editions(account,subscription,created DESC);
                CREATE TABLE IF NOT EXISTS news_seen (
                    account TEXT NOT NULL, scope TEXT NOT NULL, url TEXT NOT NULL,
                    seen REAL NOT NULL, PRIMARY KEY(account,scope,url));
                CREATE TABLE IF NOT EXISTS news_uncertain (
                    account TEXT NOT NULL, scope TEXT NOT NULL, url TEXT NOT NULL,
                    at REAL NOT NULL, PRIMARY KEY(account,scope,url));
            ''')
        for journal in (Path(str(self.path) + '-wal'), Path(str(self.path) + '-shm')):
            if journal.exists():
                make_private(journal)

    def list_subscriptions(self, account):
        account = _account(account)
        with self._lock:
            rows = self._connection.execute(
                'SELECT data FROM news_subscriptions WHERE account=? ORDER BY rowid', (account,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_subscription(self, account, id):
        account = _account(account)
        with self._lock:
            row = self._connection.execute(
                'SELECT data FROM news_subscriptions WHERE account=? AND id=?', (account, id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_subscription(self, account, config, expected_revision=None):
        account = _account(account)
        clean = validate_subscription(config)
        clean['targets'] = _targets(config.get('targets', []), clean['recipients'])
        with self._lock, self._connection:
            self._connection.execute('BEGIN IMMEDIATE')
            row = self._connection.execute(
                'SELECT revision FROM news_subscriptions WHERE account=? AND id=?', (account, clean['id'])).fetchone()
            revision = row[0] if row else 0
            if ((row and (type(expected_revision) is not int or expected_revision != revision))
                    or (not row and expected_revision not in (None, 0))):
                raise ValueError('订阅版本冲突，请刷新后重试')
            clean['revision'] = revision + 1
            self._connection.execute('''INSERT INTO news_subscriptions(account,id,revision,data) VALUES(?,?,?,?)
                ON CONFLICT(account,id) DO UPDATE SET revision=excluded.revision,data=excluded.data''',
                (account, clean['id'], clean['revision'], _json(clean)))
        return deepcopy(clean)

    def delete_subscription(self, account, id, expected_revision):
        account = _account(account)
        with self._lock, self._connection:
            self._connection.execute('BEGIN IMMEDIATE')
            row = self._connection.execute(
                'SELECT revision FROM news_subscriptions WHERE account=? AND id=?', (account, id)).fetchone()
            if not row or type(expected_revision) is not int or expected_revision != row[0]:
                raise ValueError('订阅版本冲突或已删除，请刷新后重试')
            self._connection.execute('DELETE FROM news_subscriptions WHERE account=? AND id=?', (account, id))
            rows = self._connection.execute(
                'SELECT id,data,state FROM news_editions WHERE account=? AND subscription=?', (account, id)).fetchall()
            for identity, data, state in rows:
                if state in _TERMINAL:
                    continue
                edition = json.loads(data)
                edition.update(state='cancelled', reason='订阅已删除', updatedAt=time.time())
                edition['receiptPending'] = bool(edition.get('receiptPending') or any(
                    (part.get('taskId') or part.get('requestId')) and part.get('state') not in _TERMINAL
                    for delivery in edition.get('deliveries', []) for part in delivery.get('parts', [])))
                self._connection.execute('UPDATE news_editions SET state=?,data=? WHERE account=? AND id=?',
                                         ('cancelled', _json(edition), account, identity))

    def create_edition(self, account, subscription_id, snapshot, trigger, slot, now):
        account = _account(account)
        edition = dict(id=uuid.uuid4().hex, subscriptionId=subscription_id, snapshot=deepcopy(snapshot),
                       trigger=trigger, slot=slot or '', createdAt=now, updatedAt=now, state='collecting',
                       reason='', articles=[], parts=[], warnings=[], deliveries=[])
        with self._lock, self._connection:
            self._connection.execute('BEGIN IMMEDIATE')
            if not self._connection.execute('SELECT 1 FROM news_subscriptions WHERE account=? AND id=?',
                                            (account, subscription_id)).fetchone():
                return None
            try:
                self._connection.execute('''INSERT INTO news_editions
                    (account,id,subscription,slot,created,state,data) VALUES(?,?,?,?,?,?,?)''',
                    (account, edition['id'], subscription_id, slot or None, now, 'collecting', _json(edition)))
            except sqlite3.IntegrityError:
                if slot:
                    return None
                raise
        return deepcopy(edition)

    def get_edition(self, account, id):
        account = _account(account)
        with self._lock:
            row = self._connection.execute('SELECT data FROM news_editions WHERE account=? AND id=?',
                                           (account, id)).fetchone()
        return json.loads(row[0]) if row else None

    def get_slot(self, account, subscription_id, slot):
        account = _account(account)
        with self._lock:
            row = self._connection.execute('''SELECT data FROM news_editions
                WHERE account=? AND subscription=? AND slot=?''',
                (account, subscription_id, slot)).fetchone()
        return json.loads(row[0]) if row else None

    def update_edition(self, account, id, **changes):
        account = _account(account)
        if {'id', 'subscriptionId', 'snapshot', 'trigger', 'slot', 'createdAt'} & changes.keys():
            raise ValueError('期刊身份与生成快照不可修改')
        with self._lock, self._connection:
            self._connection.execute('BEGIN IMMEDIATE')
            edition = self.get_edition(account, id)
            if edition is None:
                raise ValueError('资讯期刊不存在')
            if edition['state'] == 'cancelled':
                # A native transaction may finish after cancellation. Keep
                # auditing its receipt and dedup ledger, never resume content.
                changes = {key: value for key, value in changes.items()
                           if key in ('deliveries', 'receiptPending')}
                if not changes:
                    return edition
            edition.update(deepcopy(changes))
            edition['updatedAt'] = changes.get('updatedAt', time.time())
            self._connection.execute('UPDATE news_editions SET state=?,data=? WHERE account=? AND id=?',
                                     (edition['state'], _json(edition), account, id))
        return edition

    def history(self, account, subscription_id=None, limit=20):
        account = _account(account)
        limit = max(1, min(128, limit)) if type(limit) is int else 20
        with self._lock:
            if subscription_id is None:
                rows = self._connection.execute('''SELECT data FROM news_editions
                    WHERE account=? ORDER BY created DESC,rowid DESC LIMIT ?''', (account, limit)).fetchall()
            else:
                rows = self._connection.execute('''SELECT data FROM news_editions
                    WHERE account=? AND subscription=? ORDER BY created DESC,rowid DESC LIMIT ?''',
                    (account, subscription_id, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def pending(self, account):
        account = _account(account)
        with self._lock:
            marks = ','.join('?' for _ in _PENDING)
            rows = self._connection.execute(f'''SELECT data FROM news_editions
                WHERE account=? AND (state IN ({marks})
                    OR json_extract(data,'$.receiptPending')=1)
                ORDER BY created DESC,rowid DESC''',
                (account, *_PENDING)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def seen_urls(self, account, recipient_scope):
        account = _account(account)
        recipient_scope = _account(recipient_scope)
        with self._lock:
            rows = self._connection.execute('SELECT url FROM news_seen WHERE account=? AND scope=?',
                                           (account, recipient_scope)).fetchall()
        return {row[0] for row in rows}

    def mark_seen(self, account, recipient_scope, urls, now):
        """Called by the receipt pipeline only after the corresponding part is verified."""
        account = _account(account)
        recipient_scope = _account(recipient_scope)
        urls = _urls(urls)
        with self._lock, self._connection:
            self._connection.executemany('''INSERT INTO news_seen(account,scope,url,seen) VALUES(?,?,?,?)
                ON CONFLICT(account,scope,url) DO UPDATE SET seen=excluded.seen''',
                [(account, recipient_scope, url, now) for url in urls])
            self._connection.executemany('DELETE FROM news_uncertain WHERE account=? AND scope=? AND url=?',
                                         [(account, recipient_scope, url) for url in urls])

    def uncertain_urls(self, account, recipient_scope):
        account = _account(account)
        recipient_scope = _account(recipient_scope)
        with self._lock:
            rows = self._connection.execute('SELECT url FROM news_uncertain WHERE account=? AND scope=?',
                                           (account, recipient_scope)).fetchall()
        return {row[0] for row in rows}

    def mark_uncertain(self, account, recipient_scope, urls, now):
        """Quarantine ambiguous attempts independently of bounded edition history."""
        account = _account(account)
        recipient_scope = _account(recipient_scope)
        urls = _urls(urls)
        with self._lock, self._connection:
            self._connection.executemany('''INSERT INTO news_uncertain(account,scope,url,at)
                SELECT ?,?,?,? WHERE NOT EXISTS
                    (SELECT 1 FROM news_seen WHERE account=? AND scope=? AND url=?)
                ON CONFLICT(account,scope,url) DO UPDATE SET at=excluded.at''',
                [(account, recipient_scope, url, now, account, recipient_scope, url) for url in urls])

    def close(self):
        with self._lock:
            self._connection.close()
