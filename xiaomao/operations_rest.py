"""Durable temporary pauses, scoped by salted recipient identity."""
from contextlib import contextmanager
import json
import sqlite3
from .privacy import make_private
from .config import Settings

_REASON='临时休息中，到期后恢复原状态'

class RestManager:
    def __init__(self, resident):
        self.resident=resident
        self.path=resident.settings.runtime/'operations-rest.sqlite3'
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS rests (identity TEXT PRIMARY KEY,data TEXT NOT NULL)')
        make_private(self.path)
        self.epochs={}
        self.tick(boot=True)

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()
    def key(self,engine): return self.resident.store.fingerprint('rest:'+engine.settings.memory_scope)
    def records(self):
        with self._db() as db: return {key:json.loads(raw) for key,raw in db.execute('SELECT identity,data FROM rests')}
    def write(self,key,row):
        with self._db() as db: db.execute('INSERT OR REPLACE INTO rests VALUES(?,?)',(key,json.dumps(row,separators=(',',':'))))
    def clear(self,engine=None):
        with self._db() as db:
            if engine is None: db.execute('DELETE FROM rests');self.epochs.clear()
            else: db.execute('DELETE FROM rests WHERE identity=?',(self.key(engine),));self.epochs.pop(self.key(engine),None)
    def info(self,engine):
        row=self.records().get(self.key(engine))
        if not row:return dict(active=False,until=None,remainingSeconds=0,minutes=0,priorMode=None)
        return dict(active=True,until=row['until'],remainingSeconds=max(0,int(row['until']-self.resident.clock()+.999)),
                    minutes=row['minutes'],priorMode=row['prior'])
    def start(self,engine,minutes):
        if type(minutes) is not int or not 1<=minutes<=1440:raise ValueError('休息时长应为1至1440分钟的整数')
        if not engine.settings.conversation_targets.get(engine.settings.group):raise ValueError('请先确认会话身份')
        self.tick()
        key=self.key(engine);old=self.records().get(key);now=self.resident.clock()
        row=dict(start=now,until=now+minutes*60,minutes=minutes,prior=old['prior'] if old else engine.mode,
                 savedPause=engine.settings.is_conversation_paused(),last=now)
        # Write before pausing: a crash cannot silently resume an unexpired rest.
        self.write(key,row)
        engine.pause(_REASON,preserve_companion=True)
        self.epochs[key]=engine.epoch
        return {'rest':self.info(engine)}
    def cancel(self,engine):
        self.tick()
        row=self.records().get(self.key(engine))
        if row:self._finish(engine,row)
        return {'rest':self.info(engine)}
    def _saved_matches(self,engine,row,saved=None):
        try:
            saved=saved or Settings.load(self.resident.settings.root)
            name=engine.settings.group
            return (name in saved.allowed_groups and saved.conversation_scope(name)==engine.settings.memory_scope
                    and saved.is_conversation_paused(name)==row['savedPause'])
        except (OSError,ValueError,TypeError):return False
    def _finish(self,engine,row):
        key=self.key(engine)
        eligible=(self.resident.requested_mode!='paused' and engine.mode=='paused' and engine.reason==_REASON and self.epochs.get(key)==engine.epoch
                  and engine.settings.is_conversation_paused()==row['savedPause'] and self._saved_matches(engine,row))
        self.clear(engine)
        if not eligible:return
        if row['prior']=='auto' and not row['savedPause']:
            try:engine.enable()
            except ValueError:engine.pause('休息已结束，请核对窗口和模型设置后开启')
        elif row['prior']=='observe':engine.observe(preserve_companion=True)
        else:engine.pause('休息已结束，保留原暂停状态',preserve_companion=True)
    def tick(self,boot=False):
        engines={self.key(e):e for e in self.resident.engines};now=self.resident.clock()
        records=self.records()
        if not records:return
        try:saved=Settings.load(self.resident.settings.root)
        except (OSError,ValueError,TypeError):saved=None
        for key,row in records.items():
            engine=engines.get(key)
            if engine is None:
                with self._db() as db:db.execute('DELETE FROM rests WHERE identity=?',(key,))
                self.epochs.pop(key,None);continue
            if engine.mode=='stopped':continue
            if self.resident.requested_mode=='paused':
                # Recovery/global pause is authoritative. Never replace its
                # reason/epoch with a timer that could later resume sending.
                self.clear(engine)
                continue
            if now<row['last'] or engine.settings.is_conversation_paused()!=row['savedPause'] or not self._saved_matches(engine,row,saved):
                self.clear(engine)
                engine.pause('休息计划因系统时间或手动设置变化而取消，请手动核对后开启',preserve_companion=True)
                continue
            if boot:
                engine.pause(_REASON,preserve_companion=True);self.epochs[key]=engine.epoch
            elif engine.mode!='paused' or engine.reason!=_REASON or self.epochs.get(key)!=engine.epoch:
                self.clear(engine);continue
            if now>=row['until']:self._finish(engine,row)
            elif now>row['last']:
                row['last']=now;self.write(key,row)
