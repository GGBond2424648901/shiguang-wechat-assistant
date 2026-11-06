from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from xiaomao import windows, vision
from xiaomao.config import Calibration
from xiaomao.layout import default_calibration_regions


def resized(monkeypatch):
    old=Calibration(42,100,800,800,'设计组 (25)',default_calibration_regions('detached'),
                    True,target_group='设计组',window_kind='detached',manual_regions=True)
    desktop=windows.WindowsDesktop(old)
    info=dict(pid=100,width=600,height=720,rect=(10,10,610,730))
    pixels=Image.new('RGB',(600,720),(237,237,237))
    draw=ImageDraw.Draw(pixels)
    draw.line((5,100,594,100),fill=(213,213,213))
    draw.line((5,515,594,515),fill=(213,213,213))
    monkeypatch.setattr(windows,'input_desktop_available',lambda:True)
    monkeypatch.setattr(windows,'U',SimpleNamespace(IsIconic=lambda h:False))
    monkeypatch.setattr(desktop,'_bound_details',lambda:info.copy())
    monkeypatch.setattr(desktop,'_dpi_scale',lambda:1)
    calls=[]
    monkeypatch.setattr(windows,'capture_window',lambda *a:(calls.append(a),pixels)[1])
    monkeypatch.setattr(vision,'read_header',lambda *a:[vision.TextLine('设计组 (25)',(0,0,90,20),.99)])
    monkeypatch.setattr(vision,'calibration_send_matches',lambda *a:True)
    return desktop,old,pixels,info,calls


def test_manual_binding_resizes_only_after_two_fresh_layout_and_identity_checks(monkeypatch):
    d,old,_,_,calls=resized(monkeypatch)
    d._background_binding()
    assert len(calls)==2
    assert d.calibration is not old and (d.calibration.width,d.calibration.height)==(600,720)
    assert d.calibration.rect('input')[1]==565 and d.calibration.rect('send')[0]==480
    assert not d.calibration.manual_regions
    assert old.manual_regions and (old.width,old.height)==(800,800)


@pytest.mark.parametrize('problem',['blank','wrong_title','send_missing','moving','staged'])
def test_resize_never_guesses_layout_identity_or_interrupts_an_editor_transaction(monkeypatch,problem):
    d,old,pixels,info,calls=resized(monkeypatch)
    if problem=='blank':ImageDraw.Draw(pixels).rectangle((0,0,600,720),fill='white')
    if problem=='wrong_title':monkeypatch.setattr(vision,'read_header',lambda *a:[vision.TextLine('别的组 (25)',(0,0,90,20),.99)])
    if problem=='send_missing':monkeypatch.setattr(vision,'calibration_send_matches',lambda *a:False)
    if problem=='moving':
        def capture(*a):info['rect']=(20,20,620,740);return pixels
        monkeypatch.setattr(windows,'capture_window',capture)
    if problem=='staged':d._input_cookie=object()
    with pytest.raises(windows.DesktopUnavailable):d._background_binding()
    assert d.calibration is old
    if problem=='staged':assert not calls


def test_unchanged_manual_regions_remain_authoritative(monkeypatch):
    d,old,_,info,calls=resized(monkeypatch)
    info.update(width=800,height=800,rect=(10,10,810,810))
    d._background_binding()
    assert d.calibration is old and not calls
