import json
import httpx
import pytest
from xiaomao.config import Settings
from xiaomao.domain import Message
from xiaomao.model import ChatModel
from xiaomao.store import StateStore
from xiaomao.stickers import StickerLibrary
from test_stickers import library


def sent(store,scope,at,sticker=False):
    task=store.create(scope+str(at),at)
    store.mark_sending(task,at,group=scope,reply='hi',sticker_md5='a'*32 if sticker else '',
                       sticker_id='s_0' if sticker else '')


def test_frequency_controls_actual_per_conversation_cooldown(tmp_path):
    store=StateStore(tmp_path)
    try:
        assert not store.sticker_ready('empty',100,'requested')
        sent(store,'a',100,True)
        assert not store.sticker_ready('a',119,'expressive')
        assert store.sticker_ready('a',121,'expressive')
        assert not store.sticker_ready('a',121,'natural')
        sent(store,'a',122);sent(store,'a',123)
        assert store.sticker_ready('a',161,'natural')
        assert not store.sticker_ready('a',221,'occasional')
        sent(store,'a',222)
        assert store.sticker_ready('a',223,'occasional')
        assert store.sticker_ready('b',110,'expressive')
        with pytest.raises(ValueError):store.sticker_ready('a',223,'unknown')
    finally:store.close()


@pytest.mark.parametrize('frequency',['requested','occasional','natural','expressive'])
def test_actual_model_request_obeys_effective_sticker_frequency(tmp_path,frequency):
    library(tmp_path);requests=[]
    def endpoint(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"send":true,"text":"可以啊","sticker":""}'},'finish_reason':'stop'}]})
    cfg=Settings(tmp_path,model_base_url='http://localhost',sticker_frequency=frequency)
    model=ChatModel(cfg,httpx.Client(transport=httpx.MockTransport(endpoint)))
    model.reply_plan([Message('搞定了')],choices=StickerLibrary(tmp_path).items,optional=True)
    systems='\n'.join(m['content'] for m in requests[0]['messages'] if m['role']=='system')
    labels={'requested':'仅按需','occasional':'偶尔','natural':'自然','expressive':'爱用表情'}
    assert '本会话表情偏好：'+labels[frequency] in systems
    if frequency=='expressive':
        assert '普通聊天通常只选文字' not in systems
        assert '不必另外解释表情画面' in systems
    assert '严肃求助' in systems
