"""First calibration follows one visible WeChat main window and binds its exact recipient."""
from copy import deepcopy
from dataclasses import asdict, replace
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from xiaomao import selection
from xiaomao.config import Settings, TARGET_GROUP
from xiaomao.contacts import Contact
from xiaomao.vision import TextLine


WINDOW = dict(hwnd=42, pid=101, width=900, height=650)
PEER = Contact('peer-id', 'account-a', '摄影搭档', 'private')
GROUP = Contact('fixture@chatroom', 'account-a', '镜头交流', 'group')
SYSTEM = Contact('filehelper', 'account-a', '文件传输助手', 'service', selectable=False, reason='系统服务')


class PaintedDesktop:
    def __init__(self, binding):
        self.calibration = binding
        self.count = 0

    def capture(self):
        self.count += 1
        return Image.new('RGB', (self.calibration.width, self.calibration.height))

    def validate_target(self, frame):
        assert self.calibration.target_group in (PEER.name, GROUP.name)


def mock_capture(monkeypatch, root, headers, rows):
    monkeypatch.setattr(selection, '_initial_window', lambda: WINDOW)
    monkeypatch.setattr(selection, 'load_directory', lambda *a: pytest.fail('unbound disk settings used'))
    seen = []

    def directory(cfg, cancelled=None):
        assert cfg.binding.hwnd == WINDOW['hwnd'] and cfg.binding.pid == WINDOW['pid']
        seen.append(cfg.binding)
        return list(rows)

    monkeypatch.setattr(selection, 'directory_for_settings', directory)
    def factory(binding):
        desktop = PaintedDesktop(binding)
        seen.append(desktop)
        return desktop

    tokens = iter(headers)
    def run():
        return selection.capture_calibration(root, TARGET_GROUP, desktop_factory=factory,
            recognizer=lambda _: [TextLine(next(tokens), (1, 0, 80, 22), .99)])
    return run, seen


class Worker:
    def __init__(self, root):
        self.root, self.events = root, []

    def stop(self):
        self.events.append('stop')

    def wait_stopped(self):
        self.events.append('wait')

    def status(self):
        return dict(running=True, mode='paused')

    def start(self,initial_mode=None):
        assert initial_mode == 'paused'
        self.events.append('start')
        saved = Settings.load(self.root)
        return dict(group=saved.group, group_names=list(saved.allowed_groups), reply_mode=saved.reply_mode, mode=initial_mode)


@pytest.mark.parametrize('contact,headers', [
    (PEER, [PEER.name, PEER.name]),
    (GROUP, [GROUP.name+'(18)', GROUP.name+'(19)']),
])
def test_bootstrap_preview_then_save_uses_captured_recipient_and_drops_placeholder(tmp_path, monkeypatch, contact, headers):
    cfg = Settings(tmp_path)
    cfg.save()
    cfg = Settings.load(tmp_path)
    run, seen = mock_capture(monkeypatch, tmp_path, headers, [PEER, GROUP, SYSTEM])
    image, captured = run()
    assert image.size == (900, 650)
    assert seen[1].count == 2 and not captured.bottom_confirmed
    assert captured.target_group == contact.name and captured.target_kind == contact.kind
    assert captured._capture_contact.binding == contact.binding
    assert '_capture_contact' not in asdict(captured) and Settings.load(tmp_path) == cfg
    reviewed = deepcopy(captured)
    worker = Worker(tmp_path)
    with pytest.raises(ValueError, match='确认'):
        selection.switch_group(tmp_path, contact.name, worker, binding=reviewed, contact=contact)
    assert not worker.events
    reviewed.bottom_confirmed = True
    selection.switch_group(tmp_path, contact.name, worker, binding=reviewed, contact=contact)
    saved = Settings.load(tmp_path)
    assert worker.events == ['stop', 'wait', 'start']
    assert saved.saved_groups == saved.allowed_groups == (contact.name,)
    assert saved.conversation_targets == {contact.name:contact.binding}
    assert saved.binding.pid == WINDOW['pid'] and saved.binding.target_kind == contact.kind


@pytest.mark.parametrize('rows,headers,error', [
    ([PEER, GROUP], [PEER.name, GROUP.name+'(2)'], '变化'),
    ([replace(PEER, selectable=False, reason='同名')], [PEER.name], '同名'),
    ([PEER, replace(GROUP,account='account-b')], [PEER.name], '登录账号'),
    ([SYSTEM], [SYSTEM.name], '系统服务'),
    ([PEER], ['完全不同'], '联系人列表'),
])
def test_bootstrap_rejects_unstable_or_ambiguous_chat(tmp_path, monkeypatch, rows, headers, error):
    run, _ = mock_capture(monkeypatch, tmp_path, headers, rows)
    with pytest.raises(ValueError, match=error):
        run()
    assert Settings.load(tmp_path).binding is None


@pytest.mark.parametrize('what', ['changed-account', 'changed-recipient', 'changed-window', 'stale-directory'])
def test_first_save_rechecks_identity_before_stopping_any_worker(tmp_path, monkeypatch, what):
    rows = [PEER]
    run, _ = mock_capture(monkeypatch, tmp_path, [PEER.name]*2, rows)
    _, binding = run()
    binding.bottom_confirmed = True
    current = PEER
    if what == 'changed-window':
        monkeypatch.setattr(selection, '_initial_window', lambda: dict(WINDOW, pid=WINDOW['pid'] + 1))
    elif what == 'stale-directory':
        rows.clear()
    else:
        current = replace(PEER, account='account-b') if what == 'changed-account' else replace(PEER, id='other')
        rows[:] = [current]
    worker = Worker(tmp_path)
    with pytest.raises(ValueError):
        selection.switch_group(tmp_path, PEER.name, worker, binding=binding, contact=current)
    assert not worker.events and Settings.load(tmp_path).binding is None


@pytest.mark.parametrize('handles,locked,error', [
    ([(1,True,False)], False, None),
    ([(1,True,True)], False, '最小化'),
    ([(1,False,False)], False, '隐藏'),
    ([(1,True,False),(2,True,True)], False, '多个'),
    ([], False, '未找到'),
    ([(1,True,False)], True, '锁定'),
])
def test_discovery_uses_only_readonly_queries_and_refuses_ambiguous_or_hidden_windows(monkeypatch, handles, locked, error):
    states = {hwnd: (visible, iconic) for hwnd, visible, iconic in handles}
    gui = SimpleNamespace(
        EnumWindows=lambda visit, data: [visit(hwnd, data) for hwnd in states],
        GetWindow=lambda *_: 0, GetWindowText=lambda _: '微信',
        GetWindowPlacement=lambda _: (0, 0, (), (), (1, 2, 901, 652)),
        IsIconic=lambda hwnd: states[hwnd][1], IsWindowVisible=lambda hwnd: states[hwnd][0])
    monkeypatch.setitem(sys.modules, 'win32gui', gui)
    monkeypatch.setattr('xiaomao.windows.input_desktop_available', lambda: not locked)
    monkeypatch.setattr(selection, 'window_details', lambda hwnd: dict(WINDOW, hwnd=hwnd))
    if error:
        with pytest.raises(ValueError, match=error):
            selection._initial_window()
    else:
        assert selection._initial_window()['hwnd'] == 1
