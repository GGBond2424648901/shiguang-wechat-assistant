"""点名条件和输入文字归一化。"""
import re
import unicodedata
import json
from .domain import Message, normalized


class TriggerPolicy:
    def __init__(self, assistant_name: str, self_display_name: str, aliases=(),mode='mentions'):
        self.assistant_name = assistant_name
        self.aliases=tuple(dict.fromkeys((assistant_name,*aliases)))
        self.self_display_name = normalized(self_display_name)
        self.mode=mode

    def matches(self, message: Message) -> bool:
        if message.direction != "in" or message.kind not in ('text','image','emoji') or message.quoted or message.quality < .92:
            return False
        if message.kind!='text':return self.mode in ('all','natural') and bool(message.message_id)
        if self.mode in ('all','natural'):return bool(normalized(message.text))
        return self.addressed(message)

    def addressed(self, message: Message) -> bool:
        if message.direction!='in' or message.kind!='text' or message.quoted or message.quality<.92:
            return False
        text = normalized(message.text)
        if any(text.startswith(name) for name in self.aliases):
            return True
        if message.mentioned_self is not None:
            return message.mentioned_self
        names=(*self.aliases,self.self_display_name) if self.self_display_name else self.aliases
        return any(re.search(r'@'+re.escape(name)+r'(?=$|[^\w])',text) for name in names)


class ReplyFormatError(ValueError):
    """An internal attribution envelope must never become visible chat text."""


def _attribution_marker(text):
    return bool(re.search(r'["\'](?:speaker|reply_to_speaker)["\']\s*:\s*["\'](?:self|unknown|p_[0-9a-f]{16})["\']',text)
                or re.search(r'["\'](?:message|reply_to)["\']\s*:\s*["\']m_[0-9a-f]{16}["\']',text))


def reply_body(text: str) -> str:
    """Unwrap only our known attribution schema; leave ordinary JSON alone.

    Parse before whitespace/length processing so escaped Unicode and long
    metadata cannot truncate the actual answer. Never guess a broken envelope.
    """
    if not isinstance(text,str):raise ReplyFormatError('回复正文必须为文字')
    fields={'speaker','message','reply_to','reply_to_speaker','body'}
    for _ in range(4):
        candidate=text.strip().lstrip('\ufeff')
        fence=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',candidate,re.IGNORECASE)
        if fence:candidate=fence[1]
        try:data=json.loads(candidate)
        except (ValueError,TypeError):data=None
        if isinstance(data,str) and _attribution_marker(data):text=data;continue
        if isinstance(data,dict) and _attribution_marker(json.dumps(data,ensure_ascii=False)):
            speaker=data.get('speaker')
            valid_speaker=lambda s:isinstance(s,str) and (s in ('self','unknown','') or bool(re.fullmatch(r'p_[0-9a-f]{16}',s)))
            if (not set(data)<=fields or not valid_speaker(speaker) or not isinstance(data.get('body'),str)
                    or not data['body'].strip()
                    or any(k in data and (not isinstance(data[k],str) or (data[k] and not re.fullmatch(r'm_[0-9a-f]{16}',data[k]))) for k in ('message','reply_to'))
                    or ('reply_to_speaker' in data and not valid_speaker(data['reply_to_speaker']))):
                raise ReplyFormatError('回复含无效内部格式，未发送')
            text=data['body'];continue
        if _attribution_marker(candidate):raise ReplyFormatError('回复含残缺内部格式，未发送')
        return text
    raise ReplyFormatError('回复内部格式嵌套过多，未发送')


_ROLE_FIELD = re.compile(r'(?<![a-z0-9_])' + r'\s*'.join('emotion_delta') + r'''[\s\\"']*[:=]''', re.I)
_JSON_ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|(["\\/bfnrt]))')
_JSON_ESCAPED = {'"':'"', '\\':'\\', '/':'/', 'b':'\b', 'f':'\f', 'n':'\n', 'r':'\r', 't':'\t'}


def _has_role_field(text):
    # Inspect a separate copy: width folding and unescaping must never
    # rewrite a legitimate URL, code sample or user-requested JSON string.
    for _ in range(8):
        text = unicodedata.normalize('NFKC', text)
        text = ''.join(c for c in text if c.isspace() or not unicodedata.category(c).startswith('C'))
        if _ROLE_FIELD.search(text):
            return True
        decoded = _JSON_ESCAPE.sub(lambda m: chr(int(m[1],16)) if m[1] else _JSON_ESCAPED[m[2]], text)
        if decoded == text:
            return False
        text = decoded
    # An input that still decodes after eight layers is ambiguous. Never
    # forward that deeply escaped control-looking data as a chat message.
    return True


MAX_REPLY_CHARS = 4000  # Transport sanity ceiling, not a conversational style quota.


def clean_reply(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    text=reply_body(text)
    if re.search(r'[\[【]\s*已发送表情\s*[:：]',text):
        raise ReplyFormatError('回复含内部媒体回执，未发送')
    # Plain prose can also have intentional line breaks and spaces. Keep
    # them for the bubble planner and paste adapter, never as key events.
    text = text.replace('\r\n','\n').replace('\r','\n')
    result = ''.join(c for c in text if c in '\t\n' or not unicodedata.category(c).startswith('C'))
    if len(result)>limit:
        raise ReplyFormatError('回复超出单次发送容量，未截断或发送，请重新生成完整答复')
    if _has_role_field(result):
        raise ReplyFormatError('回复含内部角色状态，未发送')
    if re.search(r'[\[【]\s*已发送表情\s*[:：]',result):
        raise ReplyFormatError('回复含内部媒体回执，未发送')
    if not result.strip():
        raise ValueError("模型未生成可发送的文字")
    return result
