from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from xiaomao import selection, windows
from xiaomao.config import Calibration
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
    monkeypatch.setattr(selection,'preview_window',lambda *_:(image,replace(draft)))
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
