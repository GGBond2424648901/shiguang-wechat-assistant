from dataclasses import replace
import pytest
from xiaomao.config import Settings
from xiaomao.gui import apply_saved_settings


def test_slow_restart_does_not_overwrite_newer_reply_rules(tmp_path):
    original=Settings(tmp_path);original.save();original=Settings.load(tmp_path)
    newer=replace(original,reply_mode='mentions')
    class Client:
        def status(self):return {'mode':'paused','can_stop_if_idle':True}
        def stop_if_idle(self):newer.save()
        def wait_stopped(self):pass
        def start(self,**kw):return {}
    with pytest.raises(ValueError,match='设置已'):
        apply_saved_settings(Client(),original,replace(original,model_name='changed'))
    assert Settings.load(tmp_path).reply_mode=='mentions'


def test_configuration_roundtrip_compares_equal(tmp_path):
    cfg=Settings(tmp_path);cfg.save()
    assert Settings.load(tmp_path)==cfg


def test_stale_snapshot_save_cannot_erase_saved_rule(tmp_path):
    original=Settings(tmp_path);original.save();original=Settings.load(tmp_path)
    replace(original,reply_mode='mentions').save()
    with pytest.raises(ValueError,match='设置已'):
        replace(original,sticker_frequency='expressive').save(expected=original)
    assert Settings.load(tmp_path).reply_mode=='mentions'
