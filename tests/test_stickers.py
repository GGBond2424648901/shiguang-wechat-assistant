import hashlib
import json
from pathlib import Path
from dataclasses import replace
import pytest
from PIL import Image


def library(root, count=4):
    folder=root/'assets/sticker_library';folder.mkdir(parents=True)
    entries=[]
    for i in range(count):
        path=folder/f's_{i}.gif'
        a=Image.new('RGB',(40,40),(i*20,40,80));b=Image.new('RGB',(40,40),(180,20+i,20))
        a.save(path,save_all=True,append_images=[b],duration=[80,120],loop=0)
        entries.append(dict(id=f's_{i}',character='菲比' if i%2==0 else '鲸鱼娘',meaning='开心击掌',tags=['开心','击掌'],
                            path=path.relative_to(root).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),auto_select=True,animated=True,format='gif'))
    (folder/'catalog.json').write_text(json.dumps({'items':entries},ensure_ascii=False),encoding='utf-8')
    return entries


def test_candidates_prefer_phoebe_bounded_and_exclude_unapproved(tmp_path):
    from xiaomao.stickers import StickerLibrary
    from xiaomao.domain import Message
    entries=library(tmp_path,30);entries[0]['auto_select']=False
    (tmp_path/'assets/sticker_library/catalog.json').write_text(json.dumps({'items':entries}),encoding='utf-8')
    choices=StickerLibrary(tmp_path).candidates([Message('好开心，击掌！')])
    assert len(choices)==24 and choices[0].character=='菲比'
    assert 's_0' not in [s.id for s in choices]
    assert not StickerLibrary(tmp_path).candidates([Message('开心')],excluded={s['id'] for s in entries})
    assert all(s.character=='菲比' and s.animated for s in StickerLibrary(tmp_path).candidates([Message('来张菲比动图')]))


@pytest.mark.parametrize('data',[[],None,{'items':None},{'items':{}},'bad'])
def test_invalid_optional_catalog_does_not_prevent_startup(tmp_path,data):
    from xiaomao.stickers import StickerLibrary
    path=tmp_path/'assets/sticker_library/catalog.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data),encoding='utf-8')
    assert StickerLibrary(tmp_path).items==[]


def test_preparation_preserves_gif_bytes_frames_duration_and_loop(tmp_path):
    from xiaomao.stickers import StickerLibrary
    entries=library(tmp_path);lib=StickerLibrary(tmp_path)
    prepared=lib.prepare(lib.items[0])
    assert prepared.path.read_bytes()==(tmp_path/entries[0]['path']).read_bytes()
    assert prepared.md5==hashlib.md5(prepared.path.read_bytes()).hexdigest()
    with Image.open(prepared.path) as image:
        assert image.n_frames==2 and image.info['loop']==0
        assert image.info['duration']==80
        image.seek(1);assert image.info['duration']==120


def test_static_webp_becomes_one_frame_inline_gif_without_changing_source(tmp_path):
    from xiaomao.stickers import StickerLibrary
    entries=library(tmp_path);p=tmp_path/'assets/sticker_library/static.webp'
    Image.new('RGB',(100,80),'pink').save(p);original=p.read_bytes()
    entry=entries[0];entry.update(path=p.relative_to(tmp_path).as_posix(),sha256=hashlib.sha256(original).hexdigest(),animated=False,format='webp')
    (p.parent/'catalog.json').write_text(json.dumps({'items':[entry]}),encoding='utf-8')
    lib=StickerLibrary(tmp_path);out=lib.prepare(lib.items[0])
    with Image.open(out.path) as image:assert image.format=='GIF' and image.n_frames==1
    assert p.read_bytes()==original


@pytest.mark.parametrize('mutation',['path','sha','animation'])
def test_invalid_assets_never_stage(tmp_path,mutation):
    from xiaomao.stickers import StickerLibrary,StickerUnavailable
    library(tmp_path);lib=StickerLibrary(tmp_path);item=lib.items[0]
    if mutation=='path':item=replace(item,path=tmp_path/'outside.gif')
    elif mutation=='sha':item.path.write_bytes(b'changed')
    else:item=replace(item,animated=False)
    with pytest.raises(StickerUnavailable):lib.prepare(item)


def test_receipt_only_accepts_original_media_hash_new_outgoing_in_same_scope(tmp_path):
    from xiaomao.store import StateStore
    from xiaomao.domain import Message
    store=StateStore(tmp_path);task=store.create('test',100);md5='a'*32
    store.mark_sending(task,100,group='g',reply='[表情]',baseline_ids=['old'],sticker_md5=md5,sticker_id='s_0')
    raw=f'<msg><appmsg><type>8</type><appattach><emoticonmd5>{md5}</emoticonmd5></appattach></appmsg></msg>'
    message=Message('[表情]',direction='out',kind='emoji',message_id='new',created_at=101,media_raw=raw)
    for bad in (replace(message,direction='in'),replace(message,kind='text'),replace(message,message_id='old'),replace(message,created_at=230),replace(message,media_raw=raw.replace(md5,'b'*32))):
        assert not store.confirm_receipt(task,'g',bad)
    assert not store.confirm_receipt(task,'other',message)
    assert store.confirm_receipt(task,'g',message)
    assert store.recent_stickers('g',101)==['s_0'] and not store.recent_stickers('other',101)
    store.close()


def test_sticker_receipt_survives_restart_without_resending(tmp_path):
    from xiaomao.store import StateStore
    from xiaomao.domain import Message
    store=StateStore(tmp_path);task=store.create('test',100)
    store.mark_sending(task,100,group='g',reply='[表情]',sticker_md5='a'*32,sticker_id='s_0');store.close()
    store=StateStore(tmp_path);assert store.recover()==1
    msg=Message('[表情]',direction='out',kind='emoji',message_id='new',created_at=101,media_raw='<msg><emoji md5="'+('a'*32)+'"/></msg>')
    assert store.confirm_receipt(task,'g',msg)
    assert store.db.execute('select count(*) from attempts').fetchone()[0]==1
    store.close()


def test_media_receipt_rejects_xml_entities_or_ambiguous_hashes():
    from xiaomao.stickers import media_md5
    assert media_md5('<!DOCTYPE msg><msg><emoji md5="'+('a'*32)+'"/></msg>')==''
    assert media_md5('<msg><emoji md5="'+('a'*32)+'"/><emoji md5="'+('b'*32)+'"/></msg>')==''


def test_three_quick_texts_do_not_remove_sticker_time_cooldown(tmp_path):
    from xiaomao.store import StateStore
    store=StateStore(tmp_path)
    for i in range(4):
        task=store.create(str(i),100+i)
        store.mark_sending(task,100+i,group='g',reply='hi',sticker_md5='a'*32 if i==0 else '',sticker_id='s_0' if i==0 else '')
    assert not store.sticker_ready('g',110)
    assert store.sticker_ready('g',161)
    store.close()


def test_model_selection_uses_allowlist_and_required_mention_reply(tmp_path):
    import httpx
    from xiaomao.stickers import StickerLibrary
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel,ModelError
    from xiaomao.domain import Message
    library(tmp_path);choices=StickerLibrary(tmp_path).items;requests=[]
    answer={'send':True,'text':'','sticker':'s_0'}
    def handle(request):
        requests.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)},'finish_reason':'stop'}]})
    model=ChatModel(Settings(tmp_path,model_base_url='http://localhost'),httpx.Client(transport=httpx.MockTransport(handle)))
    plan=model.reply_plan([Message('好耶')],choices=choices,optional=True)
    assert plan.sticker=='s_0' and plan.text=='' and len(requests)==1
    assert '菲比' in json.dumps(requests[-1],ensure_ascii=False)
    for invalid in ({'send':True,'text':'','sticker':'../../secret'}, {'send':False,'text':'','sticker':''}):
        answer=invalid
        with pytest.raises(ModelError):model.reply_plan([Message('@小玲')],choices=choices,optional=False)
    answer={'send':False,'text':'','sticker':''}
    assert model.reply_plan([Message('嗯')],choices=choices,optional=True) is None
    answer={'send':True,'text':'先歇一会儿，我听着。'}
    assert model.reply_plan([Message('好难过')],choices=choices,optional=True).text=='先歇一会儿，我听着。'
    answer={'send':True,'text':'好耶！','sticker':'s_0'}
    plan=model.reply_plan([Message('搞定啦')],choices=choices,optional=True)
    assert plan.text=='好耶！' and plan.sticker=='s_0' and not plan.extra_stickers
