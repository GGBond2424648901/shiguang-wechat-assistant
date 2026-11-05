"""固定目标与显式本机配置。"""
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
import json
import hashlib
import math
import os
import re
import unicodedata
from urllib.parse import urlsplit
from .privacy import private_dir, make_private

TARGET_GROUP = "示例会话"


def validate_send_pacing(min_interval, hourly_limit, minute_limit, jitter):
    if type(min_interval) not in (int, float) or not 3 <= min_interval <= 3600:
        raise ValueError('最短间隔须为 3 至 3600 秒')
    if type(jitter) not in (int, float) or not 0 <= jitter <= 30:
        raise ValueError('随机加时须为 0 至 30 秒')
    if type(minute_limit) is not int or not 1 <= minute_limit <= 10:
        raise ValueError('每分钟上限须为 1 至 10 的整数')
    if type(hourly_limit) is not int or not 1 <= hourly_limit <= 500:
        raise ValueError('每小时上限须为 1 至 500 的整数')


def group_name(label: str) -> str:
    return re.sub(r'\s*[（(]\d+[）)]\s*$','',label).strip()


@dataclass
class Calibration:
    hwnd: int
    pid: int
    width: int
    height: int
    header_label: str
    regions: dict[str, list[float]]
    bottom_confirmed: bool = False
    target_group: str = ''
    target_kind: str = 'group'
    ocr_title: str = ''

    def validate(self) -> None:
        from .contacts import visual_name_key
        if self.ocr_title and self.ocr_title!=visual_name_key(self.target_group):
            raise ValueError('会话识别标题与目标不一致')
        if self.target_kind not in ('group','private'):raise ValueError('会话类型无效')
        if self.hwnd <= 0 or self.pid <= 0 or self.width < 600 or self.height < 450:
            raise ValueError("校准窗口无效")
        if not self.header_label:
            raise ValueError("校准缺少群标题")
        for name in ("header", "messages", "input", "send"):
            values = self.regions.get(name, [])
            if len(values) != 4:
                raise ValueError("需要完整的四个校准区域")
            x1, y1, x2, y2 = values
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError("校准区域必须位于窗口内")
        if self.regions["header"][3] > self.regions["messages"][1] or self.regions["messages"][3] > self.regions["input"][1]:
            raise ValueError("标题、消息与输入区域不能重叠")

    def rect(self, name: str) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.regions[name]
        return round(x1 * self.width), round(y1 * self.height), round(x2 * self.width), round(y2 * self.height)


@dataclass
class Settings:
    root: Path
    group: str = TARGET_GROUP
    group_names: tuple[str,...] = ()
    conversation_targets: dict = field(default_factory=dict)
    monitor_all_groups: bool = True
    reply_mode: str = 'natural'
    stickers_enabled: bool = True
    assistant_name: str = "小猫"
    wake_aliases: tuple[str,...] = ("小猫","小玲")
    self_display_name: str = ""
    self_name_unique_confirmed: bool = False
    at_note: str = ""
    model_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-flash"
    poll_seconds: float = 3
    merge_seconds: float = 3
    expiry_seconds: float = 120
    min_interval: float = 30
    hourly_limit: int = 6
    natural_pacing: bool = True
    minute_limit: int = 6
    pacing_jitter: float = 7
    context_limit: int = 24
    idle_seconds: float = 10
    auto_on_launch: bool = False
    split_replies: bool = True
    owner_private_token: str = ''
    receive_mode: str = 'database'
    calibration: dict | None = None

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        if self.receive_mode not in ('database','screen'):raise ValueError('消息读取方式无效')
        if not isinstance(self.auto_on_launch,bool):raise ValueError('启动自动回复偏好必须为布尔值')
        if type(self.split_replies) is not bool:raise ValueError('多气泡设置必须为布尔值')
        if not isinstance(self.owner_private_token,str) or (self.owner_private_token and not re.fullmatch('[a-f0-9]{64}',self.owner_private_token)):
            raise ValueError('私聊称呼绑定无效')
        if not isinstance(self.natural_pacing,bool):raise ValueError('自然回复节奏必须为布尔值')
        if not isinstance(self.stickers_enabled,bool):raise ValueError('自动表情设置必须为布尔值')
        if not isinstance(self.monitor_all_groups,bool):raise ValueError('群监听范围必须为布尔值')
        self.group_names=tuple(self.group_names)
        names=self.group_names or (self.group,)
        invalid=any(not isinstance(n,str) or not n.strip() or len(n)>80 or any(ord(c)<32 for c in n) for n in names)
        if invalid or len(names)>8 or self.group not in names or len({''.join(unicodedata.normalize('NFKC',n).split()) for n in names})!=len(names):
            raise ValueError('需配置1至8个不重复的明确会话名')
        if not isinstance(self.conversation_targets,dict):raise ValueError('会话身份配置无效')
        for name,target in self.conversation_targets.items():
            if (not isinstance(name,str) or not isinstance(target,dict) or target.get('kind') not in ('group','private')
                    or any(not isinstance(target.get(k),str) or not target[k] or len(target[k])>256 for k in ('id','account'))):
                raise ValueError('会话身份配置无效')
            if target['id'].endswith('@chatroom')!=(target['kind']=='group'):raise ValueError('会话类型与身份不符')
            if '/' in target['account'] or any(ord(c)<32 for c in target['account']+target['id']):
                raise ValueError('会话身份配置无效')
        if self.assistant_name!='小猫' or self.reply_mode not in ('natural','mentions','all'):
            raise ValueError('助手或回复模式无效')
        if not self.wake_aliases or len(self.wake_aliases)>4 or any(not isinstance(name,str) or not name.strip() or len(name)>16 for name in self.wake_aliases):
            raise ValueError('唤醒别名设置无效')
        if not (1 <= self.poll_seconds <= 30 and 1 <= self.merge_seconds <= 30 and 10 <= self.expiry_seconds <= 120):
            raise ValueError("观察与任务时效设置无效")
        validate_send_pacing(self.min_interval,self.hourly_limit,self.minute_limit,self.pacing_jitter)
        if not (0 <= self.context_limit <= 24 and 2 <= self.idle_seconds <= 120):
            raise ValueError("上下文或空闲时间设置无效")
        if len(self.self_display_name) > 80:
            raise ValueError("群显示名过长")
        if self.model_base_url:
            url = urlsplit(self.model_base_url)
            if url.username or url.password or url.query or url.fragment:
                raise ValueError("模型地址不能包含密码、查询参数或片段")
            if not url.hostname or (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))):
                raise ValueError("模型须使用 HTTPS，本机服务可使用 HTTP")
        if self.calibration:
            self.binding.validate()

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def allowed_groups(self) -> tuple[str,...]:
        return self.saved_groups if self.monitor_all_groups else (self.group,)

    @property
    def saved_groups(self) -> tuple[str,...]:
        return self.group_names or (self.group,)

    def select_group(self,name: str):
        from .contacts import visual_name_key
        names=(name,*(saved for saved in self.saved_groups if saved!=name))[:8]
        kind=self.conversation_targets.get(name,{}).get('kind','group')
        binding=asdict(replace(self.binding,header_label=name,target_group=name,target_kind=kind,
                              ocr_title=visual_name_key(name) if name in self.conversation_targets else '')) if self.binding else None
        return replace(self,group=name,group_names=names,monitor_all_groups=False,auto_on_launch=True,
                       conversation_targets={n:t for n,t in self.conversation_targets.items() if n in names},
                       self_name_unique_confirmed=self.self_name_unique_confirmed if name==self.group else False,calibration=binding)

    def select_conversation(self,contact):
        if not contact.selectable:raise ValueError(contact.reason)
        # A stale same-name binding may not silently change its recipient.
        previous=self.conversation_targets.get(contact.name)
        if previous and previous!=contact.binding:raise ValueError('同名会话身份改变，请先移除旧收藏再选择')
        targets={**self.conversation_targets,contact.name:contact.binding}
        return replace(self,conversation_targets=targets).select_group(contact.name)

    def with_saved_conversations(self,names):
        from .contacts import visual_name_key
        names=tuple(names)
        if not names:raise ValueError('请至少保留一个回复对象')
        targets={n:t for n,t in self.conversation_targets.items() if n in names}
        current=names[0]
        binding=asdict(replace(self.binding,header_label=current,target_group=current,
                               target_kind=targets.get(current,{}).get('kind','group'),
                               ocr_title=visual_name_key(current) if current in targets else '')) if self.binding else None
        return replace(self,group=current,group_names=names,conversation_targets=targets,calibration=binding,
                       self_name_unique_confirmed=self.self_name_unique_confirmed if current==self.group else False)

    @property
    def memory_scope(self):
        target=self.conversation_targets.get(self.group)
        if target:
            return target['account']+'/'+target['id']
        # An unbound display name may itself spell an account/recipient pair.
        return '\0title:'+self.group if '/' in self.group else self.group

    @property
    def private_tone_key(self):
        target=self.conversation_targets.get(self.group,{})
        if self.receive_mode!='database' or target.get('kind')!='private':return ''
        raw=json.dumps([target['account'],target['id']],ensure_ascii=False,separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def owner_confirmed(self):
        return bool(self.private_tone_key and self.private_tone_key==self.owner_private_token)

    def set_owner_tone(self,enabled):
        if enabled:
            if not self.private_tone_key:raise ValueError('主人称呼只能绑定到已识别的私聊')
            self.owner_private_token=self.private_tone_key
        elif self.owner_confirmed:self.owner_private_token=''

    def for_group(self,name: str):
        from .contacts import visual_name_key
        if name not in self.allowed_groups:raise ValueError('该群未被选定')
        kind=self.conversation_targets.get(name,{}).get('kind','group')
        binding=asdict(replace(self.binding,header_label=name,target_group=name,target_kind=kind,
                              ocr_title=visual_name_key(name) if name in self.conversation_targets else '')) if self.binding else None
        return replace(self,group=name,group_names=(),calibration=binding,
                       self_name_unique_confirmed=self.self_name_unique_confirmed if name==self.group else False)

    @property
    def binding(self) -> Calibration | None:
        return Calibration(**self.calibration) if self.calibration else None

    @property
    def model_ready(self) -> bool:
        from .credentials import CredentialStore
        host = urlsplit(self.model_base_url).hostname
        return bool(self.model_base_url and self.model_name and (host in ('127.0.0.1', 'localhost', '::1') or (host == 'api.deepseek.com' and CredentialStore(self.runtime).available)))

    @property
    def at_display_name(self) -> str:
        return self.self_display_name if self.self_name_unique_confirmed else ""

    @classmethod
    def load(cls, root: Path):
        path = Path(root) / "runtime" / "config.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.pop("root", None)
        # Earlier versions compared the integer attempt count to a numeric
        # hourly limit, which effectively rounded a fractional quota upward.
        old_hour = data.get('hourly_limit')
        if type(old_hour) is float and 1 <= old_hour <= 180:
            data['hourly_limit'] = math.ceil(old_hour)
        if data.get('wake_aliases')==['小猫','小铃']:
            data['wake_aliases']=['小猫','小玲']
        return cls(root=Path(root), **data)

    def save(self) -> None:
        self.__post_init__()
        private_dir(self.runtime)
        data = asdict(self)
        data.pop("root")
        temporary = self.runtime / "config.json.tmp"
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        make_private(temporary)
        os.replace(temporary, self.runtime / "config.json")
