"""固定目标与显式本机配置。"""
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
import json
import hashlib
import math
import re
import unicodedata
from urllib.parse import urlsplit
from .privacy import atomic_private_write, private_dir

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
    window_kind: str = 'main'
    manual_regions: bool = False

    def validate(self) -> None:
        from .contacts import visual_name_key
        if self.ocr_title and self.ocr_title!=visual_name_key(self.target_group):
            raise ValueError('会话识别标题与目标不一致')
        if self.target_kind not in ('group','private'):raise ValueError('会话类型无效')
        if self.window_kind not in ('main', 'detached') or type(self.manual_regions) is not bool:
            raise ValueError('校准窗口类型或区域标记无效')
        min_width, min_height = (360, 360) if self.window_kind == 'detached' else (600, 450)
        if self.hwnd <= 0 or self.pid <= 0 or self.width < min_width or self.height < min_height:
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
    conversation_rules: dict = field(default_factory=dict)
    sticker_frequency: str = 'natural'
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
    # The legacy field is the shared main-window template. Detached windows
    # only belong to their verified account/recipient entry.
    calibration: dict | None = None
    calibration_account: str = ''
    window_bindings: dict = field(default_factory=dict)
    idle_wake: bool = False

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        if self.receive_mode not in ('database','screen'):raise ValueError('消息读取方式无效')
        if not isinstance(self.auto_on_launch,bool):raise ValueError('启动自动回复偏好必须为布尔值')
        if type(self.split_replies) is not bool:raise ValueError('多气泡设置必须为布尔值')
        if type(self.idle_wake) is not bool:raise ValueError('空闲唤起设置必须为布尔值')
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
        saved_ids=[(target['account'],target['id']) for name,target in self.conversation_targets.items() if name in names]
        if len(saved_ids)!=len(set(saved_ids)):
            raise ValueError('两个回复对象使用了同一会话身份，请仅保留当前备注名')
        if self.assistant_name!='小猫' or self.reply_mode not in ('natural','mentions','all'):
            raise ValueError('助手或回复模式无效')
        from .reply_preferences import validate_rule_changes,validate_rule_map
        validate_rule_changes({'sticker_frequency':self.sticker_frequency},allow_inherit=False)
        validate_rule_map(self.conversation_rules)
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
            template = Calibration(**self.calibration)
            template.validate()
            if template.window_kind == 'main' and not self.calibration_account:
                original = self.conversation_targets.get(template.target_group or self.group, {})
                self.calibration_account = original.get('account', '')
        if not isinstance(self.window_bindings, dict):
            raise ValueError('会话窗口绑定无效')
        occupied = {}
        for key, raw in self.window_bindings.items():
            if not isinstance(key, str) or '/' not in key or not isinstance(raw, dict):
                raise ValueError('会话窗口绑定无效')
            bound = Calibration(**raw)
            bound.validate()
            handle = bound.pid, bound.hwnd
            previous = occupied.get(handle)
            if previous and 'detached' in (previous, bound.window_kind):
                raise ValueError('独立窗口已绑定到其他会话，请重新选择窗口')
            occupied[handle] = bound.window_kind
        if self.calibration and occupied.get((template.pid, template.hwnd)) == 'detached' and template.window_kind == 'main':
            raise ValueError('独立窗口不能作为共享主窗口')

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
        names=(name,*(saved for saved in self.saved_groups if saved!=name))[:8]
        return replace(self.with_saved_conversations(names), monitor_all_groups=False, auto_on_launch=True)

    def select_conversation(self,contact):
        if not contact.selectable:raise ValueError(contact.reason)
        # A stale same-name binding may not silently change its recipient.
        previous=self.conversation_targets.get(contact.name)
        if previous and previous!=contact.binding:raise ValueError('同名会话身份改变，请先移除旧收藏再选择')
        targets={**self.conversation_targets,contact.name:contact.binding}
        return replace(self,conversation_targets=targets).select_group(contact.name)

    def with_saved_conversations(self,names):
        names=tuple(names)
        if not names:raise ValueError('请至少保留一个回复对象')
        targets={n:t for n,t in self.conversation_targets.items() if n in names}
        current=names[0]
        keys = {target['account'] + '/' + target['id'] for target in targets.values()}
        bindings = {key: raw for key, raw in self.window_bindings.items() if key in keys}
        rules={key:dict(value) for key,value in self.conversation_rules.items() if key in keys}
        return replace(self,group=current,group_names=names,conversation_targets=targets,window_bindings=bindings,
                       conversation_rules=rules,
                       self_name_unique_confirmed=self.self_name_unique_confirmed if current==self.group else False)

    def with_window_binding(self, name: str, calibration: Calibration):
        """Save calibration evidence without changing the selected listeners."""
        from .contacts import visual_name_key
        if name not in self.saved_groups:
            raise ValueError('请先将会话加入回复对象')
        calibration.validate()
        target = self.conversation_targets.get(name)
        if calibration.window_kind == 'detached':
            if not target:
                raise ValueError('独立窗口须先确认登录账号和会话身份')
            if calibration.target_group != name or calibration.target_kind != target['kind']:
                raise ValueError('独立窗口已识别的会话与所选对象不符')
        raw = asdict(replace(calibration, target_group=name, target_kind=target['kind'] if target else 'group',
                             ocr_title=visual_name_key(name) if target else ''))
        bindings = dict(self.window_bindings)
        if target:
            bindings[target['account'] + '/' + target['id']] = raw
        if calibration.window_kind == 'main':
            return replace(self, window_bindings=bindings, calibration=raw,
                           calibration_account=target['account'] if target else '')
        return replace(self, window_bindings=bindings)

    def binding_for(self, name: str) -> Calibration | None:
        from .contacts import visual_name_key
        target = self.conversation_targets.get(name)
        if target:
            raw = self.window_bindings.get(target['account'] + '/' + target['id'])
            if raw:
                bound = Calibration(**raw)
                if bound.window_kind == 'detached' and (bound.target_group != name or bound.target_kind != target['kind']):
                    return None
                if bound.target_group == name and bound.target_kind == target['kind']:
                    return bound
            if self.calibration_account and self.calibration_account != target['account']:
                return None
        if not self.calibration:
            return None
        template = Calibration(**self.calibration)
        if template.window_kind != 'main':
            return None
        return replace(template, header_label=name, target_group=name,
                       target_kind=target['kind'] if target else 'group',
                       ocr_title=visual_name_key(name) if target else '')

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
        if name not in self.allowed_groups:raise ValueError('该群未被选定')
        target=self.conversation_targets.get(name,{})
        scope=target.get('account','')+'/'+target.get('id','')
        overrides=self.conversation_rules.get(scope,{})
        return replace(self,group=name,group_names=(),
                       **overrides,
                       self_name_unique_confirmed=self.self_name_unique_confirmed if name==self.group else False)

    def with_conversation_rules(self,name,changes):
        from .reply_preferences import validate_rule_changes
        changes=validate_rule_changes(changes)
        target=self.conversation_targets.get(name)
        if name not in self.saved_groups or not target:
            raise ValueError('请先绑定该会话的准确身份')
        scope=target['account']+'/'+target['id']
        rules={key:dict(value) for key,value in self.conversation_rules.items()}
        current=rules.get(scope,{})
        for key,value in changes.items():
            if value is None:current.pop(key,None)
            else:current[key]=value
        if current:rules[scope]=current
        else:rules.pop(scope,None)
        return replace(self,conversation_rules=rules)

    @property
    def binding(self) -> Calibration | None:
        return self.binding_for(self.group)

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
        atomic_private_write(self.runtime / "config.json", json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
