import json
from dataclasses import replace

import httpx
import pytest

from xiaomao.config import Settings
from xiaomao.domain import Message
from xiaomao.model import ChatModel, ModelError


def test_request_bounds_historical_images_without_mutating_memory_or_latest(tmp_path):
    payloads = []
    def endpoint(req):
        payloads.append(json.loads(req.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':'看见啦'}}]})
    model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'),
                      httpx.Client(transport=httpx.MockTransport(endpoint)))
    originals = [Message('旧图'+str(i), kind='emoji', image_data=b'\x89PNG\r\n\x1a\n'+bytes([i]),
                         message_id=str(i)) for i in range(10)]
    question = Message('上面那张呢', reply_to_id='0')
    model.reply([*originals, question])
    parts = [p for t in payloads[0]['messages'] if isinstance(t['content'], list) for p in t['content']]
    images = [p for p in parts if p['type']=='image_url']
    assert len(images) == 3  # two latest frames plus explicitly referenced old image
    assert all(p['image_url']['detail']=='low' for p in images)
    assert all(m.image_data for m in originals)
    assert '旧图0' in json.dumps(payloads[0], ensure_ascii=False)
    model.reply([Message('看看这个'), replace(originals[-1], kind='image')])
    images = [p for t in payloads[1]['messages'] if isinstance(t['content'], list) for p in t['content'] if p['type']=='image_url']
    assert images[0]['image_url']['detail']=='auto'


@pytest.mark.parametrize('exc,code', [(httpx.ReadTimeout,'read_timeout'),(httpx.ConnectTimeout,'connect_timeout'),
                                    (httpx.WriteTimeout,'write_timeout'),(httpx.ConnectError,'connection_error')])
def test_model_errors_retain_safe_actionable_cause(tmp_path, exc, code):
    def endpoint(req): raise exc('SECRET-key PRIVATE chat', request=req)
    model = ChatModel(Settings(tmp_path, model_base_url='http://localhost'),
                      httpx.Client(transport=httpx.MockTransport(endpoint)))
    with pytest.raises(ModelError) as caught: model.reply([Message('private text')])
    assert caught.value.code == code
    snapshot = model.health_snapshot()
    assert snapshot['last_error']['code'] == code
    assert 'SECRET' not in json.dumps(snapshot) and 'private text' not in json.dumps(snapshot)


def test_model_health_clears_current_error_on_recovery(tmp_path):
    replies = iter([httpx.Response(503), httpx.Response(200,json={'choices':[{'message':{'content':'回来啦'}}]})])
    model = ChatModel(Settings(tmp_path,model_base_url='http://localhost'),
                      httpx.Client(transport=httpx.MockTransport(lambda _:next(replies))))
    with pytest.raises(ModelError): model.reply([Message('你好')])
    assert model.health_snapshot()['last_error']['code']=='http_503'
    assert model.reply([Message('你好')])=='回来啦'
    assert model.health_snapshot()['last_error'] is None


def test_media_pressure_compacts_early_without_erasing_raw_on_failure(tmp_path):
    from xiaomao.memory import ConversationMemory
    memory=ConversationMemory(tmp_path,'room')
    originals=[Message(str(i),kind='emoji',image_data=b'\x89PNG\r\n\x1a\nframe',message_id=str(i)) for i in range(12)]
    for m in originals: memory.remember(m)
    plan=memory.prepare_compaction()
    assert plan is not None and plan.older==tuple(originals[:4])
    assert not memory.commit_compaction(plan,None)
    assert memory.messages==originals
    assert memory.commit_compaction(plan,'曾发过四张表情，具体用意不确定')
    assert memory.messages==originals[4:]
    assert memory.commit_compaction(memory.prepare_compaction(),'曾发过八张表情，具体用意不确定')
    assert memory.messages==originals[-4:]


def test_model_whitespace_keepalive_has_total_deadline(tmp_path,monkeypatch):
    import xiaomao.model as module
    clock=[0.]
    monkeypatch.setattr(module,'monotonic',lambda:clock[0])
    class Endless(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                clock[0]+=10
                yield b'\n'
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),
                    httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=Endless()))))
    with pytest.raises(ModelError) as caught:model.reply([Message('你好')])
    assert caught.value.code=='request_deadline'
    assert clock[0]<=60


def test_image_provider_failure_falls_back_honestly_and_backs_off(tmp_path):
    payloads=[]
    def endpoint(req):
        data=json.loads(req.content);payloads.append(data)
        images=any(isinstance(t['content'],list) and any(p['type']=='image_url' for p in t['content']) for t in data['messages'])
        if images:return httpx.Response(500)
        return httpx.Response(200,json={'choices':[{'message':{'content':'这张暂时没看清，文字我能接着聊'}}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    picture=Message('表情',kind='emoji',image_data=b'\x89PNG\r\n\x1a\nframe')
    assert '没看清' in model.reply([picture])
    assert '没看清' in model.reply([picture])
    assert len(payloads)==3
    assert '视觉服务暂不可用' in json.dumps(payloads[-1],ensure_ascii=False)
    assert picture.image_data
    assert model.health_snapshot()['vision']['code']=='http_500'


def test_bad_image_request_does_not_hide_auth_or_parameter_errors(tmp_path):
    attempts=[]
    def endpoint(req):attempts.append(req);return httpx.Response(400)
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(endpoint)))
    with pytest.raises(ModelError):model.reply([Message('图片',kind='image',image_data=b'\x89PNG\r\n\x1a\nframe')])
    assert len(attempts)==1
