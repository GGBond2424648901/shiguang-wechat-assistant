"""持久保存带盐摘要、任务状态及按群DPAPI加密的有限会话。"""
import hashlib
import hmac
from pathlib import Path
import sqlite3
import threading
import uuid
import json
import os
from .privacy import private_dir, secret_bytes,make_private
from .domain import Message,normalized


class StateStore:
    STATES = {"candidate", "generated", "staged", "sending", "verified", "unknown", "skipped"}

    def __init__(self, directory: Path):
        self.directory = private_dir(Path(directory))
        self.salt = secret_bytes(self.directory / "fingerprint.key")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "state.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, digest TEXT NOT NULL, created REAL NOT NULL,
              state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS attempts (task_id TEXT PRIMARY KEY, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_by_time ON attempts(at);
            CREATE TABLE IF NOT EXISTS latest_claims (digest TEXT PRIMARY KEY, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS send_receipts (
              task_id TEXT PRIMARY KEY, group_digest TEXT NOT NULL,
              reply_digest TEXT NOT NULL, baseline TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sticker_history (
              group_digest TEXT NOT NULL, sticker_id TEXT NOT NULL,
              PRIMARY KEY(group_digest,sticker_id));
            CREATE TABLE IF NOT EXISTS proactive (
              id TEXT PRIMARY KEY, scope TEXT NOT NULL, account TEXT NOT NULL,
              day TEXT NOT NULL, at REAL NOT NULL, state TEXT NOT NULL);
        """)
        columns={r[1] for r in self.db.execute('PRAGMA table_info(send_receipts)')}
        for name in ('sticker_md5','sticker_id','confirmed_digest'):
            if name not in columns:self.db.execute(f"ALTER TABLE send_receipts ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        # Keep only identity pairs after detailed receipts expire. Include uncertain
        # sends conservatively so an explicit novelty request cannot resend them.
        self.db.execute("INSERT OR IGNORE INTO sticker_history SELECT group_digest,sticker_id FROM send_receipts WHERE sticker_id!=''")
        self.db.commit()

    def fingerprint(self, value: str) -> str:
        return hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def reserve_proactive(self,task_id,scope,day,now,per_day_max,*,account_max=3):
        from .companion import local_day
        if (day!=local_day(now) or type(per_day_max) is not int or not 1<=per_day_max<=24
                or type(account_max) is not int or not 1<=account_max<=192):return False
        scoped=self.fingerprint(scope)
        account=self.fingerprint(scope.split('/')[0] if '/' in scope else 'legacy-account')
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM proactive WHERE id=?',(task_id,)).fetchone():return False
            latest=self.db.execute('SELECT max(at) FROM proactive WHERE account=?',(account,)).fetchone()[0]
            if latest is not None and now<latest:return False
            used="state IN ('reserved','sending','verified','unknown','partial')"
            if self.db.execute(f'SELECT count(*) FROM proactive WHERE account=? AND day=? AND {used}',(account,day)).fetchone()[0]>=account_max:return False
            if self.db.execute(f'SELECT count(*) FROM proactive WHERE scope=? AND day=? AND {used}',(scoped,day)).fetchone()[0]>=per_day_max:return False
            self.db.execute('INSERT INTO proactive VALUES(?,?,?,?,?,?)',(task_id,scoped,account,day,now,'reserved'))
            return True

    def proactive_result(self,task_id,state):
        if state not in ('reserved','sending','verified','unknown','skipped','silent','partial'):raise ValueError('主动状态无效')
        with self.lock,self.db:
            self.db.execute('UPDATE proactive SET state=? WHERE id=? AND state NOT IN (\'verified\',\'unknown\',\'partial\')',(state,task_id))

    def load_context(self,group,now):
        if os.name!='nt':return []
        path=self.directory/('conversation-'+self.fingerprint(group)+'.dpapi')
        try:
            import win32crypt
            _,plain=win32crypt.CryptUnprotectData(path.read_bytes(),b'xiaomao:conversation:v1',None,None,1)
            data=json.loads(plain)
            if data['group']!=group or not 0<=now-data['at']<=86400:return []
            return [Message(m['text'][:2000],'',m['direction']) for m in data['messages'][-24:] if isinstance(m.get('text'),str) and m.get('direction') in ('in','out')]
        except Exception:return []

    def save_context(self,group,messages,now):
        if os.name!='nt':return
        import win32crypt
        data={'group':group,'at':now,'messages':[{'text':m.text[:2000],'direction':m.direction} for m in list(messages)[-24:] if m.kind=='text' and not m.quoted and m.quality>=.92 and m.direction in ('in','out')]}
        path=self.directory/('conversation-'+self.fingerprint(group)+'.dpapi')
        with self.lock:
            encrypted=win32crypt.CryptProtectData(json.dumps(data,ensure_ascii=False).encode(),'Xiaomao Conversation',b'xiaomao:conversation:v1',None,None,1)
            temporary=path.with_suffix('.tmp');temporary.write_bytes(encrypted);make_private(temporary);os.replace(temporary,path)

    def create(self, digest: str, now: float) -> str:
        task_id = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO tasks (id,digest,created,state) VALUES (?,?,?,?)", (task_id, digest, now, "candidate"))
        return task_id

    def create_unique(self,digest,now,*,legacy_digest=None):
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM tasks WHERE digest IN (?,?)',(digest,legacy_digest)).fetchone():return None
            task_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO tasks (id,digest,created,state) VALUES (?,?,?,?)",(task_id,digest,now,"candidate"))
            return task_id

    def claim_latest(self,key,now:float,group='',*,legacy_group='') -> bool:
        digest=self.fingerprint(f'{group}:latest_recovery:{key!r}' if group else f'latest_recovery:{key!r}')
        with self.lock,self.db:
            # 旧版未带群范围的认领继续阻断，防止升级后未知发送被恢复重发。
            if group:
                old=self.fingerprint(f'latest_recovery:{key!r}')
                named=self.fingerprint(f'{legacy_group}:latest_recovery:{key!r}') if legacy_group else None
                if self.db.execute('SELECT 1 FROM latest_claims WHERE digest IN (?,?)',(old,named)).fetchone():return False
            cursor=self.db.execute('INSERT OR IGNORE INTO latest_claims VALUES (?,?)',(digest,now))
            return cursor.rowcount==1

    def update(self, task_id: str, state: str, reason: str = "") -> None:
        if state not in self.STATES:
            raise ValueError("未知任务状态")
        # reason 必须是程序自己的固定代码，不接受模型或群消息正文。
        if reason and (not reason.isascii() or not all(c.isalnum() or c == "_" for c in reason)):
            raise ValueError("原因只能是固定状态代码")
        with self.lock, self.db:
            self.db.execute("UPDATE tasks SET state=?,reason=? WHERE id=?", (state, reason, task_id))

    def get(self, task_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else {}

    def delivery_status(self,group,message_id):
        digest=self.fingerprint(f'{group}:{message_id}')
        with self.lock:
            row=self.db.execute('SELECT t.state,a.at FROM tasks t LEFT JOIN attempts a ON a.task_id=t.id WHERE t.digest=?',
                                (digest,)).fetchone()
        if row is None:return ''
        if row[0]=='verified':return 'verified'
        if row[0]=='skipped' and row[1] is None:return 'not_sent'
        return 'unconfirmed'

    def recover(self) -> int:
        with self.lock, self.db:
            self.db.execute("UPDATE tasks SET state='unknown',reason='interrupted_send' WHERE state='sending'")
            self.db.execute("UPDATE tasks SET state='skipped',reason='restart' WHERE state IN ('candidate','generated','staged')")
            return self.db.execute("SELECT count(*) FROM tasks WHERE state='unknown'").fetchone()[0]

    def interval_ready_at(self, min_interval, *, previous_part_id='', continuation_interval=0):
        with self.lock:
            last = self.db.execute('SELECT task_id, at FROM attempts ORDER BY at DESC, rowid DESC LIMIT 1').fetchone()
            if last is None:
                return None
            ordinary = last['at'] + min_interval
            if not previous_part_id:
                return ordinary
            previous = self.db.execute(
                "SELECT a.at FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.task_id=? AND t.state='verified'",
                (previous_part_id,),
            ).fetchone()
            if previous is None:
                return ordinary
            continuation = previous['at'] + continuation_interval
            # A different intervening send keeps its own ordinary spacing.
            return continuation if last['task_id'] == previous_part_id else max(ordinary, continuation)

    def allow_send(self, now: float, min_interval: float, hourly_limit: int, *, minute_limit: int = 0,
                   previous_part_id: str = '', continuation_interval: float = 0) -> tuple[bool, str]:
        with self.lock:
            ready_at = self.interval_ready_at(min_interval, previous_part_id=previous_part_id,
                                              continuation_interval=continuation_interval)
            if ready_at is not None and now < ready_at:
                return False, "interval"
            count = self.db.execute("SELECT count(*) FROM attempts WHERE at>?", (now - 3600,)).fetchone()[0]
            if count >= hourly_limit:return False,'hourly_limit'
            if minute_limit and self.db.execute('SELECT count(*) FROM attempts WHERE at>?',(now-60,)).fetchone()[0]>=minute_limit:
                return False,'minute_limit'
            return True,''

    def mark_sending(self, task_id: str, now: float, message_keys=(),group='',reply='',baseline_ids=(),sticker_md5='',sticker_id='') -> None:
        with self.lock, self.db:
            for key in message_keys:
                self.db.execute('INSERT OR IGNORE INTO latest_claims VALUES (?,?)',
                                (self.fingerprint(f'{group}:latest_recovery:{key!r}' if group else f'latest_recovery:{key!r}'),now))
            self.db.execute("INSERT INTO attempts VALUES (?,?)", (task_id, now))
            self.db.execute("UPDATE tasks SET state='sending' WHERE id=?", (task_id,))
            if reply:
                self.db.execute('INSERT OR REPLACE INTO send_receipts (task_id,group_digest,reply_digest,baseline,at,sticker_md5,sticker_id) VALUES(?,?,?,?,?,?,?)',
                    (task_id,self.fingerprint(group),self.fingerprint(normalized(reply)),
                     json.dumps([self.fingerprint(i) for i in baseline_ids if i]),now,
                     self.fingerprint(sticker_md5) if sticker_md5 else '',sticker_id))
                if sticker_id:
                    self.db.execute('INSERT OR IGNORE INTO sticker_history VALUES (?,?)',
                                    (self.fingerprint(group),sticker_id))

    def confirm_receipt(self,task_id,group,message):
        if message.direction!='out' or not message.message_id:return False
        with self.lock,self.db:
            row=self.db.execute('SELECT * FROM send_receipts WHERE task_id=?',(task_id,)).fetchone()
            if not row or row['group_digest']!=self.fingerprint(group):return False
            if row['sticker_md5']:
                from .stickers import media_md5
                md5=media_md5(message.media_raw) if message.kind=='emoji' else ''
                if not md5 or row['sticker_md5']!=self.fingerprint(md5):return False
            elif message.kind!='text' or row['reply_digest']!=self.fingerprint(normalized(message.text)):return False
            if self.fingerprint(message.message_id) in json.loads(row['baseline']):return False
            if not -5<=message.created_at-row['at']<=120:return False
            cursor=self.db.execute("UPDATE tasks SET state='verified',reason='database_receipt' WHERE id=? AND state IN ('sending','unknown')",(task_id,))
            if cursor.rowcount != 1:return False
            self.db.execute("UPDATE send_receipts SET confirmed_digest=? WHERE task_id=?",
                            (self.fingerprint(message.message_id),task_id))
            return True

    def recent_stickers(self,group,now,*,all_history=False):
        with self.lock:
            if all_history:
                return [r[0] for r in self.db.execute('SELECT sticker_id FROM sticker_history WHERE group_digest=?',(self.fingerprint(group),))]
            return [r[0] for r in self.db.execute("SELECT DISTINCT sticker_id FROM send_receipts WHERE group_digest=? AND at>? AND sticker_id!=''",(self.fingerprint(group),now-1800))]

    def sticker_ready(self,group,now,frequency='natural'):
        from .reply_preferences import STICKER_PACING
        if frequency not in STICKER_PACING:raise ValueError('表情频率无效')
        if frequency=='requested':return False
        seconds,texts=STICKER_PACING[frequency]
        with self.lock:
            scope=self.fingerprint(group)
            last=self.db.execute("SELECT max(at) FROM send_receipts WHERE group_digest=? AND sticker_id!=''",(scope,)).fetchone()[0]
            if last is None:return True
            if now-last<seconds:return False
            return self.db.execute("SELECT count(*) FROM send_receipts WHERE group_digest=? AND at>? AND sticker_id=''",(scope,last)).fetchone()[0]>=texts

    def counts(self) -> dict:
        with self.lock:
            return dict(self.db.execute("SELECT state,count(*) FROM tasks GROUP BY state").fetchall())

    def prune(self, now: float) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM tasks WHERE created<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM attempts WHERE at<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM latest_claims WHERE at<?", (now - 7 * 86400,))
            self.db.execute('DELETE FROM send_receipts WHERE at<?',(now-7*86400,))

    def close(self) -> None:
        with self.lock:
            self.db.close()
