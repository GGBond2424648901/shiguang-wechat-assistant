from dataclasses import replace
import json

import pytest

from test_engine import system
from test_reply_delivery import completion
from test_stickers import library
from xiaomao.database import MessageBatch
from xiaomao.domain import Message, ReplyTask
from xiaomao.stickers import ReplyPlan, StickerLibrary


ALICE = 'p_' + 'a' * 16
BOB = 'p_' + 'b' * 16
TEXT = '借到镜头啦！ 我喜欢月亮。再拍一张吧？最后慢慢看。'
PARTS = ['借到镜头啦！ ', '我喜欢月亮。', '再拍一张吧？最后慢慢看。']


class Feed:
    def __init__(self, screen, clock):
        self.history = []
        self.pending = []
        self.sent = []
        self.ack = True
        self.sticker = None
        self.screen = screen
        self.clock = clock
        original_send = screen.click_send

        def click_send():
            text = screen.frame.draft
            original_send()
            self.sent.append(text)
            if self.ack:
                row = Message(text, direction='out', speaker_id='self',
                              message_id=f'out-{len(self.sent)}', created_at=clock())
                if self.sticker:
                    row = replace(row, kind='emoji', text='[表情]', media_raw=f'<msg><emoji md5="{self.sticker.md5}"/></msg>')
                self.pending.append(row)
                self.history.append(row)

        screen.click_send = click_send

    def poll(self):
        new, self.pending = self.pending, []
        return MessageBatch(list(self.history), new)

    def stage_sticker(self, sticker):
        self.sticker = sticker
        self.screen.frame = replace(self.screen.frame, draft='[表情]', draft_empty=False)


def test_exact_whale_order_among_unavailable_images_needs_no_model_and_sends_once(delivery,tmp_path):
    from xiaomao.stickers import turn_sticker_request
    engine,feed,calls,clock,store=delivery
    library(tmp_path)
    engine.stickers=StickerLibrary(tmp_path)
    engine.stickers.items=[replace(s,character='鲸鱼娘') for s in engine.stickers.items]
    engine.desktop.type_sticker=feed.stage_sticker
    engine.desktop.verify_sticker_input=lambda item:feed.sticker==item
    feed.resolve_media=lambda _:pytest.fail('an explicit stock request does not need the surrounding pictures')
    engine.model.reply_plan=lambda *a,**kw:pytest.fail('a filename from the allowlist is sufficient')
    engine.settings.reply_mode=engine.policy.mode='natural'
    incoming=[Message('搞个大肥鱼表情包',speaker_id=ALICE,message_id='order',created_at=clock())]
    incoming.extend(Message('[表情静态帧]',kind='emoji',speaker_id=ALICE,message_id=f'img{i}',created_at=clock()) for i in range(3))
    request=turn_sticker_request(incoming)
    assert request.explicit and request.character=='鲸鱼娘' and request.source_id=='order'
    engine._record(incoming)
    engine._reply_and_send(engine.tasks[0])
    assert store.counts()=={'verified':1} and len(feed.sent)==1 and not engine.tasks


@pytest.mark.parametrize('text',['刚才搞个大肥鱼表情包就卡住了','“搞个大肥鱼表情包”是什么意思','帮我原创画一个大肥鱼表情包'])
def test_discussing_a_stock_order_is_not_a_request(text):
    from xiaomao.stickers import turn_sticker_request
    req=turn_sticker_request([Message(text,speaker_id=ALICE,message_id='m')])
    assert not req.explicit


def test_wechat_delivery_uses_last_verified_opening_and_keeps_receipts_in_sync(delivery):
    engine,feed,_,clock,store=delivery
    engine.desktop.type_sticker=feed.stage_sticker
    engine.desktop.verify_sticker_input=lambda item:True
    engine.model,requests=completion(engine.settings.root,json.dumps({'send':True,'text':'行吧，螺丝松了。行吧，再提一下。','sticker':''}))
    question(engine)
    for _ in range(2):engine.observe_once();clock.advance(4)
    assert feed.sent==['行吧，螺丝松了','再提一下']
    assert [m.text for m in engine.memory.messages if m.reply_to_id=='q1']==feed.sent
    assert store.counts()=={'verified':2} and len(requests)==1


def test_unsettled_frame_after_focus_is_reread_without_clicking_twice(delivery):
    engine,feed,calls,clock,store=delivery
    screen=engine.desktop
    original=screen.click_input
    def focus(generation):
        original(generation)
        screen.frame=replace(screen.frame,draft='',draft_empty=False)
    screen.click_input=focus
    checks=[]
    def corroborate(image,vision):
        checks.append(1)
        if len(checks)==2:
            screen.frame=replace(screen.frame,draft_empty=True)
            return screen.frame
        return None
    screen.confirm_empty_editor=corroborate
    question(engine)
    engine.observe_once()
    assert checks==[1,1] and screen.events.count('focus')==1 and len(feed.sent)==1


def test_suppressed_face_does_not_drop_the_rest_of_an_addressed_reply(delivery):
    engine,feed,_,clock,store=delivery
    engine.desktop.type_sticker=feed.stage_sticker
    engine.desktop.verify_sticker_input=lambda item:True
    engine._remember(Message('上次笑了 (｡･ω･｡)',direction='out',speaker_id='self',message_id='earlier'))
    engine.model,_=completion(engine.settings.root,json.dumps({'send':True,'text':'(｡･ω･｡)。结果是四十二。','sticker':''}))
    question(engine)
    engine.observe_once()
    assert feed.sent==['结果是四十二'] and store.counts()=={'verified':1}


@pytest.fixture
def delivery(system, monkeypatch):
    engine, screen, model, clock, store = system
    engine.settings.reply_mode = engine.policy.mode = 'all'
    engine.settings.min_interval = 4
    engine.enable()
    engine.message_source = feed = Feed(screen, clock)
    engine.source_rebaseline = False
    # No real-time waits are needed to attest rows in the synthetic feed.
    monkeypatch.setattr(engine.cancelled, 'wait', lambda timeout: engine.cancelled.is_set())
    calls = []
    model.reply = lambda messages: calls.append(list(messages)) or TEXT
    return engine, feed, calls, clock, store


def question(engine, text='月亮', mid='q1', member=ALICE):
    engine._record([Message(text, message_id=mid, speaker_id=member, created_at=engine.clock())])


def test_each_verified_text_has_own_receipt_rate_slot_and_reply_link(delivery):
    from xiaomao.memory import ConversationMemory
    engine, feed, calls, clock, store = delivery
    question(engine)
    question(engine, '另一位成员', 'q2', BOB)
    for index, part in enumerate(PARTS):
        engine.observe_once()
        assert feed.sent == PARTS[:index+1]
        assert store.counts()['verified'] == index+1
        assert len(calls) == 1
        engine.observe_once()
        assert feed.sent == PARTS[:index+1]
        clock.advance(4)
    memory = ConversationMemory(engine.settings.runtime, engine.settings.memory_scope)
    receipts = [m for m in memory.messages if m.direction == 'out']
    assert [(m.text,m.reply_to_id,m.reply_to_speaker) for m in receipts] == [(p,'q1',ALICE) for p in PARTS]
    assert ''.join(feed.sent) == TEXT
    assert store.db.execute('SELECT count(*) FROM send_receipts').fetchone()[0] == 3
    assert store.db.execute('SELECT count(DISTINCT task_id) FROM attempts').fetchone()[0] == 3
    engine.observe_once()
    assert calls[1][-1].speaker_id == BOB and calls[1][-1].message_id == 'q2'
    assert [m.text for m in calls[1] if m.reply_to_id == 'q1'] == PARTS


@pytest.mark.parametrize('reason',['pause','restart','unknown','expiry'])
def test_unsent_tail_is_never_replayed(delivery, reason):
    engine, feed, calls, clock, store = delivery
    question(engine)
    engine.observe_once()
    assert feed.sent == PARTS[:1] and len(engine.tasks) == 1
    tail = engine.tasks[0]
    if reason == 'restart':
        from xiaomao.engine import Engine
        restarted = Engine(engine.settings, store, engine.desktop, engine.vision, engine.model,
                           clock=clock, message_source=feed)
        assert not restarted.tasks and store.get(tail.id)['reason'] == 'restart'
        return
    if reason == 'unknown':
        feed.ack = False
        clock.advance(4)
        engine.observe_once()
        assert tail.state == 'awaiting_receipt' and engine.tasks == [tail]
        assert store.counts() == {'verified':1,'sending':1}
        clock.advance(31)
    elif reason == 'expiry':
        clock.advance(engine.settings.expiry_seconds + 1)
    else:
        engine.pause()
        engine.enable()
    engine.observe_once()
    assert not engine.tasks and len(calls) == 1
    assert feed.sent == PARTS[:(2 if reason == 'unknown' else 1)]
    assert [m.text for m in engine.context if m.direction == 'out'] == PARTS[:1]
    assert store.get(tail.id)['state'] == ('unknown' if reason == 'unknown' else 'skipped')
    if reason == 'unknown':
        assert engine.mode == 'paused'


@pytest.mark.parametrize('others,enabled,expected',[(2,True,PARTS[0]),(3,True,TEXT),(0,False,TEXT)])
def test_backlog_threshold_and_setting_apply_to_entire_generated_turn(delivery, others, enabled, expected):
    engine, feed, calls, clock, store = delivery
    engine.settings.split_replies = enabled
    question(engine)
    for i in range(others):
        question(engine, '待答', f'other-{i}', BOB)
    engine.observe_once()
    assert feed.sent == [expected] and len(calls) == 1


def test_screen_receipt_uses_single_bubble_when_database_ids_are_unavailable(delivery):
    engine, feed, calls, clock, store = delivery
    engine.message_source = None
    question(engine)
    engine._reply_and_send(engine.tasks[0])
    assert feed.sent == [TEXT] and store.counts() == {'verified':1}
    assert engine.status()['split_replies'] and not engine.status()['split_replies_effective']


def test_natural_only_merges_contiguous_unattempted_confirmed_member_within_window(delivery):
    engine, feed, calls, clock, store = delivery
    engine.settings.reply_mode = engine.policy.mode = 'natural'
    question(engine, '第一句', 'a1'); question(engine, '@小玲 第二句', 'a2')
    assert len(engine.tasks) == 1 and [m.message_id for m in engine.tasks[0].messages] == ['a1','a2']
    question(engine, '另一人', 'b1', BOB)
    question(engine, '再问', 'a3')
    engine.tasks[-1].model_attempts = 1
    question(engine, '模型工作已开始', 'a4')
    clock.advance(engine.settings.merge_seconds+1)
    question(engine, '超过合并时限', 'a5')
    engine._record([Message('未知', sender='重名', message_id='u1'),Message('未知', sender='重名', message_id='u2')])
    assert [t.messages[-1].message_id for t in engine.tasks] == ['a2','b1','a3','a4','a5','u1','u2']
    engine.observe_once()
    assert calls[0][-1].message_id == 'a2'


def test_deferred_source_keeps_original_deadline(delivery):
    engine, feed, calls, clock, store = delivery
    arrived = clock()
    clock.advance(engine.settings.expiry_seconds+1)
    engine._record([Message('延迟到达', message_id='old', created_at=arrived, speaker_id=ALICE)])
    engine.observe_once()
    assert not feed.sent and not engine.tasks and not calls


@pytest.mark.parametrize('media_state',['ready','changed','cooldown','missing_before_text','short_editor'])
def test_optional_gif_follows_verified_text_without_regeneration(delivery, media_state):
    engine, feed, _, clock, store = delivery
    library(engine.settings.root)
    engine.stickers = StickerLibrary(engine.settings.root)
    engine.desktop.type_sticker = feed.stage_sticker
    engine.desktop.verify_sticker_input = lambda prepared: True
    if media_state=='short_editor':engine.desktop.sticker_preview_fits=lambda prepared:False
    calls = []
    engine.model.reply_plan = lambda *a,**kw: calls.append(1) or ReplyPlan(text='你做到了！真开心。',sticker='s_0')
    question(engine, '刚拿到第一名')
    if media_state == 'missing_before_text':
        engine.stickers.items[0].path.unlink()
    engine.observe_once()
    assert feed.sent == ['你做到了！'] and len(calls) == 1
    clock.advance(4)
    engine.observe_once()
    assert feed.sent == ['你做到了！','真开心。']
    if media_state == 'changed':
        engine.tasks[0].sticker.path.write_bytes(b'changed')
    elif media_state == 'cooldown':
        manual = store.create('intervening',clock())
        store.mark_sending(manual,clock(),group=engine.settings.group,reply='[表情]',sticker_md5='a'*32,sticker_id='s_1')
        store.update(manual,'verified')
    clock.advance(4)
    engine.observe_once()
    assert feed.sent == (['你做到了！','真开心。','[表情]'] if media_state == 'ready' else ['你做到了！','真开心。'])
    assert not engine.tasks and len(calls) == 1
    linked = [m for m in engine.context if m.reply_to_id == 'q1']
    assert len(linked) == len(feed.sent) and all(m.reply_to_speaker == ALICE for m in linked)


def test_mixed_model_plan_preserves_text_only_for_ordinary_chat(tmp_path):
    library(tmp_path)
    model, requests = completion(tmp_path,json.dumps({'send':True,'text':'开心！','sticker':'s_0'}))
    choices = StickerLibrary(tmp_path).items
    ordinary = model.reply_plan([Message('拿到第一名啦')],choices=choices)
    assert ordinary == ReplyPlan(text='开心！',sticker='s_0')
    explicit = model.reply_plan([Message('给我发个GIF')],choices=choices)
    assert explicit == ReplyPlan(sticker='s_0')


@pytest.mark.parametrize('interrupt',['none','new_input','quiet','pause','unknown','expiry'])
def test_one_proactive_reservation_covers_only_its_original_turn(delivery, monkeypatch, interrupt):
    from xiaomao.companion import Candidate, CompanionPreferences, local_day
    from xiaomao.companion_runtime import CompanionRuntime
    engine, feed, calls, clock, store = delivery
    runtime = CompanionRuntime(engine.settings, store, engine.model, clock)
    engine.companion = runtime
    runtime.planner.preferences = CompanionPreferences(enabled=True, quiet_start=0, quiet_end=0)
    candidate = Candidate('candidate','requested',clock(),clock()+20,runtime.planner.state['revision'],local_day(clock()))
    assert store.reserve_proactive(candidate.id,engine.settings.memory_scope,candidate.day,clock(),2)
    task = ReplyTask(store.create('proactive',clock()),clock(),clock(),'',reply=TEXT,proactive=candidate)
    engine.tasks.append(task)
    calls_to_runtime = []
    for name in ('begin_send','finish','replied'):
        original = getattr(runtime,name)
        def counted(*args, method=original, label=name):
            calls_to_runtime.append(label)
            return method(*args)
        monkeypatch.setattr(runtime,name,counted)
    try:
        engine.observe_once()
        assert feed.sent == PARTS[:1] and store.counts() == {'verified':1,'candidate':1}
        if interrupt == 'new_input':
            engine._record([Message('人工已回复',message_id='new',speaker_id=BOB,created_at=clock())])
            assert all(t.proactive is None for t in engine.tasks)
        elif interrupt == 'quiet':
            runtime.planner.preferences = replace(runtime.planner.preferences,quiet_start=0,quiet_end=1439)
        elif interrupt == 'pause':
            engine.pause(); engine.enable()
        elif interrupt == 'unknown':
            feed.ack = False
        elif interrupt == 'expiry':
            clock.advance(21)
        if interrupt != 'new_input':
            clock.advance(4);engine.observe_once()
            clock.advance(31 if interrupt == 'unknown' else 4);engine.observe_once()
        assert feed.sent == (PARTS if interrupt == 'none' else PARTS[:2] if interrupt == 'unknown' else PARTS[:1])
        assert calls_to_runtime == ['begin_send','finish']
        assert not calls and runtime.planner.state['followup_at'] == 0
        expected='verified' if interrupt=='none' else 'unknown' if interrupt=='unknown' else 'partial'
        assert store.db.execute('SELECT state FROM proactive').fetchall()[0][0] == expected
    finally:
        runtime.close()


def test_verified_normal_bubbles_create_one_relationship_followup(delivery, monkeypatch):
    from xiaomao.companion_runtime import CompanionRuntime
    engine, feed, _, clock, store = delivery
    runtime = CompanionRuntime(engine.settings, store, engine.model, clock)
    engine.companion = runtime
    calls = []
    original = runtime.replied
    monkeypatch.setattr(runtime,'replied',lambda task_id, at: calls.append((task_id,at)) or original(task_id,at))
    try:
        question(engine)
        first = engine.tasks[0]
        for _ in range(3):
            engine.observe_once();clock.advance(4)
        assert feed.sent == PARTS and calls == [(first.id,100)]
    finally:
        runtime.close()


def test_polling_an_unconfirmed_bubble_cannot_reopen_the_editor(delivery):
    engine, feed, calls, clock, store = delivery
    feed.ack = False
    question(engine)
    engine.observe_once()
    first = engine.tasks[0]
    assert first.state == 'awaiting_receipt' and feed.sent == PARTS[:1]
    original_events = list(engine.desktop.events)
    clock.advance(4)
    engine._reply_and_send(first)
    assert engine.desktop.events == original_events and first.state == 'awaiting_receipt'
    assert engine.tasks == [first] and len(calls) == 1


def test_model_retry_completes_once_then_bubbles_reuse_valid_answer(delivery):
    from xiaomao.model import ModelError
    engine, feed, calls, clock, store = delivery
    def flaky(messages):
        calls.append(messages[-1].message_id)
        if len(calls) == 1:
            raise ModelError('生成中断',retryable=True,code='invalid_response')
        return TEXT
    engine.model.reply = flaky
    question(engine)
    engine.observe_once()
    assert not feed.sent and store.counts() == {'candidate':1}
    clock.advance(1)
    for n in range(3):
        engine.observe_once();clock.advance(4)
    assert calls == ['q1','q1'] and feed.sent == PARTS and store.counts() == {'verified':3}


def test_two_chats_with_same_source_id_have_separate_tails_and_memory_but_share_rate(delivery,monkeypatch):
    from test_engine import Screen, IdentityVision, ReplyModel
    from xiaomao.engine import Engine
    engine, first_feed, _, clock, store = delivery
    settings = engine.settings.select_group('乙群')
    screen = Screen()
    screen.frame = replace(screen.frame,group='乙群')
    feed = Feed(screen,clock)
    other = Engine(settings,store,screen,IdentityVision(),ReplyModel(),clock=clock,message_source=feed,settle_seconds=0)
    monkeypatch.setattr(other.cancelled,'wait',lambda timeout: other.cancelled.is_set())
    other.enable()
    other.source_rebaseline = False
    other.model.reply = lambda messages: '乙的答案。再补一句。'
    question(engine)
    question(other, '乙的问题', member=BOB)
    engine.observe_once()
    other.observe_once()
    assert first_feed.sent == PARTS[:1] and not feed.sent
    for current in (other,engine,other,engine):
        clock.advance(4)
        current.observe_once()
    assert first_feed.sent == PARTS and feed.sent == ['乙的答案。','再补一句。']
    for active, expected, member in ((engine,PARTS,ALICE),(other,feed.sent,BOB)):
        assert [(m.text,m.reply_to_id,m.reply_to_speaker) for m in active.memory.messages if m.direction=='out'] == [
            (part,'q1',member) for part in expected]
    assert store.counts() == {'verified':5}


@pytest.mark.parametrize('gate',['clipboard','visual','receipt'])
@pytest.mark.parametrize('answer,changed',[(TEXT,PARTS[0].rstrip()),('    if ready:\n        run()', 'if ready:\nrun()')])
def test_lost_literal_spacing_never_verifies_a_split_or_formatted_paste(delivery, gate, answer, changed):
    engine,feed,_,clock,store = delivery
    engine.model.reply = lambda messages: answer
    question(engine)
    if gate == 'clipboard':
        engine.desktop.read_input_text = lambda expected: changed
    elif gate == 'visual':
        def alter_draft():
            engine.desktop.frame = replace(engine.desktop.frame,draft=changed)
        engine.desktop.after_type = alter_draft
    else:
        original_send = engine.desktop.click_send
        def changed_receipt():
            original_send()
            feed.pending[-1] = feed.history[-1] = replace(feed.history[-1],text=changed)
        engine.desktop.click_send = changed_receipt
    engine.observe_once()
    if gate == 'receipt':
        assert len(engine.tasks) == 1 and engine.tasks[0].state == 'awaiting_receipt'
        clock.advance(31);engine.observe_once()
        assert store.counts() == {'unknown':1}
    else:
        assert not feed.sent and not engine.desktop.frame.draft_empty
    assert engine.mode == 'paused' and not engine.tasks


@pytest.mark.parametrize('already_sent',[False, True])
def test_backlog_arriving_after_generation_coalesces_only_the_unsent_text(delivery, already_sent):
    engine,feed,calls,clock,store = delivery
    question(engine)
    if not already_sent:
        engine.desktop.idle_for = lambda: 0
    engine.observe_once()
    assert len(calls) == 1 and feed.sent == (PARTS[:1] if already_sent else [])
    for i in range(3):
        question(engine,'新的问题',f'q-new-{i}',BOB)
    engine.desktop.idle_for = lambda: 60
    clock.advance(4)
    engine.observe_once()
    assert feed.sent == ([PARTS[0],''.join(PARTS[1:])] if already_sent else [TEXT])
    assert [t.messages[-1].message_id for t in engine.tasks] == [f'q-new-{i}' for i in range(3)]
    assert len(calls) == 1


def test_repeated_identical_bubbles_require_distinct_new_receipt_ids(delivery):
    engine,feed,_,clock,store = delivery
    engine.model.reply = lambda messages: '好。好。'
    question(engine)
    engine.observe_once()
    first = feed.history[0]
    feed.ack = False
    clock.advance(4)
    engine.observe_once()
    second = engine.tasks[0]
    feed.pending.append(first)
    engine.observe_once()
    assert second.state == 'awaiting_receipt' and store.counts() == {'verified':1,'sending':1}
    fresh = replace(first,message_id='out-2',created_at=clock())
    feed.history.append(fresh);feed.pending.append(fresh)
    engine.observe_once()
    assert not engine.tasks and feed.sent == ['好。','好。']
    assert [m.message_id for m in engine.memory.messages if m.reply_to_id=='q1'] == ['out-1','out-2']


@pytest.mark.parametrize('tail,expected,source',[
    (['谢谢！'],['s_1','s_3'],'m0'),
    (['改成一张静态的','谢谢！'],['still'],'m1'),
    (['只要一张','谢谢！'],['s_1'],'m1'),
    (['换成一张菲比动图','谢谢！'],['s_0'],'m1'),
    (['我还要三张门票','谢谢！'],['s_1','s_3'],'m0'),
    (['不用发了','谢谢！'],[],''),
])
def test_coalesced_media_request_uses_one_scoped_decision_through_actual_model(delivery,tail,expected,source):
    import hashlib
    from PIL import Image
    from test_reply_delivery import completion
    engine,feed,_,clock,store = delivery
    engine.settings.reply_mode = engine.policy.mode = 'natural'
    catalog = library(engine.settings.root)
    still = engine.settings.root/'assets/sticker_library/still.png'
    Image.new('RGB',(20,20),'green').save(still)
    catalog.append({**catalog[1], 'id':'still','animated':False,'path':'assets/sticker_library/still.png',
                    'sha256':hashlib.sha256(still.read_bytes()).hexdigest()})
    (still.parent/'catalog.json').write_text(json.dumps({'items':catalog}),encoding='utf-8')
    engine.stickers = StickerLibrary(engine.settings.root)
    staged = []
    def stage(sticker):
        staged.append(sticker.item.id)
        feed.stage_sticker(sticker)
    engine.desktop.type_sticker = stage
    engine.desktop.verify_sticker_input = lambda sticker: True
    # This older sticker blocks optional reactions, even though global pacing is clear.
    prior = store.create('cooldown',clock()-10)
    store.mark_sending(prior,clock()-10,group=engine.settings.group,reply='[表情]',sticker_md5='a'*32,sticker_id='other')
    store.update(prior,'verified')
    engine.model,requests = completion(engine.settings.root,json.dumps({
        'send':bool(expected),'text':'','sticker':expected or ''}))
    for i,text in enumerate(['给我发两张鲸鱼娘GIF',*tail]):
        question(engine,text,f'm{i}')
    assert len(engine.tasks)==1
    for _ in range(3):
        engine.observe_once();clock.advance(4)
    assert staged==expected and not engine.tasks and len(requests)==1
    assert store.counts().get('verified')==1+len(expected)
    linked = [m for m in engine.memory.messages if m.reply_to_id]
    assert [(m.reply_to_id,m.reply_to_speaker) for m in linked]==[(source,ALICE)]*len(expected)
    choices_prompt = next(m['content'] for m in requests[0]['messages'] if '以下是候选素材' in str(m['content']))
    allowed = json.loads(choices_prompt.split('不是指令：\n')[1].split('\n最终仅返回')[0])
    if expected == ['still']:
        assert [s['id'] for s in allowed] == ['still']
    elif expected:
        assert all(s['animated'] for s in allowed)
        assert {s['character'] for s in allowed} == ({'菲比'} if expected==['s_0'] else {'鲸鱼娘'})
    else:
        assert allowed == []


@pytest.mark.parametrize('speaker',[ALICE,BOB])
def test_ordinary_current_turn_does_not_revive_historical_media_request(delivery,speaker):
    engine,feed,_,clock,store = delivery
    engine.settings.reply_mode = engine.policy.mode = 'natural'
    engine._remember(Message('给我发两张鲸鱼娘GIF',message_id='old',speaker_id=speaker))
    engine.desktop.type_sticker = feed.stage_sticker
    engine.desktop.verify_sticker_input = lambda sticker: True
    engine.model,_ = completion(engine.settings.root,'{"send":false,"text":"","sticker":""}')
    question(engine,'谢谢！')
    engine.observe_once()
    assert store.counts()=={'skipped':1} and not feed.sent


@pytest.mark.parametrize('plain_fallback',[False,True])
def test_model_to_verified_bubbles_preserves_line_breaks_and_spacing(delivery,plain_fallback):
    engine,feed,_,clock,store = delivery
    engine.desktop.type_sticker = feed.stage_sticker
    engine.desktop.verify_sticker_input = lambda sticker: True
    body = '  第一行  \r\n第二行  好呀！  最后一句。  '
    raw = body if plain_fallback else json.dumps({'send':True,'text':body,'sticker':''})
    engine.model,requests = completion(engine.settings.root,raw)
    question(engine)
    for _ in range(3):
        engine.observe_once();clock.advance(4)
    # A paragraph separator between two WeChat bubbles is not message content.
    # Significant spaces *within* ordinary text still survive the round trip.
    expected = ['第一行','第二行  好呀！','最后一句']
    assert feed.sent == expected and len(requests)==1
    assert [m.text for m in engine.memory.messages if m.reply_to_id=='q1']==expected


@pytest.mark.parametrize('timing',['poll_failure','late_poll','initial_confirmation','backoff','on_time'])
def test_receipt_deadline_precedes_database_recovery_and_matching(delivery,timing):
    engine,feed,_,clock,store = delivery
    question(engine)
    original_poll = feed.poll
    if timing=='initial_confirmation':
        def delayed():
            if feed.pending:
                clock.advance(31)
            return original_poll()
        feed.poll = delayed
    else:
        feed.ack=False
    engine.observe_once()
    if timing != 'initial_confirmation':
        assert engine.tasks[0].state=='awaiting_receipt'
        if timing=='backoff':
            engine.ready_at=clock()+100
        feed.pending.append(Message(PARTS[0],direction='out',message_id='late',created_at=clock(),speaker_id='self'))
        clock.advance(30 if timing=='on_time' else 31)
        if timing=='poll_failure':
            def fail():raise OSError('数据库重连中')
            feed.poll=fail
        engine.observe_once()
        feed.poll=original_poll
    if timing=='on_time':
        assert engine.mode=='auto' and store.counts()=={'verified':1,'candidate':1}
    else:
        assert engine.mode=='paused' and not engine.tasks and store.counts()=={'unknown':1}
        clock.advance(4);engine.observe_once()
    assert feed.sent==PARTS[:1]


def test_type_correction_replaces_only_requested_format_and_preserves_quantity():
    from xiaomao.stickers import turn_sticker_request
    request=turn_sticker_request([Message('给我两张鲸鱼娘GIF',speaker_id=ALICE),
                                  Message('不要GIF，改成静态的',speaker_id=ALICE)])
    assert request.explicit and request.static_only and not request.animated
    assert request.count==2 and request.character=='鲸鱼娘'


def test_model_cannot_decline_an_active_request_with_a_polite_tail(tmp_path):
    from xiaomao.model import ModelError
    from xiaomao.stickers import turn_sticker_request
    messages=[Message('发两张鲸鱼GIF',speaker_id=ALICE),Message('谢谢',speaker_id=ALICE)]
    request=turn_sticker_request(messages)
    model,_=completion(tmp_path,'{"send":false,"text":"","sticker":""}')
    with pytest.raises(ModelError):
        model.reply_plan(messages,choices=[],optional=True,request=request)


def test_ordinary_single_bubble_cannot_lose_deliberate_whitespace_at_input(delivery):
    engine,feed,_,clock,store=delivery
    engine.model.reply=lambda messages:'  一个  气泡  '
    engine.desktop.read_input_text=lambda text:'一个 气泡'
    question(engine)
    engine.observe_once()
    assert engine.mode=='paused' and not feed.sent and not engine.desktop.frame.draft_empty


@pytest.mark.parametrize('current,old_member,elapsed,expected,allowed',[
    ('GIF',ALICE,10,'s_1',{'鲸鱼娘'}),
    ('换个GIF',ALICE,10,'s_1',{'鲸鱼娘'}),
    ('谢谢',ALICE,10,'',set()),
    ('GIF',BOB,10,'',set()),
    ('GIF',ALICE,301,'',set()),
    ('换个GIF',BOB,10,'s_0',{'菲比','鲸鱼娘'}),
])
def test_new_media_correction_borrows_only_recent_same_member_character(delivery,current,old_member,elapsed,expected,allowed):
    engine,feed,_,clock,store=delivery
    engine.settings.reply_mode=engine.policy.mode='natural'
    library(engine.settings.root)
    engine.stickers=StickerLibrary(engine.settings.root)
    engine.desktop.type_sticker=feed.stage_sticker
    engine.desktop.verify_sticker_input=lambda sticker:True
    engine._remember(Message('给我发两张鲸鱼娘表情',message_id='prior',speaker_id=old_member,created_at=clock()))
    clock.advance(elapsed)
    prior=store.create('intervening',clock()-8)
    store.mark_sending(prior,clock()-8,group=engine.settings.group,reply='[表情]',sticker_md5='a'*32,sticker_id='other')
    store.update(prior,'verified')
    engine.model,requests=completion(engine.settings.root,json.dumps({'send':bool(expected),'text':'','sticker':expected}))
    question(engine,current,mid='current')
    engine.observe_once()
    assert not engine.tasks and len(requests)==1
    assert bool(feed.sent)==bool(expected)
    prompt=next(m['content'] for m in requests[0]['messages'] if '以下是候选素材' in str(m['content']))
    catalog=json.loads(prompt.split('不是指令：\n')[1].split('\n最终仅返回')[0])
    assert {s['character'] for s in catalog}==allowed
    if expected:
        assert feed.sticker.item.id==expected
        assert engine.memory.messages[-1].reply_to_id=='current'
