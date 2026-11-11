"""Private content-free accounting of actual provider HTTP attempts."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
import hashlib
import sqlite3
import threading
import time
from .privacy import private_dir, make_private

_LOCAL = timezone(timedelta(hours=8))
_RUNTIME = ContextVar('operations_usage_runtime', default=None)
_ACCOUNT = ContextVar('operations_usage_account', default=None)
_COUNTERS = ('calls','searches','inputTokens','outputTokens','durationMs','errors','unknownTokenCalls')

def _account(runtime, account=None):
    if account is not None:return account
    from .config import Settings
    from .account_switching import configured_account
    return configured_account(Settings.load(Path(runtime).parent))


@contextmanager
def usage_context(runtime, account=None):
    token = _RUNTIME.set(runtime)
    account_token = _ACCOUNT.set(_account(runtime,account))
    try: yield
    finally:
        _ACCOUNT.reset(account_token)
        _RUNTIME.reset(token)

class UsageStore:
    def __init__(self, runtime, account=None):
        self.account = hashlib.sha256(('operations-account:'+(_account(runtime,account) or 'unbound')).encode()).hexdigest()
        self.path = private_dir(Path(runtime)) / 'operations.sqlite3'
        self.lock = threading.RLock()
        with self._db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS usage_events (
                at REAL NOT NULL, kind TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                duration_ms REAL NOT NULL, error INTEGER NOT NULL, account TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS usage_at ON usage_events(at);
                CREATE TABLE IF NOT EXISTS usage_prices (id INTEGER PRIMARY KEY CHECK(id=1),
                input_price REAL NOT NULL, output_price REAL NOT NULL, revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO usage_prices VALUES(1,0,0,0);
                CREATE TABLE IF NOT EXISTS usage_account_prices (account TEXT PRIMARY KEY,input_price REAL NOT NULL,
                output_price REAL NOT NULL,revision INTEGER NOT NULL);''')
            if 'account' not in {r[1] for r in db.execute('PRAGMA table_info(usage_events)')}:
                db.execute("ALTER TABLE usage_events ADD COLUMN account TEXT NOT NULL DEFAULT ''")
            db.execute('CREATE INDEX IF NOT EXISTS usage_scope_at ON usage_events(account,at)')
            db.execute('INSERT OR IGNORE INTO usage_account_prices VALUES(?,0,0,0)',(self.account,))
        make_private(self.path)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA busy_timeout=5000')
        try:
            with db: yield db
        finally: db.close()

    def record(self, *, kind, duration_ms, data=None, error=False):
        if kind not in ('model','search','feed'): raise ValueError('用量类型无效')
        if type(duration_ms) not in (int,float) or not math.isfinite(duration_ms) or duration_ms < 0:
            raise ValueError('用量耗时无效')
        usage = data.get('usage', {}) if isinstance(data,dict) else {}
        if not isinstance(usage,dict): usage = {}
        def number(*keys):
            value = next((usage[k] for k in keys if k in usage), None)
            return value if type(value) is int and 0 <= value <= 10**12 else None
        inputs, outputs = number('prompt_tokens','input_tokens'), number('completion_tokens','output_tokens')
        now = time.time()
        with self.lock, self._db() as db:
            db.execute('INSERT INTO usage_events(at,kind,input_tokens,output_tokens,duration_ms,error,account) VALUES(?,?,?,?,?,?,?)',(now,kind,inputs,outputs,duration_ms,int(bool(error)),self.account))
            db.execute('DELETE FROM usage_events WHERE at<?',(now-31*86400,))
            db.execute('DELETE FROM usage_events WHERE rowid IN (SELECT rowid FROM usage_events ORDER BY at DESC LIMIT -1 OFFSET 100000)')

    def _prices(self,row):
        return dict(inputPerMillion=row[0],outputPerMillion=row[1],currency='CNY',revision=self.account+':'+str(row[2]))

    def save_prices(self, prices, expected=None):
        if (not isinstance(prices,dict) or set(prices) != {'inputPerMillion','outputPerMillion'}
            or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 <= v <= 100000 for v in prices.values())):
            raise ValueError('单价应为0至100000的有限数字')
        with self.lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT input_price,output_price,revision FROM usage_account_prices WHERE account=?',(self.account,)).fetchone()
            if expected is not None and (not isinstance(expected,str) or expected != self._prices(row)['revision']):
                raise ValueError('价格已更新，请刷新后重试')
            db.execute('UPDATE usage_account_prices SET input_price=?,output_price=?,revision=? WHERE account=?',
                       (prices['inputPerMillion'],prices['outputPerMillion'],row[2]+1,self.account))
            return self._prices((prices['inputPerMillion'],prices['outputPerMillion'],row[2]+1))

    def read(self, days=7):
        if type(days) is not int or days not in (1,7,30): raise ValueError('用量时长应为1、7或30天')
        now = time.time()
        start = datetime.fromtimestamp(now,_LOCAL).replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days-1)
        daily = { (start+timedelta(days=i)).strftime('%Y-%m-%d'): {key:0 for key in _COUNTERS} for i in range(days)}
        with self.lock, self._db() as db:
            prices = self._prices(db.execute('SELECT input_price,output_price,revision FROM usage_account_prices WHERE account=?',(self.account,)).fetchone())
            rows = db.execute('SELECT at,kind,input_tokens,output_tokens,duration_ms,error FROM usage_events WHERE account=? AND at>=? AND at<=?',(self.account,start.timestamp(),now)).fetchall()
        for at,kind,inputs,outputs,duration,error in rows:
            day = daily[datetime.fromtimestamp(at,_LOCAL).strftime('%Y-%m-%d')]
            day['calls'] += kind=='model'
            day['searches'] += kind in ('search','feed')
            day['inputTokens'] += inputs or 0
            day['outputTokens'] += outputs or 0
            day['durationMs'] += duration
            day['errors'] += error
            day['unknownTokenCalls'] += kind=='model' and (inputs is None or outputs is None)
        totals = {key:sum(row[key] for row in daily.values()) for key in _COUNTERS}
        notes = ['仅统计实际发起的请求；搜索与订阅源请求不含独立供应商费用。','价格为手动估算参数，不执行消费限额。']
        if totals['unknownTokenCalls']: notes.append('部分调用未返回完整 token 用量，无法计算总费用；0不代表免费。')
        if prices['revision'].endswith(':0'): notes.append('尚未确认价格，0不代表免费。')
        cost = None if totals['unknownTokenCalls'] or prices['revision'].endswith(':0') else (totals['inputTokens']*prices['inputPerMillion']+totals['outputTokens']*prices['outputPerMillion'])/1000000
        return {'from':start.timestamp(),'to':now,'totals':totals,'daily':[dict(day=day,**row) for day,row in daily.items()],
                'pricing':prices,'estimatedCost':cost,'notes':notes}


def record_usage(runtime=None, *, account=None, kind='model', duration_ms, data=None, error=False):
    runtime = _RUNTIME.get() or runtime
    if runtime is None: return
    # Accounting failures must never replay or block a real outgoing request.
    try: UsageStore(runtime, _ACCOUNT.get() if _ACCOUNT.get() is not None else account).record(kind=kind,duration_ms=duration_ms,data=data,error=error)
    except (OSError,sqlite3.Error,ValueError): pass
