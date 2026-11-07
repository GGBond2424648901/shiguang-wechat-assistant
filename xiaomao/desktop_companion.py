"""Account-scoped companion preferences, memory and encrypted backups."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .account_switching import configured_account
from .client import ControlUnavailable
from .companion import CompanionPreferences
from .companion_backup import export_backup, preview_backup, restore_backup
from .companion_store import CompanionStore
from .config import Settings
from .memory import ConversationMemory, MemoryPersistenceError
from .persona_documents import file_revision
from .ui_labels import member_label, memory_text


class DesktopCompanion:
    def __init__(self, root, client, read_only):
        self.root, self.client, self.read_only = root, client, read_only
        self._backup = None

    def _session(self, session):
        settings = Settings.load(self.root)
        name = next((name for name in settings.allowed_groups if settings.conversation_pause_key(name) == session), None)
        if not name:
            raise ValueError('该会话已离开监听范围，操作已取消')
        return settings, settings.for_group(name)

    @staticmethod
    def _revision(cfg, prefs):
        raw = json.dumps([cfg.memory_scope, prefs], sort_keys=True, ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()

    def _preferences(self, cfg):
        if not (cfg.runtime / 'fingerprint.key').is_file():
            return asdict(CompanionPreferences())
        notes = CompanionStore(cfg.runtime, cfg.memory_scope, read_only=True)
        return asdict(CompanionPreferences(**notes.data['preferences']))

    def _checked_snapshot(self, session, cfg):
        if self._session(session)[1].memory_scope != cfg.memory_scope:
            raise ValueError('会话身份已变化，请重新选择')

    def read(self, session):
        _, cfg = self._session(session)
        prefs = self._preferences(cfg)
        self._checked_snapshot(session, cfg)
        return {'session': session, 'bound': bool(cfg.conversation_targets.get(cfg.group)),
                'preferences': prefs, 'revision': self._revision(cfg, prefs)}

    def _active(self, settings, cfg):
        status = self.client.status()
        account = status.get('account_scope')
        if account and account != configured_account(settings):
            raise ControlUnavailable('后台正在核对登录账号')
        if cfg.group not in status.get('group_names', (status.get('group'),)):
            raise ControlUnavailable('后台监听范围正在同步')
        return status

    def state(self, session):
        settings, cfg = self._session(session)
        try:
            status = self._active(settings, cfg)
        except ControlUnavailable as error:
            return {'reason': str(error), 'preview': '', 'connected': False, 'history': [], 'routines': []}
        current = next((row for row in status.get('groups', ()) if row.get('name') == cfg.group), None)
        row = current if current is not None else status if status.get('group') == cfg.group else {}
        info = row.get('companion') or {}
        self._checked_snapshot(session, cfg)
        return {'connected': True, 'mode': row.get('mode', status.get('mode')),
                'reason': (row.get('reason') if row.get('mode') == 'paused' else info.get('reason')) or '',
                'nextAt': info.get('next_candidate_at', 0), 'nextStage': info.get('next_stage', ''),
                'once': info.get('once_state', ''), 'previewState': info.get('preview_state', ''),
                'preview': info.get('preview', ''), 'pending': row.get('pending_count'),
                'history': (info.get('routine_history') or [])[-64:], 'routines': (info.get('routines') or [])[:8]}

    def save(self, session, expected, preferences):
        settings, cfg = self._session(session)
        if not cfg.conversation_targets.get(cfg.group):
            raise ValueError('请先确认会话身份，再配置主动聊天')
        if self._revision(cfg, self._preferences(cfg)) != expected:
            raise ValueError('本会话陪伴设置已更新，草稿仍保留')
        try:
            prefs = asdict(CompanionPreferences(**preferences))
        except TypeError:
            raise ValueError('陪伴设置字段无效') from None
        if set(preferences) != set(prefs):
            raise ValueError('陪伴设置字段不完整')
        self._active(settings, cfg)
        self._checked_snapshot(session, cfg)
        self.client.companion_configure(cfg.group, cfg.memory_scope, prefs)
        return self.read(session)

    @staticmethod
    def preset(name):
        from .routines import PRESETS, routine_preset
        if name not in PRESETS:
            raise ValueError('定时关怀模板无效')
        return routine_preset(name)

    def command(self, session, action):
        if action not in ('preview', 'once'):
            raise ValueError('主动聊天操作无效')
        settings, cfg = self._session(session)
        self._active(settings, cfg)
        self._checked_snapshot(session, cfg)
        operation = self.client.companion_preview if action == 'preview' else self.client.companion_once
        operation(group=cfg.group, scope=cfg.memory_scope)
        return {'accepted': True}

    def memory(self, session):
        _, cfg = self._session(session)
        if not (cfg.runtime / 'fingerprint.key').is_file():
            return {'messages': 0, 'facts': 0, 'diaries': 0, 'summary': '', 'sources': [], 'journal': []}
        try:
            notes = CompanionStore(cfg.runtime, cfg.memory_scope, read_only=True)
            memory = ConversationMemory(cfg.runtime, cfg.memory_scope, read_only=True)
        except FileNotFoundError:
            return {'messages': 0, 'facts': 0, 'diaries': 0, 'summary': '', 'sources': [], 'journal': []}
        except MemoryPersistenceError as error:
            raise ValueError(str(error)) from None
        people = memory.member_names()
        facts = notes.data['facts']
        diary = notes.data['diary']
        self._checked_snapshot(session, cfg)
        return {'messages': len(memory.messages), 'facts': len(facts), 'diaries': len(diary),
                'summary': memory_text(memory.summary, people),
                'sources': [{'author': member_label(row['speaker'], people, '共同话题'), 'quote': row['quote']}
                            for row in reversed(facts[-30:])],
                'journal': [{'day': row['day'], 'text': memory_text(row['text'], people)} for row in reversed(diary[-7:])]}

    def export(self, session, path, password):
        _, cfg = self._session(session)
        if not cfg.conversation_targets.get(cfg.group):
            raise ValueError('请先确认备份的会话身份')
        try:
            memory = ConversationMemory(cfg.runtime, cfg.memory_scope)
        except MemoryPersistenceError as error:
            raise ValueError(str(error)) from None
        notes = CompanionStore(cfg.runtime, cfg.memory_scope)
        self._checked_snapshot(session, cfg)
        export_backup(path, password, memory, notes)
        return {'saved': True}

    def backup_preview(self, session, path, password):
        _, cfg = self._session(session)
        self._backup = None
        if not cfg.conversation_targets.get(cfg.group):
            raise ValueError('请先确认备份的会话身份')
        path = Path(path)
        revision = file_revision(path)
        info = preview_backup(path, password, cfg.memory_scope)
        if file_revision(path) != revision:
            raise ValueError('备份文件已变化，请重新核对')
        self._checked_snapshot(session, cfg)
        token = uuid4().hex
        self._backup = (token, path, revision, cfg.memory_scope, monotonic() + 300)
        return {'token': token, 'name': path.name, **info}

    def restore(self, session, token, password):
        from .selection import stop_confirmed
        from .windows import worker_scope
        settings, cfg = self._session(session)
        backup = self._backup
        self._backup = None
        if (not backup or backup[0] != token or backup[3] != cfg.memory_scope or backup[4] < monotonic()
                or file_revision(backup[1]) != backup[2]):
            raise ValueError('备份预览已过期或文件有变化，请重新核对')
        preview_backup(backup[1], password, cfg.memory_scope)
        try:
            state = self._active(settings, cfg)
        except ControlUnavailable:
            if worker_scope(self.root) is not None or (cfg.runtime / 'control.json').exists():
                raise ValueError('无法确认后台状态，请稍后重试合并') from None
            state = None
        if state:
            if state.get('pending_count'):
                raise ValueError('仍有待发回复，请等本轮结束再合并备份')
            if state.get('can_stop_if_idle'):
                self.client.stop_if_idle()
                self.client.wait_stopped()
            else:
                stop_confirmed(self.root, self.client)
        try:
            self._checked_snapshot(session, cfg)
            if file_revision(backup[1]) != backup[2]:
                raise ValueError('备份文件已变化，请重新核对')
            restore_backup(backup[1], password, ConversationMemory(cfg.runtime, cfg.memory_scope),
                           CompanionStore(cfg.runtime, cfg.memory_scope))
        finally:
            if state:
                mode = state.get('mode')
                self.client.start(initial_mode=mode if mode in ('paused', 'observe', 'auto') else 'observe')
        return {'saved': True}
