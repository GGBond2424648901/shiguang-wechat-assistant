"""Private, account-scoped daily-news preferences at the desktop boundary."""

from .account_switching import configured_account
from .client import ControlUnavailable
from .config import Settings
from .news import CATEGORIES, default_subscription, validate_subscription
from .news_store import NewsStore


class DesktopNews:
    def __init__(self, root, client, read_only=False):
        self.root, self.client, self.read_only = root, client, read_only

    def _settings(self):
        settings = Settings.load(self.root)
        return settings, configured_account(settings) or 'unbound'

    def _store(self): return NewsStore(self.root / 'runtime')

    def read(self):
        settings, account = self._settings()
        store = self._store()
        try:
            return dict(subscriptions=store.list_subscriptions(account), categories=CATEGORIES,
                        defaultSubscription=default_subscription(), accountReady=account != 'unbound')
        finally: store.close()

    def state(self, subscription):
        if not isinstance(subscription, str) or len(subscription) > 64:
            raise ValueError('订阅编号无效')
        settings, account = self._settings()
        store = self._store()
        try:
            history = store.history(account, subscription)
            try:
                status = self.client.status()
                connected = (not status.get('account_scope') or status['account_scope'] == configured_account(settings))
                reason = '' if connected else '后台正在切换微信账号'
            except ControlUnavailable:
                connected, reason = False, '助手后台尚未连接，定时推送将在启动后恢复'
            return dict(connected=connected, edition=history[0] if history else None, history=history, reason=reason)
        finally: store.close()

    def _invalidate(self, account, identity):
        try: self.client.news_invalidate(account, identity)
        except ControlUnavailable: pass

    def save(self, subscription, expected=None):
        if self.read_only: raise ValueError('当前为只读预览')
        if not isinstance(subscription, dict): raise ValueError('资讯订阅字段无效')
        # targets are host authority, never renderer-provided names or scopes.
        value = {key: value for key, value in subscription.items() if key not in ('targets', 'revision')}
        value = validate_subscription(value)
        settings, account = self._settings()
        targets = []
        for session in value['recipients']:
            name = next((n for n in settings.allowed_groups if settings.conversation_pause_key(n) == session), None)
            if not name or not settings.conversation_targets.get(name):
                raise ValueError('目标已离开监听范围或身份尚未核实，请重新选择')
            targets.append(dict(session=session, name=name, scope=settings.conversation_scope(name)))
        if value['enabled'] and not targets:
            raise ValueError('开启定时推送前，请先选择已确认身份的目标')
        store = self._store()
        try:
            previous = store.get_subscription(account, value['id'])
            if previous and previous['revision'] != expected:
                raise ValueError('订阅已在其他窗口更新，草稿仍保留，请重新加载')
            if previous: self._invalidate(account, value['id'])
            return store.save_subscription(account, {**value, 'targets': targets}, expected)
        finally: store.close()

    def delete(self, subscription, expected):
        if self.read_only: raise ValueError('当前为只读预览')
        _, account = self._settings()
        store = self._store()
        try:
            current = store.get_subscription(account, subscription)
            if current is None or current['revision'] != expected:
                raise ValueError('订阅已更新，请刷新后再删除')
            self._invalidate(account, subscription)
            store.delete_subscription(account, subscription, expected)
            return {'deleted': True}
        finally: store.close()

    def command(self, subscription, action, edition=None):
        if self.read_only: raise ValueError('当前为只读预览')
        if action not in ('preview', 'push', 'cancel'):
            raise ValueError('资讯操作无效')
        settings, account = self._settings()
        status = self.client.status()
        if status.get('account_scope') and status['account_scope'] != configured_account(settings):
            raise ValueError('后台正在切换微信账号，请稍后再试')
        return self.client.news_command(subscription, action, edition)
