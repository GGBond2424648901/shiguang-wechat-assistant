from dataclasses import replace
import json

import httpx
import pytest

from test_engine import system
from test_sticker_engine import setup_sticker
from test_stickers import library
from xiaomao.domain import Message


@pytest.mark.parametrize('text,count', [('一个太少了',2),('一张不够啦',2),('再来两张吧',2),('再发一个',1)])
def test_more_stickers_followup_keeps_same_members_recent_character_but_new_quantity(text,count):
    from xiaomao.stickers import turn_sticker_request
    member='p_'+'a'*16
    previous=Message('发三个鲸鱼娘GIF',speaker_id=member,message_id='old',created_at=100)
    current=Message(text,speaker_id=member,message_id='new',created_at=120)
    request=turn_sticker_request([current],history=[previous])
    assert request.explicit and request.character=='鲸鱼娘' and request.animated
    assert request.count==count and request.source_id=='new'


@pytest.mark.parametrize('kind',['other','expired','cancelled','topic','quoted','unknown','no_time'])
def test_more_stickers_followup_does_not_revive_unrelated_or_cancelled_tasks(kind):
    from xiaomao.stickers import turn_sticker_request
    member='p_'+'a'*16
    history=[Message('发个鲸鱼表情',speaker_id=member,message_id='old',created_at=100)]
    current=Message('一个太少了',speaker_id=member,message_id='new',created_at=120)
    if kind=='other':current=replace(current,speaker_id='p_'+'b'*16)
    if kind=='expired':current=replace(current,created_at=401)
    if kind=='cancelled':history.append(Message('不用发了',speaker_id=member,created_at=110,message_id='cancel'))
    if kind=='topic':history.append(Message('午饭吃一个包子',speaker_id=member,created_at=110,message_id='topic'))
    if kind=='quoted':current=replace(current,quoted=True)
    if kind=='unknown':current=replace(current,speaker_id='')
    if kind=='no_time':current=replace(current,created_at=0)
    assert not turn_sticker_request([current],history=history).explicit


def test_more_stickers_followup_checks_intervening_topic_inside_current_batch():
    from xiaomao.stickers import turn_sticker_request
    a,b='p_'+'a'*16,'p_'+'b'*16
    prior=Message('发个鲸鱼表情',speaker_id=a,message_id='old',created_at=100)
    lunch=Message('午饭吃了一个包子',speaker_id=a,message_id='lunch',created_at=110)
    latest=Message('一个太少了',speaker_id=a,message_id='new',created_at=120)
    assert not turn_sticker_request([lunch,latest],history=[prior]).explicit
    assert not turn_sticker_request([latest],history=[prior,lunch]).explicit
    assert turn_sticker_request([replace(lunch,speaker_id=b),latest],history=[prior]).count==2


@pytest.mark.parametrize('text,character,animated,count',[
    ('换个鲸鱼的表情包','鲸鱼娘',False,1),
    ('发两个菲比表情包','菲比',False,2),
    ('@小玲 来两张鲸鱼GIF吧','鲸鱼娘',True,2),
    ('来张八条动图','呆猫八条',True,1),
    ('发100个表情包','',False,3),
])
def test_explicit_request_filters_character_animation_and_bounded_quantity(tmp_path,text,character,animated,count):
    from xiaomao.stickers import StickerLibrary,sticker_request
    library(tmp_path)
    messages=[Message(text)]
    request=sticker_request(messages)
    assert request.explicit and request.character==character and request.animated==animated and request.count==count
    choices=StickerLibrary(tmp_path).candidates(messages)
    assert all((not character or s.character==character) and (not animated or s.animated) for s in choices)


def test_gif_correction_carries_only_same_members_requested_character():
    from xiaomao.stickers import sticker_request
    a,b='p_'+'a'*16,'p_'+'b'*16
    history=[Message('换个鲸鱼的表情包',speaker_id=a),
             Message('来个菲比表情',speaker_id=b),Message('你需要发个GIF吧',speaker_id=a)]
    request=sticker_request(history)
    assert request.character=='鲸鱼娘' and request.animated and request.explicit
    assert sticker_request([replace(m,speaker_id='') for m in history]).character==''
    assert not sticker_request([Message('别发表情包了')]).explicit
    assert not sticker_request([Message('他说“发两个表情包”，这句话是什么意思？')]).explicit
    correction=sticker_request([history[0],Message('GIF吧',speaker_id=a)])
    assert correction.explicit and correction.animated and correction.character=='鲸鱼娘'
    assert not sticker_request([history[0],Message('GIF吧',speaker_id=b)]).explicit
    again=sticker_request([history[0],Message('GIF吧',speaker_id=a),Message('再来个GIF吧',speaker_id=a)])
    assert again.character=='鲸鱼娘' and again.animated
    assert not sticker_request([Message('别再发鲸鱼表情了',speaker_id=a),Message('GIF吧',speaker_id=a)]).explicit


@pytest.mark.parametrize('text',[
    '[已发送表情：一头蓝色鲸鱼，甩着尾巴]',
    '给你啦 [已发送表情：菲比，微笑]',
    '"[已发送表情：鲸鱼，挥手]"',
])
def test_internal_media_receipt_never_passes_text_send_boundary(text):
    from xiaomao.policy import clean_reply,ReplyFormatError
    with pytest.raises(ReplyFormatError):clean_reply(text)


def test_sticker_history_is_data_instead_of_assistant_reply_example(tmp_path):
    from xiaomao.model import ChatModel
    from xiaomao.config import Settings
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"send":true,"text":"在呢","sticker":""}'},'finish_reason':'stop'}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(handle)))
    model.reply_plan([Message('[已发送表情：菲比，微笑]',direction='out',kind='emoji'),Message('在吗')],choices=[])
    turns=requests[-1]['messages']
    assert any('微笑' in str(m['content']) for m in turns)
    assert not any(m['role']=='assistant' and '已发送表情' in str(m['content']) for m in turns)


def test_model_can_select_two_distinct_allowlisted_stickers_but_no_unsolicited_batch(tmp_path):
    from xiaomao.model import ChatModel,ModelError
    from xiaomao.config import Settings
    from xiaomao.stickers import StickerLibrary
    library(tmp_path);choices=StickerLibrary(tmp_path).items
    answer={'send':True,'text':'','sticker':['s_0','s_2']}
    def handle(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)},'finish_reason':'stop'}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(handle)))
    plan=model.reply_plan([Message('发两个菲比表情包')],choices=choices)
    assert plan.sticker=='s_0' and plan.extra_stickers==('s_2',)
    with pytest.raises(ModelError):model.reply_plan([Message('好开心')],choices=choices)
    answer['sticker']=['s_0','s_0']
    with pytest.raises(ModelError):model.reply_plan([Message('发两个表情包')],choices=choices)
    answer['sticker']='s_0'
    with pytest.raises(ModelError):model.reply_plan([Message('发两个表情包')],choices=choices)
    answer.update(sticker=['s_0','s_2'],text='只能发文字')
    plan=model.reply_plan([Message('发两个菲比表情包')],choices=choices)
    assert not plan.text and plan.extra_stickers==('s_2',)


def test_explicit_request_can_send_during_spontaneous_sticker_cooldown(system):
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    task.messages=[replace(task.messages[0],text='小玲 发个鲸鱼表情包')]
    old=store.create('old',clock()-40)
    store.mark_sending(old,clock()-40,group=engine.settings.group,reply='[表情]',sticker_md5='a'*32,sticker_id='s_0')
    assert not store.sticker_ready(engine.settings.group,clock())
    engine._reply_and_send(task)
    assert screen.events==['focus','sticker','send'] and store.get(task.id)['state']=='verified'
    assert task.sticker.item.character=='鲸鱼娘'


def test_two_stickers_are_separate_receipted_sends_with_original_recipient_and_rate_limits(system):
    from xiaomao.stickers import ReplyPlan
    from xiaomao.database import MessageBatch
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    task.messages=[replace(task.messages[0],text='小玲 发两个菲比表情包')]
    model.reply_plan=lambda *a,**kw:ReplyPlan(sticker='s_0',extra_stickers=('s_2',))
    sent=[];staged=[]
    original_type=screen.type_sticker;original_send=screen.click_send
    def type_sticker(prepared):staged.append(prepared);original_type(prepared)
    def send():
        original_send()
        sent.append(Message('[表情]',direction='out',kind='emoji',message_id=f'out{len(sent)+1}',created_at=clock(),
                    media_raw='<msg><emoji md5="'+staged[-1].md5+'"/></msg>'))
    screen.type_sticker=type_sticker;screen.click_send=send
    engine.message_source=type('Feed',(),{'poll':lambda self:MessageBatch(sent,sent)})()
    engine._reply_and_send(task)
    assert store.get(task.id)['state']=='verified' and len(engine.tasks)==1
    second=engine.tasks[0]
    engine._reply_and_send(second)
    assert len(sent)==1  # ordinary send interval still applies
    clock.advance(engine.settings.min_interval+1)
    engine._reply_and_send(second)
    assert len(sent)==2 and not engine.tasks and store.get(second.id)['state']=='verified'
    assert [m.reply_to_id for m in engine.context if m.message_id in ('out1','out2')]==['in1','in1']
    assert store.db.execute('select count(*) from attempts').fetchone()[0]==2


def test_unconfirmed_first_sticker_never_queues_second(system):
    from xiaomao.stickers import ReplyPlan
    from xiaomao.database import MessageBatch
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    task.messages=[replace(task.messages[0],text='小玲 发两个表情包')]
    model.reply_plan=lambda *a,**kw:ReplyPlan(sticker='s_0',extra_stickers=('s_2',))
    engine.message_source=type('Feed',(),{'poll':lambda self:MessageBatch([],[])})()
    engine._reply_and_send(task)
    assert task.state=='awaiting_receipt' and engine.tasks==[task] and screen.events.count('send')==1
    engine.pause()
    assert not engine.tasks and store.get(task.id)['state']=='unknown'


def test_pause_then_enable_between_receipt_and_followup_cancels_old_batch(system):
    from xiaomao.stickers import ReplyPlan
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    task.messages=[replace(task.messages[0],text='小玲 发两个菲比表情包')]
    model.reply_plan=lambda *a,**kw:ReplyPlan(sticker='s_0',extra_stickers=('s_2',))
    retire=engine._retire
    def interrupted(current,state,reason=''):
        retire(current,state,reason)
        if state=='verified':engine.pause();engine.enable()
    engine._retire=interrupted
    engine._reply_and_send(task)
    assert not engine.tasks and screen.events.count('send')==1


def test_distinct_ids_with_identical_assets_are_not_two_different_stickers(tmp_path):
    from xiaomao.stickers import StickerLibrary
    entries=library(tmp_path)
    entries[2].update(path=entries[0]['path'],sha256=entries[0]['sha256'])
    (tmp_path/'assets/sticker_library/catalog.json').write_text(json.dumps({'items':entries}),encoding='utf-8')
    choices=StickerLibrary(tmp_path).candidates([Message('发两个菲比表情包')])
    assert len(choices)==1


def test_selfchat_acceptance_does_not_mistake_only_first_receipt_for_batch_success():
    from scripts.verify_sticker_selfchat import acceptance_passed
    evidence={'task_states':['verified','unknown'],'attempts':2,'files':[
        {'sent_sha256':'a','original_sha256':'a','frames':2},
        {'sent_sha256':'b','original_sha256':'b','frames':3}]}
    assert not acceptance_passed(evidence,2,0)
    evidence['task_states']=['verified','verified']
    assert acceptance_passed(evidence,2,0)
    assert not acceptance_passed(evidence,2,1)
