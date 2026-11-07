"""Opt-in integration acceptance: stickers to your own File Transfer Assistant.

The normal worker must be manually paused, and the self-chat must already be
visible with an empty editor. This never changes its configured recipients.
"""
import argparse
from dataclasses import asdict,replace
import json
from pathlib import Path
import threading
import time
from xiaomao.client import ControlClient
from xiaomao.config import Settings
from xiaomao.database import DatabaseHub
from xiaomao.domain import Message,ReplyTask
from xiaomao.engine import Engine
if __package__:
    from .qa_model import isolated_model,selfchat_feed,require_empty_selfchat
else:
    from qa_model import isolated_model,selfchat_feed,require_empty_selfchat
from xiaomao.selection import refresh_binding
from xiaomao.stickers import StickerLibrary
from xiaomao.store import StateStore
from xiaomao.vision import Vision
from xiaomao.windows import WindowsDesktop


def acceptance_passed(evidence,expected,pending):
    if evidence.get('text_test'):
        return (not pending and evidence['task_states']==['verified'] and evidence['attempts']==1 and not evidence['files']
                and any(m['kind']=='text' and m['reply_to']=='synthetic-sticker-acceptance' for m in evidence['context']))
    return (not pending and evidence['task_states']==['verified']*expected and evidence['attempts']==expected
            and len(evidence['files'])==expected and len({f['sent_sha256'] for f in evidence['files']})==expected
            and all(f['frames']==1 or f['original_sha256']==f['sent_sha256'] for f in evidence['files']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-self-test',action='store_true')
    parser.add_argument('--static',action='store_true',help='verify static artwork through single-frame GIF transport')
    parser.add_argument('--batch',action='store_true',help='verify two requested whale GIFs with individual receipts')
    parser.add_argument('--text-question',action='store_true',help='verify ordinary text explanation while sticker support is enabled')
    parser.add_argument('--catalog-whale',action='store_true',help='verify a direct whale order with three accompanying unavailable pictures')
    args=parser.parse_args()
    if not args.send_self_test:parser.error('--send-self-test is required')
    root=Path(__file__).resolve().parents[1];client=ControlClient(root)
    if client.status()['mode']!='paused' or client.pending().get('tasks'):raise RuntimeError('正常后台必须已暂停且无待发任务')
    original=Settings.load(root);binding=refresh_binding(original)
    original=replace(original,calibration=asdict(binding))
    name='文件传输助手'
    binding=replace(binding,header_label=name,target_group=name,target_kind='private',ocr_title=name)
    testroot=root/'runtime/qa'/('sticker-self-'+str(time.time_ns()));testroot.mkdir(parents=True)
    cfg=replace(original,root=testroot,group=name,group_names=(),conversation_targets={},auto_on_launch=False,
        calibration=asdict(binding),reply_mode='all',split_replies=False,natural_pacing=False,idle_seconds=2,receive_mode='database')
    desktop=WindowsDesktop(binding);vision=Vision()
    # Read-only validation; do not navigate or clear an existing draft.
    require_empty_selfchat(desktop,vision)
    hub=DatabaseHub(original,threading.Event());hub._ensure()
    feed=selfchat_feed(hub);feed.poll()
    store=StateStore(testroot/'runtime');model=isolated_model(original,cfg)
    engine=Engine(cfg,store,desktop,vision,model,message_source=feed,settle_seconds=0)
    engine.stickers=StickerLibrary(root)
    # The synthetic test explicitly requests a dynamic reaction, so offer only
    # original multi-frame GIFs. Production selection includes static artwork.
    if not args.catalog_whale:engine.stickers.items=[s for s in engine.stickers.items if s.animated!=args.static]
    request=Message('谢谢帮忙！发张菲比的可爱静态表情收个尾吧。' if args.static else '好耶！终于把这件事搞定啦，发张菲比动图一起庆祝一下！',message_id='synthetic-sticker-acceptance',speaker_id='p_'+'a'*16,created_at=time.time())
    if args.batch:request=replace(request,text='发两个鲸鱼GIF吧，要不同的。')
    if args.text_question:request=replace(request,text='你好像一次只能发送一个，是为什么？')
    if args.catalog_whale:request=replace(request,text='搞个大肥鱼表情包')
    now=time.time();task=ReplyTask(store.create('selfchat-acceptance',now),now,now,'synthetic',[request])
    if args.catalog_whale:
        task.messages.extend(replace(request,message_id=f'synthetic-picture-{i}',text='[表情静态帧]',kind='emoji') for i in range(3))
    if args.text_question:
        task.context=[Message('发个表情包',speaker_id=request.speaker_id,message_id='synthetic-previous'),
                      Message('[已发送表情：菲比，微笑]',direction='out',kind='emoji',speaker_id='self',message_id='synthetic-previous-out',reply_to_speaker=request.speaker_id,reply_to_id='synthetic-previous')]
    engine.mode='auto';engine.cancelled.clear();engine.tasks.append(task)
    evidence={'request':'synthetic reaction / self-chat only','target':name,'static_test':args.static,'batch_test':args.batch,'text_test':args.text_question,'files':[]}
    select=model.reply_plan
    def choose(*a,**kw):
        if args.catalog_whale:raise AssertionError('explicit catalog order reached the model')
        plan=select(*a,**kw)
        evidence['candidate_count']=len(kw['choices'])
        if args.text_question:
            if plan is None or not plan.text or plan.sticker:raise RuntimeError('本轮模型没有给出文字解释，验收未发送')
        elif plan is None or not plan.sticker:raise RuntimeError('本轮模型没有选择表情，验收未发送')
        return plan
    model.reply_plan=choose
    original_prepare=engine.stickers.prepare
    def prepare(item):
        result=original_prepare(item)
        evidence.update(sticker=item.id,character=item.character,meaning=item.meaning,frames=result.frames,
                        original_sha256=item.sha256,sent_sha256=result.sha256,md5=result.md5)
        evidence['files'].append({'id':item.id,'character':item.character,'frames':result.frames,'original_sha256':item.sha256,'sent_sha256':result.sha256})
        return result
    engine.stickers.prepare=prepare
    try:
        deadline=time.monotonic()+100
        last_step=None
        while engine.tasks and engine.mode=='auto' and time.monotonic()<deadline:
            current=engine._next_task()
            if args.catalog_whale:engine.observe_once()
            elif current.state=='awaiting_receipt':engine._observe_database(engine.epoch)
            else:engine._reply_and_send(current)
            if args.catalog_whale:
                step=(current.state,engine.reason)
                if step!=last_step:
                    print('验收阶段',*step,flush=True)
                    last_step=step
            time.sleep(.6)
        evidence.update(state=store.get(task.id)['state'],mode=engine.mode,reason=engine.reason,
                        task_states=[r[0] for r in store.db.execute('select state from tasks')],
                        attempts=store.db.execute('select count(*) from attempts').fetchone()[0],
                        context=[{'kind':m.kind,'text':m.text,'reply_to':m.reply_to_id} for m in engine.context if m.direction=='out'])
        evidence['passed']=acceptance_passed(evidence,2 if args.batch else 1,len(engine.tasks))
        if args.catalog_whale:
            evidence['catalog_whale']=True
            evidence['passed']=evidence['passed'] and evidence.get('character')=='鲸鱼娘' and any(
                m['reply_to']==request.message_id for m in evidence['context'])
        if evidence['passed']:
            desktop.capture().save(testroot/'sent.png')
        elif task.sticker and not desktop.cancelled.is_set():
            # Evidence only for the isolated generated draft, never chat history.
            image=desktop.capture();image.crop(desktop.calibration.rect('input')).save(testroot/'draft.png')
            evidence['draft_scale']=desktop._dpi_scale()
        (testroot/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'evidence':str(testroot/'result.json'),**evidence},ensure_ascii=False))
        if not evidence['passed']:raise SystemExit(1)
    finally:
        engine.stop_event.set()
        if engine.companion:engine.companion.close()
        hub.close();store.close()


if __name__=='__main__':main()
