from PIL import Image
from test_vision import binding
import pytest


def test_foreground_capture_reads_verified_visible_pixels_not_stale_paint_cache(monkeypatch):
    from xiaomao.windows import WindowsDesktop
    desktop=WindowsDesktop(binding());events=[]
    details={'width':800,'height':600,'rect':(10,20,810,620)}
    desktop._background_binding=lambda:details
    desktop.check=lambda:events.append('check') or details
    desktop.is_foreground=lambda:True
    desktop._dpi_scale=lambda:1
    monkeypatch.setattr('PIL.ImageGrab.grab',lambda **kwargs:events.append(kwargs) or Image.new('RGB',(800,600),'white'))
    def cached(*args):raise AssertionError('Foreground must not use cached PrintWindow')
    monkeypatch.setattr('xiaomao.windows.capture_window',cached)
    result=desktop.capture()
    assert result.size==(800,600) and events==['check',{'bbox':details['rect'],'all_screens':True},'check']


def test_background_capture_does_not_raise_window_or_read_other_apps(monkeypatch):
    from xiaomao.windows import WindowsDesktop
    desktop=WindowsDesktop(binding());events=[]
    desktop._background_binding=lambda:{'width':800,'height':600,'rect':(10,20,810,620)}
    desktop.is_foreground=lambda:False;desktop._dpi_scale=lambda:1
    monkeypatch.setattr('xiaomao.windows.capture_window',lambda *a:events.append('window_only') or Image.new('RGB',(800,600),'white'))
    def capture(**kwargs):raise AssertionError('Must not capture occluding apps')
    monkeypatch.setattr('PIL.ImageGrab.grab',capture)
    desktop.capture();assert events==['window_only']


def test_visible_capture_rejects_window_movement_and_capture_failure(monkeypatch):
    from xiaomao.windows import WindowsDesktop,DesktopTemporarilyHidden
    desktop=WindowsDesktop(binding())
    details={'width':800,'height':600,'rect':(10,20,810,620)}
    moved={**details,'rect':(20,20,820,620)}
    desktop._background_binding=lambda:details
    desktop.is_foreground=lambda:True
    checks=iter([details,moved]);desktop.check=lambda:next(checks)
    monkeypatch.setattr('PIL.ImageGrab.grab',lambda **kwargs:Image.new('RGB',(800,600)))
    with pytest.raises(DesktopTemporarilyHidden):desktop.capture()
    desktop.check=lambda:details
    def failed(**kwargs):raise OSError('capture failed')
    monkeypatch.setattr('PIL.ImageGrab.grab',failed)
    with pytest.raises(DesktopTemporarilyHidden):desktop.capture()
