import pytest


def test_persona_can_be_changed_without_editing_code(tmp_path):
    from xiaomao.persona import load_persona,save_persona
    assert '小玲' in load_persona(tmp_path)
    save_persona(tmp_path,'# 小玲\n温柔、清晰，技术问题先解释原因。')
    assert load_persona(tmp_path)=='# 小玲\n温柔、清晰，技术问题先解释原因。'


def test_persona_editor_rejects_accidental_plaintext_api_key(tmp_path):
    from xiaomao.persona import save_persona
    value='# 人格\nsk-'+'a'*32
    with pytest.raises(ValueError):
        save_persona(tmp_path,value)
    assert not (tmp_path/'config'/'persona.md').exists()


def test_sections_round_trip_original_and_edit_one_duplicate_heading(tmp_path):
    from dataclasses import replace
    from xiaomao.persona import join_persona, load_persona, save_persona, split_persona
    raw = '前言  \r\n# 渐青\r\n这是身份\r\n\r\n【语言风格】\r\n慢慢说\r\n\r\n## 我的自定义\r\n```md\r\n# 示例\r\n```\r\n【语言风格】\r\n话题结束就收住\r\n'
    save_persona(tmp_path, raw)
    sections = split_persona(load_persona(tmp_path))
    assert [s.category for s in sections] == ['custom','identity','style','custom','style']
    assert join_persona(sections) == raw
    sections[-1] = replace(sections[-1], body='在句尾保留句号\r\n')
    save_persona(tmp_path, join_persona(sections))
    assert (tmp_path/'config/persona.md').read_bytes() == raw.replace('话题结束就收住','在句尾保留句号').encode()


def test_editing_default_voice_stops_the_default_postprocessor(tmp_path):
    from dataclasses import replace
    from xiaomao.config import Settings
    from xiaomao.model import ChatModel
    from xiaomao.persona import DEFAULT, join_persona, save_persona, split_persona
    from xiaomao.wechat_voice import SECTION
    model = ChatModel(Settings(tmp_path))
    assert model.finalize_wechat_bubble('浪来了。', [], []) == '浪来了'
    updated = [replace(s, body='表达按常用书面语，保留句号。\n') if s.heading == SECTION else s
               for s in split_persona(DEFAULT)]
    save_persona(tmp_path, join_persona(updated))
    assert model.finalize_wechat_bubble('浪来了。', [], []) == '浪来了。'
    save_persona(tmp_path, DEFAULT+'\n【表达风格】\n我写的每句话都要以句号结束。\n')
    assert model.finalize_wechat_bubble('浪来了。', [], []) == '浪来了。'


def test_persona_title_identifies_the_character_even_when_it_mentions_voice():
    from xiaomao.persona import DEFAULT, split_persona
    title=split_persona(DEFAULT)[0]
    assert title.category=='identity' and '你是小玲' in title.body
