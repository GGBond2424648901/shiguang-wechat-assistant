"""Activation may precede history navigation, but must never edit an unsafe chat."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('fault', ['', 'unsafe', 'wrong_chat', 'draft', 'popup', 'disabled', 'user_input', 'moved'])
def test_detached_history_can_activate_without_bypassing_editor_guards(monkeypatch, fault):
    import win32gui
    from xiaomao import windows
    from test_vision import binding

    b = binding()
    b.window_kind = 'detached'
    b.target_group = '示例会话'
    desktop = windows.WindowsDesktop(b)
    events, frames = [], []
    info = {'rect': (100, 100, 900, 700)}

    def read(image, calibration):
        frames.append(image)
        return SimpleNamespace(safe=fault != 'unsafe',
                               group='其他会话' if fault == 'wrong_chat' else b.target_group,
                               at_bottom=False, draft_empty=fault != 'draft')

    desktop._wake_vision = SimpleNamespace(read_controls=read)
    monkeypatch.setattr(desktop, 'capture', lambda: object())
    monkeypatch.setattr(desktop, '_bound_details', lambda: info)
    monkeypatch.setattr(windows, 'U', SimpleNamespace(GetLastActivePopup=lambda h: -1 if fault == 'popup' else h))
    monkeypatch.setattr(win32gui, 'IsWindowEnabled', lambda h: fault != 'disabled')
    def to_client(h, point):
        if fault == 'moved':
            # The final geometry check must still match the click coordinates.
            desktop._bound_details = lambda: {'rect': (101, 100, 901, 700)}
        return (400, 520)
    monkeypatch.setattr(win32gui, 'ScreenToClient', to_client)
    monkeypatch.setattr(win32gui, 'SendMessageTimeout', lambda *args: events.append(args))
    desktop._activate_idle_editor(lambda: fault != 'user_input' or len(frames) < 2)
    if fault:
        assert events == []
    else:
        assert len(frames) == 2
        assert [event[1] for event in events] == [0x201, 0x202]
        assert all(event[0] == b.hwnd for event in events)
        # Only a mouse click; text input and send remain behind Engine checks.

