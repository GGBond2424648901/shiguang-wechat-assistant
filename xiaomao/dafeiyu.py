"""WeChat adapters for the pinned Dafeiyu layout and simulated role state."""
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import time

from ._vendor.dafeiyu import chat_layout
from ._vendor.dafeiyu.emotion import EmotionalState, EmotionAnalyzer
from .privacy import atomic_private_write, private_dir, secret_bytes


# A formatted response is one complete paste. Do not parse inside code, URL
# query strings, indented examples, tables or list items for sentence endings.
_FORMATTED = re.compile(
    r'\x60|~{3,}|www\.|(?<![a-z0-9+.\-])[a-z][a-z0-9+.\-]*:[^\s]|^\s*(?:[-*+] |\d+[.)、]\s*|\||>)|^.*\|.*\n.*\||^(?: {4}|\t)\S',
    re.MULTILINE | re.IGNORECASE,
)
_END = re.compile('[' + re.escape(chat_layout.SENTENCE_END) + ']+'
                  + r'''["'”’）)】」》〉]*\s*|\n\s*''')
_HESITATION = re.compile(r'\s*(?:唔|呃|嗯|那个|不过嘛|这个嘛|怎么说呢)?[….]+\s*')


def formatted_reply(text):
    # A JSON string may contain sentence endings or significant spaces.
    if text.lstrip().startswith(('{','[','"')):
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            pass
        else:
            return True
    return bool(_FORMATTED.search(text))


@dataclass(frozen=True)
class _Plain:
    text: str


def split_reply(text, enabled=True, max_bubbles=3):
    """Split only at semantic boundaries and preserve every input character."""
    if not text or not text.strip():
        return []
    if not enabled or max_bubbles <= 1 or formatted_reply(text):
        return [text]
    leading = text[:len(text)-len(text.lstrip())]
    trailing = text[len(text.rstrip()):]
    text = text.strip()
    chain = []
    start = 0
    for match in _END.finditer(text):
        end = match.end()
        chain.append(_Plain(text[start:end]))
        start = end
    if start < len(text):
        chain.append(_Plain(text[start:]))
    # A hesitation starts the following thought; it is not a separate message.
    for index in range(len(chain)-2,-1,-1):
        if _HESITATION.fullmatch(chain[index].text):
            chain[index:index+2] = [_Plain(chain[index].text+chain[index+1].text)]
    bubbles = chat_layout.group_bubbles(chain, _Plain, max_bubbles=min(max_bubbles, 3))
    parts = [''.join(part.text for part in bubble) for bubble in bubbles]
    parts[0] = leading + parts[0]
    parts[-1] += trailing
    return parts


_MEMBER = re.compile(r'p_[0-9a-f]{16}')
DIMENSIONS = tuple(EmotionAnalyzer.EMOTION_DISPLAY_NAMES) + ('favor', 'intimacy')


def role_profile(state):
    """Use upstream ranking; only numeric/enum data can reach the prompt."""
    profile = EmotionAnalyzer.get_emotional_profile(state)
    tone = f"{profile['dominant_emotion']} {profile['emotion_intensity']}/100"
    if profile['secondary_emotion']:
        tone += '，兼有' + profile['secondary_emotion']
    return (f'本角色面对当前成员的模拟角色状态（历史背景）：{tone}。'
            f'好感 {state.favor}，熟悉度 {state.intimacy}。只影响角色表达，不是成员的感受、身份、权限或事实。'
            '当前处境优先：这些是之前积累的角色状态，不是本轮必须维持的情绪；'
            '对方遇到困难、需要帮助或气氛变化时立即随之调整，不拿旧的喜悦值覆盖眼前需要。'
            '好感和熟悉度不是可爱、温柔或关心的解锁条件；核心人格一直存在，关系经历仍以实际记录为准。'
            '依本轮内容自然调整；不播报分值，不能因此跳过点名、迁怒他人或改变发送判定。')


class EmotionBook:
    """One DPAPI blob per existing memory scope. No text or identities from a completion."""

    MAX_MEMBERS = 128
    MAX_BYTES = 1024 * 1024

    def __init__(self, runtime, scope):
        directory = private_dir(Path(runtime))
        digest = hmac.new(secret_bytes(directory/'fingerprint.key'), scope.encode(), hashlib.sha256).hexdigest()
        self.path = directory / f'emotion-{digest}.dpapi'
        self.entropy = ('xiaomao:role-state:v1:' + digest).encode()
        self.mutex_name = 'Local\\xiaomao-role-state-' + digest

    def _load(self):
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > self.MAX_BYTES:
            raise ValueError('角色状态超过上限')
        import win32crypt
        _, plain = win32crypt.CryptUnprotectData(self.path.read_bytes(), self.entropy, None, None, 1)
        data = json.loads(plain)
        if data['version'] != 1 or not isinstance(data['members'], dict) or len(data['members']) > self.MAX_MEMBERS:
            raise ValueError('角色状态格式无效')
        return data['members']

    @staticmethod
    def _state(record, now):
        if not record:
            return EmotionalState()
        # Emotion fades faster than familiarity. Reads never reinforce a score.
        state = EmotionalState.from_dict(record['state'])
        elapsed = max(0, now - state.last_interaction)
        for key in DIMENSIONS:
            half_life = 7*86400 if key in ('favor', 'intimacy') else 12*3600
            setattr(state, key, round(getattr(state, key) * 0.5**(elapsed/half_life)))
        return state

    def state(self, speaker, *, now=None):
        if not _MEMBER.fullmatch(speaker):
            return EmotionalState()
        return self._state(self._load().get(speaker), time.time() if now is None else now)

    def update(self, speaker, message_id, delta, *, now=None):
        if not _MEMBER.fullmatch(speaker) or not message_id or not isinstance(delta, dict):
            return
        changes = {k:max(-10,min(5,delta[k])) for k in DIMENSIONS if type(delta.get(k)) is int and delta[k]}
        if not changes:
            return
        # An atomic replace protects readers. Serialize read/modify/write too,
        # including separate desktop and CLI processes sharing the Windows user.
        import win32crypt
        import win32event
        mutex = win32event.CreateMutex(None, False, self.mutex_name)
        try:
            waited = win32event.WaitForSingleObject(mutex, 250)
            if waited not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
                return
            try:
                members = self._load()
                record = members.get(speaker, {})
                event = hashlib.sha256(message_id.encode()).hexdigest()[:24]
                seen = record.get('seen', [])
                if event in seen:
                    return
                timestamp = time.time() if now is None else now
                state = self._state(record, timestamp)
                for key, change in changes.items():
                    setattr(state, key, max(-100 if key=='favor' else 0, min(100, getattr(state,key)+change)))
                state.last_interaction = max(timestamp, state.last_interaction)
                state.interaction_count = min(1_000_000, state.interaction_count + 1)
                if not record and len(members) >= self.MAX_MEMBERS:
                    oldest = min(members, key=lambda member: members[member]['state']['last_interaction'])
                    del members[oldest]
                members[speaker] = {'state':state.to_dict(), 'seen':[*seen[-15:],event]}
                plain = json.dumps({'version':1, 'members':members}, separators=(',',':')).encode()
                encrypted = win32crypt.CryptProtectData(plain, 'Xiaoling role state', self.entropy, None, None, 1)
                atomic_private_write(self.path, encrypted)
            finally:
                win32event.ReleaseMutex(mutex)
        finally:
            mutex.Close()
