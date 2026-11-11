"""Presentation preferences and the desktop settings form adapters."""

from dataclasses import asdict, replace
import hashlib
import json

from .client import ControlUnavailable
from .config import SEND_PACING_PRESETS, Settings, configuration_lock, validate_send_pacing
from .credentials import CredentialError, CredentialStore
from .privacy import atomic_private_write, private_dir
from .settings_service import apply_saved_settings

DEFAULT_APPEARANCE = {'transparency': 21, 'reduceMotion': False, 'zoomPercent': 100}
GENERAL = ('model_name', 'auto_on_launch', 'split_replies', 'idle_wake',
           'self_display_name', 'self_name_unique_confirmed', 'web_search_enabled')


def config_revision(cfg):
    data = asdict(cfg)
    data.pop('root')
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DesktopSettings:
    def __init__(self, root, client):
        self.root = root
        self.client = client
        self.appearance_file = root / 'runtime/desktop-appearance.json'

    @staticmethod
    def validate_appearance(transparency, reduce_motion, zoom_percent):
        if type(transparency) is not int or not 0 <= transparency <= 100 or type(reduce_motion) is not bool:
            raise ValueError('玻璃透明度应为 0–100%，动效设置应为开或关')
        if type(zoom_percent) is not int or not 75 <= zoom_percent <= 200:
            raise ValueError('界面缩放应为 75–200%')

    def appearance(self):
        if not self.appearance_file.is_file():
            return {**DEFAULT_APPEARANCE, 'revision': None}
        raw = self.appearance_file.read_bytes()
        try:
            if len(raw) > 8192:
                raise ValueError('size')
            saved = json.loads(raw)
            if not isinstance(saved, dict) or set(saved) not in (
                    {'transparency', 'reduceMotion'}, set(DEFAULT_APPEARANCE)):
                raise ValueError('fields')
            saved = {**DEFAULT_APPEARANCE, **saved}
            self.validate_appearance(saved['transparency'], saved['reduceMotion'], saved['zoomPercent'])
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError('外观设置文件无法读取，请检查本机配置') from error
        return {**saved, 'revision': hashlib.sha256(raw).hexdigest()}

    def save_appearance(self, transparency, reduceMotion, expected, zoomPercent=None):
        self.validate_appearance(transparency, reduceMotion, 100 if zoomPercent is None else zoomPercent)
        directory = private_dir(self.appearance_file.parent)
        with configuration_lock(directory):
            current = self.appearance()
            if current['revision'] != expected:
                raise ValueError('界面外观已更新，请重新打开设置')
            data = {'transparency': transparency, 'reduceMotion': reduceMotion,
                    'zoomPercent': current['zoomPercent'] if zoomPercent is None else zoomPercent}
            atomic_private_write(self.appearance_file, json.dumps(data).encode())
        return self.appearance()

    def read(self):
        cfg = Settings.load(self.root)
        keys = CredentialStore(cfg.runtime)
        try:
            rows = [asdict(row) for row in keys.list_keys()]
            credential_error = ''
        except CredentialError as error:
            rows, credential_error = [], str(error)
        accounts = {cfg.calibration_account, *(row['account'] for row in cfg.conversation_targets.values())}
        identity = json.dumps([sorted(accounts), cfg.conversation_scope(cfg.group)], ensure_ascii=False)
        return {
            **{key: getattr(cfg, key) for key in GENERAL},
            'owner_confirmed': cfg.owner_confirmed, 'owner_available': bool(cfg.private_tone_key),
            'account': cfg.account_display_name,
            'scopeRevision': hashlib.sha256(identity.encode()).hexdigest(),
            'current': cfg.group, 'receive': cfg.receive_mode,
            'pacing': [cfg.min_interval, cfg.hourly_limit, cfg.minute_limit, cfg.pacing_jitter],
            'continuationInterval': cfg.continuation_interval,
            'pacingPresets': [{'label': label, 'values': rate} for label, rate in SEND_PACING_PRESETS.items()],
            'models': list(dict.fromkeys((cfg.model_name, 'deepseek-flash', 'deepseek-v4-pro'))),
            'keys': rows, 'credentialError': credential_error, 'revision': config_revision(cfg),
        }

    def _current(self, expected):
        cfg = Settings.load(self.root)
        if config_revision(cfg) != expected:
            raise ValueError('运行设置已更新，请重新读取后保存')
        return cfg

    def _apply(self, before, after):
        if before == after:
            return self.read()
        try:
            self.client.status()
        except ControlUnavailable:
            from .windows import worker_scope
            if worker_scope(self.root) is not None:
                raise ValueError('后台暂未连接，设置未保存，请稍后重试') from None
            after.save(expected=before)
        else:
            apply_saved_settings(self.client, before, after)
        return self.read()

    def save(self, expected, fields):
        if not isinstance(fields, dict) or (set(fields) | {'web_search_enabled'}) != {*GENERAL, 'owner_confirmed'}:
            raise ValueError('运行设置字段无效')
        original = self._current(expected)
        fields = {'web_search_enabled': original.web_search_enabled, **fields}
        if (fields['model_name'] not in (original.model_name, 'deepseek-flash', 'deepseek-v4-pro')
                or not isinstance(fields['self_display_name'], str)
                or len(fields['self_display_name']) > 80
                or any(type(fields[key]) is not bool for key in fields if key not in ('model_name', 'self_display_name'))):
            raise ValueError('模型、昵称或启动设置无效')
        updated = replace(original, **{key: fields[key] for key in GENERAL})
        updated.set_owner_tone(fields['owner_confirmed'])
        if updated.self_name_unique_confirmed:
            updated.at_note = ''
        return self._apply(original, updated)

    def pacing(self, expected, minimum, hourly, minute, jitter, continuation=None):
        original = self._current(expected)
        interval = original.continuation_interval if continuation is None else continuation
        validate_send_pacing(minimum, hourly, minute, jitter, interval)
        updated = replace(original, min_interval=minimum, hourly_limit=hourly, minute_limit=minute,
                          pacing_jitter=jitter, continuation_interval=interval, natural_pacing=True)
        return self._apply(original, updated)

    def credentials(self, mode, values):
        cfg = Settings.load(self.root)
        store = CredentialStore(cfg.runtime)
        try:
            if mode == 'append':
                store.add_keys(values)
            elif mode == 'replace':
                store.set_keys(values)
            else:
                raise ValueError('密钥保存方式无效')
        except CredentialError as error:
            raise ValueError(str(error)) from None
        return self.read()

    def remove_key(self, id):
        try:
            CredentialStore(Settings.load(self.root).runtime).remove_key(id)
        except CredentialError as error:
            raise ValueError(str(error)) from None
        return self.read()

    def test_model(self):
        from .domain import Message
        from .model import ChatModel
        model = ChatModel(Settings.load(self.root))
        try:
            if model.settings.model_name not in model.models():
                raise ValueError('当前密钥无法使用所选模型')
            model.reply([Message('小猫，只回复连接正常', '本机连接测试')])
        except (CredentialError, RuntimeError) as error:
            raise ValueError(str(error)) from None
        return {'message': 'DeepSeek 已连接并返回文字'}
