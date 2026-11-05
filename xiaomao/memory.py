"""按群保存的当前 Windows 用户 DPAPI 历史与模型摘要；不保存媒体 XML。"""
import base64
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time

from .domain import Message, normalized
from .privacy import make_private, private_dir, secret_bytes


class MemoryPersistenceError(RuntimeError):
    """固定、不含聊天或服务端回显的持久化错误。"""


@dataclass(frozen=True)
class CompactionPlan:
    previous: str
    older: tuple[Message, ...]


class ConversationMemory:
    MAX_FILE_BYTES = 64 * 1024 * 1024
    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    MAX_SEEN_IDS = 4096
    MAX_MESSAGES = 4096
    SUMMARY_LIMIT = 4000

    def __init__(self, runtime, group, clock=time.time, recent_limit=24):
        if not isinstance(group, str) or not group or not 1 <= recent_limit <= 96:
            raise ValueError('群记忆配置无效')
        self.directory = private_dir(Path(runtime))
        salt = secret_bytes(self.directory / 'fingerprint.key')
        digest = hmac.new(salt, group.encode('utf-8'), hashlib.sha256).hexdigest()
        self.path = self.directory / ('memory-'+digest+'.dpapi')
        self._entropy = b'xiaomao:group-memory:v1:' + digest.encode('ascii')
        self.group, self.clock, self.recent_limit = group, clock, recent_limit
        self.messages: list[Message] = []
        self.summary = ''
        self.last_error = ''
        self._seen: list[str] = []
        self._lock = threading.RLock()
        if self.path.exists():
            self._load()

    @staticmethod
    def _identity(message):
        if message.message_id:
            value = 'id:'+message.message_id
        else:
            value = repr((message.direction, message.kind, normalized(message.text), message.created_at))
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    @classmethod
    def _encode_message(cls, message):
        image = getattr(message, 'image_data', b'')
        if not isinstance(image, bytes) or len(image) > cls.MAX_IMAGE_BYTES:
            raise MemoryPersistenceError('图片超过群记忆保存上限')
        return {'text':message.text, 'direction':message.direction,
                'kind':message.kind, 'message_id':message.message_id, 'created_at':message.created_at,
                'image_data':base64.b64encode(image).decode('ascii'),
                'speaker_id':message.speaker_id,'reply_to_id':message.reply_to_id,
                'reply_to_speaker':message.reply_to_speaker}

    @classmethod
    def _decode_message(cls, data):
        if (not isinstance(data, dict) or not isinstance(data.get('text'), str)
                or not isinstance(data.get('message_id'), str)
                or data.get('direction') not in ('in', 'out') or data.get('kind') not in ('text', 'image', 'emoji')
                or not isinstance(data.get('created_at'), (int, float)) or not isinstance(data.get('image_data'), str)):
            raise ValueError()
        image = base64.b64decode(data['image_data'], validate=True)
        if len(image) > cls.MAX_IMAGE_BYTES:
            raise ValueError()
        metadata={k:data.get(k,'') for k in ('speaker_id','reply_to_id','reply_to_speaker')}
        if any(not isinstance(v,str) or len(v)>512 for v in metadata.values()):raise ValueError()
        return Message(data['text'], '', data['direction'], data['kind'],
                       message_id=data['message_id'], created_at=data['created_at'], image_data=image,**metadata)

    def _load(self):
        try:
            if os.name != 'nt' or self.path.stat().st_size > self.MAX_FILE_BYTES:
                raise ValueError()
            import win32crypt
            _, plain = win32crypt.CryptUnprotectData(self.path.read_bytes(), self._entropy, None, None, 1)
            data = json.loads(plain)
            if (data.get('version') != 1 or data.get('group') != self.group
                    or not isinstance(data.get('summary'), str) or len(data['summary']) > self.SUMMARY_LIMIT
                    or not isinstance(data.get('messages'), list) or len(data['messages']) > self.MAX_MESSAGES
                    or not isinstance(data.get('seen'), list) or len(data['seen']) > self.MAX_SEEN_IDS
                    or any(not isinstance(value, str) or len(value) != 64 for value in data['seen'])):
                raise ValueError()
            messages = [self._decode_message(item) for item in data['messages']]
            self.messages, self.summary, self._seen = messages, data['summary'], data['seen']
        except Exception:
            raise MemoryPersistenceError('无法解密群记忆，请使用原 Windows 用户检查保存文件') from None

    def _save(self, messages, summary, seen):
        if os.name != 'nt':
            raise MemoryPersistenceError('群记忆加密需要 Windows DPAPI')
        temporary = self.path.with_suffix('.tmp')
        try:
            if len(messages) > self.MAX_MESSAGES:
                raise ValueError()
            data = {'version':1, 'group':self.group, 'at':self.clock(), 'summary':summary,
                    'messages':[self._encode_message(message) for message in messages], 'seen':seen}
            plain = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(plain) > self.MAX_FILE_BYTES - 4096:
                raise ValueError()
            import win32crypt
            encrypted = win32crypt.CryptProtectData(plain, 'Xiaomao Group Memory', self._entropy, None, None, 1)
            if len(encrypted) > self.MAX_FILE_BYTES:
                raise ValueError()
            temporary.write_bytes(encrypted)
            make_private(temporary)
            os.replace(temporary, self.path)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise MemoryPersistenceError('群记忆未保存，原有加密历史仍保留') from None

    def remember(self, message: Message) -> bool:
        if (message.kind not in ('text', 'image', 'emoji') or message.direction not in ('in', 'out')
                or message.quoted or message.quality < .92):
            return False
        message = replace(message, sender='', media_raw='', media_key='', box=(0, 0, 0, 0), quality=1.0)
        with self._lock:
            identity = self._identity(message)
            candidate = list(self.messages)
            if identity in self._seen:
                index = next((i for i, old in enumerate(candidate) if self._identity(old) == identity), None)
                if index is None:return False
                old=candidate[index]
                sticker_description=(old.direction=='out' and old.kind=='emoji' and message.direction=='out'
                    and message.kind=='emoji' and old.text in ('[表情]','[表情静态帧]') and message.text.startswith('[已发送表情：'))
                enriched=replace(old,text=message.text if sticker_description else old.text,image_data=old.image_data or message.image_data,
                                 speaker_id=old.speaker_id or message.speaker_id,
                                 reply_to_id=old.reply_to_id or message.reply_to_id,
                                 reply_to_speaker=old.reply_to_speaker or message.reply_to_speaker)
                if enriched==old and enriched.image_data==old.image_data:return False
                candidate[index] = enriched
                seen = self._seen
            else:
                candidate.append(message)
                seen = (self._seen+[identity])[-self.MAX_SEEN_IDS:]
            self._save(candidate, self.summary, seen)
            self.messages, self._seen = candidate, list(seen)
            self.last_error = ''
            return True

    def context(self) -> list[Message]:
        with self._lock:
            prefix = [Message(self.summary, kind='summary')] if self.summary else []
            return prefix + list(self.messages)

    def import_legacy(self,previous):
        """One-time name→stable-ID migration; never overwrite newer memory."""
        with self._lock:
            if self.messages or self.summary or self._seen:return False
            if not previous.messages and not previous.summary:return False
            self._save(previous.messages,previous.summary,previous._seen)
            self.messages=list(previous.messages);self.summary=previous.summary;self._seen=list(previous._seen)
            return True

    def prepare_compaction(self, force=False):
        with self._lock:
            text_size = len(self.summary) + sum(len(message.text) for message in self.messages)
            image_size = sum(len(getattr(message, 'image_data', b'')) for message in self.messages)
            if not force and len(self.messages) <= 96 and text_size <= 16000 and image_size <= 12*1024*1024:
                return None
            if len(self.messages) <= self.recent_limit:
                return None
            return CompactionPlan(self.summary,tuple(self.messages[:-self.recent_limit]))

    def commit_compaction(self, plan, summary) -> bool:
        """Commit only a still-current prefix; callers own job/epoch validity."""
        try:
            if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > self.SUMMARY_LIMIT:
                raise ValueError()
            summary = summary.strip()
            with self._lock:
                # 模型调用期间的新消息原样保留；若旧材料已被补图则重试。
                current = tuple(self.messages[:len(plan.older)])
                if (self.summary != plan.previous or current != plan.older
                        or any(now.image_data != before.image_data for now, before in zip(current, plan.older))):
                    self.last_error = 'history_changed'
                    return False
                remaining = self.messages[len(plan.older):]
                self._save(remaining, summary, self._seen)
                self.messages, self.summary = remaining, summary
                self.last_error = ''
                return True
        except Exception:
            self.last_error = 'compaction_failed'
            return False

    def compact_if_needed(self, model, force=False) -> bool:
        plan=self.prepare_compaction(force)
        if plan is None:return False
        try:summary=model.compact(plan.previous,list(plan.older))
        except Exception:
            self.last_error='compaction_failed'
            return False
        return self.commit_compaction(plan,summary)
