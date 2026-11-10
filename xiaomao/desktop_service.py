"""Typed command boundary for the local desktop panel."""

from pathlib import Path
import re
from threading import RLock

from .account_switching import configured_account
from .client import ControlClient, ControlUnavailable
from .config import Settings
from .desktop_calibration import DesktopCalibration
from .desktop_catalog import DesktopCatalog
from .desktop_companion import DesktopCompanion
from .desktop_import import DesktopImportQueue
from .desktop_persona import DesktopPersona
from .desktop_settings import DesktopSettings, config_revision
from .desktop_sessions import DesktopSessions
from .member_names import safe_name
from .memory import ConversationMemory
from .receipts import receipt_page
from .reply_preferences import persist_preferences
from .ui_labels import member_label

MODES = ('auto', 'observe', 'paused', 'starting', 'stopped', 'disconnected')
STATES = ('candidate', 'generated', 'staged', 'sending', 'awaiting_receipt', 'unknown', 'verified', 'failed', 'skipped')


def clean_text(value, limit=360):
    if not isinstance(value, str):
        return ''
    return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[密钥已隐藏]', value)[:limit]


class DesktopService:
    def __init__(self, root, *, client=None, read_only=False):
        self.root = Path(root).resolve()
        self.client = client or ControlClient(self.root)
        self.read_only = read_only
        self.catalog = DesktopCatalog(self.root, read_only=read_only)
        self.settings = DesktopSettings(self.root, self.client)
        self.persona = DesktopPersona(self.root)
        self.sessions = DesktopSessions(self.root, self.client)
        self.calibration = DesktopCalibration(self.root, self.client)
        self.companion = DesktopCompanion(self.root, self.client, read_only)
        self._receipts = {}
        self._receipt_lock = RLock()
        self._mutations = RLock()
        self.events = lambda topic, payload: None
        self.imports = DesktopImportQueue(self.root, self.catalog,
                                          lambda payload: self.events('imports', payload))
        self._readers = {
            'view.status': self.status, 'view.queue': self.queue,
            'stickers.query': self.stickers_query, 'stickers.detail': self.stickers_detail,
            'stickers.guide': self.catalog.guide, 'stickers.image': self.stickers_image,
            'imports.list': self.imports.snapshot, 'imports.preview': self.imports.preview,
            'appearance.read': self.settings.appearance, 'settings.read': self.settings.read,
            'persona.read': self.persona.read, 'persona.effective': self.persona.effective,
            'sessions.read': self.sessions.read, 'sessions.directory': self.sessions.directory,
            'companion.read': self.companion.read, 'companion.state': self.companion.state,
            'companion.memory': self.companion.memory, 'companion.preset': self.companion.preset,
        }
        self._writers = {
            'run.start': self.start, 'run.mode': self.mode, 'run.selected': self.selected,
            'run.recover': self.recover, 'run.stop': self.stop,
            'stickers.preferences': self.catalog.save_preferences,
            'stickers.saveDetail': self.catalog.save_detail,
            'stickers.frequency': self.stickers_frequency,
            'imports.pick': self.imports.pick, 'imports.remove': self.imports.remove,
            'imports.analyze': self.imports.analyze, 'imports.cancel': self.imports.cancel_analysis,
            'imports.commit': self.imports.commit,
            'appearance.save': self.settings.save_appearance,
            'settings.save': self.settings.save, 'settings.pacing': self.settings.pacing,
            'settings.credentials': self.settings.credentials, 'settings.removeKey': self.settings.remove_key,
            'settings.testModel': self.settings.test_model,
            'persona.save': self.persona.save, 'persona.saveDocument': self.persona.save_document,
            'persona.importText': self.persona.import_text, 'persona.importFiles': self.persona.import_files,
            'persona.export': self.persona.export,
            'sessions.save': self.sessions.save, 'sessions.rules': self.sessions.rules,
            'sessions.remove': self.sessions.remove,
            'calibration.discover': self.calibration.discover, 'calibration.openWechat': self.calibration.open_wechat,
            'calibration.capture': self.calibration.capture, 'calibration.identify': self.calibration.identify,
            'calibration.save': self.calibration.save,
            'companion.save': self.companion.save, 'companion.command': self.companion.command,
            'companion.export': self.companion.export, 'companion.backupPreview': self.companion.backup_preview,
            'companion.restore': self.companion.restore,
        }

    def call(self, method, params):
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ValueError('操作参数无效')
        if method == 'calibration.cancel':
            return self.calibration.cancel(**params)
        if method in self._readers:
            return self._readers[method](**params)
        action = self._writers.get(method)
        if action is None:
            raise ValueError('不支持的界面操作')
        if self.read_only:
            raise ValueError('当前为只读预览')
        with self._mutations:
            return action(**params)

    def _session(self, cfg, identity):
        for name in cfg.allowed_groups:
            if cfg.conversation_pause_key(name) == identity:
                return name
        raise ValueError('监听会话已变化，请重新选择')

    def _connected_status(self, cfg):
        status = self.client.status()
        account = status.get('account_scope')
        if account and account != configured_account(cfg):
            raise ControlUnavailable('正在核对已切换的微信账号')
        return status

    def status(self):
        cfg = Settings.load(self.root)
        try:
            data = self._connected_status(cfg)
            connected = True
        except ControlUnavailable as error:
            data = {'mode': 'disconnected', 'reason': str(error)}
            connected = False
        states = {row['name']: row for row in data.get('groups', ()) if isinstance(row.get('name'), str)}
        sessions = []
        for name in (() if self.sessions.is_placeholder(cfg) else cfg.allowed_groups):
            row = states.get(name, {})
            target = cfg.conversation_targets.get(name, {})
            mode = row.get('mode', 'paused' if cfg.is_conversation_paused(name) else data.get('mode', 'observe'))
            sessions.append({
                'id': cfg.conversation_pause_key(name), 'name': name, 'kind': target.get('kind', 'group'),
                'bound': bool(target), 'mode': mode if mode in MODES else 'observe',
                'replyMode': row.get('reply_mode', cfg.for_group(name).reply_mode),
                'stickerFrequency': row.get('sticker_frequency', cfg.for_group(name).sticker_frequency),
                'reason': clean_text(row.get('reason')),
                'calibrated': bool(row.get('calibrated', cfg.binding_for(name))),
                'windowState': clean_text(row.get('window_state')),
                'pending': int(row.get('pending_count') or 0),
                'lastResult': self._last_result(row.get('last_result')),
            })
        mode = data.get('mode')
        counts = data.get('counts') or {}
        return {
            'connected': connected, 'readOnly': self.read_only,
            'mode': mode if mode in MODES else 'observe',
            'reason': clean_text(data.get('reason')),
            'accountLabel': clean_text(data.get('account_display_name', cfg.account_display_name), 80),
            'primary': cfg.conversation_pause_key(cfg.group) if sessions else '', 'sessions': sessions,
            'tray': bool(data.get('tray', {}).get('running')),
            'stats': {'pending': int(data.get('pending_count') or 0),
                      **{key: int(counts.get(key) or 0) for key in ('verified', 'unknown', 'failed', 'skipped')}},
            'revision': config_revision(cfg),
        }

    @staticmethod
    def _last_result(row):
        if not row or row.get('state') not in STATES:
            return None
        return {'state': row['state'], 'summary': clean_text(row.get('summary')),
                'reason': clean_text(row.get('reason')), 'at': row.get('at')}

    def queue(self, session='', offset=0, limit=40, receipt_offset=0, revision=''):
        cfg = Settings.load(self.root)
        if revision and revision != config_revision(cfg):
            raise ValueError('微信账号或监听范围已变化，请刷新队列')
        names = (self._session(cfg, session),) if session else cfg.allowed_groups
        if self.sessions.is_placeholder(cfg):
            names = ()
        if (type(offset) is not int or not 0 <= offset <= 160
                or (limit is not None and (type(limit) is not int or not 1 <= limit <= 80))
                or type(receipt_offset) is not int or not 0 <= receipt_offset <= len(names) * ConversationMemory.MAX_MESSAGES):
            raise ValueError('队列分页无效')
        try:
            self._connected_status(cfg)
            pending = self.client.pending()['tasks']
        except ControlUnavailable as error:
            return {'tasks': [], 'receipts': [], 'receiptTotal': 0, 'total': 0, 'note': clean_text(str(error))}
        rows = []
        for row in pending:
            group = row.get('group', cfg.group)
            if group not in names:
                continue
            sender = row.get('sender', '')
            display = safe_name(row.get('sender_name'))
            messages = row.get('messages')
            body = ' / '.join(clean_text(m, 600) for m in messages) if isinstance(messages, list) else row.get('text', '')
            task = {
                'id': clean_text(row.get('queue_id') or row.get('id'), 100) or str(len(rows)),
                'group': group, 'sender': member_label(sender, {sender: display} if display else {}, '对方'),
                'text': clean_text(body, 1800), 'reply': clean_text(row.get('reply'), 1800),
                'state': row.get('state') if row.get('state') in STATES else 'candidate',
                'progress': clean_text(row.get('progress')),
            }
            rows.append(task)
            rows.extend({**task, 'id': clean_text(part['id'], 100), 'reply': clean_text(part['reply'], 1800),
                         'state': 'generated', 'progress': '等待前一条确认'}
                        for part in row.get('following', ()))
        with self._receipt_lock:
            recent, receipt_total, note, self._receipts = receipt_page(
                cfg, names, self._receipts, offset=receipt_offset)
        fresh = Settings.load(self.root)
        if (fresh.allowed_groups != cfg.allowed_groups
                or fresh.conversation_targets != cfg.conversation_targets
                or configured_account(fresh) != configured_account(cfg)):
            raise ValueError('微信账号或监听范围已变化，请刷新队列')
        self._connected_status(fresh)
        return {
            'tasks': rows[offset:] if limit is None else rows[offset:offset + limit],
            'total': len(rows), 'note': note,
            'receiptTotal': receipt_total,
            'receipts': [{
                'id': row.id, 'at': row.at, 'group': row.group, 'kind': row.kind,
                'text': clean_text(row.text, 1800), 'trigger': clean_text(row.trigger, 1800),
                'linked': row.linked,
            } for row in recent],
        }

    def start(self):
        self.client.start()
        return self.status()

    def mode(self, action):
        if action not in ('enable', 'observe', 'pause'):
            raise ValueError('运行模式无效')
        getattr(self.client, action)()
        return self.status()

    def selected(self, session, action):
        if action not in ('enable', 'pause'):
            raise ValueError('会话操作无效')
        cfg = Settings.load(self.root)
        name = self._session(cfg, session)
        if name not in cfg.conversation_targets:
            raise ValueError('请先校准并核对会话身份')
        self._connected_status(cfg)
        getattr(self.client, action + '_selected')(name, cfg.conversation_scope(name))
        return self.status()

    def recover(self, session):
        cfg = Settings.load(self.root)
        if self._session(cfg, session) != cfg.group:
            raise ValueError('请先选择主要会话的队列')
        if cfg.group not in cfg.conversation_targets:
            raise ValueError('请先校准并核对会话身份')
        self._connected_status(cfg)
        self.client.reply_latest(group=cfg.group, scope=cfg.conversation_scope(cfg.group))
        return self.status()

    def stop(self):
        from .selection import stop_confirmed
        stop_confirmed(self.root, self.client)
        return {'stopped': True}

    def stickers_query(self, **params):
        cfg = Settings.load(self.root)
        return {**self.catalog.query(**params), 'frequency': cfg.sticker_frequency,
                'configRevision': config_revision(cfg)}

    def stickers_detail(self, id):
        return self.catalog.detail(id)

    def stickers_image(self, id, **params):
        return self.catalog.image(id, **params)

    def stickers_frequency(self, frequency, expected):
        cfg = Settings.load(self.root)
        if config_revision(cfg) != expected:
            raise ValueError('运行设置已更新，请刷新后再保存')
        result = persist_preferences(self.root, {'sticker_frequency': frequency})
        return {'saved': True, 'offline': bool(result.get('offline'))}
