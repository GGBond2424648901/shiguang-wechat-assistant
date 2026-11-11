"""Read-only operational projections; all sending stays in the resident."""
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import math
import hmac
from pathlib import Path
import sqlite3
import queue
import threading
import time
from urllib.parse import urlsplit
import httpx
from .account_switching import configured_account
from .client import ControlUnavailable
from .companion import CompanionPreferences, LOCAL
from .companion_store import CompanionStore
from .config import Settings
from .news_schedule import schedule_occurrences
from .news_store import NewsStore
from .operations_usage import UsageStore


_PROBE_SECONDS=4.0
_PROBES=threading.BoundedSemaphore(2)


def _probe_model(endpoint, headers):
    """Bound total wall time as well as individual socket reads; never read a body."""
    if not _PROBES.acquire(blocking=False):
        return 'unknown','已有网络检测进行中，请稍后重试'
    results=queue.Queue(maxsize=1)
    def run():
        try:
            with httpx.Client(timeout=httpx.Timeout(3,connect=2),follow_redirects=False,trust_env=False) as client:
                with client.stream('GET',endpoint,headers=headers) as response:code=response.status_code
            result=('ok','模型服务可连接') if code==200 else ('warn',f'模型服务返回 HTTP {code}')
        except Exception:result=('warn','模型服务暂时无法连接，请检查网络和设置')
        finally:_PROBES.release()
        results.put(result)
    threading.Thread(target=run,name='operations-network-probe',daemon=True).start()
    try:return results.get(timeout=_PROBE_SECONDS)
    except queue.Empty:return 'warn','模型连接检测超时，请稍后重试'


def _probe_search(runtime, key, account):
    if not _PROBES.acquire(blocking=False):return 'unknown','已有网络检测进行中，请稍后重试'
    results=queue.Queue(maxsize=1)
    def run():
        try:
            from .web_search import search_public
            from .operations_usage import usage_context
            with usage_context(runtime,account=account), httpx.Client(timeout=httpx.Timeout(3,connect=2),follow_redirects=False,trust_env=False) as client:
                # Fixed public query: diagnostics never copies private chat or target names.
                rows=search_public(client,key,'公开科技新闻',runtime=runtime,timeout=httpx.Timeout(3,connect=2))
            result=('warn','公共搜索服务暂不可用，请检查网络和凭据') if rows is None else ('ok','公共搜索服务已响应' if rows else '公共搜索服务已响应，本次无匹配结果')
        except Exception:result=('warn','公共搜索检测未能完成，请稍后重试')
        finally:_PROBES.release()
        results.put(result)
    threading.Thread(target=run,name='operations-search-probe',daemon=True).start()
    try:return results.get(timeout=_PROBE_SECONDS)
    except queue.Empty:return 'warn','公共搜索检测超时，请稍后重试'


class DesktopOperations:
    def __init__(self, root, client, read_only=False):
        self.root,self.client,self.read_only = Path(root),client,read_only

    def _snapshot(self):
        cfg = Settings.load(self.root)
        account = configured_account(cfg)
        targets = {cfg.conversation_pause_key(name):(name,cfg.for_group(name)) for name in cfg.allowed_groups
                   if cfg.conversation_targets.get(name)}
        connected,reason,status = False,'后台尚未连接',{}
        try:
            status = self.client.status()
            if status.get('account_scope') != account or not account:
                reason,status = '后台正在核对登录账号',{}
            else: connected,reason = True,''
        except ControlUnavailable: pass
        return cfg,account,targets,status,connected,reason

    def _check(self, cfg):
        fresh=Settings.load(self.root)
        if (configured_account(fresh) != configured_account(cfg)
            or {n:fresh.conversation_scope(n) for n in fresh.allowed_groups} != {n:cfg.conversation_scope(n) for n in cfg.allowed_groups}):
            raise ValueError('会话身份或微信账号已变化，请刷新')

    @staticmethod
    def _prefs(cfg):
        if not (cfg.runtime/'fingerprint.key').is_file(): return asdict(CompanionPreferences()),{}
        notes=CompanionStore(cfg.runtime,cfg.memory_scope,read_only=True)
        return notes.data['preferences'],notes.data['planner']

    @staticmethod
    def _state(status,name,scope=None):
        return next((r for r in status.get('groups',[]) if r.get('name')==name
                     and ('scope' not in r or r['scope']==scope)),{})

    @staticmethod
    def _plan_blocker(target, state, status, connected, connection_reason):
        """Current readiness only; even an unblocked schedule is not a send promise."""
        if not connected: return connection_reason
        if not state or state.get('scope') != target.memory_scope:
            return '后台正在核对会话身份，请等待监听范围同步'
        if state.get('mode') == 'observe': return state.get('reason') or '本会话仅观察，不发送'
        if status.get('window_reload_pending'): return '后台正在同步窗口配置'
        if not state.get('calibrated'): return '会话窗口尚未校准，等待窗口核验'
        window = state.get('window_state')
        if window in ('待校准', '需重新绑定', '被桌面隐藏', '已最小化', '窗口不可见', '窗口状态暂不可读'):
            return f'会话窗口{window}，等待窗口就绪'
        if state.get('mode') != 'auto': return state.get('reason') or '会话发送状态尚未就绪'
        return ''

    def overview(self, hours=24):
        if type(hours) is not int or hours not in (24,48,72): raise ValueError('请选择24、48或72小时')
        cfg,account,targets,status,connected,connection_reason=self._snapshot()
        now=time.time(); end=now+hours*3600; rows=[]
        if account and (cfg.runtime/'news.sqlite3').exists():
            store=NewsStore(cfg.runtime)
            try: subscriptions=store.list_subscriptions(account)
            finally: store.close()
            for sub in subscriptions:
                for session in sub.get('recipients',[]):
                    if session not in targets: continue
                    name,target=targets[session]
                    if not any(t.get('session')==session and t.get('scope')==target.memory_scope for t in sub.get('targets',[])): continue
                    state=self._state(status,name,target.memory_scope)
                    paused=target.is_conversation_paused() or state.get('mode')=='paused'
                    blocker=self._plan_blocker(target,state,status,connected,connection_reason)
                    pause_reason='本会话已暂停' + (f'：{state["reason"]}' if state.get('mode')=='paused' and state.get('reason') else '')
                    reason='订阅未开启' if not sub['enabled'] else pause_reason if paused else blocker
                    for due in schedule_occurrences(sub,now,end):
                        rows.append(dict(id=f'news:{sub["id"]}:{session}:{due}',kind='news',title=sub['name'],at=due,
                            conditional=False,session=session,name=name,state='disabled' if not sub['enabled'] else 'paused' if paused else 'waiting' if blocker else 'scheduled',reason=reason,page='news',subscription=sub['id']))
        for session,(name,target) in targets.items():
            prefs,planner=self._prefs(target); state=self._state(status,name,target.memory_scope)
            paused=target.is_conversation_paused() or state.get('mode')=='paused'
            blocker=self._plan_blocker(target,state,status,connected,connection_reason)
            pause_reason='本会话已暂停' + (f'：{state["reason"]}' if state.get('mode')=='paused' and state.get('reason') else '')
            plan_reason='主动聊天未开启' if not prefs['enabled'] else pause_reason if paused else blocker
            reason=plan_reason or (state.get('companion') or {}).get('reason','')
            mode='disabled' if not prefs['enabled'] else 'paused' if paused else 'waiting' if blocker else 'conditional'
            day=datetime.fromtimestamp(now,LOCAL).replace(hour=0,minute=0,second=0,microsecond=0)
            for routine in prefs['routines']:
                for offset in range(hours//24+2):
                    date=day+timedelta(days=offset)
                    if date.weekday() not in routine['weekdays']: continue
                    hour,minute=map(int,routine['time'].split(':')); due=date.replace(hour=hour,minute=minute).timestamp()
                    if now<=due<=end:
                        rows.append(dict(id=f'routine:{session}:{routine["id"]}:{due}',kind='routine',title=routine['intent'],at=due,
                            conditional=False,session=session,name=name,state='disabled' if not routine['enabled'] or not prefs['enabled'] else 'paused' if paused else 'waiting' if blocker else 'scheduled',reason=plan_reason if routine['enabled'] else '关怀未开启',page='companion'))
            windows=[]
            if planner.get('unanswered'):
                if prefs['gentle_followups']:
                    windows=[(kind,label,planner.get(kind+'_at',0)) for kind,label in [('checkin','轻轻追问'),('signoff','自然收尾')]]
            else:
                if prefs['idle'] and prefs['idle_probability']:
                    due=max(planner.get('next_idle',0),planner.get('last_activity',0)+prefs['idle_min_minutes']*60) if planner.get('last_in') else 0
                    windows.append(('idle','空闲话题',due))
                if prefs['followup'] and prefs['followup_probability'] and planner.get('followup_at'):
                    windows.append(('followup','聊后续话',planner['followup_at']))
            for kind,label,due in windows:
                # These are opportunities to assess, never promised send times.
                # Followup/sequence timers are consumed after the 120s grace.
                if kind!='idle' and (not due or now>=due+120):continue
                if due and due>end:continue
                row=dict(id=f'proactive:{session}:{kind}',kind='proactive',title=label,at=None,conditional=True,
                         session=session,name=name,state=mode,reason=reason or '需满足聊天记录、概率、免打扰和发送条件',page='companion')
                if due:
                    start=max(now,due)
                    row.update(windowStart=start,windowEnd=min(end,start+120 if kind=='idle' else due+120))
                rows.append(row)
            for entry in prefs['dates']:
                for offset in range(hours//24+2):
                    date=day+timedelta(days=offset)
                    if date.strftime('%m-%d')==entry['date'] and date.timestamp()<=end:
                        rows.append(dict(id=f'date:{session}:{entry["date"]}:{date.year}',kind='proactive',title=entry['label'],at=None,
                            windowStart=max(now,date.timestamp()),windowEnd=min(end,date.timestamp()+86400),conditional=True,
                            session=session,name=name,state=mode,reason=reason or '重要日期需满足主动聊天条件',page='companion'))
        self._check(cfg)
        rows.sort(key=lambda r:(r['at'] is None,r['at'] or r.get('windowStart',end),r['id']))
        return dict(now=now,timezone='Asia/Shanghai',connected=connected,reason=connection_reason,rows=rows[:512],truncated=len(rows)>512)

    def history(self, limit=80):
        if type(limit) is not int or not 1<=limit<=200: raise ValueError('历史条数应为1至200')
        cfg,account,targets,status,connected,reason=self._snapshot(); rows=[]; routine_ids=set()
        # Stale worker account cannot contribute receipts, including local files.
        if not connected and reason=='后台正在核对登录账号':
            return dict(rows=[],connected=False,reason=reason)
        if account and (cfg.runtime/'news.sqlite3').exists():
            store=NewsStore(cfg.runtime)
            try: editions=store.history(account,limit=128)
            finally: store.close()
            for edition in editions:
                for target in edition.get('snapshot',{}).get('targets',[]):
                    session=target.get('session')
                    if session not in targets or target.get('scope')!=targets[session][1].memory_scope: continue
                    receipt=next((d for d in edition.get('deliveries',[]) if d.get('session')==session and d.get('scope')==target['scope']),{})
                    rows.append(dict(id=f'news:{edition["id"]}:{session}',kind='news',title=edition['snapshot']['name'],at=edition.get('updatedAt',edition['createdAt']),state=receipt.get('state',edition['state']),reason=receipt.get('reason') or edition.get('reason',''),session=session,name=targets[session][0],page='news',subscription=edition['subscriptionId']))
        for session,(name,target) in targets.items():
            _,planner=self._prefs(target)
            state=self._state(status,name,target.memory_scope).get('companion') or {}
            for record in (state.get('routine_history') or planner.get('routine_history',[]))[-64:]:
                if record.get('candidate_id'): routine_ids.add(record['candidate_id'])
                rows.append(dict(id=f'routine:{session}:{record.get("routine_id","")}:{record.get("day","")}',kind='routine',
                    title=record.get('definition',{}).get('intent','定时关怀'),at=record.get('last_attempt_at') or record.get('due_at',0),
                    state=record.get('status','unknown'),reason=record.get('detail',''),session=session,name=name,page='companion'))
        path=cfg.runtime/'state.sqlite3'; salt_path=cfg.runtime/'fingerprint.key'
        if path.exists() and salt_path.exists():
            salt=salt_path.read_bytes()
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                for session,(name,target) in targets.items():
                    digest=hmac.new(salt,target.memory_scope.encode(),hashlib.sha256).hexdigest()
                    for identity,at,state in db.execute('SELECT id,at,state FROM proactive WHERE scope=? ORDER BY at DESC LIMIT ?',(digest,limit)):
                        if identity in routine_ids: continue
                        rows.append(dict(id='proactive:'+identity,kind='proactive',title='主动聊天',at=at,state=state,reason='',session=session,name=name,page='companion'))
        self._check(cfg); rows.sort(key=lambda r:r['at'],reverse=True)
        result=dict(rows=rows[:limit],connected=connected)
        if not connected:result['reason']=reason
        return result

    def usage(self, days=7):
        cfg,account,targets,status,connected,_=self._snapshot()
        result=UsageStore(cfg.runtime,account).read(days)
        result['queue']=dict(connected=False,pending=None,oldestSeconds=None)
        if connected:
            try:
                pending=self.client.pending().get('tasks')
                current=self.client.status()
                if not isinstance(pending,list) or current.get('account_scope')!=account:
                    raise ControlUnavailable('后台监听范围正在同步')
                scopes={target.memory_scope for _,target in targets.values()}
                names={name for name,_ in targets.values()}
                if any(row.get('group') in names and not row.get('scope') for row in pending):
                    raise ControlUnavailable('后台队列身份尚未就绪')
                rows=[row for row in pending if row.get('scope') in scopes
                      and any(row.get('group')==name and row['scope']==target.memory_scope for name,target in targets.values())]
                ages=[row.get('ageSeconds') for row in rows]
                valid=all(type(age) in (int,float) and math.isfinite(age) and age>=0 for age in ages)
                result['queue']=dict(connected=True,pending=len(rows),oldestSeconds=max(ages) if ages and valid else None)
            except ControlUnavailable:pass
        self._check(cfg)
        return result

    def save_prices(self, prices, expected=None):
        if self.read_only: raise ValueError('只读窗口不能修改价格')
        cfg=Settings.load(self.root)
        result=UsageStore(cfg.runtime,configured_account(cfg)).save_prices(prices,expected)
        self._check(cfg)
        return result

    def _session(self, session):
        cfg=Settings.load(self.root)
        name=next((n for n in cfg.allowed_groups if cfg.conversation_pause_key(n)==session and cfg.conversation_targets.get(n)),None)
        if name is None: raise ValueError('会话已离开监听范围，请重新选择')
        status=self.client.status()
        if status.get('account_scope')!=configured_account(cfg): raise ValueError('微信账号正在核对，请稍后重试')
        self._check(cfg)
        return cfg.for_group(name)

    def rest(self, session, minutes):
        if self.read_only: raise ValueError('只读窗口不能设置休息')
        if type(minutes) is not int or not 1<=minutes<=1440: raise ValueError('休息时长应为1至1440分钟的整数')
        cfg=self._session(session)
        return self.client.rest_selected(cfg.group,cfg.memory_scope,minutes)

    def cancel_rest(self, session):
        if self.read_only: raise ValueError('只读窗口不能取消休息')
        cfg=self._session(session)
        return self.client.cancel_rest_selected(cfg.group,cfg.memory_scope)

    def diagnostics(self, network=False):
        if type(network) is not bool: raise ValueError('网络检测开关无效')
        cfg,account,targets,status,connected,reason=self._snapshot(); items=[]
        def add(code,label,state,detail,action=None):
            row=dict(code=code,label=label,state=state,detail=detail)
            if action: row['action']=action
            items.append(row)
        add('backend','本机后台','ok' if connected else 'warn','后台已连接' if connected else reason,'settings')
        add('account','微信账号','ok' if connected and account else 'unknown','当前账号已核对' if connected and account else '等待确认登录账号','sessions')
        try:
            from .credentials import CredentialStore
            keys=CredentialStore(cfg.runtime).get_keys()
            available=bool(keys)
        except Exception: available=False
        local=urlsplit(cfg.model_base_url).hostname in ('localhost','127.0.0.1','::1')
        add('credentials','模型凭据','ok' if available or local else 'warn','已配置可用凭据' if available else '本地模型无需密钥' if local else '请配置模型凭据','settings')
        add('calibration','窗口校准','ok' if targets and all(t.binding and t.binding.bottom_confirmed for _,t in targets.values()) else 'warn','所有监听窗口已校准' if targets and all(t.binding and t.binding.bottom_confirmed for _,t in targets.values()) else '部分会话等待窗口校准','sessions')
        paused=status.get('mode')=='paused'
        add('pause','回复状态','warn' if paused else 'ok' if connected else 'unknown','后台已暂停' if paused else '后台可继续监听' if connected else '后台状态未知','sessions')
        blocks=[r for r in status.get('groups',[]) if r.get('mode')!='auto' or not r.get('calibrated') or r.get('pending_count')]
        add('send_blocks','发送条件','warn' if blocks else 'ok' if connected else 'unknown',f'{len(blocks)}个会话存在暂停、校准或队列条件' if blocks else '未发现本地发送阻塞' if connected else '连接后台后可核对','sessions')
        if network:
            state,detail='unknown','尚未执行模型连接检测'
            try:
                endpoint=cfg.model_base_url.rstrip('/')+'/models'
                headers={'Authorization':'Bearer '+keys[0]} if available and not local else {}
                host=urlsplit(endpoint)
                if not local and not (host.scheme=='https' and host.hostname=='api.deepseek.com' and host.port in (None,443)):
                    state,detail='warn','模型端点未通过安全校验'
                else:
                    state,detail=_probe_model(endpoint,headers)
            except Exception: state,detail='warn','模型服务暂时无法连接，请检查网络和设置'
            add('network_model','模型网络',state,detail,'settings')
            search_state,search_detail=_probe_search(cfg.runtime,keys[0],account) if available else ('unknown','未配置搜索凭据，尚未测试公共搜索')
            add('network_search','公共搜索网络',search_state,search_detail,'settings')
        self._check(cfg)
        return dict(at=time.time(),network=network,items=items)
