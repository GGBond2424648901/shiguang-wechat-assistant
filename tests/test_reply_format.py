import json
import httpx
import pytest
from test_engine import system
from xiaomao.domain import Message
from xiaomao.model import ChatModel


@pytest.mark.parametrize('broken',[False,True])
def test_model_output_guard_reaches_sender_and_keeps_worker_running(system,broken):
    e,screen,_,clock,store=system
    body='这回是正常中文呀 (・ω・)'
    output='{"speaker":"self","body":"残缺' if broken else json.dumps({
        'speaker':'self','message':'m_'+'a'*16,'reply_to_speaker':'p_'+'b'*16,'body':body})
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,
            json={'choices':[{'message':{'content':output}}]}))) as client:
        e.model=ChatModel(e.settings,client=client)
        e.mode='auto';e.cancelled.clear()
        e._record([Message('小猫你好',message_id='new',created_at=clock())]);task=e.tasks[0]
        for _ in range(3):
            if not e.tasks:break
            e._reply_and_send(task);clock.advance(20)
        assert e.mode=='auto' and not e.tasks
        if broken:
            assert not screen.events and screen.prepared==0
            assert store.get(task.id)['state']=='skipped'
        else:
            assert screen.events==['focus','type','send']
            assert screen.frame.messages[-1].text==body
            assert store.get(task.id)['state']=='verified'
