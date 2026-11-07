from dataclasses import replace
from test_engine import system
from test_stickers import library


def setup_sticker(system):
    from xiaomao.stickers import StickerLibrary,ReplyPlan
    from xiaomao.domain import ReplyTask,Message
    from xiaomao.database import MessageBatch
    engine,screen,model,clock,store=system
    library(engine.settings.root);engine.stickers=StickerLibrary(engine.settings.root)
    engine.enable()
    task=ReplyTask(store.create('sticker',clock()),clock(),clock(),'friend',[Message('小玲击掌',message_id='in1',speaker_id='p_0123456789abcdef')])
    engine.tasks.append(task);clock.advance(4)
    model.reply_plan=lambda *a,**kw:ReplyPlan(sticker=kw['choices'][0].id)
    def type_sticker(prepared):
        screen.events.append('sticker');screen.frame=replace(screen.frame,draft='image',draft_empty=False)
    screen.type_sticker=type_sticker;screen.verify_sticker_input=lambda prepared:True
    def poll():
        if 'send' in screen.events:
            m=Message('[表情]',direction='out',kind='emoji',message_id='out1',created_at=clock(),media_raw='<msg><emoji md5="'+task.sticker.md5+'"/></msg>')
            return MessageBatch([m],[m])
        return MessageBatch([],[])
    engine.message_source=type('Feed',(),{'poll':lambda self:poll()})()
    return task


def test_engine_sticker_uses_one_send_receipt_and_remembers_meaning_and_recipient(system):
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    engine._reply_and_send(task)
    assert screen.events==['focus','sticker','send'] and store.get(task.id)['state']=='verified'
    out=next(m for m in engine.context if m.message_id=='out1')
    assert '开心击掌' in out.text and out.reply_to_id=='in1' and out.reply_to_speaker=='p_0123456789abcdef'
    assert not store.sticker_ready(engine.settings.group,clock())


def test_sticker_mismatch_preserves_draft_without_clicking_send(system):
    engine,screen,model,clock,store=system;setup_sticker(system)
    screen.verify_sticker_input=lambda prepared:False
    engine._reply_and_send(engine.tasks[0])
    assert engine.mode=='paused' and 'send' not in screen.events and not screen.frame.draft_empty


def test_missing_sticker_falls_back_to_text_before_any_input(system):
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    engine.stickers.items[0].path.unlink()
    model.reply_plan=lambda *a,**kw:__import__('xiaomao.stickers',fromlist=['ReplyPlan']).ReplyPlan(sticker=engine.stickers.items[0].id)
    engine._reply_and_send(task)
    assert task.sticker_failed and not screen.events and engine.mode=='auto'


def test_disabled_stickers_never_invokes_selection(system):
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    engine.settings.stickers_enabled=False
    model.reply_plan=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('disabled'))
    screen.idle_for=lambda:0
    engine._reply_and_send(task)
    assert task.reply=='喵，下午好。' and task.sticker is None


def test_newer_mention_sticker_invalidates_older_generated_choice_before_edit(system):
    engine,screen,model,clock,store=system;task=setup_sticker(system)
    screen.idle_for=lambda:0
    engine._reply_and_send(task)
    assert task.sticker and task.reply
    newer=store.create('newer-mention',clock())
    store.mark_sending(newer,clock(),group=engine.settings.group,reply='[表情]',sticker_md5='a'*32,sticker_id='s_2')
    screen.idle_for=lambda:60
    engine._reply_and_send(task)
    assert not screen.events and not task.sticker and not task.reply and task.sticker_failed
