from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
from PIL import Image

from xiaomao import selection, windows
from xiaomao.config import Calibration, Settings
from xiaomao.desktop_calibration import DesktopCalibration


@pytest.fixture
def editor(tmp_path, monkeypatch):
    regions = {'header': [.1,.05,.9,.15], 'messages': [.1,.16,.9,.65],
               'input': [.1,.7,.9,.85], 'send': [.7,.9,.9,.98]}
    draft = Calibration(1,2,800,600,'示例群',regions,target_group='示例群')
    image = Image.new('RGB',(800,600),'white')
    calls = []
    monkeypatch.setattr(windows,'list_calibration_windows',lambda:[
        dict(hwnd=1,pid=2,title='微信',kind='main',minimized=False)])
    monkeypatch.setattr(windows,'CalibrationWindowLease',lambda _:SimpleNamespace(close=lambda:calls.append('closed')))
    monkeypatch.setattr(windows,'activate_calibration_window',lambda *_:None)
    monkeypatch.setattr(selection,'preview_window',lambda *_,**__:(image,replace(draft)))
    service = DesktopCalibration(tmp_path,SimpleNamespace())
    window = service.discover()['windows'][0]['id']
    yield service, window, regions, calls, image
    service.cancel()


def test_first_picture_does_not_wait_for_recognition(editor,monkeypatch):
    service, window, regions, calls, _ = editor
    monkeypatch.setattr(selection,'identify_reviewed_window',lambda *a,**kw:pytest.fail('OCR blocks the first picture'))
    preview = service.capture(window,deferIdentify=True)
    assert preview['image'].startswith('data:image/jpeg;base64,') and not preview['identified']
    assert preview['regions']==regions and not calls


def test_recognition_engine_is_reused_but_identity_and_save_are_checked_again(editor,monkeypatch):
    service, window, regions, calls, image = editor
    engines = []
    def identify(cfg,draft,cancelled,**kw):
        engines.append(kw['vision'])
        return image,selection.CurrentGroup('示例群',draft)
    def verify(cfg,draft,cancelled,**kw):
        engines.append(kw['vision'])
        raise ValueError('fresh verification failed')
    monkeypatch.setattr(selection,'identify_reviewed_window',identify)
    monkeypatch.setattr(selection,'verify_reviewed_window',verify)
    first=service.capture(window,deferIdentify=True)
    service.identify(first['token'],regions)
    service.identify(first['token'],regions)
    with pytest.raises(ValueError,match='fresh verification'):
        service.save(first['token'],regions,True)
    service.cancel()
    service.capture(window)
    assert len(engines)==4 and all(engine is engines[0] for engine in engines)


def test_deferred_identity_mismatch_still_offers_the_observed_conversation(editor,monkeypatch):
    service, window, regions, _, _=editor
    preview=service.capture(window,deferIdentify=True)
    def changed(*a,**kw):raise selection.CalibrationIdentityError('请确认新对象','另一个示例群','group')
    monkeypatch.setattr(selection,'identify_reviewed_window',changed)
    result=service.identify(preview['token'],regions)
    assert result['observed']=='另一个示例群' and not result['identified']
    assert result['target']=='示例群' and result['image']==preview['image']


@pytest.mark.parametrize('kind,version,available', [
    ('main', (4, 2, 0, 13), True), ('main', (4, 1, 7, 59), False),
    ('detached', (4, 2, 0, 13), True), ('detached', (4, 1, 7, 59), False),
])
def test_supported_window_offers_voice_selection(tmp_path, monkeypatch, kind, version, available):
    from test_windows_wechat_voice import binding, composer

    calibrated = replace(binding(), window_kind=kind)
    image = composer()
    info = {'hwnd': calibrated.hwnd, 'pid': calibrated.pid, 'title': '微信', 'kind': kind,
            'minimized': False, 'width': calibrated.width, 'height': calibrated.height, 'scale': 1}
    monkeypatch.setattr(windows, 'list_calibration_windows', lambda: [info])
    monkeypatch.setattr(windows, 'CalibrationWindowLease', lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(windows, 'activate_calibration_window', lambda *args: None)
    monkeypatch.setattr(windows, 'capture_calibration_window', lambda *args: (image, info))
    monkeypatch.setattr(windows, '_wechat_client_version', lambda _: version)
    service = DesktopCalibration(tmp_path, SimpleNamespace())
    try:
        window = service.discover()['windows'][0]['id']
        result = service.capture(window, deferIdentify=True)
        assert result['voiceSupported'] is available
        if available:
            x1, y1, x2, y2 = result['regions']['voice']
            assert x1 * 882 < 785 < x2 * 882 and y1 * 641 < 605 < y2 * 641
            assert result['regions']['input'][3] * 641 <= 579
            assert result['regions']['header'][3] * 641 >= 70
        else:
            assert 'voice' not in result['regions']
    finally:
        service.cancel()


def test_optional_voice_box_round_trips_and_can_be_removed(editor, monkeypatch):
    service, window, regions, _, image = editor
    voice = [.70, .91, .73, .96]
    proposed = {**regions, 'voice': voice}
    preview = service.capture(window, deferIdentify=True)

    def identify(cfg, draft, cancelled, **kw):
        return image, selection.CurrentGroup('示例群', draft)

    monkeypatch.setattr(selection, 'identify_reviewed_window', identify)
    assert service.identify(preview['token'], proposed)['regions']['voice'] == voice
    assert 'voice' not in service.identify(preview['token'], regions)['regions']

    def verify(cfg, draft, cancelled, **kw):
        return replace(draft, bottom_confirmed=True), None

    Settings(service.root, receive_mode='screen').save()
    service.cancel()
    window = service.discover()['windows'][0]['id']
    preview = service.capture(window, deferIdentify=True)
    monkeypatch.setattr(selection, 'verify_reviewed_window', verify)
    service.client = SimpleNamespace(start=lambda **kw: {}, reload_windows=lambda: {})
    assert service.save(preview['token'], proposed, True)['applied']
    assert Settings.load(service.root).binding_for('示例群').regions['voice'] == voice


def test_invalid_optional_voice_box_is_not_silently_saved(editor):
    service, window, regions, _, _ = editor
    preview = service.capture(window, deferIdentify=True)
    with pytest.raises(ValueError, match='校准区域'):
        service.identify(preview['token'], {**regions, 'voice': [.7, .9, .7, .99]})


def test_saving_voice_box_checks_both_fresh_calibration_images(tmp_path, monkeypatch):
    from test_windows_wechat_voice import binding, composer
    from xiaomao.vision import TextLine

    cfg = Settings(tmp_path, group='示例群', receive_mode='screen')
    calibrated = replace(binding(), target_group='示例群', header_label='示例群(3)', target_kind='group')
    calibrated.regions['voice'] = [770 / 882, 591 / 641, 800 / 882, 620 / 641]
    info = {'kind': 'main', 'width': 882, 'height': 641, 'title': '微信', 'scale': 1}
    vision = SimpleNamespace(ocr=lambda *_: [TextLine('示例群(3)', (1, 1, 90, 22), .99)],
                             read=lambda *_: SimpleNamespace(safe=True, at_bottom=True, draft_empty=True, messages=(1,)))
    monkeypatch.setattr('xiaomao.vision.calibration_send_matches', lambda *args: True)
    pixels = iter((composer(), composer(recording=True)))
    monkeypatch.setattr(windows, 'capture_calibration_window', lambda *args: (next(pixels), info))

    with pytest.raises(ValueError, match='语音按钮'):
        selection.verify_reviewed_window(cfg, calibrated, Event(), vision=vision)

    monkeypatch.setattr(windows, 'capture_calibration_window', lambda *args: (composer(), info))
    checked, _ = selection.verify_reviewed_window(cfg, calibrated, Event(), vision=vision)
    assert checked.regions['voice'] == calibrated.regions['voice']


def test_text_adaptation_preserves_reviewed_voice_box():
    from test_layout import layout_image, old_binding
    from xiaomao.layout import adapt_calibration

    old = old_binding()
    voice = [.92, .93, .95, .97]
    old.regions['voice'] = voice
    adapted = adapt_calibration(layout_image(), old)
    assert adapted is not old
    assert adapted.regions['voice'] == voice
