import struct
import sqlite3
from dataclasses import replace
from PIL import Image,ImageDraw
import pytest
from test_stickers import library


def test_catalog_order_counts_unique_transported_art_not_png_metadata(tmp_path):
    from PIL.PngImagePlugin import PngInfo
    from hashlib import sha256
    from xiaomao.stickers import Sticker,StickerLibrary,StickerRequest
    folder=tmp_path/'assets/sticker_library';folder.mkdir(parents=True)
    items=[]
    for i,color in enumerate(('red','red','blue')):
        path=folder/f'{i}.png';info=PngInfo();info.add_text('variant',str(i))
        Image.new('RGB',(40,40),color).save(path,pnginfo=info)
        items.append(Sticker(str(i),'鲸鱼娘','',(),path,sha256(path.read_bytes()).hexdigest(),False))
    picks=StickerLibrary(tmp_path).prepare_order(items,StickerRequest(True,'鲸鱼娘',count=2))
    assert [p.item.id for p in picks]==['0','2'] and len({p.sha256 for p in picks})==2


def test_file_drop_transfers_the_original_path_not_a_flattened_bitmap(tmp_path):
    from xiaomao.clipboard import file_drop,pasted_file
    path=tmp_path/'动态.gif';path.write_bytes(b'gif')
    payload=file_drop(path)
    assert struct.unpack('<IiiII',payload[:20])==(20,0,0,0,1)
    assert payload[20:].decode('utf-16-le')==str(path)+'\0\0'
    class Clipboard:
        seq=1;data={13:b'original'}
        def snapshot(self):return self.seq,self.data
        def current_sequence(self):return self.seq
        def restore(self,data,seq):
            if seq!=self.seq:return None
            self.data=data;self.seq+=1;return self.seq
    clip=Clipboard();received=[]
    pasted_file(clip,path,lambda:received.append(clip.data),lambda:None,lambda _:None)
    assert received==[{15:payload}] and clip.data=={13:b'original'}


def test_file_paste_never_retries_and_preserves_concurrent_user_clipboard(tmp_path):
    from xiaomao.clipboard import pasted_file,ClipboardUnavailable
    class Clipboard:
        seq=1;data={13:b'original'}
        def snapshot(self):return self.seq,self.data
        def current_sequence(self):return self.seq
        def restore(self,data,seq):
            if seq!=self.seq:return None
            self.seq+=1;self.data=data;return self.seq
    c=Clipboard();calls=[]
    def focus():
        if c.seq==2:c.seq=3;c.data={13:b'new copy'}
    with pytest.raises(ClipboardUnavailable):pasted_file(c,tmp_path/'a.gif',lambda:calls.append(1),focus,lambda _:None)
    assert not calls and c.data=={13:b'new copy'}


def test_actual_animated_appmsg_is_received_as_emoji_and_can_confirm_hash(tmp_path):
    from test_database import database
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    from xiaomao.stickers import media_md5
    path=tmp_path/'messages.db';database(path,[(1,1,100,1,2,'old')])
    source=MemoryDatabase(path);feed=DatabaseFeed(lambda:[source],'filehelper','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll();md5='a'*32
    raw=f'<msg><appmsg><type>8</type><appattach><emoticonmd5>{md5}</emoticonmd5><fileext>gif</fileext></appattach></appmsg></msg>'
    with sqlite3.connect(path) as conn:conn.execute(f'INSERT INTO [{feed.table}] VALUES(2,2,?,101,2,1,?,0)',((8<<32)|49,raw))
    msg=feed.poll().new[0]
    assert msg.kind=='emoji' and msg.direction=='out' and media_md5(msg.media_raw)==md5
    source.close()


def test_upload_completion_updates_same_row_and_emits_one_receipt(tmp_path):
    from test_database import database
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'messages.db';database(path,[(1,1,100,1,2,'old')])
    source=MemoryDatabase(path);feed=DatabaseFeed(lambda:[source],'filehelper','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll()
    raw='<msg><appmsg><type>8</type><appattach><emoticonmd5></emoticonmd5></appattach></appmsg></msg>'
    with sqlite3.connect(path) as conn:conn.execute(f'INSERT INTO [{feed.table}] VALUES(2,0,?,101,2,1,?,0)',((8<<32)|49,raw))
    assert not feed.poll().new
    with sqlite3.connect(path) as conn:conn.execute(f'UPDATE [{feed.table}] SET server_id=99,message_content=? WHERE local_id=2',(raw.replace('<emoticonmd5></emoticonmd5>','<emoticonmd5>'+('a'*32)+'</emoticonmd5>'),))
    result=feed.poll().new
    assert len(result)==1 and result[0].message_id=='filehelper/s/99'
    assert not feed.poll().new
    source.close()


def test_upload_receipt_is_not_lost_when_another_database_is_busy(tmp_path):
    from test_database import database
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'messages.db';database(path,[(1,1,100,1,2,'old')]);source=MemoryDatabase(path)
    class Other:
        path=tmp_path/'other.db';fail=False;conn=sqlite3.connect(':memory:')
        def connection(self):
            if self.fail:raise RuntimeError('WAL busy')
            return self.conn
    other=Other()
    feed=DatabaseFeed(lambda:[source,other],'filehelper','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll();raw='<msg><appmsg><type>8</type><appattach><emoticonmd5></emoticonmd5></appattach></appmsg></msg>'
    with sqlite3.connect(path) as c:c.execute(f'INSERT INTO [{feed.table}] VALUES(2,0,?,101,2,1,?,0)',((8<<32)|49,raw))
    feed.poll()
    with sqlite3.connect(path) as c:c.execute(f'UPDATE [{feed.table}] SET server_id=99,message_content=? WHERE local_id=2',(raw.replace('<emoticonmd5></emoticonmd5>','<emoticonmd5>'+('a'*32)+'</emoticonmd5>'),))
    other.fail=True
    with pytest.raises(RuntimeError):feed.poll()
    other.fail=False
    assert [m.message_id for m in feed.poll().new]==['filehelper/s/99']
    source.close();other.conn.close()


def test_reused_pending_row_never_revives_an_old_incoming_message(tmp_path):
    from test_database import database
    from xiaomao.database import DatabaseFeed,MemoryDatabase
    path=tmp_path/'messages.db';database(path,[(1,1,100,1,2,'old')]);source=MemoryDatabase(path)
    feed=DatabaseFeed(lambda:[source],'g','wxid_self',table='Msg_0123456789abcdef0123456789abcdef',clock=lambda:101)
    feed.poll();raw='<msg><appmsg><type>8</type><appattach><emoticonmd5></emoticonmd5></appattach></appmsg></msg>'
    with sqlite3.connect(path) as c:c.execute(f'INSERT INTO [{feed.table}] VALUES(2,0,?,101,2,1,?,0)',((8<<32)|49,raw))
    feed.poll()
    with sqlite3.connect(path) as c:c.execute(f'UPDATE [{feed.table}] SET real_sender_id=2,create_time=10,server_id=99,message_content=? WHERE local_id=2',(raw.replace('<emoticonmd5></emoticonmd5>','<emoticonmd5>'+('a'*32)+'</emoticonmd5>'),))
    assert not feed.poll().new
    source.close()


def test_draft_verification_compares_pixels_not_just_nonempty(tmp_path):
    from xiaomao.stickers import StickerLibrary,matches_draft
    library(tmp_path);lib=StickerLibrary(tmp_path);prepared=lib.prepare(lib.items[0])
    with Image.open(prepared.path) as f:frame=f.convert('RGB')
    draft=Image.new('RGB',(600,150),(237,237,237));draft.paste(frame,(12,4))
    assert matches_draft(draft,prepared)
    wrong=Image.new('RGB',(600,150),(237,237,237));ImageDraw.Draw(wrong).rectangle((12,4,90,80),fill='green')
    assert not matches_draft(wrong,prepared)


@pytest.mark.parametrize('caret',[False,True])
def test_empty_sticker_preview_does_not_scan_animation_frames(tmp_path, monkeypatch, caret):
    import cv2
    from xiaomao.stickers import StickerLibrary, matches_draft

    library(tmp_path)
    lib = StickerLibrary(tmp_path)
    sticker = lib.prepare(lib.items[0])
    editor = Image.new('RGB',(180,150),(237,237,237))
    if caret:
        ImageDraw.Draw(editor).rectangle((9,2,10,23), fill=(7,193,96))
    monkeypatch.setattr(cv2,'matchTemplate',lambda *args:pytest.fail('等待渲染的空输入区不应逐帧做图像比对'))
    assert not matches_draft(editor, sticker)


def test_late_gif_frame_is_checked_before_scanning_all_interior_crops(tmp_path, monkeypatch):
    import cv2
    from types import SimpleNamespace
    from xiaomao.stickers import matches_draft

    frames = [Image.new('RGB',(150,150),(i*4,20,30)) for i in range(30)]
    current = Image.new('RGB',(150,150),(40,170,190))
    frames.append(current)
    path = tmp_path/'long.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    sticker = SimpleNamespace(verified_bytes=path.read_bytes)
    editor = Image.new('RGB',(225,143),(237,237,237))
    editor.paste(current.crop((0,0,150,116)),(10,6))
    original = cv2.matchTemplate
    searches = []

    def search(*args):
        searches.append(1)
        return original(*args)

    monkeypatch.setattr(cv2,'matchTemplate',search)
    assert matches_draft(editor, sticker, 1.25)
    assert len(searches) <= 9*len(frames)


def test_short_editor_can_verify_scrolled_bottom_of_sticker(tmp_path):
    from xiaomao.stickers import StickerLibrary,matches_draft
    import hashlib,json
    entries=library(tmp_path);path=tmp_path/entries[0]['path']
    frame=Image.new('RGB',(200,200),'white');draw=ImageDraw.Draw(frame)
    for y in range(0,200,8):draw.rectangle((8,y,192,y+6),fill=(y,100,200-y))
    frame.save(path);entries[0].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),animated=False)
    (path.parent/'catalog.json').write_text(json.dumps({'items':entries[:1]}),encoding='utf-8')
    lib=StickerLibrary(tmp_path);prepared=lib.prepare(lib.items[0])
    frame=Image.open(prepared.path).convert('RGB').resize((150,150),Image.Resampling.LANCZOS)
    draft=Image.new('RGB',(600,124),(237,237,237));draft.paste(frame.crop((0,34,150,150)),(10,0))
    assert matches_draft(draft,prepared,1.25)


def test_scrolled_sticker_can_be_clipped_even_when_editor_crop_is_taller_than_thumbnail(tmp_path):
    from xiaomao.stickers import StickerLibrary,matches_draft
    import hashlib,json
    entries=library(tmp_path);path=tmp_path/entries[0]['path']
    art=Image.new('RGB',(200,200),'white');draw=ImageDraw.Draw(art)
    for y in range(0,200,8):draw.rectangle((8,y,192,y+6),fill=(y,100,200-y))
    art.save(path);entries[0].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),animated=False)
    (path.parent/'catalog.json').write_text(json.dumps({'items':entries[:1]}),encoding='utf-8')
    lib=StickerLibrary(tmp_path);prepared=lib.prepare(lib.items[0])
    thumb=Image.open(prepared.path).convert('RGB').resize((150,150),Image.Resampling.LANCZOS)
    # Qt keeps the caret in view and clips the top of the image, while the
    # broader preview crop still includes blank footer space below the editor.
    draft=Image.new('RGB',(240,193),(237,237,237))
    draft.paste(thumb.crop((0,30,150,150)),(12,10))
    assert matches_draft(draft,prepared,1.25)
    insufficient=Image.new('RGB',(240,193),(237,237,237))
    insufficient.paste(thumb.crop((0,100,150,150)),(12,10))
    assert not matches_draft(insufficient,prepared,1.25)


def test_manual_text_crop_does_not_hide_sticker_below_send_footer(tmp_path):
    """A short text/OCR crop must not reject a GIF visible at editor bottom."""
    from xiaomao.config import Calibration
    from xiaomao.windows import WindowsDesktop
    from xiaomao.stickers import StickerLibrary
    import hashlib,json
    entries=library(tmp_path);path=tmp_path/entries[0]['path']
    art=Image.new('RGB',(200,200),'white');draw=ImageDraw.Draw(art)
    for y in range(0,200,8):draw.rectangle((8,y,192,y+6),fill=(y,100,200-y))
    art.save(path);entries[0].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),animated=False)
    (path.parent/'catalog.json').write_text(json.dumps({'items':entries[:1]}),encoding='utf-8')
    lib=StickerLibrary(tmp_path);prepared=lib.prepare(lib.items[0]);original=prepared.path.read_bytes()
    binding=Calibration(1,1,766,809,'测试群',{
        'header':[.02,.03,.7,.15],'messages':[.01,.22,.98,.75],
        'input':[.02,676/809,.96,732/809],'send':[.80,738/809,.96,779/809]},
        True,target_group='测试群',window_kind='detached',manual_regions=True)
    desktop=WindowsDesktop(binding);desktop._dpi_scale=lambda:1.25
    desktop.assert_input_focus=lambda:None
    frame=Image.open(prepared.path).convert('RGB').resize((150,150),Image.Resampling.LANCZOS)
    visible=Image.new('RGB',(766,809),(237,237,237));visible.paste(frame,(20,649))
    desktop.capture=lambda:visible
    # Readback can see a low preview, but the actual short editor must be
    # expanded before a new paste; footer pixels are not usable editor height.
    assert not desktop.sticker_preview_fits(prepared)
    assert desktop.verify_sticker_input(prepared)
    assert prepared.path.read_bytes()==original  # No flattened/downscaled transport.
    wrong=Image.new('RGB',(766,809),(237,237,237));wrong.paste(frame,(20,460))
    desktop.capture=lambda:wrong
    assert not desktop.verify_sticker_input(prepared)  # Same art in chat is not a draft.
