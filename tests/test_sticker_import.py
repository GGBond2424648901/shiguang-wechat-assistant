from pathlib import Path
import hashlib
import pytest
from PIL import Image
from xiaomao.sticker_import import StickerShelf,inspect_picture
from xiaomao.stickers import StickerLibrary
from xiaomao.domain import Message


def original(path,animated=True):
    image=Image.new('RGB',(80,80),'pink')
    other=Image.new('RGB',(80,80),'blue')
    image.save(path,save_all=animated,append_images=[other] if animated else [],duration=[60,140] if animated else 100,loop=3)
    return path


def details(category='菲比'):
    return dict(category=category,meaning='开心地和朋友击掌',tags=['开心','击掌'],source='https://example.com/original')


def test_import_is_reviewed_deduplicated_and_animated_original_survives(tmp_path):
    source=original(tmp_path/'upload.gif')
    picture=inspect_picture(source)
    library=StickerLibrary(tmp_path);shelf=StickerShelf(tmp_path)
    assert not library.items and not library.candidates([Message('开心')])
    assert shelf.commit(picture,details())
    assert not shelf.commit(picture,details())
    picks=library.candidates([Message('开心！')])
    assert len(picks)==1 and picks[0].animated
    prepared=library.prepare(picks[0])
    assert prepared.path.read_bytes()==source.read_bytes()
    with Image.open(prepared.path) as image:
        assert image.n_frames==2 and image.info['loop']==3 and image.info['duration']==60
        image.seek(1);assert image.info['duration']==140
    shelf.save_preferences({'菲比':'停用'},False)
    assert not library.candidates([Message('发个菲比GIF')])
    with pytest.raises(ValueError):library.prepare(picks[0])


def test_changed_file_cannot_be_committed_after_review(tmp_path):
    source=original(tmp_path/'change.gif')
    p=inspect_picture(source);source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='变化'):StickerShelf(tmp_path).commit(p,details())
    assert not StickerLibrary(tmp_path).items


def test_new_category_request_priority_and_neutral_are_used_by_sender(tmp_path):
    shelf=StickerShelf(tmp_path)
    for i,name in enumerate(('菲比','蓝鲸','自定义小狗')):
        image=Image.new('RGB',(80,80),(i*60,99,32));p=tmp_path/(name+'.png');image.save(p)
        shelf.commit(inspect_picture(p),details(name))
    shelf.save_preferences({'蓝鲸':'优先','菲比':'标准','自定义小狗':'备用'},False)
    lib=StickerLibrary(tmp_path)
    assert lib.candidates([Message('开心击掌')])[0].character=='蓝鲸'
    assert all(s.character=='自定义小狗' for s in lib.candidates([Message('发一张自定义小狗的表情')]))
    shelf.save_preferences({'菲比':'停用','蓝鲸':'优先','自定义小狗':'备用'},True)
    chosen=lib.candidates([Message('开心')])
    assert chosen and all(s.priority=='标准' and s.character!='菲比' for s in chosen)


def test_known_annotation_skips_model_but_unknown_uses_image(tmp_path):
    source=original(tmp_path/'known.gif');picture=inspect_picture(source);shelf=StickerShelf(tmp_path)
    class Model:
        calls=0
        def analyze_sticker(self,image,category):
            self.calls+=1;assert image.startswith(b'\x89PNG')
            return dict(character='通用',meaning='打招呼',tags=['你好'])
    m=Model()
    known={picture.digest:dict(character='菲比',meaning='挥手',tags=['你好'],source='本地标注')}
    data,state=shelf.analyze(picture,'',m,known)
    assert not m.calls and data['source']=='本地标注'
    data,state=shelf.analyze(picture,'',m,{})
    assert m.calls==1 and data['character']=='通用'


def test_animated_webp_is_rejected_without_losing_animation(tmp_path):
    source=original(tmp_path/'animation.webp')
    with pytest.raises(ValueError,match='GIF'):inspect_picture(source)
