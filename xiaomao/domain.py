"""观察结果与内存任务，不包含电脑控制权限。"""
from dataclasses import dataclass, field
import unicodedata


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


@dataclass(frozen=True)
class Message:
    text: str
    sender: str = ""
    direction: str = "in"
    kind: str = "text"
    quoted: bool = False
    quality: float = 1.0
    box: tuple[int, int, int, int] = (0, 0, 0, 0)
    message_id: str = ""
    created_at: float = 0
    image_data: bytes = field(default=b'',repr=False,compare=False)
    media_raw: str = field(default='',repr=False,compare=False)
    media_key: str = field(default='',repr=False,compare=False)
    speaker_id: str = ''
    reply_to_id: str = ''
    reply_to_speaker: str = ''
    delivery_status: str = field(default='',compare=False)

    @property
    def key(self) -> tuple:
        return (self.direction, self.sender, self.kind, self.quoted, normalized(self.text))


@dataclass(frozen=True)
class Frame:
    group: str
    messages: tuple[Message, ...] = ()
    draft: str = ""
    safe: bool = True
    reason: str = ""
    at_bottom: bool = True
    draft_empty: bool = True
    signature: str = ""
    exact_draft_line: str = ""


@dataclass
class ReplyTask:
    id: str
    created: float
    last_added: float
    sender: str
    messages: list[Message] = field(default_factory=list)
    reply: str = ""
    state: str = "candidate"
    context: list[Message] = field(default_factory=list)
    model_attempts: int = 0
    retry_at: float = 0
    sent_at: float = 0
    media_attempts: int = 0
    pacing_planned: bool = False
    reply_not_before: float = 0
    send_interval: float = 0
    proactive: object = None
    sticker: object = None
    sticker_failed: bool = False
    sticker_requested: bool = False
    remaining_stickers: list = field(default_factory=list)
    remaining_text: tuple[str, ...] = ()
    continuation_of: str = ''
    sticker_source_id: str = ''
    raw_bubble: str | None = None
    sticker_clipboard_wait: str = ''
    wait_reason: str = ''
    idle_deadline: float = 0
