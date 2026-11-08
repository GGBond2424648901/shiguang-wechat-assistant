from dataclasses import replace
import pytest
from test_engine import system
from test_bubble_delivery import delivery, question
from test_stickers import library
from xiaomao.domain import Message, ReplyTask
from xiaomao.stickers import StickerLibrary, matches_draft
from xiaomao.windows import DesktopUnavailable


def interrupted(delivery, tmp_path):
    engine,feed,_,clock,store=delivery
    feed.assert_identity=lambda:None
    library(tmp_path)
    engine.stickers=StickerLibrary(tmp_path)
    sticker=engine.stickers.prepare(engine.stickers.items[0])
    task=ReplyTask(store.create('interrupt',clock()),clock(),clock(),'',
                   reply=sticker.description,state='generated',sticker=sticker,sticker_requested=True)
    engine.tasks.append(task)
    pastes=[]
    def paste(item):pastes.append(item);feed.stage_sticker(item)
    engine.desktop.type_sticker=paste
    def focus_lost(item):raise DesktopUnavailable('编辑焦点或人工输入发生变化，已停止输入发送')
    engine.desktop.verify_sticker_input=focus_lost
    engine.desktop.verify_owned_sticker_input=lambda item:True
    engine.observe_once()
    return engine,feed,clock,store,task,pastes


def test_interrupted_gif_keeps_receiving_and_resumes_without_second_paste(delivery,tmp_path):
    engine,feed,clock,store,task,pastes=interrupted(delivery,tmp_path)
    assert engine.mode=='auto' and task in engine.tasks and task.state=='staged'
    assert not store.db.execute('SELECT 1 FROM attempts').fetchone()
    assert '表情草稿' in engine.pending()[0]['progress']
    feed.pending.append(Message('刚才有人说话',message_id='new',speaker_id='p_'+'b'*16,created_at=clock()))
    engine.desktop.idle_for=lambda:0
    clock.advance(4);engine.observe_once()
    assert not feed.sent and 'new' in engine.received_ids
    engine.desktop.idle_for=lambda:60
    clock.advance(4);engine.observe_once()
    assert len(pastes)==1 and feed.sent==['[表情]'] and store.get(task.id)['state']=='verified', (engine.mode,engine.reason,store.get(task.id),engine.pending())


@pytest.mark.parametrize('change',['different','cleared','expired','pause'])
def test_interrupted_gif_never_overwrites_draft_or_resends_expired_work(delivery,tmp_path,change):
    engine,feed,clock,store,task,pastes=interrupted(delivery,tmp_path)
    assert engine.mode=='auto'
    engine.desktop.verify_owned_sticker_input=lambda item:False
    if change=='cleared':engine.desktop.frame=replace(engine.desktop.frame,draft='',draft_empty=True)
    if change=='pause':engine.pause()
    clock.advance(engine.settings.expiry_seconds+1 if change=='expired' else 4)
    engine.observe_once()
    assert len(pastes)==1 and not feed.sent
    assert engine.mode==('paused' if change=='pause' else 'auto')
    if change=='different':assert task in engine.tasks and task.wait_reason=='sticker_draft'
    else:assert task not in engine.tasks


def test_existing_draft_does_not_stop_database_listener(delivery):
    engine,feed,_,clock,store=delivery
    engine.desktop.frame=replace(engine.desktop.frame,draft='人工草稿',draft_empty=False)
    question(engine);engine.observe_once()
    assert engine.mode=='auto' and not feed.sent and engine.tasks[0].wait_reason=='draft'
    assert engine.desktop.frame.draft=='人工草稿'


@pytest.mark.parametrize('change',['identity','human_input','wrong_chat','uncertain_receipt'])
def test_resumed_sticker_still_checks_identity_focus_and_never_reclicks(delivery,tmp_path,change):
    engine,feed,clock,store,task,pastes=interrupted(delivery,tmp_path)
    def unavailable(*args):raise DesktopUnavailable('安全条件已变化')
    if change=='identity':feed.assert_identity=unavailable
    elif change=='human_input':engine.desktop.assert_input_focus=unavailable
    elif change=='wrong_chat':engine.desktop.frame=replace(engine.desktop.frame,group='其他会话')
    else:feed.ack=False
    clock.advance(4);engine.observe_once()
    if change=='uncertain_receipt':
        assert feed.sent==['[表情]']
        clock.advance(engine.settings.expiry_seconds+1);engine.observe_once()
        engine.observe_once()
        assert feed.sent==['[表情]'] and engine.mode=='paused'
    else:assert not feed.sent
    assert len(pastes)==1


def test_unloaded_sticker_preview_waits_instead_of_pausing_listener(delivery,tmp_path):
    engine,feed,clock,store,task,pastes=interrupted(delivery,tmp_path)
    # Simulate the normal paste path's failed readback (loading/animation),
    # before any click. The strict recovery path still has to prove ownership.
    engine.tasks.remove(task)
    replacement=ReplyTask(store.create('loading',clock()),clock(),clock(),'',
        reply=task.reply,state='generated',sticker=task.sticker,sticker_requested=True)
    engine.tasks.append(replacement)
    engine.desktop.frame=replace(engine.desktop.frame,draft='',draft_empty=True)
    engine.desktop.verify_sticker_input=lambda item:False
    engine.observe_once()
    assert engine.mode=='auto' and replacement.draft_waiting and not feed.sent


@pytest.mark.parametrize('extra',['none','text','second','clipped'])
def test_resume_proof_requires_one_complete_sticker_and_no_other_draft(tmp_path,extra):
    from PIL import Image,ImageDraw
    library(tmp_path);lib=StickerLibrary(tmp_path);prepared=lib.prepare(lib.items[0])
    with Image.open(prepared.path) as source:
        thumb=source.convert('RGB');thumb.thumbnail((120,120))
    draft=Image.new('RGB',(520,180),(237,237,237));draft.paste(thumb,(10,5))
    if extra=='text':ImageDraw.Draw(draft).text((230,10),'do not send my draft',fill='black')
    elif extra=='second':draft.paste(thumb,(230,5))
    elif extra=='clipped':draft= draft.crop((0,40,520,180))
    assert matches_draft(draft,prepared,exclusive=True)==(extra=='none')


@pytest.mark.parametrize('extra',['none','footer_text','scrollbar'])
def test_recovery_checks_editor_footer_and_right_edge_not_just_text_crop(tmp_path,extra):
    from PIL import Image,ImageDraw
    from xiaomao.config import Calibration
    from xiaomao.windows import WindowsDesktop
    library(tmp_path);lib=StickerLibrary(tmp_path);sticker=lib.prepare(lib.items[0])
    binding=Calibration(1,1,800,700,'测试群',{
        'header':[0,0,1,.1],'messages':[0,.1,1,.6],
        'input':[.02,.65,.90,.88],'send':[.80,.90,.97,.96]},True)
    desktop=WindowsDesktop(binding);desktop._dpi_scale=lambda:1
    desktop.assert_input_focus=lambda:None;desktop.reveal_sticker_preview=lambda:None
    image=Image.new('RGB',(800,700),(237,237,237))
    with Image.open(sticker.path) as source:
        thumb=source.convert('RGB');thumb.thumbnail((120,120))
    image.paste(thumb,(20,465));draw=ImageDraw.Draw(image)
    draw.rectangle(binding.rect('send'),fill=(7,193,96))
    if extra=='footer_text':draw.text((20,652),'my extra text',fill='black')
    elif extra=='scrollbar':draw.rectangle((784,500,788,600),fill=(100,100,100))
    desktop.capture=lambda:image
    assert desktop.verify_owned_sticker_input(sticker)==(extra=='none')


@pytest.mark.parametrize('caret',['adjacent','far','scrollbar','wide'])
def test_full_height_image_caret_is_not_mistaken_for_extra_draft(tmp_path,caret):
    from PIL import Image,ImageDraw
    library(tmp_path);lib=StickerLibrary(tmp_path);prepared=lib.prepare(lib.items[0])
    with Image.open(prepared.path) as source:
        thumb=source.convert('RGB');thumb.thumbnail((120,120))
    draft=Image.new('RGB',(520,180),(237,237,237));draft.paste(thumb,(10,8))
    x=7 if caret!='far' else 300
    color=(160,160,160) if caret=='scrollbar' else (7,193,96)
    ImageDraw.Draw(draft).rectangle((x,8,x+(8 if caret=='wide' else 1),8+thumb.height-1),fill=color)
    assert matches_draft(draft,prepared,exclusive=True)==(caret=='adjacent')
