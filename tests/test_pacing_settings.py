"""Direct pacing saves preserve settings changed after the editor opened."""
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from xiaomao.config import Settings
from xiaomao.gui import Panel


class IdleClient:
    def __init__(self, directory):
        self.directory = directory
        self.restarted = None
        self.stopped = False

    def status(self):
        return {'mode': 'auto', 'can_stop_if_idle': True, 'pending_count': 0}

    def stop_if_idle(self):
        self.stopped = True

    def wait_stopped(self):
        assert self.stopped

    def start(self, *, initial_mode):
        self.restarted = Settings.load(self.directory)
        return {'mode': initial_mode}


@pytest.fixture
def pacing_panel(tmp_path):
    cfg = Settings(tmp_path, group='甲', group_names=('甲', '乙'), natural_pacing=False,
                   conversation_targets={'甲': {'account': 'test', 'id': 'a@chatroom', 'kind': 'group'},
                                         '乙': {'account': 'test', 'id': 'b@chatroom', 'kind': 'group'}})
    cfg.save()
    client = IdleClient(tmp_path)
    p = SimpleNamespace(directory=tmp_path, settings=cfg, client=client, jobs=[],
                        view=SimpleNamespace(render=lambda: None), show_status=lambda status: None)
    p.work = lambda action, done, failed, **kw: p.jobs.append((action, done, failed))
    return p


def apply(panel, rate, done, failed):
    assert callable(getattr(Panel, 'apply_pacing_settings', None)), 'Settings need an isolated pacing save'
    Panel.apply_pacing_settings(panel, rate, done, failed)


def test_pacing_save_loads_latest_rules_and_listeners_before_restart(pacing_panel):
    panel = pacing_panel
    completed = []
    apply(panel, (17.5, 120, 4, 2.5), lambda: completed.append('saved'), completed.append)
    current = replace(panel.settings, group='乙', group_names=('乙', '甲'), reply_mode='mentions',
                      conversation_rules={'test/a@chatroom': {'reply_mode': 'all', 'sticker_frequency': 'requested'}},
                      poll_seconds=2, idle_seconds=11, context_limit=18)
    current.save()
    action, done, _ = panel.jobs.pop()
    result = action()
    done(result)
    saved = Settings.load(panel.directory)
    expected = asdict(current)
    expected.update(min_interval=17.5, hourly_limit=120, minute_limit=4, pacing_jitter=2.5, natural_pacing=True)
    assert asdict(saved) == expected
    assert panel.settings == saved == panel.client.restarted
    assert completed == ['saved']
    assert result['mode'] == 'auto'


def test_pacing_save_preserves_edit_that_happens_while_waiting_for_idle(pacing_panel):
    panel = pacing_panel
    changed = replace(panel.settings, reply_mode='mentions')
    panel.client.stop_if_idle = changed.save
    panel.client.wait_stopped = lambda: None
    apply(panel, (17, 120, 4, 2), lambda: None, lambda error: None)
    action, _, _ = panel.jobs.pop()
    with pytest.raises(ValueError, match='设置已'):
        action()
    assert Settings.load(panel.directory) == changed
    assert panel.client.restarted is None


def test_desktop_pacing_saves_faster_continuations_and_higher_caps(pacing_panel):
    from xiaomao.desktop_settings import DesktopSettings

    service = DesktopSettings(pacing_panel.directory, pacing_panel.client)
    before = service.read()
    updated = service.pacing(before['revision'], 3, 3600, 60, 2.5, continuation=.75)
    assert updated['pacing'] == [3, 3600, 60, 2.5]
    assert updated['continuationInterval'] == .75

    # Older callers that only send the four ordinary settings keep the new value.
    changed = service.pacing(updated['revision'], 4, 600, 20, 1)
    assert changed['continuationInterval'] == .75
    with pytest.raises(ValueError, match='同轮续发'):
        service.pacing(changed['revision'], 4, 600, 20, 1, continuation=.1)
    assert Settings.load(pacing_panel.directory).continuation_interval == .75


def test_invalid_pacing_does_not_stop_or_save(pacing_panel):
    panel = pacing_panel
    errors = []
    apply(panel, (2, 120, 4, 2), lambda: None, errors.append)
    if panel.jobs:
        action, _, failed = panel.jobs.pop()
        try:
            action()
        except ValueError as error:
            failed(str(error))
    assert errors and '最短间隔' in errors[0]
    assert not panel.client.stopped
    assert Settings.load(panel.directory) == panel.settings
