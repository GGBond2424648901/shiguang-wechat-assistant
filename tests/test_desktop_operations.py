import math
import sqlite3
import httpx
import pytest
from xiaomao.config import Settings
from xiaomao.client import ControlUnavailable
from test_desktop_services import configured, FixtureClient
from test_engine import system


def test_usage_records_real_attempts_unknown_tokens_and_prices(tmp_path):
    from xiaomao.operations_usage import UsageStore, record_usage
    runtime = tmp_path / 'runtime'
    record_usage(runtime, kind='model', duration_ms=12, data={'usage': {'prompt_tokens': 100, 'completion_tokens': 40}})
    record_usage(runtime, kind='model', duration_ms=2, error=True)
    record_usage(runtime, kind='search', duration_ms=4)
    store = UsageStore(runtime)
    result = store.read(7)
    assert result['totals']['calls'] == 2
    assert result['totals']['searches'] == 1
    assert result['totals']['inputTokens'] == 100
    assert result['totals']['unknownTokenCalls'] == 1
    assert result['estimatedCost'] is None
    saved = store.save_prices({'inputPerMillion': 2, 'outputPerMillion': 8}, expected=result['pricing']['revision'])
    assert saved['inputPerMillion'] == 2
    with pytest.raises(ValueError, match='更新'): store.save_prices({'inputPerMillion': 1, 'outputPerMillion': 1}, expected=result['pricing']['revision'])
    for value in (True, -1, math.inf, '2', 100001):
        with pytest.raises(ValueError): store.save_prices({'inputPerMillion': value, 'outputPerMillion': 1})
    with pytest.raises(ValueError): store.read(True)
    with sqlite3.connect(store.path) as db:
        assert not any('prompt' in row[1] or 'name' in row[1] for row in db.execute('pragma table_info(usage_events)'))


def test_model_instrumentation_counts_http_attempts_not_cooldown(tmp_path):
    from xiaomao.model import ChatModel, ModelError
    from xiaomao.operations_usage import UsageStore
    cfg = Settings(tmp_path, model_base_url='http://127.0.0.1:9999/v1')
    responses = iter([httpx.Response(500), httpx.Response(200, json={'usage': {'prompt_tokens': 5, 'completion_tokens': 3}, 'choices': []})])
    with httpx.Client(transport=httpx.MockTransport(lambda req: next(responses))) as client:
        model = ChatModel(cfg, client)
        with pytest.raises(ModelError): model._request('POST', '/chat/completions', {})
        model._request('POST', '/chat/completions', {})
    usage = UsageStore(cfg.runtime).read(1)
    assert usage['totals']['calls'] == 2 and usage['totals']['errors'] == 1
    assert usage['totals']['inputTokens'] == 5 and usage['totals']['unknownTokenCalls'] == 1


def test_operations_reject_stale_account_and_readonly_mutations(tmp_path):
    from xiaomao.desktop_operations import DesktopOperations
    cfg = configured(tmp_path)
    client = FixtureClient()
    client.status = lambda: {'account_scope': 'wrong-account', 'groups': [{'name':'小禾', 'companion': {'routine_history':[{'status':'verified'}]}}]}
    ops = DesktopOperations(tmp_path, client)
    assert ops.history()['rows'] == []
    assert not ops.overview()['connected']
    readonly = DesktopOperations(tmp_path, client, read_only=True)
    with pytest.raises(ValueError, match='只读'): readonly.save_prices({'inputPerMillion':0,'outputPerMillion':0})
    with pytest.raises(ValueError, match='只读'): readonly.rest(cfg.conversation_pause_key('小禾'), 10)
    for duration in (True, 0, 1441, 2.5):
        with pytest.raises(ValueError): ops.rest(cfg.conversation_pause_key('小禾'), duration)
    with pytest.raises(ValueError): ops.overview(12)


def test_overview_uses_weekdays_disabled_flags_and_no_promised_proactive_time(tmp_path,monkeypatch):
    from datetime import datetime
    from dataclasses import asdict
    from xiaomao.companion import CompanionPreferences,LOCAL
    from xiaomao.routines import routine_preset
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao.news import default_subscription
    from xiaomao.news_store import NewsStore
    from xiaomao import desktop_operations as module
    cfg=configured(tmp_path);client=FixtureClient()
    now=datetime(2026,11,11,8,0,tzinfo=LOCAL).timestamp() # Wednesday
    monkeypatch.setattr(module.time,'time',lambda:now)
    routine=routine_preset('morning');routine.update(enabled=True,weekdays=[2])
    prefs=asdict(CompanionPreferences(enabled=True,routines=[routine]))
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda target:(prefs,{'last_activity':now})))
    sub=default_subscription();session=cfg.conversation_pause_key('晨光群')
    sub.update(enabled=False,recipients=[session],targets=[dict(session=session,name='晨光群',scope=cfg.conversation_scope('晨光群'))])
    with_store=NewsStore(cfg.runtime)
    try:with_store.save_subscription('account-a',sub)
    finally:with_store.close()
    rows=DesktopOperations(tmp_path,client).overview(48)['rows']
    assert len([r for r in rows if r['kind']=='routine'])==2 # one date, two scoped sessions
    assert all(r['state']=='disabled' for r in rows if r['kind']=='news')
    assert all(r['at'] is None and r['conditional'] for r in rows if r['kind']=='proactive')
    assert any(r['state']=='paused' for r in rows if r['kind']=='routine' and r['name']=='小禾')
    # A stored snapshot with a removed identity never leaks a row for its title.
    assert not any(r['name']=='错误对象' for r in rows)


@pytest.mark.parametrize('blocker,fragment', [
    ('offline', '后台尚未连接'), ('account', '核对登录账号'),
    ('observe', '仅观察'), ('uncalibrated', '校准'), ('missing', '会话身份'),
    ('stale_scope', '会话身份'), ('missing_scope', '会话身份'),
    ('window', '需重新绑定'), ('reload', '窗口配置'), ('ready', ''),
])
def test_future_plans_explain_current_blockers_without_promising_execution(tmp_path, monkeypatch, blocker, fragment):
    from datetime import datetime
    from dataclasses import asdict
    from xiaomao.companion import CompanionPreferences, LOCAL
    from xiaomao.routines import routine_preset
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao.news import default_subscription
    from xiaomao.news_store import NewsStore
    cfg = configured(tmp_path); client = FixtureClient(); name = cfg.group
    now = datetime(2026, 11, 11, 8, 0, tzinfo=LOCAL).timestamp()
    monkeypatch.setattr('xiaomao.desktop_operations.time.time', lambda: now)
    target = dict(name=name, scope=cfg.conversation_scope(name), mode='auto', calibrated=True, window_state='在后台')
    status = dict(account_scope='account-a', groups=[target])
    if blocker == 'account': status['account_scope'] = 'other-account'
    if blocker == 'observe': target.update(mode='observe', reason='仅观察，不发送')
    if blocker == 'uncalibrated': target['calibrated'] = False
    if blocker == 'missing': status['groups'] = []
    if blocker == 'stale_scope': target['scope'] = 'old-scope'
    if blocker == 'missing_scope': target.pop('scope')
    if blocker == 'window': target['window_state'] = '需重新绑定'
    if blocker == 'reload': status['window_reload_pending'] = True
    def worker_status():
        if blocker == 'offline': raise ControlUnavailable('offline')
        return status
    client.status = worker_status
    routine = routine_preset('morning'); routine.update(enabled=True, weekdays=[2])
    prefs = asdict(CompanionPreferences(enabled=True, routines=[routine]))
    monkeypatch.setattr(DesktopOperations, '_prefs', staticmethod(lambda target: (prefs, {'last_activity':now})))
    sub = default_subscription(); session = cfg.conversation_pause_key(name)
    sub.update(enabled=True, recipients=[session], targets=[dict(session=session, name=name, scope=cfg.conversation_scope(name))])
    store = NewsStore(cfg.runtime)
    try: saved = store.save_subscription('account-a', sub)
    finally: store.close()
    ops = DesktopOperations(tmp_path, client)
    overview = ops.overview()
    rows = [row for row in overview['rows'] if row['name'] == name]
    assert {row['kind'] for row in rows} == {'news', 'routine', 'proactive'}
    assert all(row['conditional'] and row['at'] is None for row in rows if row['kind'] == 'proactive')
    for row in rows:
        expected = ('conditional' if row['conditional'] else 'scheduled') if blocker == 'ready' else 'waiting'
        assert row['state'] == expected
        assert fragment in row['reason']
    if blocker in ('offline', 'account'):
        assert not overview['connected'] and fragment in overview['reason']
    # Explicit disablement and pause remain authoritative even when disconnected.
    target.update(mode='paused', reason='当前微信账号身份待确认')
    monkeypatch.setattr(Settings, 'is_conversation_paused', lambda *args: True)
    assert all(row['state'] == 'paused' for row in ops.overview()['rows'] if row['name'] == name)
    if blocker not in ('offline', 'account', 'missing', 'stale_scope'):
        assert all('身份待确认' in row['reason'] for row in ops.overview()['rows'] if row['name'] == name)
    prefs['enabled'] = False
    saved['enabled'] = False
    store = NewsStore(cfg.runtime)
    try: store.save_subscription('account-a', saved, saved['revision'])
    finally: store.close()
    assert all(row['state'] == 'disabled' for row in ops.overview()['rows'] if row['name'] == name)


def test_history_retains_actual_receipts_and_excludes_removed_scopes(tmp_path,monkeypatch):
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao.store import StateStore
    cfg=configured(tmp_path);client=FixtureClient()
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda target:({},{})))
    store=StateStore(cfg.runtime)
    try:
        store.reserve_proactive('visible',cfg.conversation_scope('小禾'),'2026-11-11',1794355200,5,account_max=20)
        store.proactive_result('visible','unknown')
        store.reserve_proactive('hidden','account-a/removed','2026-11-11',1794355201,5,account_max=20)
        store.proactive_result('hidden','verified')
        result=DesktopOperations(tmp_path,client).history()
        assert [(r['id'],r['state']) for r in result['rows']]==[('proactive:visible','unknown')]
    finally:store.close()


def test_diagnostics_network_is_bounded_and_sanitized(tmp_path,monkeypatch):
    from xiaomao.desktop_operations import DesktopOperations
    configured(tmp_path);client=FixtureClient();calls=[]
    def fail(*args,**kwargs):
        calls.append(kwargs)
        raise httpx.ConnectError('RAW_SECRET_OR_SERVER_ERROR')
    monkeypatch.setattr(httpx.Client,'stream',fail)
    ops=DesktopOperations(tmp_path,client)
    assert all(row['code']!='network_model' for row in ops.diagnostics()['items'])
    result=ops.diagnostics(True)
    assert 'RAW_SECRET' not in str(result) and not client.calls
    assert result['network']


def test_account_matched_but_stale_worker_scope_cannot_supply_history(tmp_path,monkeypatch):
    from xiaomao.desktop_operations import DesktopOperations
    cfg=configured(tmp_path);client=FixtureClient()
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda target:({},{})))
    client.status=lambda:dict(account_scope='account-a',groups=[dict(name='小禾',scope='account-a/removed',companion=dict(routine_history=[dict(routine_id='old',day='2026-11-11',status='verified',due_at=20)]))])
    assert DesktopOperations(tmp_path,client).history()['rows']==[]


def test_overview_handles_unavailable_companion_and_includes_conditional_sequence(tmp_path,monkeypatch):
    from dataclasses import asdict
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao.companion import CompanionPreferences
    cfg=configured(tmp_path);client=FixtureClient()
    client.status=lambda:dict(account_scope='account-a',groups=[dict(name='晨光群',companion=None)])
    prefs=asdict(CompanionPreferences(enabled=True))
    planner=dict(last_activity=100,unanswered=True,opener_at=90,checkin_at=120,signoff_at=4000)
    monkeypatch.setattr('xiaomao.desktop_operations.time.time',lambda:100)
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda target:(prefs,planner)))
    rows=DesktopOperations(tmp_path,client).overview()['rows']
    assert any(r['title']=='轻轻追问' and r['at'] is None for r in rows)
    assert any(r['title']=='自然收尾' and r['at'] is None for r in rows)


def test_history_uses_recipient_receipt_instead_of_overall_news_state(tmp_path,monkeypatch):
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao.news import default_subscription
    from xiaomao.news_store import NewsStore
    cfg=configured(tmp_path);client=FixtureClient();session=cfg.conversation_pause_key('小禾')
    sub=default_subscription();target=dict(session=session,name='小禾',scope=cfg.conversation_scope('小禾'))
    sub.update(recipients=[session],targets=[target])
    store=NewsStore(cfg.runtime)
    try:
        sub=store.save_subscription('account-a',sub)
        edition=store.create_edition('account-a',sub['id'],sub,'manual',None,100)
        store.update_edition('account-a',edition['id'],state='partial',deliveries=[dict(**target,state='unknown',reason='发送结果未知，不会重发')])
    finally:store.close()
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda target:({},{})))
    rows=DesktopOperations(tmp_path,client).history()['rows']
    assert len(rows)==1 and rows[0]['state']=='unknown' and rows[0]['reason']=='发送结果未知，不会重发'


def test_search_attempts_context_and_missing_key_do_not_fabricate_calls(tmp_path):
    from xiaomao.operations_usage import UsageStore,usage_context
    from xiaomao.web_search import search_public
    from xiaomao.model import ChatModel,ModelError
    original=tmp_path/'original';isolated=tmp_path/'isolated'
    cfg=Settings(isolated,model_base_url='http://127.0.0.1:9999/v1')
    def endpoint(req):
        if req.url.path.endswith('search'):return httpx.Response(503)
        return httpx.Response(200,json={'usage':{'prompt_tokens':10,'completion_tokens':5}})
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as transport,usage_context(original/'runtime'):
        ChatModel(cfg,transport)._request('POST','/chat/completions',{})
        assert search_public(transport,'test-key','public topic') is None
    result=UsageStore(original/'runtime').read(1)
    assert result['totals']['calls']==1 and result['totals']['searches']==1 and result['totals']['errors']==1
    assert not (isolated/'runtime'/'operations.sqlite3').exists()
    missing=Settings(tmp_path/'missing')
    with pytest.raises(ModelError):ChatModel(missing)._request('POST','/chat/completions',{})
    assert not (missing.runtime/'operations.sqlite3').exists()


def test_slow_network_probe_has_total_deadline_without_holding_mutation_lock(tmp_path,monkeypatch):
    import threading
    import time
    from xiaomao import desktop_operations as module
    from xiaomao.desktop_operations import DesktopOperations
    configured(tmp_path)
    release=threading.Event()
    def slow(*args,**kwargs):
        release.wait(.5)
        raise httpx.ReadTimeout('PRIVATE_DETAIL')
    monkeypatch.setattr(httpx.Client,'stream',slow)
    monkeypatch.setattr(module,'_PROBE_SECONDS',.04,raising=False)
    started=time.monotonic()
    try:
        result=DesktopOperations(tmp_path,FixtureClient()).diagnostics(True)
        assert time.monotonic()-started < .25
        assert next(r for r in result['items'] if r['code']=='network_model')['state'] in ('warn','unknown')
    finally:release.set()


def test_usage_and_prices_are_account_scoped_even_when_request_finishes_after_switch(tmp_path):
    from dataclasses import replace
    from xiaomao.operations_usage import UsageStore,record_usage
    cfg=configured(tmp_path)
    record_usage(cfg.runtime,kind='model',duration_ms=5,data={'usage':{'prompt_tokens':7,'completion_tokens':2}})
    first=UsageStore(cfg.runtime).read(1)
    UsageStore(cfg.runtime).save_prices({'inputPerMillion':5,'outputPerMillion':8},first['pricing']['revision'])
    targets={name:{**target,'account':'account-b'} for name,target in cfg.conversation_targets.items()}
    replace(cfg,conversation_targets=targets).save()
    second=UsageStore(cfg.runtime).read(1)
    assert second['totals']['calls']==0 and second['pricing']['revision'].endswith(':0')
    record_usage(cfg.runtime,account='account-a',kind='model',duration_ms=2,error=True)
    assert UsageStore(cfg.runtime).read(1)['totals']['calls']==0
    cfg.save()
    restored=UsageStore(cfg.runtime).read(1)
    assert restored['totals']['calls']==2 and restored['pricing']['inputPerMillion']==5
    assert b'account-a' not in UsageStore(cfg.runtime).path.read_bytes()


def test_diagnostics_does_not_claim_public_search_without_testing(tmp_path,monkeypatch):
    from xiaomao.desktop_operations import DesktopOperations
    configured(tmp_path)
    monkeypatch.setattr('xiaomao.desktop_operations._probe_model',lambda *args:('ok','模型服务可连接'))
    result=DesktopOperations(tmp_path,FixtureClient()).diagnostics(True)
    item=next(r for r in result['items'] if r['code']=='network_search')
    assert item['state']=='unknown' and '凭据' in item['detail']


def test_price_revision_from_another_account_cannot_update_current_account(tmp_path):
    from dataclasses import replace
    from xiaomao.operations_usage import UsageStore
    cfg=configured(tmp_path)
    revision=UsageStore(cfg.runtime).read(1)['pricing']['revision']
    replace(cfg,conversation_targets={n:{**t,'account':'account-b'} for n,t in cfg.conversation_targets.items()}).save()
    with pytest.raises(ValueError,match='更新'):
        UsageStore(cfg.runtime).save_prices({'inputPerMillion':1,'outputPerMillion':1},revision)


def test_public_search_probe_uses_fixed_query_real_accounting_and_short_timeout(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao.credentials import CredentialStore
    from xiaomao.operations_usage import UsageStore
    configured(tmp_path);attempts=[]
    monkeypatch.setattr(CredentialStore,'get_keys',lambda self:['sk-test-only'])
    monkeypatch.setattr('xiaomao.desktop_operations._probe_model',lambda *args:('ok','模型服务可连接'))
    @contextmanager
    def stream(self,*args,**kwargs):
        attempts.append(kwargs)
        yield httpx.Response(200,json={'results':[]})
    monkeypatch.setattr(httpx.Client,'stream',stream)
    result=DesktopOperations(tmp_path,FixtureClient()).diagnostics(True)
    assert next(r for r in result['items'] if r['code']=='network_search')['state']=='ok'
    assert attempts[0]['json']['query']=='公开科技新闻'
    assert attempts[0]['timeout'].read<=3
    assert UsageStore(tmp_path/'runtime').read(1)['totals']['searches']==1


def test_explicit_operator_text_survives_real_engine_finalization(system):
    from xiaomao.model import ChatModel
    from xiaomao.domain import ReplyTask
    engine,screen,_,clock,store=system
    from xiaomao.persona import save_persona
    save_persona(engine.settings.root,'# 测试人格\n【微信语言执行约定·V3.0】\n闲聊不带句号。')
    engine.enable();engine.model=ChatModel(engine.settings)
    assert engine.model._wechat_voice_enabled()
    text='第一段。\n第二段。'
    identity=store.create('approved-text',clock())
    task=ReplyTask(identity,clock(),clock(),'',reply=text,state='generated',operator_plan=True,
                   pacing_planned=True,reply_not_before=clock()+30)
    engine.tasks.append(task)
    engine._reply_and_send(task)
    assert task.reply==text and screen.events==[]
    task.continuation_of='approved-parent';task.part_index=1
    engine._reply_and_send(task)
    assert task.reply==text and screen.events==[]


@pytest.mark.parametrize('planner,expected',[
    ({'last_activity':100,'last_in':100,'next_idle':10000,'followup_at':0},(10000,10120)),
    ({'last_activity':100,'last_in':100,'next_idle':12000,'followup_at':0},(12000,12120)),
])
def test_proactive_idle_window_uses_persisted_timer(tmp_path,monkeypatch,planner,expected):
    from dataclasses import asdict
    from xiaomao.companion import CompanionPreferences
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao import desktop_operations as module
    configured(tmp_path);prefs=asdict(CompanionPreferences(enabled=True))
    monkeypatch.setattr(module.time,'time',lambda:9000)
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda cfg:(prefs,planner)))
    rows=DesktopOperations(tmp_path,FixtureClient()).overview()['rows']
    idle=next(r for r in rows if r['title']=='空闲话题')
    assert (idle['windowStart'],idle['windowEnd'])==expected and idle['at'] is None
    assert not any(r['title']=='聊后续话' for r in rows)


def test_proactive_rebased_sequence_and_consumed_followup_are_truthful(tmp_path,monkeypatch):
    from dataclasses import asdict
    from xiaomao.companion import CompanionPreferences
    from xiaomao.desktop_operations import DesktopOperations
    from xiaomao import desktop_operations as module
    configured(tmp_path);prefs=asdict(CompanionPreferences(enabled=True))
    planner={'unanswered':True,'last_activity':100,'opener_at':100,'checkin_at':10000,'signoff_at':0}
    monkeypatch.setattr(module.time,'time',lambda:9000)
    monkeypatch.setattr(DesktopOperations,'_prefs',staticmethod(lambda cfg:(prefs,planner)))
    row=next(r for r in DesktopOperations(tmp_path,FixtureClient()).overview()['rows'] if r['title']=='轻轻追问')
    assert row['windowStart']==10000 and row['windowEnd']==10120
    planner['checkin_at']=8800
    assert not any(r['title']=='轻轻追问' for r in DesktopOperations(tmp_path,FixtureClient()).overview()['rows'])


def test_usage_queue_is_scoped_and_missing_age_is_unknown(tmp_path):
    from xiaomao.desktop_operations import DesktopOperations
    cfg=configured(tmp_path);client=FixtureClient()
    client.pending=lambda:dict(tasks=[dict(id='one',group='小禾',scope=cfg.conversation_scope('小禾'),ageSeconds=15,createdAt=100),
        dict(id='removed',group='小禾',scope='account-a/removed',ageSeconds=999),dict(id='two',group='晨光群',scope=cfg.conversation_scope('晨光群'),ageSeconds=8)])
    result=DesktopOperations(tmp_path,client).usage(1)
    assert result['queue']==dict(connected=True,pending=2,oldestSeconds=15)
    client.pending=lambda:dict(tasks=[dict(id='one',group='小禾',scope=cfg.conversation_scope('小禾'))])
    assert DesktopOperations(tmp_path,client).usage(1)['queue']['oldestSeconds'] is None
    client.status=lambda:dict(account_scope='wrong-account')
    assert DesktopOperations(tmp_path,client).usage(1)['queue']==dict(connected=False,pending=None,oldestSeconds=None)


def test_engine_pending_reports_factual_age(system):
    from xiaomao.domain import ReplyTask
    engine,_,_,clock,store=system
    identity=store.create('queued-age',clock());engine.tasks.append(ReplyTask(identity,clock(),clock(),'',reply='待发',state='generated'))
    clock.advance(17)
    row=engine.pending()[0]
    assert row['createdAt']==100 and row['ageSeconds']==17


def test_operator_readback_requires_exact_approved_characters():
    from xiaomao.domain import ReplyTask
    from xiaomao.engine import bubble_matches
    task=ReplyTask('operator',100,100,'',reply='批准：Ａ。',state='generated',operator_plan=True)
    assert bubble_matches(task,'批准：Ａ。')
    assert not bubble_matches(task,'批准:A。')


def test_usage_queue_account_switch_and_unavailable_worker_are_unknown(tmp_path):
    from xiaomao.desktop_operations import DesktopOperations
    configured(tmp_path);client=FixtureClient();original=client.status
    states=iter([original(),dict(account_scope='other-account')])
    client.status=lambda:next(states)
    client.pending=lambda:dict(tasks=[])
    assert DesktopOperations(tmp_path,client).usage(1)['queue']==dict(connected=False,pending=None,oldestSeconds=None)
    def unavailable():raise ControlUnavailable('unavailable')
    client.status=original;client.pending=unavailable
    assert DesktopOperations(tmp_path,client).usage(1)['queue']==dict(connected=False,pending=None,oldestSeconds=None)
