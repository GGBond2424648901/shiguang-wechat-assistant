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


def test_control_client_cannot_be_redirected_to_an_external_host(tmp_path):
    from xiaomao.client import ControlClient, ControlUnavailable
    directory=tmp_path/'runtime'
    directory.mkdir()
    (directory/'control.json').write_text(json.dumps({'host':'evil.example','port':80,'token':'a'*64,'pid':1}),encoding='utf-8')
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
