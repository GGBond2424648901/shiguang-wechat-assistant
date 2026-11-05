"""Local allowlisted reactions. Catalog data never grants file or tool access."""
from dataclasses import dataclass, replace
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from PIL import Image


class StickerUnavailable(ValueError):pass


@dataclass(frozen=True)
class ReplyPlan:
    text: str = ''
    sticker: str = ''
    extra_stickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class StickerRequest:
    explicit: bool = False
    character: str = ''
    animated: bool = False
    count: int = 1
    static_only: bool = False
    suppressed: bool = False
    source_id: str = ''

    def accepts(self,item):
        return (not self.suppressed and (not self.character or item.character==self.character)
                and (not self.animated or item.animated) and (not self.static_only or not item.animated))


_ANIMATED = re.compile(r'动图|动态图|动态|gif|会动的',re.I)
_STATIC = re.compile(r'静态|静图|静止|不动的')
_QUANTITY = re.compile(r'([0-9]{1,6}|[一二两三四五六七八九十])\s*[个张只幅]')
_CANCEL = re.compile(r'(?:别|不用|不要|不必|不想|停止|取消).{0,5}(?:发|要|表情|动图|gif|静态)|^(?:算了|不用了|不要了|取消|停止)[吧啊啦。！!\s]*$',re.I)
_SHORT_FORMAT = re.compile(r'(?:gif|动图|动态图|静态(?:图|的)?)(?:吧|呀|啊|呢)?[。！!？?]?',re.I)
_MEDIA_CHANGE = re.compile(r'(?:换|改|再来|再发|需要发).{0,12}(?:gif|动图|动态|静态|表情)',re.I)
_CATALOG_ORDER = re.compile(
    r'\s*(?:@?(?:小玲|小猫)[\s，,：:]*)?(?:(?:请|麻烦你?|帮我|给我|能不能|再)\s*){0,3}'
    r'(?:搞|整|弄)\s*(?:[一二两三123]?\s*[个张只幅])?\s*'
    r'(?:大肥鱼|鲸鱼娘|鲸鱼|菲比|呆猫八条|八条)(?:的)?\s*'
    r'(?:(?:动态|静态|GIF)\s*)?(?:表情包?|动图|GIF)(?:吧|呀|呗|嘛|啊)?[。！!？?\s]*',re.I)


def catalog_order(text):
    return _CATALOG_ORDER.fullmatch(text) is not None


def _sticker_cue(message,prior):
    """Return a new directive, or None for a neutral utterance."""
    if (message.direction!='in' or message.kind!='text' or message.quoted or message.quality<.92
            or re.search(r'他说|她说|原话|这句话|[“「『]',message.text)):return None
    state=prior
    for clause in re.split(r'[，,。！!；;]',message.text):
        clause=clause.strip()
        if not clause:continue
        animated=bool(_ANIMATED.search(clause))
        static=bool(_STATIC.search(clause))
        media=animated or static or '表情' in clause
        if _CANCEL.search(clause):
            state=replace(state,explicit=False,suppressed=True,source_id=message.message_id)
            continue
        number=_QUANTITY.search(clause)
        character=next((name for name,aliases in (
            ('菲比',('菲比',)),('呆猫八条',('呆猫八条','八条')),('鲸鱼娘',('鲸鱼娘','鲸鱼','大肥鱼')))
            if any(alias in clause for alias in aliases)),'')
        directive=catalog_order(clause) or bool(re.search(r'发|来|给|换|要|求|改|看看',clause))
        short_format=bool(_SHORT_FORMAT.fullmatch(clause))
        quantity_only=number and re.fullmatch(r'(?:只要|只发|改成|改为|换成|还是|要|发|就)?\s*'+_QUANTITY.pattern+r'(?:就够了|即可|就行|吧)?[？?]?',clause)
        new_character=character and re.fullmatch(r'(?:改成|换成|换个|要)(?:菲比|八条|呆猫八条|鲸鱼娘|鲸鱼|大肥鱼)(?:的|吧)?',clause)
        if not (media and directive or state.explicit and (short_format or quantity_only or new_character)):
            continue
        # “不要 GIF，改成静态的” changes the format of this request. An
        # earlier complete cancellation, in a different message, stays final.
        base=prior if state.suppressed and prior.explicit and re.search(r'改|换|只要',clause) else state
        count=base.count if base.explicit else 1
        if number:
            value=number[1]
            count=max(1,min(3,int(value) if value.isascii() else {'一':1,'二':2,'两':2,'三':3}.get(value,3)))
        state=StickerRequest(True,character or (base.character if base.explicit else ''),
                             animated or (not static and base.explicit and base.animated),count,
                             static or (not animated and base.explicit and base.static_only),source_id=message.message_id)
    return state if state is not prior else None


def _previous_request(history,current,*,require_time):
    """Only the nearest inbound from this member can supply a correction."""
    if not re.fullmatch(r'p_[a-f0-9]{16}',current.speaker_id):return StickerRequest()
    history=list(history)[-8:]
    for i in range(len(history)-1,-1,-1):
        previous=history[i]
        if previous.direction!='in' or previous.speaker_id!=current.speaker_id:continue
        if require_time and not (previous.message_id and current.message_id and previous.created_at and current.created_at):
            break
        if previous.created_at and current.created_at and not 0<=current.created_at-previous.created_at<=300:
            break
        return sticker_request(history[:i+1])
    return StickerRequest()


def turn_sticker_request(sources,*,history=()):
    """Only source messages admitted to this ungenerated task may carry a request."""
    if not sources:return StickerRequest()
    latest=sources[-1]
    if not re.fullmatch(r'p_[a-f0-9]{16}',latest.speaker_id):
        sources=[latest]
    state=StickerRequest()
    for message in sources:
        if message.speaker_id==latest.speaker_id:
            base=state
            if (history and not state.explicit and not state.suppressed
                    and (_SHORT_FORMAT.fullmatch(message.text.strip()) or _MEDIA_CHANGE.search(message.text))):
                previous=_previous_request(history,message,require_time=True)
                if previous.explicit:
                    # A new "GIF" / "换个GIF" may carry the recent character,
                    # never an old batch quantity or an old receipt identity.
                    base=replace(previous,count=1,source_id='')
            state=_sticker_cue(message,base) or state
    return state


def named_catalog_order(sources):
    request=turn_sticker_request(sources)
    if request.explicit and request.character and any(
            m.message_id==request.source_id and catalog_order(m.text) for m in sources):
        return request
    return None


def sticker_request(messages):
    """Standalone history API: only the latest message may start a request."""
    if not messages:return StickerRequest()
    messages=list(messages)[-9:]
    latest=messages[-1]
    prior=_previous_request(messages[:-1],latest,require_time=False)
    return _sticker_cue(latest,prior) or StickerRequest()


@dataclass(frozen=True)
class Sticker:
    id: str
    character: str
    meaning: str
    tags: tuple[str, ...]
    path: Path
    sha256: str
    animated: bool

    def choice(self):
        return {'id':self.id,'character':self.character,'meaning':self.meaning,'tags':self.tags,'animated':self.animated}


@dataclass(frozen=True)
class PreparedSticker:
    item: Sticker
    path: Path
    md5: str
    sha256: str
    frames: int

    @property
    def description(self):return f'[已发送表情：{self.item.character}，{self.item.meaning}]'

    def verified_bytes(self):
        data=self.path.read_bytes()
        if len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest()!=self.sha256:
            raise StickerUnavailable('表情缓存已变化，未发送')
        return data


def media_md5(raw):
    """WeChat emoji(47) and file-picker animated sticker(appmsg 8)."""
    if not isinstance(raw,str) or len(raw)>65536 or '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():return ''
    try:
        root=ET.fromstring(raw)
        if root.tag!='msg':return ''
        emoji=root.findall('emoji');apps=root.findall('appmsg')
        if len(emoji)==1 and not apps:value=emoji[0].get('md5','')
        elif len(apps)==1 and not emoji and apps[0].findtext('type')=='8':
            values=apps[0].findall('appattach/emoticonmd5')
            value=values[0].text if len(values)==1 else ''
        else:return ''
        return value.lower() if isinstance(value,str) and re.fullmatch('[a-fA-F0-9]{32}',value) else ''
    except ET.ParseError:return ''


EMOTIONS=(('开心','好耶','哈哈','成功','搞定','庆祝','击掌'),('困','晚安','睡觉','休息'),
          ('安慰','抱抱','难过','伤心','委屈','累','烦'),('惊讶','震惊','竟然','什么','啊'),
          ('疑惑','思考','为什么','不懂'),('你好','早安','来了','招手','在吗'),
          ('喜欢','可爱','害羞','脸红','夸奖'),('谢谢','感谢','感动'),('再见','拜拜','挥手'),
          ('生气','愤怒','气死','不满'),('吃','饿','饭','馋'),('围观','吃瓜','观察'))


class StickerLibrary:
    def __init__(self,root):
        self.root=Path(root).resolve();self.folder=self.root/'assets/sticker_library';self.items=[]
        try:
            path=self.folder/'catalog.json'
            if path.stat().st_size>8*1024*1024:return
            data=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or not isinstance(data.get('items'),list):return
            seen=set()
            for row in data.get('items',[])[:10000]:
                if not isinstance(row,dict) or row.get('auto_select') is not True:continue
                identity=row.get('id','');digest=row.get('sha256','')
                if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identity) or identity in seen:continue
                if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):continue
                file=(self.root/row.get('path','')).resolve()
                if not file.is_relative_to(self.folder.resolve()) or not file.is_file():continue
                if type(row.get('animated')) is not bool:continue
                tags=row.get('tags',[])
                if not isinstance(tags,list) or any(not isinstance(t,str) for t in tags):continue
                self.items.append(Sticker(identity,str(row.get('character',''))[:20],str(row.get('meaning',''))[:120],
                    tuple(t[:20] for t in tags[:8]),file,digest,row['animated']))
                seen.add(identity)
        except (OSError,ValueError,TypeError):self.items=[]

    def candidates(self,messages,excluded=(),*,request=None):
        # Rank the latest request first; history supplies only light emotional context.
        latest=messages[-1].text if messages else ''
        text=' '.join(m.text for m in list(messages)[-4:])[-1600:]
        groups=[g for g in EMOTIONS if any(t in latest for t in g)]
        grams={latest[i:i+2] for i in range(max(0,len(latest)-1))}
        def score(item):
            description=item.meaning+' '+''.join(item.tags)
            value=sum(8 for t in item.tags if t and t in latest)+sum(3 for g in groups if any(t in description for t in g))
            value+=min(6,sum(g in description for g in grams))
            value+=sum(.2 for t in item.tags if t and t in text)
            if item.character=='菲比':value+=2
            # Stable per-turn variety, with no delay or extra model request.
            value+=int(hashlib.sha256((latest+item.id).encode()).hexdigest()[:4],16)/65536
            return value
        if request is None:request=sticker_request(messages)
        excluded_hashes={s.sha256 for s in self.items if s.id in excluded}
        ranked=sorted((s for s in self.items if s.sha256 not in excluded_hashes and request.accepts(s)),key=score,reverse=True)
        unique=[];hashes=set()
        for item in ranked:
            if item.sha256 not in hashes:unique.append(item);hashes.add(item.sha256)
        ranked=unique
        # Avoid spending the entire choice budget on near-duplicate labels.
        result=[];tags={}
        for item in ranked:
            key=item.tags[:2]
            if tags.get(key,0)>=3:continue
            result.append(item);tags[key]=tags.get(key,0)+1
            if len(result)==24:break
        if len(result)<24:result.extend(s for s in ranked if s not in result)
        return result[:24]

    def prepare_order(self,choices,request):
        prepared=[];hashes=set()
        for item in choices:
            if not request.accepts(item):continue
            try:sticker=self.prepare(item)
            except (StickerUnavailable,OSError):continue
            if sticker.sha256 in hashes:continue
            hashes.add(sticker.sha256)
            prepared.append(sticker)
            if len(prepared)==request.count:break
        return prepared

    def prepare(self,item):
        try:
            path=item.path.resolve()
            if not path.is_relative_to(self.folder.resolve()) or path.stat().st_size>8*1024*1024:raise ValueError()
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=item.sha256:raise ValueError()
            with Image.open(io.BytesIO(data)) as image:
                frames=getattr(image,'n_frames',1)
                if (image.format not in ('GIF','WEBP','JPEG','PNG') or image.width*image.height>4_000_000
                        or frames>400 or (frames>1)!=item.animated):raise ValueError()
                if image.format!='GIF':
                    # Never silently flatten an animated input. Current animated catalog is GIF.
                    if frames>1:raise ValueError()
                    rgba=image.convert('RGBA');rgba.thumbnail((1000,1000),Image.Resampling.LANCZOS)
                    # Composite alpha onto the chat's light background; preserve original on disk.
                    base=Image.new('RGBA',rgba.size,(255,255,255,255));base.alpha_composite(rgba)
                    output=io.BytesIO();base.convert('RGB').save(output,format='GIF');data=output.getvalue()
            digest=hashlib.sha256(data).hexdigest()
            from .privacy import private_dir,atomic_private_write
            target=private_dir(self.root/'runtime/stickers')/(digest+'.gif')
            if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:atomic_private_write(target,data)
            return PreparedSticker(item,target,hashlib.md5(data).hexdigest(),digest,frames)
        except (OSError,ValueError,Image.DecompressionBombError):
            raise StickerUnavailable('表情素材未通过校验，本轮使用文字') from None


def matches_draft(image,prepared,scale=1.0):
    """Match an actual GIF frame in the editor, not a generic nonempty draft."""
    import cv2
    import numpy as np
    data=prepared.verified_bytes()
    # The editor limits stickers to a 120 DIP box. Test adjacent rounding sizes.
    screen=np.array(image.convert('RGB'))
    if screen.shape[0]<40 or screen.shape[1]<40:return False
    screen=screen[:,:min(screen.shape[1],round(180*scale))]
    with Image.open(io.BytesIO(data)) as source:
        for index in range(source.n_frames):
            source.seek(index);frame=source.convert('RGBA')
            for edge in (round(120*scale),round(120*scale)-1,round(120*scale)+1):
                thumb=frame.copy();thumb.thumbnail((edge,edge),Image.Resampling.LANCZOS)
                base=Image.new('RGBA',thumb.size,(237,237,237,255));base.alpha_composite(thumb)
                template=np.array(base.convert('RGB'))
                if template.shape[1]>screen.shape[1]:continue
                height=template.shape[0]
                fragments=[template]
                if height>screen.shape[0]:
                    # WeChat scrolls a tall attachment to the caret in a short
                    # editor. Match >=70% of the frame at full width, not a tiny
                    # arbitrary patch. The exact file hash is checked after send.
                    visible=round(height*.70)
                    if visible>screen.shape[0]:continue
                    fragments=[template[top:top+visible] for top in range(0,height-visible+1,2)]
                for fragment in fragments:
                    scores=cv2.matchTemplate(screen,fragment,cv2.TM_SQDIFF)
                    _,_,location,_=cv2.minMaxLoc(scores)
                    x,y=location;crop=screen[y:y+fragment.shape[0],x:x+fragment.shape[1]]
                    error=np.abs(crop.astype('int16')-fragment.astype('int16'))
                    if float(error.mean())<9 and float((error>35).mean())<.08:return True
    return False
