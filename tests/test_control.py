import json
import httpx
import pytest


@pytest.fixture
def service(tmp_path):
    from xiaomao.config import Settings
    from xiaomao.engine import Engine
    from xiaomao.store import StateStore
    from xiaomao.control import ControlServer
    store = StateStore(tmp_path/'runtime')
    engine = Engine(Settings(tmp_path), store, object(), object(), object())
    with ControlServer(tmp_path, engine) as server:
        yield server, engine, tmp_path
    store.close()


def test_control_rejects_missing_token_and_browser_origin(service):
    server, _, _ = service
    with httpx.Client(trust_env=False) as client:
        assert client.get(server.url+'/status').status_code == 401
        assert client.post(server.url+'/enable', headers={'Authorization':'Bearer wrong'}, json={}).status_code == 401
        assert client.post(server.url+'/pause',headers={'Authorization':'Bearer '+server.token,'Origin':'https://evil.example'},json={}).status_code == 403


def test_authenticated_control_returns_state_without_secrets(service):
    from xiaomao.client import ControlClient
    server, engine, root = service
    result = ControlClient(root).status()
    assert result['mode'] == 'observe'
    assert server.token not in json.dumps(result)
    result = ControlClient(root).pause()
    assert result['mode'] == 'paused'
    assert engine.cancelled.is_set()


def test_large_or_unexpected_control_input_is_rejected(service):
    server, _, _ = service
    headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'}
    with httpx.Client(trust_env=False) as client:
        assert client.post(server.url+'/pause',headers=headers,content='x'*5000).status_code == 413
        assert client.post(server.url+'/pause',headers=headers,json={'chat':'other'}).status_code == 400
        assert client.get(server.url+'/arbitrary-send',headers=headers).status_code == 404


def test_valid_large_routine_preferences_fit_dedicated_control_body_limit(service):
    server,engine,_=service
    engine.companion_configure=lambda **payload: {'received':len(payload['preferences']['style'])}
    headers={'Authorization':'Bearer '+server.token}
    payload=dict(group='会话',scope='account/peer',preferences=dict(style='😀'*2000,
        interests='😀'*500,dates=[{'date':'11-07','label':'😀'*80} for _ in range(8)],
        routines=[dict(id=str(i),time='09:00',weekdays=list(range(7)),
        enabled=True,intent='😀'*160,prefer_sticker=True) for i in range(8)]))
    from xiaomao.companion import CompanionPreferences
    CompanionPreferences(**payload['preferences'])
    with httpx.Client(trust_env=False) as client:
        response=client.post(server.url+'/companion_configure',headers=headers,json=payload)
        assert response.status_code==200
        assert response.json()=={'received':2000}
        payload['preferences']['style']='😀'*9000
        assert client.post(server.url+'/companion_configure',headers=headers,json=payload).status_code==413


def test_outbound_plan_control_rejects_wrong_types_and_requires_exact_scope(service):
    server,engine,root=service
    engine.queue_outbound=lambda **payload: {'queued':True,'id':payload['request_id']}
    from xiaomao.client import ControlClient
    result=ControlClient(root).queue_outbound('小夏','account/peer','你好',['allowlisted'],'outbound-001')
    assert result['queued'] and result['id']=='outbound-001'
    payload=dict(group='小夏',scope='account/peer',text='你好',sticker_ids=['allowlisted'],request_id='outbound-002')
    headers={'Authorization':'Bearer '+server.token}
    with httpx.Client(trust_env=False) as client:
        for change in ({'sticker_ids':'file.gif'},{'scope':None},{'path':'outside.gif'},{'sticker_ids':[1]}):
            assert client.post(server.url+'/queue_outbound',headers=headers,json={**payload,**change}).status_code==400


@pytest.mark.parametrize('size,drained',[(40000,True),(200000,False)])
def test_oversized_authenticated_settings_body_is_drained_only_within_bound(service,size,drained):
    import io
    from types import SimpleNamespace
    server,_,_=service
    handler=server.server.RequestHandlerClass.__new__(server.server.RequestHandlerClass)
    handler.headers={'Host':server.url.removeprefix('http://'),'Authorization':'Bearer '+server.token,
                     'Content-Length':str(size),'Content-Type':'application/json'}
    handler.path='/companion_configure';handler.rfile=io.BytesIO(b'x'*size)
    handler.connection=SimpleNamespace(gettimeout=lambda:None,settimeout=lambda value:None)
    answers=[];handler.answer=lambda status,data:answers.append(status)
    handler.route('POST')
    assert answers==[413]
    assert handler.rfile.tell()==(size if drained else 0)


@pytest.mark.parametrize('endpoint',[{'host':'evil.example','port':80,'token':'a'*64,'pid':1}, []])
def test_control_client_rejects_invalid_or_non_local_endpoint(tmp_path,endpoint):
    from xiaomao.client import ControlClient, ControlUnavailable
    directory=tmp_path/'runtime'
    directory.mkdir()
    (directory/'control.json').write_text(json.dumps(endpoint),encoding='utf-8')
    with pytest.raises(ControlUnavailable):
        ControlClient(tmp_path).status()


def test_companion_once_is_authenticated_empty_and_requires_auto(service):
    from xiaomao.client import ControlClient,ControlUnavailable
    server,engine,root=service
    with httpx.Client(trust_env=False) as client:
        assert client.post(server.url+'/companion_once',json={}).status_code==401
        headers={'Authorization':'Bearer '+server.token}
        assert client.post(server.url+'/companion_once',headers=headers,json={'target':'someone'}).status_code==400
    with pytest.raises(ControlUnavailable,match='开启自动回复'):ControlClient(root).companion_once()


@pytest.mark.parametrize('failure_stage',['headers','body'])
def test_disconnected_status_reader_does_not_trigger_second_error_response(service,failure_stage):
    from types import SimpleNamespace
    server,_,_=service
    handler=server.server.RequestHandlerClass.__new__(server.server.RequestHandlerClass)
    statuses=[]
    handler.send_response=statuses.append
    handler.send_header=lambda *a:None
    def aborted(data):raise ConnectionAbortedError('reader closed')
    handler.end_headers=(lambda:aborted(b'headers')) if failure_stage=='headers' else lambda:None
    handler.wfile=SimpleNamespace(write=aborted)
    handler.answer(200,{'mode':'auto'})
    assert statuses==[200]
