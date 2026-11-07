from xiaomao.config import Settings
from xiaomao.domain import Message
from xiaomao.model import ChatModel
from xiaomao.persona import DEFAULT,save_persona


def test_merged_persona_can_opt_in_to_wechat_rules_without_discarding_sources(tmp_path):
    source='# 个性化大肥鱼·超拟人软萌聊天人格V3.0\n新增的详细场景\n'+DEFAULT+'\n【微信语言执行约定·V3.0】\n执行微信语言检查，原文完整保留\n'
    save_persona(tmp_path,source)
    model=ChatModel(Settings(tmp_path))
    assert model.finalize_wechat_bubble('行，给你留着。',[Message('行吧，知道啦',direction='out')],[])=='给你留着'
    literal='C:/data/a.json'
    assert model.finalize_wechat_bubble(literal,[],[Message('原样输出路径')])==literal
    assert source in model._persona_instructions([])


def test_custom_persona_without_opt_in_keeps_its_punctuation(tmp_path):
    save_persona(tmp_path,'# 一个作家\n保留规范的句号。')
    assert ChatModel(Settings(tmp_path)).finalize_wechat_bubble('收到。',[],[])=='收到。'


def test_merged_persona_proactive_uses_recent_voice_history(tmp_path, monkeypatch):
    from xiaomao.companion import Candidate
    save_persona(tmp_path,'# 合并主稿\n【微信语言执行约定·V3.0】\n原规则保留')
    model=ChatModel(Settings(tmp_path))
    captured=[]
    monkeypatch.setattr('xiaomao.model.history_hint',lambda messages:'最近句首需轮换：行吧')
    def generate(instructions,*args,**kwargs):
        captured.append(instructions)
        return {'send':True,'text':'你那边忙完了吗'}
    monkeypatch.setattr(model,'_companion_json',generate)
    assert model.proactive([Message('行吧',direction='out')],Candidate('one','requested',1,2,0,'2026-11-07'))
    assert '最近句首需轮换：行吧' in captured[0]
