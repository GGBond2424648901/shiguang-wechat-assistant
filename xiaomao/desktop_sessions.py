"""Scoped contact selection and editable conversation preferences."""

from dataclasses import replace
import hashlib
from threading import Lock
from time import monotonic

from . import selection
from .account_switching import configured_account
from .client import ControlUnavailable
from .config import Settings, TARGET_GROUP
from .contacts import search_directory
from .desktop_settings import config_revision
from .reply_preferences import persist_preferences, validate_rule_changes
from .settings_service import apply_saved_settings


_UNSET = object()


def _contact_key(row):
    return 'contact_' + hashlib.sha256((row.account + '/' + row.id).encode('utf-8')).hexdigest()[:32]


def _account_key(cfg):
    return hashlib.sha256(configured_account(cfg).encode()).hexdigest()


class DesktopSessions:
    def __init__(self, root, client):
        self.root = root
        self.client = client
        self._directory_lock = Lock()
        self._cache = None

    def read(self):
        cfg = Settings.load(self.root)
        rows = []
        for name in (() if self.is_placeholder(cfg) else cfg.saved_groups):
            target = cfg.conversation_targets.get(name, {})
            identity = cfg.conversation_pause_key(name)
            binding = cfg.binding_for(name)
            rules = cfg.conversation_rules.get(cfg.conversation_scope(name), {})
            rows.append({'id': identity, 'contactId': 'contact_' + identity[:32] if target else '',
                         'name': name, 'kind': target.get('kind', 'group'),
                         'bound': bool(target), 'paused': cfg.is_conversation_paused(name),
                         'calibrated': bool(binding and binding.bottom_confirmed),
                         'windowKind': binding.window_kind if binding else None,
                         'windowSize': [binding.width, binding.height] if binding else None,
                         'replyMode': rules.get('reply_mode'), 'stickerFrequency': rules.get('sticker_frequency'),
                         'webSearch': rules.get('web_search')})
        return {'items': rows, 'monitorAll': cfg.monitor_all_groups, 'stickersEnabled': cfg.stickers_enabled,
                'defaultReplyMode': cfg.reply_mode, 'defaultStickerFrequency': cfg.sticker_frequency,
                'webSearchEnabled': cfg.web_search_enabled,
                'accountLabel': cfg.account_display_name,
                'accountKey': _account_key(cfg), 'revision': config_revision(cfg)}

    @staticmethod
    def is_placeholder(cfg):
        return cfg.saved_groups == (TARGET_GROUP,) and not cfg.conversation_targets and cfg.binding is None

    def _directory(self, cfg, refresh=False):
        account = configured_account(cfg)
        with self._directory_lock:
            if not refresh and self._cache and self._cache[0] == account and monotonic() < self._cache[1]:
                return self._cache[2]
            self._cache = None
            rows = tuple(selection.load_directory(self.root))
            if configured_account(Settings.load(self.root)) != account:
                raise ValueError('微信账号已切换，请刷新通讯录')
            self._cache = (account, monotonic() + 20, rows)
            return rows

    def directory(self, query='', kind='all', offset=0, refresh=False, accountKey=None):
        if (not isinstance(query, str) or len(query) > 120 or kind not in ('all', 'group', 'private', 'service')
                or type(offset) is not int or not 0 <= offset <= 50000 or type(refresh) is not bool):
            raise ValueError('通讯录搜索条件无效')
        cfg = Settings.load(self.root)
        if accountKey is not None and accountKey != _account_key(cfg):
            raise ValueError('微信账号已切换，请刷新通讯录')
        rows = search_directory(self._directory(cfg, refresh), query.strip(), kind)
        saved = {t['account'] + '/' + t['id']: name for name, t in cfg.conversation_targets.items()}
        page = []
        for row in rows[offset:offset + 60]:
            existing = saved.get(row.account + '/' + row.id)
            binding = cfg.binding_for(existing) if existing is not None else None
            page.append({'id': _contact_key(row), 'name': row.name, 'kind': row.kind, 'eligible': row.selectable,
                         'reason': row.reason, 'saved': existing is not None,
                         'calibrated': bool(binding and binding.bottom_confirmed)})
        return {'items': page, 'total': len(rows), 'offset': offset}

    def _current(self, expected):
        cfg = Settings.load(self.root)
        if config_revision(cfg) != expected:
            raise ValueError('会话配置已更新，草稿已保留，请刷新并核对')
        return cfg

    def _apply(self, cfg, updated):
        if cfg != updated:
            try:
                self.client.status()
            except ControlUnavailable:
                from .windows import worker_scope
                if worker_scope(self.root) is not None:
                    raise ValueError('后台暂未连接，未覆盖运行配置，请稍后再试') from None
                updated.save(expected=cfg)
            else:
                apply_saved_settings(self.client, cfg, updated)
        return self.read()

    def save(self, expected, order, monitorAll, stickersEnabled=None, defaultReplyMode=None):
        if (not isinstance(order, list) or len(order) > 8
                or not all(isinstance(key, str) for key in order) or len(set(order)) != len(order)
                or type(monitorAll) is not bool or (stickersEnabled is not None and type(stickersEnabled) is not bool)):
            raise ValueError('请选择 1–8 个不同会话')
        cfg = self._current(expected)
        if not order and not self.is_placeholder(cfg):
            raise ValueError('请选择要保存的监听会话')
        old = {cfg.conversation_pause_key(name): name for name in cfg.saved_groups}
        if self.is_placeholder(cfg) and any(key not in old for key in order):
            order = [key for key in order if key not in old]
        if any(key not in old for key in order):
            rows = self._directory(cfg, refresh=True)
            available = {_contact_key(row): row for row in rows if row.selectable and row.kind in ('group', 'private')}
            contacts = [available[key] for key in order if key in available]
            if any(key not in old and key not in available for key in order):
                raise ValueError('联系人已改变，请刷新目录并重新勾选')
            names = [old[key] if key in old else available[key].name for key in order]
            if len(set(names)) != len(names):
                raise ValueError('存在同名会话，请刷新并重新选择')
            updated = selection.bind_saved_conversations(cfg, names, rows, contacts)
        else:
            updated = cfg.with_saved_conversations([old[key] for key in order]) if order else cfg
        for name in updated.saved_groups:
            if (name not in cfg.saved_groups or
                    updated.conversation_targets.get(name) != cfg.conversation_targets.get(name)):
                updated = updated.with_conversation_pause(name, True)
        values = {'monitor_all_groups': monitorAll}
        if stickersEnabled is not None:
            values['stickers_enabled'] = stickersEnabled
        if defaultReplyMode is not None:
            validate_rule_changes({'reply_mode': defaultReplyMode}, allow_inherit=False)
            values['reply_mode'] = defaultReplyMode
        return self._apply(cfg, replace(updated, **values))

    def remove(self, session, accountKey):
        cfg = Settings.load(self.root)
        if accountKey != _account_key(cfg):
            raise ValueError('微信账号已切换，请重新选择会话')
        if not isinstance(session, str) or not session:
            raise ValueError('请选择要移除的会话')
        names = [name for name in cfg.saved_groups if cfg.conversation_pause_key(name) != session]
        if self.is_placeholder(cfg) or len(names) == len(cfg.saved_groups):
            raise ValueError('监听会话已变化，请重新选择')
        return self._apply(cfg, cfg.with_saved_conversations(names))

    def rules(self, expected, session, replyMode, stickerFrequency, before=None, *, webSearch=_UNSET):
        values = {'reply_mode': replyMode, 'sticker_frequency': stickerFrequency}
        if webSearch is not _UNSET:
            values['web_search'] = webSearch
        validate_rule_changes(values)
        cfg = self._current(expected) if before is None else Settings.load(self.root)
        name = next((name for name in cfg.saved_groups if cfg.conversation_pause_key(name) == session), None)
        if not name or not cfg.conversation_targets.get(name):
            raise ValueError('请选择已确认身份的会话')
        if before is None:
            return self._apply(cfg, cfg.with_conversation_rules(name, values))

        previous = validate_rule_changes(before)
        if set(previous) != set(values):
            raise ValueError('回复规则的原始值不完整，请重新读取会话')
        changes = {key: value for key, value in values.items() if value != previous[key]}
        if changes:
            persist_preferences(self.root, changes, name, cfg.conversation_scope(name), client=self.client,
                                expected={key: previous[key] for key in changes})
        return self.read()
