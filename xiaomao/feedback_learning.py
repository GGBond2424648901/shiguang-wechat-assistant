"""Surface explicit feedback as evidence; never rewrite a persona or grant permissions."""
import json
import re

from .companion_memory import source_ref

_MEMBER = re.compile(r'p_[a-f0-9]{16}')
_STYLE = re.compile(r'说话|语气|回复|回答|接话|追问|口头禅|称呼|撒娇|颜文字|表情|安慰|玩笑|嘿嘿|客服|机器人|温柔|自然|简短')
_FEEDBACK = re.compile(r'不要|别|少|多|喜欢|希望|以后|可以|太|总|老|刚才|这样|一点|点儿')


def explicit_feedback(text):
    """A fast hint for prioritizing raw evidence, not a semantic fact classifier."""
    return isinstance(text,str) and bool(_STYLE.search(text) and _FEEDBACK.search(text))


def pending_feedback(records,speaker,now,exclude=()):
    if not _MEMBER.fullmatch(speaker):return ''
    ignored=set(exclude);selected=[]
    for item in records:
        at=item.get('created_at',0)
        if (item.get('direction')!='in' or item.get('kind')!='text' or item.get('speaker_id')!=speaker
                or not item.get('message_id') or item['message_id'] in ignored
                or not isinstance(at,(int,float)) or not 0<at<=now or now-at>7*86400
                or not explicit_feedback(item.get('text'))):continue
        selected.append(item)
    # Preserve negation and context. These are still unclassified quotations,
    # including when reflection is offline; they are never saved as facts here.
    return '\n'.join(json.dumps({'source':source_ref(item['message_id']),'speaker':speaker,
                                'at':item['created_at'],'source_text':item['text'][:2000]},ensure_ascii=False)
                     for item in sorted(selected,key=lambda item:item['created_at'])[-3:])
