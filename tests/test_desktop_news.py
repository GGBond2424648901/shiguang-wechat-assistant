from dataclasses import replace
import pytest

from xiaomao.client import ControlUnavailable
from xiaomao.desktop_service import DesktopService
from xiaomao.news import default_subscription
from test_desktop_services import configured, FixtureClient


def build(tmp_path):
    cfg = configured(tmp_path)
    client = FixtureClient()
    client.news_invalidate = lambda *args: client.calls.append(('invalidate', args))
    client.news_command = lambda *args: {'accepted': True, 'edition': 'edition123'}
    return cfg, client, DesktopService(tmp_path, client=client)


def test_save_reloads_and_push_uses_only_verified_scopes(tmp_path):
    cfg, client, service = build(tmp_path)
    draft = default_subscription()
    session = cfg.conversation_pause_key('小禾')
    draft.update(recipients=[session], targets=[{'name': '错误对象', 'scope': 'other/identity'}])
    saved = service.call('news.save', {'subscription': draft, 'expected': None})
    assert saved['targets'] == [{'session': session, 'name': '小禾', 'scope': cfg.conversation_scope('小禾')}]
    assert service.call('news.read', {})['subscriptions'][0] == saved
    assert service.call('news.command', {'subscription': saved['id'], 'action': 'preview'})['accepted']
    saved = service.call('news.save', {'subscription': {**saved, 'name': '新简报'}, 'expected': saved['revision']})
    assert saved['revision'] == 2 and client.calls[-1][0] == 'invalidate'
    with pytest.raises(ValueError, match='更新'):
        service.call('news.save', {'subscription': saved, 'expected': 1})


def test_news_is_account_scoped_and_stale_target_cannot_save(tmp_path):
    cfg, _, service = build(tmp_path)
    draft = default_subscription(); draft['recipients'] = [cfg.conversation_pause_key('小禾')]
    service.call('news.save', {'subscription': draft, 'expected': None})
    targets = {name: {**value, 'account': 'account-b'} for name, value in cfg.conversation_targets.items()}
    replace(cfg, conversation_targets=targets).save()
    assert service.call('news.read', {})['subscriptions'] == []
    with pytest.raises(ValueError, match='目标'):
        service.call('news.save', {'subscription': draft, 'expected': None})


def test_deleting_subscription_cancels_then_removes_and_readonly_rejects(tmp_path):
    _, client, service = build(tmp_path)
    saved = service.call('news.save', {'subscription': default_subscription(), 'expected': None})
    assert service.call('news.delete', {'subscription': saved['id'], 'expected': 1}) == {'deleted': True}
    assert client.calls[-1][0] == 'invalidate'
    assert service.call('news.read', {})['subscriptions'] == []
    readonly = DesktopService(tmp_path, client=client, read_only=True)
    for method, params in [('news.save', {'subscription': saved}), ('news.delete', {'subscription': saved['id'], 'expected': 1}), ('news.command', {'subscription': saved['id'], 'action': 'preview'})]:
        with pytest.raises(ValueError, match='只读'): readonly.call(method, params)


def test_offline_state_keeps_preferences_and_does_not_start_worker(tmp_path):
    _, client, service = build(tmp_path)
    draft = default_subscription()
    service.call('news.save', {'subscription': draft, 'expected': None})
    def offline(): raise ControlUnavailable('offline')
    client.status = offline
    state = service.call('news.state', {'subscription': draft['id']})
    assert not state['connected'] and '后台' in state['reason']
    assert len(service.call('news.read', {})['subscriptions']) == 1


def test_enabling_schedule_requires_confirmed_recipient(tmp_path):
    _, _, service = build(tmp_path)
    draft = default_subscription(); draft['enabled'] = True
    with pytest.raises(ValueError, match='目标'):
        service.call('news.save', {'subscription': draft, 'expected': None})
