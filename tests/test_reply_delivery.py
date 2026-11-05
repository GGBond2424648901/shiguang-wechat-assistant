import json
from dataclasses import replace
from types import SimpleNamespace
import httpx
import pytest
from test_engine import system


def completion(tmp_path,raw,finish='stop'):
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':raw}}]})
    return ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(handle))),requests


def test_complete_plain_reply_is_not_discarded_when_optional_sticker_schema_is_missing(tmp_path):
    from xiaomao.domain import Message
    text='可以连续发两三张，刚才只是默认挑了一张。'
    model,requests=completion(tmp_path,text)
    plan=model.reply_plan([Message('你好像一次只能发送一个，是为什么？')],choices=[],optional=True)
    assert plan.text==text and not plan.sticker and len(requests)==1


def test_large_pi_request_quotes_the_requested_precision_without_inventing_a_calculation(tmp_path):
    from xiaomao.domain import Message
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'这十亿位快一GB了，我慢慢敲','sticker':''}))
    answer=model.reply_plan([Message('@小玲\u2005给我算出圆周率100000000位')],choices=[]).text
    assert '100000000 位' in answer and '3.141592653589793' in answer
    assert '十亿' not in answer and 'GB' not in answer and '慢慢敲' not in answer


def test_oversized_direct_pi_request_does_not_depend_on_model_finishing_a_long_number(tmp_path):
    from xiaomao.domain import Message
    model,requests=completion(tmp_path,'3.14159',finish='length')
    answer=model.reply_plan([Message('@小玲 给我算出圆周率100000000位')],choices=[]).text
    assert '100000000 位' in answer and not requests


@pytest.mark.parametrize('question',['怎么用代码算出圆周率100000000位？','他说“给我算出圆周率100000000位”是什么意思？'])
def test_questions_about_an_algorithm_or_quotation_keep_the_model_answer(tmp_path,question):
    from xiaomao.domain import Message
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'这得分开说','sticker':''}))
    assert model.reply_plan([Message(question)],choices=[]).text=='这得分开说'


def test_fenced_decision_is_parsed_as_data_not_sent_as_code(tmp_path):
    from xiaomao.domain import Message
    model,_=completion(tmp_path,'```json\n{"send":true,"text":"可以发两张。","sticker":""}\n```')
    assert model.reply_plan([Message('可以发两个吗？')],choices=[]).text=='可以发两张。'


def test_optional_media_decline_drops_leftover_draft_but_mentions_still_need_an_answer(tmp_path):
    from xiaomao.domain import Message
    from xiaomao.model import ModelError
    model,requests=completion(tmp_path,json.dumps({'send':False,'text':'写过但决定不发','sticker':'unused'}))
    assert model.reply_plan([Message('嗯嗯')],choices=[],optional=True) is None and len(requests)==1
    with pytest.raises(ModelError):
        model.reply_plan([Message('@小玲 在吗？')],choices=[],optional=False)


@pytest.mark.parametrize('raw',['[NO_REPLY]',json.dumps({'send':True,'text':'【NO_REPLY】','sticker':''})])
def test_upstream_silence_marker_is_a_decision_only_in_optional_chat(tmp_path,raw):
    from xiaomao.domain import Message
    from xiaomao.model import ModelError
    model,_=completion(tmp_path,raw)
    assert model.reply_plan([Message('嗯')],choices=[],optional=True) is None
    with pytest.raises(ModelError) as failure:
        model.reply_plan([Message('@小玲 在吗？')],choices=[])
    assert failure.value.retryable


def test_explaining_a_marker_remains_visible_text(tmp_path):
    from xiaomao.domain import Message
    expected='[NO_REPLY] 是表示保持安静的标记。'
    model,_=completion(tmp_path,json.dumps({'send':True,'text':expected,'sticker':''}))
    assert model.reply_plan([Message('解释这个标记')],choices=[]).text==expected


def test_optional_group_picture_needs_a_decision_and_has_one_kind_of_reaction(tmp_path):
    from test_stickers import library
    from xiaomao.domain import Message
    from xiaomao.stickers import StickerLibrary
    library(tmp_path);choices=StickerLibrary(tmp_path).items
    image=Message('[表情静态帧]',kind='emoji',message_id='pic',image_data=b'\x89PNG\r\n\x1a\nfixture')
    model,_=completion(tmp_path,'这只猫像在等你表态。')
    assert model.reply_plan([image],choices=choices,optional=True) is None
    from xiaomao.model import _message_ref
    previous=Message('我偷吃了宵夜',message_id='snack',speaker_id='p_'+'a'*16)
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'宵夜都吃光啦？','sticker':'s_0','join_to':_message_ref('snack')}))
    plan=model.reply_plan([previous,image],choices=choices,optional=True)
    assert plan.text=='宵夜都吃光啦？' and not plan.sticker
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'','sticker':'s_0','join_to':'image_request'}))
    assert model.reply_plan([image],choices=choices,optional=True).sticker=='s_0'
    for reference in ('','not-a-real-message'):
        model,_=completion(tmp_path,json.dumps({'send':True,'text':'猫好像在等你','sticker':'','join_to':reference}))
        assert model.reply_plan([previous,image],choices=choices,optional=True) is None


def test_identity_question_does_not_replay_old_pixels_and_visual_followup_still_sees_them(tmp_path):
    from xiaomao.domain import Message
    model,payloads=completion(tmp_path,'我是小玲。')
    pic=Message('[图片]',kind='image',image_data=b'\x89PNG\r\n\x1a\nfixture',message_id='cat')
    for question,expect_pixels in [('你谁？',False),('你是谁呀',False),('@小玲\u2005你谁？',False),('你是谁？图上的字呢？',True),('上面拿着什么？',True)]:
        model.reply([pic,Message(question)])
        assert ('image_url' in json.dumps(payloads[-1]['messages']))==expect_pixels


@pytest.mark.parametrize('member,delay,paired',[('a',1,True),('b',1,False),('a',90,False)])
def test_just_sent_visual_question_and_its_picture_share_the_target_turn(tmp_path,member,delay,paired):
    from xiaomao.domain import Message
    model,payloads=completion(tmp_path,'{"send":false,"text":"","sticker":""}')
    question=Message('帮我看一下，图上写着什么？',speaker_id='p_'+'a'*16,message_id='q',created_at=100)
    pic=Message('[图片]',kind='image',speaker_id='p_'+member*16,message_id='pic',created_at=100+delay,
                image_data=b'\x89PNG\r\n\x1a\nfixture')
    model.reply_plan([question,pic],choices=[],optional=True)
    body=json.dumps(payloads[-1]['messages'][-1]['content'],ensure_ascii=False)
    assert (question.text in body)==paired and 'image_url' in body


def test_proactive_draft_does_not_publish_silence_or_a_nested_decision(tmp_path):
    from xiaomao.companion import Candidate
    from xiaomao.model import ModelError
    candidate=Candidate('test','idle',100,220,1,'2026-11-05')
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'[NO_REPLY]'}))
    assert model.proactive([],candidate) is None
    model,_=completion(tmp_path,json.dumps({'send':True,'text':'{"send":false,"text":""}'}))
    with pytest.raises(ModelError):model.proactive([],candidate)


@pytest.mark.parametrize('raw,finish',[
    ('{"send":true,"text":"截断','stop'),
    ('正常但截断','length'),
    ('send: false','stop'),
    ('{"send":false,"text":"","sticker":""}','length'),
    ('[已发送表情：菲比，微笑]','stop'),
    ('无需回复','stop'),
    ('```python\nprint(1)\n```','stop'),
    ('回复：send=false, text="", sticker=""','stop'),
    ('<think>内部分析</think> 可以的。','stop'),
    ('<sticker>s_0</sticker>','stop'),
    ('给你啦 [表情:s_0]','stop'),
])
def test_plain_reply_fallback_never_sends_truncated_data_or_internal_decisions(tmp_path,raw,finish):
    from xiaomao.domain import Message
    from xiaomao.model import ModelError
    model,_=completion(tmp_path,raw,finish)
    with pytest.raises(ModelError):model.reply_plan([Message('你好')],choices=[],optional=True)


def test_sticker_capable_history_uses_same_schema_as_current_text_decision(tmp_path):
    from xiaomao.domain import Message
    model,requests=completion(tmp_path,'{"send":true,"text":"能发两张。","sticker":""}')
    model.reply_plan([Message('在呢。',direction='out'),Message('刚发了一张。',direction='out',speaker_id='self',message_id='out1'),Message('为什么只有一个？')],choices=[],optional=True)
    history=[json.loads(m['content']) for m in requests[0]['messages'] if m['role']=='assistant']
    assert history==[{'send':True,'text':'在呢。','sticker':''},{'send':True,'text':'刚发了一张。','sticker':''}]


def test_invalid_reply_outcome_remains_visible_after_next_empty_poll(system):
    from xiaomao.domain import ReplyTask,Message
    from xiaomao.database import MessageBatch
    from xiaomao.model import ModelError
    engine,screen,model,clock,store=system;engine.enable()
    task=ReplyTask(store.create('failed',clock()),clock(),clock(),'friend',[Message('小玲为什么没回复？')])
    engine.tasks.append(task)
    def fail(*a,**kw):raise ModelError('格式异常',retryable=True,code='invalid_response')
    model.reply=fail
    for _ in range(3):engine._reply_and_send(task);clock.advance(2)
    assert not screen.events and not engine.tasks
    engine.message_source=SimpleNamespace(poll=lambda:MessageBatch([],[]))
    engine._observe_database(engine.epoch)
    data=engine.status()
    assert data['pending_count']==0 and data['last_result']['reason']=='model_format_failed'
    assert data['last_result']['state']=='skipped' and data['last_result']['summary'].startswith('未发送')
    assert data['mode']=='auto'


def test_idle_panel_shows_failed_outcome_instead_of_empty_success(tmp_path):
    from xiaomao.theme import recent_result
    data={'pending_count':0,'last_result':{'state':'skipped','reason':'model_format_failed','summary':'未发送：模型回复格式连续异常'}}
    title,reason=recent_result(data,'')
    assert title=='上一条消息未发送' and '格式' in reason
    data.update(mode='paused',last_result={'state':'unknown','reason':'uncertain_send','summary':'发送结果未知，未重发'})
    title,reason=recent_result(data,'')
    assert title=='上一条发送结果待确认' and '继续监听' not in reason and '暂停' in reason


@pytest.mark.parametrize('text,count',[
    ('发送两个GIF表情包',2),('给我发送两张菲比动图',2),('请发给我三张表情包',3),('来这两个表情包',2),
])
def test_send_phrase_quantity_is_not_silently_reduced_to_one(text,count):
    from xiaomao.stickers import sticker_request
    from xiaomao.domain import Message
    assert sticker_request([Message(text)]).count==count
