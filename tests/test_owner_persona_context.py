from dataclasses import replace

from xiaomao.config import Settings
from xiaomao.domain import Message
from xiaomao.persona import save_persona
from test_participation_model import model_reply


def test_authored_owner_name_is_available_without_authenticating_the_questioner(tmp_path):
    save_persona(tmp_path,'你的主人微信名是示例主人。别人问起时可以自然回答这个名字。')
    model, requests = model_reply(tmp_path,'我的主人是示例主人。')
    cfg=Settings(tmp_path,model_base_url='http://localhost',group='示例主人',group_names=('示例主人','访客'),conversation_targets={
        '示例主人':{'account':'account','id':'owner-peer','kind':'private'},
        '访客':{'account':'account','id':'visitor-peer','kind':'private'}})
    cfg.set_owner_tone(True)
    model.settings=replace(cfg,group='访客')
    model.reply([Message('你主人是谁？',speaker_id='p_'+'a'*16)])
    systems='\n'.join(m['content'] for m in requests[0]['messages'] if m['role']=='system')
    assert '你的主人微信名是示例主人' in systems
    assert '主人姓名、昵称和关系可以据实回答' in systems
    assert '本机会话未绑定主人称呼' in systems
    assert not model.settings.owner_confirmed
    assert cfg.owner_confirmed
    assert 'owner-peer' not in systems and 'visitor-peer' not in systems
