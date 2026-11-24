"""Choose a reply medium from a selected message and its conversation settings."""

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import Literal
import unicodedata


@dataclass(frozen=True)
class VoicePreferences:
    enabled: bool = False
    frequency: int = 0
    role_id: str | None = None


ReplyMedium = Literal['text', 'voice', 'sing']

_QUOTES = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"')
_CLAUSE = re.compile(r'[,.:;!?。\n]+')
_LEADING_MENTION = re.compile(r'^@[^\s]+(?:\s+|$)')
_TRAILING_MENTION = re.compile(r'@[^@\s]+$')
_REQUEST = r'^(?:(?:那就|那你就|那么|那)\s*)?(?:(?:请|麻烦|拜托|你|能不能|可不可以|可以|能|帮我|给我)\s*)*'
_RECORDING = r'(?:微信语音|语音(?:消息)?|录音)'
_TEXT_ONLY = re.compile(
    r'(?:别|不要|不用|不必|不想|不能).{0,7}?(?:语音|录音)'
    r'|' + _REQUEST + r'(?:还是|改成)?(?:(?:只用|用|发|回|只要|仅用?)?(?:个|条)?(?:文字|文本)|打字)'
    r'(?:回|说|答|告诉我|解释|介绍|描述|总结|就(?:好|行|可以)|吧|$)'
    r'|^(?:could you |can you |please |just )*(?:reply|respond|answer) (?:in|with) text\b'
    r"|\b(?:do not|don't|no) (?:send (?:me )?(?:a )?)?(?:voice (?:notes?|messages?)|audio)\b"
)
_SING = re.compile(
    _REQUEST + r'(?:(?:给我|为我)唱(?!反调|[红白]脸)|用语音(?:给我)?唱'
    r'|唱(?:[一两]小?段|[一两]句|[一两]?[首个曲]|一下|给我听)'
    r'|(?:我?想听(?:听)?你|让我听(?:听)?你)唱|唱歌(?:给我听|吧|好吗|吗|$)|唱《)'
    r'|^(?:could you |can you |will you |please )?sing (?:me |for me\b|(?:a |the |some )?song\b)'
)
_FOLLOWUP_SING = re.compile(r'^那(?:就|你(?:就)?)?\s*唱$')
_VOICE = re.compile(
    _REQUEST + r'(?:(?:发|回复?|来|录)(?:给我|我|来)?(?:一?(?:小?段|条|个|句))?' + _RECORDING +
    r'(?=$|\s|[吧吗呗呀啊呢]|给(?:我|大家)|过来|就|试试|说|讲|回答|说明)'
    r'|用' + _RECORDING + r'(?:给我|来)?(?:回|说|讲|答|告诉我)'
    r'|' + _RECORDING + r'(?:回|说|讲|答|吧|就(?:好|行|可以)))'
    r'|^(?:我?想听|让我听|给我听)(?:听)?你(?:的声音|说话)'
    r'|^(?:could you |can you |will you |please )?(?:send|record) (?:me )?(?:a )?(?:voice (?:note|message)|audio message)\b'
)


def _explicit_medium(message: str) -> ReplyMedium | None:
    text = _QUOTES.sub('', unicodedata.normalize('NFKC', message).casefold())
    clauses = [_TRAILING_MENTION.sub('', _LEADING_MENTION.sub('', clause.strip())).rstrip()
               for clause in _CLAUSE.split(text)]
    if any(_TEXT_ONLY.search(clause) for clause in clauses):
        return 'text'
    if any(_SING.search(clause) or _FOLLOWUP_SING.fullmatch(clause) for clause in clauses):
        return 'sing'
    if any(_VOICE.search(clause) for clause in clauses):
        return 'voice'
    return None


def choose_reply_medium(messages: str | Sequence[str], preferences: VoicePreferences, *,
                        sample: float, sender_ready: bool) -> ReplyMedium:
    """Use the latest explicit request in the turn before applying the frequency."""
    if not preferences.enabled or not sender_ready:
        return 'text'
    if isinstance(messages, str):
        messages = (messages,)
    for message in reversed(messages):
        if (medium := _explicit_medium(message)) is not None:
            return medium
    return 'voice' if 0 <= sample < preferences.frequency / 100 else 'text'
